// Recorder.mc - FIT session + the 16 developer RECORD fields.
//
// Field layout (see docs/PLAN.md section 5):
//   0..12  acc_0..acc_12  DOUBLE  packed accelerometer samples (2 per field, 26 slots)
//   13     press          FLOAT   Activity.Info.rawAmbientPressure (Pa)
//   14     tmr            UINT32  System.getTimer() at the accelerometer callback (ms)
//   15     meta           UINT32  Pack.packMeta(seq, nValid, state, flags)
// Phase 2 (only after the field-count spike test passes on the watch):
//   20/21  lap_air/lap_h  FLOAT   LAP fields written at each live-detected jump
//   30     jumps          UINT16  SESSION field
using Toybox.ActivityRecording;
using Toybox.Activity;
using Toybox.FitContributor as Fit;
using Toybox.System;
using Toybox.Lang;

class Recorder {
    const N_ACC_FIELDS = 13;
    static const STATE_IDLE = 0;
    static const STATE_RECORDING = 1;
    static const STATE_PAUSED = 2;
    const ENABLE_LAP_FIELDS = false;      // phase 2 - flip after spike test S2

    var session = null;
    var accFields = new [N_ACC_FIELDS];
    var pressField = null;
    var tmrField = null;
    var metaField = null;
    var lapAirField = null;
    var lapHeightField = null;
    var jumpsField = null;
    var state = STATE_IDLE;
    var seq = 0;
    var batches = 0;
    var jumpCount = 0;

    function initialize() {
    }

    function isRecording() {
        return state == STATE_RECORDING;
    }

    function start() {
        if (session == null) {
            session = ActivityRecording.createSession({
                :name => "Kite",
                :sport => Activity.SPORT_KITESURFING,
                :subSport => Activity.SUB_SPORT_GENERIC
            });
            for (var i = 0; i < N_ACC_FIELDS; i++) {
                accFields[i] = session.createField("acc_" + i, i, Fit.DATA_TYPE_DOUBLE, {:mesgType => Fit.MESG_TYPE_RECORD});
            }
            pressField = session.createField("press", 13, Fit.DATA_TYPE_FLOAT, {:mesgType => Fit.MESG_TYPE_RECORD, :units => "Pa"});
            tmrField = session.createField("tmr", 14, Fit.DATA_TYPE_UINT32, {:mesgType => Fit.MESG_TYPE_RECORD, :units => "ms"});
            metaField = session.createField("meta", 15, Fit.DATA_TYPE_UINT32, {:mesgType => Fit.MESG_TYPE_RECORD});
            if (ENABLE_LAP_FIELDS) {
                lapAirField = session.createField("lap_air", 20, Fit.DATA_TYPE_FLOAT, {:mesgType => Fit.MESG_TYPE_LAP, :units => "s"});
                lapHeightField = session.createField("lap_h", 21, Fit.DATA_TYPE_FLOAT, {:mesgType => Fit.MESG_TYPE_LAP, :units => "m"});
                jumpsField = session.createField("jumps", 30, Fit.DATA_TYPE_UINT16, {:mesgType => Fit.MESG_TYPE_SESSION});
            }
        }
        session.start();
        state = STATE_RECORDING;
    }

    function pause() {
        if (session != null && state == STATE_RECORDING) {
            session.stop();
            state = STATE_PAUSED;
        }
    }

    function save() {
        if (session != null) {
            if (state == STATE_RECORDING) { session.stop(); }
            if (jumpsField != null) { jumpsField.setData(jumpCount); }
            session.save();
            session = null;
        }
        state = STATE_IDLE;
    }

    function discard() {
        if (session != null) {
            if (state == STATE_RECORDING) { session.stop(); }
            session.discard();
            session = null;
        }
        state = STATE_IDLE;
    }

    // Called once per second from the accelerometer listener with the raw
    // milli-g arrays of the batch (normally 25 samples each).
    function onBatch(xs, ys, zs, pressurePa, gpsOk) {
        if (session == null || state != STATE_RECORDING) { return; }
        var n = xs.size();
        if (n > 2 * N_ACC_FIELDS) { n = 2 * N_ACC_FIELDS; }
        var k = 0;
        for (var f = 0; f < N_ACC_FIELDS; f++) {
            var c0 = Pack.CODE_INVALID, c1 = Pack.CODE_INVALID, c2 = Pack.CODE_INVALID;
            var c3 = Pack.CODE_INVALID, c4 = Pack.CODE_INVALID, c5 = Pack.CODE_INVALID;
            if (k < n) {
                c0 = Pack.encodeMg(xs[k]); c1 = Pack.encodeMg(ys[k]); c2 = Pack.encodeMg(zs[k]);
            }
            if (k + 1 < n) {
                c3 = Pack.encodeMg(xs[k + 1]); c4 = Pack.encodeMg(ys[k + 1]); c5 = Pack.encodeMg(zs[k + 1]);
            }
            accFields[f].setData(Pack.packPair(c0, c1, c2, c3, c4, c5));
            k += 2;
        }
        var flags = 0;
        if (pressurePa != null) {
            pressField.setData(pressurePa.toFloat());
            flags = flags | 0x1;
        }
        if (gpsOk) { flags = flags | 0x2; }
        tmrField.setData(System.getTimer() & 0x7FFFFFFF);
        metaField.setData(Pack.packMeta(seq, n, state, flags));
        seq = (seq + 1) & 0xFFFF;
        batches += 1;
    }

    // Phase 2: live detector found a jump -> write a lap so Garmin Connect lists it
    function onJump(airtimeS, heightM) {
        jumpCount += 1;
        if (session != null && ENABLE_LAP_FIELDS) {
            lapAirField.setData(airtimeS);
            lapHeightField.setData(heightM);
            session.addLap();
        }
    }
}
