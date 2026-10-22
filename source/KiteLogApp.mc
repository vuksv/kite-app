// KiteLogApp.mc - application entry point.
using Toybox.Application;
using Toybox.WatchUi;
using Toybox.Lang;

const LIVE_DETECTOR = false;      // phase 2: set true to show live jump counts

class KiteLogApp extends Application.AppBase {
    var recorder;
    var hub;
    var live = null;

    function initialize() {
        AppBase.initialize();
    }

    function onStart(state) {
        recorder = new Recorder();
        if (LIVE_DETECTOR) {
            live = new LiveJumpDetector(recorder, 25);
        }
        hub = new SensorHub(recorder, live);
        hub.enable();
    }

    function onStop(state) {
        // the user left the app with a session open: keep the data
        if (recorder != null && recorder.state != Recorder.STATE_IDLE) {
            recorder.save();
        }
        if (hub != null) { hub.disable(); }
    }

    function getInitialView() {
        return [new KiteLogView(self), new KiteLogDelegate(self)];
    }
}
