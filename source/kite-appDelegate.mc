import Toybox.Lang;
import Toybox.WatchUi;

class kite_appDelegate extends WatchUi.BehaviorDelegate {

    function initialize() {
        BehaviorDelegate.initialize();
    }

    function onMenu() as Boolean {
        WatchUi.pushView(new Rez.Menus.MainMenu(), new kite_appMenuDelegate(), WatchUi.SLIDE_UP);
        return true;
    }

}