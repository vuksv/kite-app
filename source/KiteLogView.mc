// KiteLogView.mc - 176x176 2-colour MIP display.  Keeps the top-right subwindow
// circle of the Instinct 2X free (nothing drawn above y = 40 on the right side).
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

    function initialize(a) {
        View.initialize();
        app = a;
    }

    function onShow() {
        timer = new Timer.Timer();
        timer.start(method(:onTick), 1000, true);
    }

    function onHide() {
        if (timer != null) { timer.stop(); timer = null; }
    }

    function onTick() {
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
        // status line (left of the subwindow)
        var status = rec.isRecording() ? "REC" : (rec.state == Recorder.STATE_PAUSED ? "PAUSED" : "READY");
        dc.drawText(6, 4, Graphics.FONT_XTINY, status, Graphics.TEXT_JUSTIFY_LEFT);
        // GPS quality bars
        var q = hub.gpsQuality;
        for (var i = 0; i < 4; i++) {
            var y = 24 - i * 3;
            if (q > i) { dc.fillRectangle(70 + i * 6, y, 4, 4 + i * 3); } else { dc.drawRectangle(70 + i * 6, y, 4, 4 + i * 3); }
        }
        // timer (big)
        var info = Activity.getActivityInfo();
        var ms = (info != null && info.timerTime != null) ? info.timerTime : 0;
        dc.drawText(w / 2, 36, Graphics.FONT_NUMBER_MEDIUM, fmtTime(ms), Graphics.TEXT_JUSTIFY_CENTER);
        // speed in knots
        var spd = hub.speedMps;
        var spdTxt = (spd == null) ? "--.-" : (spd * 1.94384).format("%.1f");
        dc.drawText(w / 2, 86, Graphics.FONT_NUMBER_HOT, spdTxt, Graphics.TEXT_JUSTIFY_CENTER);
        dc.drawText(w / 2, 134, Graphics.FONT_XTINY, "kn", Graphics.TEXT_JUSTIFY_CENTER);
        // live jump counter (phase 2) / batch health (phase 1)
        var bottom;
        if (app.live != null) {
            bottom = "J " + app.live.jumps + "  " + app.live.lastAirtime.format("%.1f") + "s";
        } else {
            bottom = "b" + rec.batches + " n" + hub.lastBatchSize;
        }
        dc.drawText(w / 2, h - 26, Graphics.FONT_TINY, bottom, Graphics.TEXT_JUSTIFY_CENTER);
        if (!rec.isRecording() && rec.state == Recorder.STATE_IDLE) {
            dc.drawText(w / 2, h - 44, Graphics.FONT_XTINY, WatchUi.loadResource(Rez.Strings.PressStart), Graphics.TEXT_JUSTIFY_CENTER);
        }
    }
}
