pragma Singleton
import QtQuick
import QtQuick.Window

// Resize grips can live above a whole page, so a scrollbar's local z value
// cannot give it input priority. Cut only visible scrollbar targets out of
// the grips' hit regions, in window coordinates, for both hover and presses.
QtObject {
    property var _bars: []

    function registerBar(bar) { _bars.push(bar) }
    function unregisterBar(bar) {
        var index = _bars.indexOf(bar)
        if (index >= 0) _bars.splice(index, 1)
    }

    function covers(item, point) {
        for (var i = 0; i < _bars.length; ++i) {
            var bar = _bars[i]
            if (!bar || !bar.visible || !bar.enabled || bar.opacity <= 0
                    || bar.Window.window !== item.Window.window) continue
            var local = bar.mapFromItem(item, point.x, point.y)
            if (!bar.thumbContains(local)) continue
            // A scrollbar behind a clipped or fully transparent ancestor
            // cannot be hit and must not leave a hole in the resize grip.
            var ancestor = bar.parent
            var exposed = true
            while (ancestor) {
                if (ancestor.opacity <= 0) { exposed = false; break }
                if (ancestor.clip) {
                    var p = ancestor.mapFromItem(item, point.x, point.y)
                    if (p.x < 0 || p.y < 0 || p.x >= ancestor.width || p.y >= ancestor.height) {
                        exposed = false
                        break
                    }
                }
                ancestor = ancestor.parent
            }
            if (exposed) return true
        }
        return false
    }

    function resizeContains(item, point) {
        return point.x >= 0 && point.y >= 0 && point.x < item.width && point.y < item.height
            && (item.pressed || !covers(item, point))
    }
}
