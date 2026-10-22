// KiteLogDelegate.mc - buttons.  START: start / pause.  BACK while recording or
// paused: Save / Discard / Resume menu.  BACK while idle: exit.
using Toybox.WatchUi;
using Toybox.Lang;

class KiteLogDelegate extends WatchUi.BehaviorDelegate {
    var app;

    function initialize(a) {
        BehaviorDelegate.initialize();
        app = a;
    }

    function onSelect() {
        var rec = app.recorder;
        if (rec.isRecording()) {
            rec.pause();
        } else {
            rec.start();
        }
        WatchUi.requestUpdate();
        return true;
    }

    function onBack() {
        var rec = app.recorder;
        if (rec.state == Recorder.STATE_IDLE) {
            return false;                       // let the system exit the app
        }
        var menu = new WatchUi.Menu2({:title => "Session"});
        menu.addItem(new WatchUi.MenuItem(WatchUi.loadResource(Rez.Strings.Resume), null, :resume, null));
        menu.addItem(new WatchUi.MenuItem(WatchUi.loadResource(Rez.Strings.Save), null, :save, null));
        menu.addItem(new WatchUi.MenuItem(WatchUi.loadResource(Rez.Strings.Discard), null, :discard, null));
        WatchUi.pushView(menu, new SessionMenuDelegate(app), WatchUi.SLIDE_UP);
        return true;
    }
}

class SessionMenuDelegate extends WatchUi.Menu2InputDelegate {
    var app;

    function initialize(a) {
        Menu2InputDelegate.initialize();
        app = a;
    }

    function onSelect(item) {
        var id = item.getId();
        if (id == :save) {
            app.recorder.save();
        } else if (id == :discard) {
            app.recorder.discard();
        } else if (id == :resume) {
            if (!app.recorder.isRecording()) { app.recorder.start(); }
        }
        WatchUi.popView(WatchUi.SLIDE_DOWN);
    }
}
