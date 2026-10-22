// LiveJumpDetector.mc - PHASE 2 (experimental).  A deliberately simple on-watch
// detector so the display can show "jumps: N / last airtime" while riding.  The
// offline pipeline (tools/) is the authoritative analysis; this only needs to be
// cheap (25 samples/s on a ~96 KB, 120k-step watchdog device).
//
// Idea: the wrist vibrates on the water and goes quiet in the air while the
// kite still carries 50-90 % of body weight (|f| ~ 0.5-0.9 g, NOT 0 g).
//   smooth sample : |d|f|/dt| small AND 0.3 g < |f| < 1.4 g
//   candidate     : >= MIN_SMOOTH consecutive smooth samples preceded (<1.2 s) by a load peak > 1.4 g
//   landing       : first |f| > 2.0 g after the candidate -> airtime = smooth run * 40 ms
using Toybox.Math;
using Toybox.Lang;

class LiveJumpDetector {
    const MIN_SMOOTH = 15;          // 0.6 s at 25 Hz
    const SMOOTH_DIFF_MG = 90;      // |f| sample-to-sample change (milli-g)
    const LEVEL_LO = 300;
    const LEVEL_HI = 1400;
    const LOAD_PEAK = 1400;
    const IMPACT = 2000;
    const PEAK_MEMORY = 30;         // samples (1.2 s)

    var recorder;
    var lastMag = 1000;
    var smoothRun = 0;
    var sinceLoadPeak = 1000;
    var inCandidate = false;
    var jumps = 0;
    var lastAirtime = 0.0;
    var maxAirtime = 0.0;
    var samplePeriod = 0.04;

    function initialize(rec, sampleRate) {
        recorder = rec;
        samplePeriod = 1.0 / sampleRate;
    }

    function onBatch(xs, ys, zs) {
        var n = xs.size();
        for (var i = 0; i < n; i++) {
            var x = xs[i], y = ys[i], z = zs[i];
            var mag = Math.sqrt((x * x + y * y + z * z).toFloat()).toNumber();   // milli-g
            var d = mag - lastMag;
            if (d < 0) { d = -d; }
            lastMag = mag;
            sinceLoadPeak += 1;
            if (mag > LOAD_PEAK && !inCandidate) { sinceLoadPeak = 0; }
            if (inCandidate && mag > IMPACT) {
                // landing
                lastAirtime = smoothRun * samplePeriod;
                if (lastAirtime > maxAirtime) { maxAirtime = lastAirtime; }
                jumps += 1;
                recorder.onJump(lastAirtime, 0.4 * lastAirtime * lastAirtime);   // h ~ a_eff T^2 / 8, a_eff = 3.2
                inCandidate = false;
                smoothRun = 0;
                continue;
            }
            if (d < SMOOTH_DIFF_MG && mag > LEVEL_LO && mag < LEVEL_HI) {
                smoothRun += 1;
                if (!inCandidate && smoothRun >= MIN_SMOOTH && sinceLoadPeak < PEAK_MEMORY + MIN_SMOOTH) {
                    inCandidate = true;
                }
            } else {
                if (inCandidate && smoothRun > 0 && mag <= IMPACT) {
                    // rough but no impact yet: tolerate 3 samples of noise
                    smoothRun += 1;
                    if (smoothRun > 12 * 25) { inCandidate = false; smoothRun = 0; }   // > 12 s: give up
                } else {
                    smoothRun = 0;
                }
            }
        }
    }
}
