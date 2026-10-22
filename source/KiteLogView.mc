// KiteLogView.mc - 176x176 2-colour MIP display.  The Instinct 2X has a 62x62
// round subwindow in the top-right corner; it shows the recording state and GPS
// bars, the timer sits to its left, speed is the big number in the middle.
using Toybox.WatchUi;
using Toybox.Graphics;
using Toybox.System;
using Toybox.Timer;
using Toybox.Activity;
using Toybox.Position;
using Toybox.Lang;

class KiteLogView extends WatchUi.View {
    var app;
    var timer;
    var sub;                        // subwindow bounding box {x, y, w, h}

    function initialize(a) {
        View.initialize();
        app = a;
    }

    function onLayout(dc) {
        // fallback = the instinct2x subwindow position
        sub = [113, 1, 62, 62];
        if (WatchUi has :getSubscreen) {
            var r = WatchUi.getSubscreen();
            if (r != null) { sub = [r.x, r.y, r.width, r.height]; }
        }
    }

    function onShow() {
        timer = new Timer.Timer();
        timer.start(method(:onTick), 1000, true);
    }

    function onHide() {
        if (timer != null) { timer.stop(); timer = null; }
    }

    function onTick() as Void {
        WatchUi.requestUpdate();
    }

    function fmtTime(ms) {
        var s = ms / 1000;
        var h = s / 3600;
        var m = (s % 3600) / 60;
        var ss = s % 60;
        if (h > 0) {
            return h.format("%d") + ":" + m.format("%02d") + ":" + ss.format("%02d");
        }
        return m.format("%02d") + ":" + ss.format("%02d");
    }

    function onUpdate(dc) {
        var w = dc.getWidth();
        var h = dc.getHeight();
        dc.setColor(Graphics.COLOR_BLACK, Graphics.COLOR_WHITE);
        dc.clear();
        var rec = app.recorder;
        var hub = app.hub;

        // subwindow: state word + GPS quality bars
        var cx = sub[0] + sub[2] / 2;
        var cy = sub[1] + sub[3] / 2;
        var status = rec.isRecording() ? "REC" : (rec.state == Recorder.STATE_PAUSED ? "PAUSE" : "READY");
        dc.drawText(cx, cy - 20, Graphics.FONT_XTINY, status, Graphics.TEXT_JUSTIFY_CENTER);
        var q = hub.gpsQuality;
        for (var i = 0; i < 4; i++) {
            var bh = 4 + i * 3;
            var bx = cx - 13 + i * 7;
            var by = cy + 14 - bh;
            if (q > i) { dc.fillRectangle(bx, by, 5, bh); } else { dc.drawRectangle(bx, by, 5, bh); }
        }

        // timer, left of the subwindow (smaller font once it needs hours)
        var info = Activity.getActivityInfo();
        var ms = (info != null && info.timerTime != null) ? info.timerTime : 0;
        var tFont = (ms >= 3600000) ? Graphics.FONT_NUMBER_MILD : Graphics.FONT_NUMBER_MEDIUM;
        dc.drawText(sub[0] / 2 + 4, cy, tFont, fmtTime(ms), Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);

        // speed in knots
        var spd = hub.speedMps;
        var spdTxt = (spd == null) ? "--" : (spd * 1.94384).format("%.1f");
        dc.drawText(w / 2, 62, Graphics.FONT_NUMBER_THAI_HOT, spdTxt, Graphics.TEXT_JUSTIFY_CENTER);
        dc.drawText(w / 2, 112, Graphics.FONT_XTINY, "kn", Graphics.TEXT_JUSTIFY_CENTER);

        // hint while idle, then live jump counter (phase 2) / batch health (phase 1)
        if (rec.state == Recorder.STATE_IDLE) {
            dc.drawText(w / 2, h - 46, Graphics.FONT_XTINY, WatchUi.loadResource(Rez.Strings.PressStart), Graphics.TEXT_JUSTIFY_CENTER);
        }
        var bottom;
        if (app.live != null) {
            bottom = "J " + app.live.jumps + "  " + app.live.lastAirtime.format("%.1f") + "s";
        } else {
            bottom = "b" + rec.batches + " n" + hub.lastBatchSize;
        }
        dc.drawText(w / 2, h - 28, Graphics.FONT_TINY, bottom, Graphics.TEXT_JUSTIFY_CENTER);
    }
}
