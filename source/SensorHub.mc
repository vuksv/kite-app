// SensorHub.mc - GPS, 25 Hz accelerometer batches, 1 Hz barometer read.
using Toybox.Sensor;
using Toybox.Position;
using Toybox.Activity;
using Toybox.System;
using Toybox.Lang;
using Toybox.WatchUi;

class SensorHub {
    var recorder;
    var live;                       // LiveJumpDetector or null
    var speedMps = null;            // latest GPS speed
    var gpsQuality = Position.QUALITY_NOT_AVAILABLE;
    var pressurePa = null;
    var batchCount = 0;
    var lastBatchSize = 0;
    var sampleRate = 25;
    var lastBatchTimer = 0;         // System.getTimer() at the last accelerometer callback
    var restarts = 0;               // how often the listener had to be re-registered
    var listenerError = null;

    function initialize(rec, liveDetector) {
        recorder = rec;
        live = liveDetector;
    }

    function enable() {
        enableGps();
        enableAccel();
    }

    function disable() {
        Sensor.unregisterSensorDataListener();
        Position.enableLocationEvents(Position.LOCATION_DISABLE, method(:onPosition));
    }

    // Prefer multi-band on the Instinct 2X Solar, then SatIQ, then GPS+GLONASS, else plain GPS.
    function enableGps() {
        var opts = {:acquisitionType => Position.LOCATION_CONTINUOUS};
        if (Position has :hasConfigurationSupport) {
            var cfgs = [];
            if (Position has :CONFIGURATION_GPS_GLONASS_GALILEO_BEIDOU_L1_L5) { cfgs.add(Position.CONFIGURATION_GPS_GLONASS_GALILEO_BEIDOU_L1_L5); }
            if (Position has :CONFIGURATION_SAT_IQ) { cfgs.add(Position.CONFIGURATION_SAT_IQ); }
            if (Position has :CONFIGURATION_GPS_GLONASS_GALILEO_BEIDOU_L1) { cfgs.add(Position.CONFIGURATION_GPS_GLONASS_GALILEO_BEIDOU_L1); }
            if (Position has :CONFIGURATION_GPS_GLONASS) { cfgs.add(Position.CONFIGURATION_GPS_GLONASS); }
            for (var i = 0; i < cfgs.size(); i++) {
                if (Position.hasConfigurationSupport(cfgs[i])) {
                    opts[:configuration] = cfgs[i];
                    break;
                }
            }
        }
        try {
            Position.enableLocationEvents(opts, method(:onPosition));
        } catch (e) {
            Position.enableLocationEvents(Position.LOCATION_CONTINUOUS, method(:onPosition));
        }
    }

    function enableAccel() {
        var maxRate = 25;
        if (Sensor has :getMaxSampleRate) {
            var r = Sensor.getMaxSampleRate();
            if (r != null && r < maxRate) { maxRate = r; }
        }
        sampleRate = maxRate;
        var options = {
            :period => 1,                               // one callback per second
            :accelerometer => {:enabled => true, :sampleRate => sampleRate}
        };
        listenerError = null;
        try {
            Sensor.registerSensorDataListener(method(:onSensorData), options);
        } catch (e) {
            listenerError = e.getErrorMessage();
            System.println("accel listener failed: " + listenerError);
        }
        lastBatchTimer = System.getTimer();
    }

    // Called once a second by the view.  True when the accelerometer stream is silent
    // (no batch for 4 s).  Re-registers the listener after 10 s of silence (up to 6 times);
    // the screen shows "NO ACCEL" meanwhile.
    function checkAccel() {
        var since = System.getTimer() - lastBatchTimer;
        if (since < 4000) { return false; }
        if (since > 10000 * (restarts + 1) && restarts < 6) {
            restarts += 1;
            Sensor.unregisterSensorDataListener();
            enableAccel();
            lastBatchTimer = System.getTimer() - 4000;
        }
        return true;
    }

    function onPosition(info as Position.Info) as Void {
        speedMps = info.speed;
        gpsQuality = info.accuracy;
    }

    function onSensorData(sensorData as Sensor.SensorData) as Void {
        var ad = sensorData.accelerometerData;
        if (ad == null) { return; }
        var xs = ad.x;
        var ys = ad.y;
        var zs = ad.z;
        lastBatchSize = xs.size();
        batchCount += 1;
        lastBatchTimer = System.getTimer();
        // read the barometer on the same cadence so the two share one clock
        var ai = Activity.getActivityInfo();
        pressurePa = (ai != null) ? ai.rawAmbientPressure : null;
        var gpsOk = gpsQuality >= Position.QUALITY_USABLE;
        recorder.onBatch(xs, ys, zs, pressurePa, gpsOk);
        if (live != null) {
            live.onBatch(xs, ys, zs);
        }
    }
}
