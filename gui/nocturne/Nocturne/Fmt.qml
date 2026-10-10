pragma Singleton

import QtQuick

// Formatting helpers shared across views.
QtObject {

    // Seconds → "3:07" or "1:02:14".
    function duration(seconds) {
        var s = Math.max(0, Math.floor(Number(seconds) || 0))
        var h = Math.floor(s / 3600)
        var m = Math.floor((s % 3600) / 60)
        var r = s % 60
        if (h > 0)
            return h + ":" + ("0" + m).slice(-2) + ":" + ("0" + r).slice(-2)
        return m + ":" + ("0" + r).slice(-2)
    }

    // Seconds → "01:23.45", the LRC stamp. Two hundredths of a second, and
    // zero-padded minutes, because these are read as a column beside the lyric
    // lines and a ragged left edge makes a column hard to scan. Negative means
    // "this line has no time", which the lyrics view model spells as -1.
    function timecode(seconds) {
        var v = Number(seconds)
        if (!isFinite(v) || v < 0)
            return ""
        var centis = Math.round(v * 100)
        var m = Math.floor(centis / 6000)
        var s = Math.floor((centis % 6000) / 100)
        var c = centis % 100
        return ("0" + m).slice(-2) + ":" + ("0" + s).slice(-2) + "." + ("0" + c).slice(-2)
    }

    // Seconds → "1 hr 12 min" / "48 min" / "2 min".
    function longDuration(seconds) {
        var s = Math.max(0, Math.floor(Number(seconds) || 0))
        if (s < 60)
            return s + " sec"
        var h = Math.floor(s / 3600)
        var m = Math.round((s % 3600) / 60)
        if (h > 0)
            return h + " hr" + (m > 0 ? " " + m + " min" : "")
        return m + " min"
    }

    // The backend stores release dates as an integer YYYYMMDD, using 00 for
    // parts it does not know.
    function releaseYear(value) {
        var n = Number(value) || 0
        if (n <= 0)
            return ""
        var y = Math.floor(n / 10000)
        return y > 0 ? String(y) : ""
    }

    function releaseDate(value) {
        var n = Number(value) || 0
        if (n <= 0)
            return ""
        var y = Math.floor(n / 10000)
        var m = Math.floor((n % 10000) / 100)
        var d = n % 100
        if (y <= 0)
            return ""
        if (m <= 0)
            return String(y)
        var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        var label = months[m - 1] + " " + y
        return d > 0 ? d + " " + label : label
    }

    function count(n, singular, plural) {
        var v = Number(n) || 0
        return v + " " + (v === 1 ? singular : (plural || singular + "s"))
    }

    function mb(value) {
        var v = Number(value) || 0
        if (v >= 1024)
            return (v / 1024).toFixed(1) + " GB"
        return v.toFixed(0) + " MB"
    }

    // First character of a name, for placeholder art.
    function initial(text) {
        var s = String(text || "").trim()
        return s.length > 0 ? s.charAt(0).toUpperCase() : "?"
    }
}
