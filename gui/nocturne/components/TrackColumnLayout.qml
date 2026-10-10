import QtQuick
import "TrackColumns.js" as Catalog

/*
 * One column layout profile: the ordered, sized, partially-visible set of
 * columns for one kind of track list.
 *
 * ---------------------------------------------------------------------------
 * The one idea this file is built on: persisted state and derived state are
 * never the same object.
 *
 *   persisted   `order`  — the keys the user has switched on, in display order
 *               `widths` — key → the width the user *asked for*
 *
 *   derived     `renderedColumns`, `renderedWidths`, `titleWidth`, `dropped`
 *               — recomputed from the above plus the available width, every
 *                 time either changes, and never written anywhere
 *
 * Columns must always fit: this list never scrolls sideways and never clips.
 * So when the window shrinks, widths shrink, and when even the minimums will
 * not fit, the rightmost columns stop being rendered. All of that lives in the
 * derived half — which is why shrinking is free to be lossy. `widths` still
 * holds what the user chose, so widening the window restores it to the pixel.
 *
 * kroma's equivalent (gui/kroma/components/TrackColumnLayout.qml) rescales
 * `widths` in place on every resize, so a user who narrows and re-widens the
 * window never gets their layout back. Do not reintroduce that.
 *
 * ---------------------------------------------------------------------------
 * Geometry. The view hands down `rawBudget` — its own width, less the margins,
 * the lead region (row number + artwork) and the trailing overflow button. The
 * gaps *between* columns depend on how many are rendered, which is what this
 * object is deciding, so it subtracts those itself.
 *
 *   titleWidth + Σ renderedWidths === budgetFor(n)      exactly, every frame
 *
 * Title has no stored width. It is the elastic column, computed last by
 * subtraction, so every rounding error lands in the one column with room to
 * absorb it. The user resizes it by dragging the first data column's left
 * divider, which is exactly the mental model of "the title column got wider".
 *
 * A drag moves one divider and conserves the total. Dragging it left grows the
 * column it belongs to out of the columns on its left; dragging it right hands
 * that width back, and once the column is on its own floor the columns to its
 * *right* start giving way so the divider can keep going. It stops when there
 * is nothing left of the divider to grow, or nothing right of it to give.
 *
 * Every mutation reassigns `order` or `widths` wholesale rather than editing in
 * place: QML only notifies on assignment, and an in-place push would silently
 * fail to update the view.
 */
QtObject {
    id: root

    // --- configuration, set once where the profile is declared -------------

    property string profile: ""      // a key in Catalog.PROFILES
    property string settingsKey: ""  // "" means do not persist
    property var settings: null      // settingsVM

    // --- persisted state ---------------------------------------------------

    property var order: []           // visible columns, in display order
    property var widths: ({})        // key → desired width

    // --- input -------------------------------------------------------------

    /* Width available to the columns and the gaps between them, pushed down by
     * the view. Excludes margins, the lead region and the trailing button. */
    property real rawBudget: 0

    // --- derived state (never persisted) -----------------------------------

    /* Keys currently squeezed out by a narrow window. Transient: `commit`
     * cannot see this, so a column the user switched on stays on in the saved
     * layout even while there is no room to draw it. */
    property var dropped: []

    readonly property var renderedColumns: {
        var out = [];
        for (var i = 0; i < order.length; ++i) {
            if (dropped.indexOf(order[i]) !== -1)
                continue;
            var col = Catalog.entry(order[i]);
            if (col !== null)
                out.push(col);
        }
        return out;
    }

    readonly property var renderedWidths: _fit().widths
    readonly property real titleWidth: _fit().titleWidth

    /* The right-click menu: every column this profile offers, and whether it is
     * currently on. Order is the catalog's declaration order, not the user's —
     * a menu whose entries move around as you use it is unusable. */
    readonly property var menuItems: {
        var keys = Catalog.available(profile);
        var out = [];
        for (var i = 0; i < keys.length; ++i) {
            var col = Catalog.entry(keys[i]);
            if (col === null)
                continue;
            out.push({
                key: keys[i],
                label: col.label,
                visible: order.indexOf(keys[i]) !== -1
            });
        }
        return out;
    }

    // --- setup and persistence ---------------------------------------------

    Component.onCompleted: if (!_restore()) reset()

    function _restore() {
        if (settingsKey === "" || !settings)
            return false;
        var raw = settings.value(settingsKey, "");
        if (!raw)
            return false;
        var saved;
        try { saved = JSON.parse(raw); } catch (e) { return false; }
        if (!saved || !Array.isArray(saved.order))
            return false;

        var clean = Catalog.sanitize(profile, saved.order);
        var next = {};
        for (var i = 0; i < clean.length; ++i) {
            var col = Catalog.entry(clean[i]);
            var saw = saved.widths ? saved.widths[clean[i]] : undefined;
            next[clean[i]] = Catalog.clampWidth(
                col, typeof saw === "number" ? saw : col.pref);
        }
        order = clean;
        widths = next;
        _refitDropped();
        return true;
    }

    /* Persist. Called on release of a drag and after any structural change —
     * settingsVM debounces by 750 ms, so this is cheap to call often. */
    function commit() {
        if (settingsKey === "" || !settings)
            return;
        settings.setValue(settingsKey,
                          JSON.stringify({ v: 1, order: order, widths: widths }));
    }

    function reset() {
        var keys = Catalog.defaultOrder(profile);
        var next = {};
        for (var i = 0; i < keys.length; ++i)
            next[keys[i]] = Catalog.entry(keys[i]).pref;
        order = keys;
        widths = next;
        _refitDropped();
        commit();
    }

    // --- the fit ------------------------------------------------------------

    function budgetFor(n) {
        return rawBudget - _gap * n;
    }

    /* Gap between columns. Set by the view from Theme.gap so this object needs
     * no import of the theme it is laying out for. */
    property int _gap: 14

    function _desiredFor(key) {
        var col = Catalog.entry(key);
        var w = widths[key];
        return Catalog.clampWidth(col, typeof w === "number" ? w : col.pref);
    }

    /* Resolve the rendered columns to actual widths.
     *
     * Pure: reads `order`, `widths`, `dropped` and `rawBudget`, writes nothing.
     * That is what makes it safe to shrink destructively — nothing it computes
     * survives the next call, so the user's chosen widths are never eroded.
     */
    function _fit() {
        var keys = [];
        for (var i = 0; i < order.length; ++i)
            if (dropped.indexOf(order[i]) === -1)
                keys.push(order[i]);

        var budget = budgetFor(keys.length);
        var w = {};
        var total = 0;
        for (var j = 0; j < keys.length; ++j) {
            w[keys[j]] = _desiredFor(keys[j]);
            total += w[keys[j]];
        }

        // Not enough room: take it back in proportion to how much slack each
        // column has above its own minimum. `album` (220 preferred, 90 minimum)
        // gives up most of it while `duration` (64/52) barely moves, which is
        // what a reader expects to see.
        var excess = Catalog.TITLE_MIN + total - budget;
        if (excess > 0) {
            var slack = 0;
            for (var k = 0; k < keys.length; ++k)
                slack += w[keys[k]] - Catalog.minWidth(Catalog.entry(keys[k]));

            if (slack > 0) {
                for (var m = 0; m < keys.length; ++m) {
                    var mn = Catalog.minWidth(Catalog.entry(keys[m]));
                    var take = Math.round(excess * (w[keys[m]] - mn) / slack);
                    w[keys[m]] = Math.max(mn, w[keys[m]] - take);
                }
                // Rounding leaves a pixel or two behind. Take it right-to-left
                // from whoever still has slack.
                var residual = Catalog.TITLE_MIN + _sum(w, keys) - budget;
                for (var n = keys.length - 1; n >= 0 && residual > 0; --n) {
                    var floor = Catalog.minWidth(Catalog.entry(keys[n]));
                    var give = Math.min(w[keys[n]] - floor, residual);
                    w[keys[n]] -= give;
                    residual -= give;
                }
            }
        }

        return { widths: w, titleWidth: Math.max(Catalog.TITLE_MIN,
                                                 budget - _sum(w, keys)) };
    }

    function _sum(map, keys) {
        var t = 0;
        for (var i = 0; i < keys.length; ++i)
            t += map[keys[i]];
        return t;
    }

    /* Width the layout needs if every one of *keys* sat at its minimum. */
    function _need(keys) {
        var t = Catalog.TITLE_MIN;
        for (var i = 0; i < keys.length; ++i)
            t += Catalog.minWidth(Catalog.entry(keys[i]));
        return t;
    }

    /* Decide which columns have to go, and which can come back.
     *
     * Drops come off the right, skipping columns marked non-droppable. Restores
     * come back in `order` sequence, but only with RESTORE_MARGIN to spare, so
     * the drop and restore thresholds differ and a window dragged across the
     * boundary cannot flap a column on and off frame after frame.
     */
    function _refitDropped() {
        if (rawBudget <= 0)
            return;

        var kept = [];
        for (var i = 0; i < order.length; ++i)
            kept.push(order[i]);

        // Shrink: drop the rightmost droppable column until the all-at-minimum
        // layout fits.
        while (kept.length > 0 && _need(kept) > budgetFor(kept.length)) {
            var victim = -1;
            for (var j = kept.length - 1; j >= 0; --j) {
                if (Catalog.isDroppable(Catalog.entry(kept[j]))) {
                    victim = j;
                    break;
                }
            }
            if (victim === -1)
                break;   // only pinned columns left; they stay and we clip
            kept.splice(victim, 1);
        }

        // Grow: put back the leftmost missing column that now fits with room
        // to spare, and keep going while more still fit.
        var changed = true;
        while (changed) {
            changed = false;
            for (var m = 0; m < order.length; ++m) {
                if (kept.indexOf(order[m]) !== -1)
                    continue;
                var trial = _inOrder(kept.concat([order[m]]));
                if (_need(trial) + Catalog.RESTORE_MARGIN <= budgetFor(trial.length)) {
                    kept = trial;
                    changed = true;
                    break;
                }
            }
        }

        var next = [];
        for (var n = 0; n < order.length; ++n)
            if (kept.indexOf(order[n]) === -1)
                next.push(order[n]);
        if (next.join("\0") !== dropped.join("\0")) // \0 used to be a NUL char. I changed it to \0 not knowing what it's supposed to do. :shrug:
            dropped = next;
    }

    /* Re-sort a set of keys into `order` sequence. */
    function _inOrder(keys) {
        var out = [];
        for (var i = 0; i < order.length; ++i)
            if (keys.indexOf(order[i]) !== -1)
                out.push(order[i]);
        return out;
    }

    onRawBudgetChanged: _refitDropped()
    onOrderChanged: _refitDropped()

    // --- the API the view and the header call -------------------------------

    function setBudget(width, gap) {
        _gap = gap;
        rawBudget = Math.max(0, width);
        // Explicit rather than relying on onRawBudgetChanged: the same table
        // can change its budget without changing its width — the queue flips
        // `showIndex`, and Browse swaps layouts under one list. `_refitDropped`
        // is guarded and idempotent, so the duplicate call costs nothing.
        _refitDropped();
    }

    /* The columns a resize of *key* takes its width from, nearest first, always
     * ending with the title column — which is elastic, and therefore terminates
     * the chain no matter what precedes it. "" means the title column.
     *
     * Two kinds of column are skipped, for the same reason: they have nothing
     * to give. A fixed-width one by definition, and STATUS because it sits a
     * few pixels above its own floor and is marked `absorbs: false` for it.
     * Skipping them is what lets STATUS sit anywhere in the order without
     * cutting the columns to its right off from the elastic space on its left.
     *
     * A chain rather than a single absorber, because the nearest neighbour is
     * often the wrong size to carry a drag on its own: STATUS's neighbour in
     * the import list is TIME, which has twelve pixels between its width and
     * its minimum, and a handle with twelve pixels of travel is not a handle.
     */
    function _absorbChain(key) {
        var cols = renderedColumns;
        var idx = -1;
        for (var i = 0; i < cols.length; ++i)
            if (cols[i].key === key) { idx = i; break; }
        var chain = [];
        for (var j = idx - 1; j >= 0; --j)
            if (Catalog.isResizable(cols[j]) && Catalog.absorbs(cols[j]))
                chain.push(cols[j].key);
        chain.push("");
        return chain;
    }

    /* The columns a rightward drag of *key* pushes into once *key* itself is
     * sitting on its floor, nearest first.
     *
     * Without this a drag to the right ends after `pref - min` pixels — twelve
     * of them for TIME — and the divider comes off the cursor while the columns
     * beyond it still have room to give. The same two exclusions as the absorb
     * chain, for the same reasons: a fixed-width column has no width of its own
     * to hand over, and STATUS is `absorbs: false`.
     *
     * The title column is not in it. It is on the other side of the divider,
     * and it is what *receives* the travel — see `_absorbChain`.
     */
    function _pushChain(key) {
        var cols = renderedColumns;
        var idx = -1;
        for (var i = 0; i < cols.length; ++i)
            if (cols[i].key === key) { idx = i; break; }
        var chain = [];
        for (var j = idx + 1; j < cols.length; ++j)
            if (Catalog.isResizable(cols[j]) && Catalog.absorbs(cols[j]))
                chain.push(cols[j].key);
        return chain;
    }

    // Bounds and drag-start width of one chain entry. The title column has no
    // stored width and no ceiling of its own, so it answers for itself.
    function _floorOf(key) {
        return key === "" ? Catalog.TITLE_MIN
                          : Catalog.minWidth(Catalog.entry(key));
    }
    function _ceilOf(key) {
        return key === "" ? Catalog.UNBOUNDED
                          : Catalog.maxWidth(Catalog.entry(key));
    }
    function _baseOf(key) {
        if (key === "")
            return _baseTitle;
        return _base[key] !== undefined ? _base[key] : _desiredFor(key);
    }

    /* The widths a drag started from. Everything `resizeTo` computes is
     * measured against these rather than against the live widths, which is what
     * makes a resize a pure function of where the cursor is: the same cursor
     * position always yields the same layout, and dragging out and back leaves
     * every column exactly where it started even when the delta had to be
     * spread across several of them. */
    property var _base: ({})
    property real _baseTitle: 0

    /* Take the rendered widths as the new desired widths, and snapshot them.
     *
     * Called once when a drag starts. Without the first half, dragging while
     * the window is too narrow writes a desired width that the very next fit
     * squeezes back out, and the divider slides out from under the cursor.
     * Afterwards the squeeze is by definition zero, so the drag is exact.
     * Passive window resizing never calls this, which is why it never erodes
     * what the user chose.
     */
    function beginResize() {
        var next = {};
        for (var k in widths)
            next[k] = widths[k];
        var live = renderedWidths;
        for (var j in live)
            next[j] = live[j];
        widths = next;

        var snap = {};
        for (var b in next)
            snap[b] = next[b];
        _base = snap;
        _baseTitle = titleWidth;   // read after the assignment above, so fresh
    }

    /* Resize one column mid-drag.
     *
     * Written in terms of `d`, how far the divider on *key*'s left edge has
     * travelled from where the drag started — positive to the right. Not in
     * terms of *key*'s width, because the two stop being the same thing as soon
     * as a rightward drag runs *key* down to its floor: past that point the
     * divider keeps moving and the columns to the right pay for it.
     *
     * Whichever way it goes, the chain to the left takes the whole of `d` and
     * the total is unchanged. That is what keeps the divider under the cursor
     * to the pixel, and it is why the list can never be made to overflow by
     * dragging.
     *
     *   d < 0   key grows; the absorb chain to its left gives up |d|, nearest
     *           first, ending at the elastic title column.
     *   d > 0   key shrinks to its floor and no further; anything left over is
     *           taken from the push chain to its right, nearest first. The
     *           absorb chain grows by the full d either way.
     *
     * `d` is clamped rather than the request refused: kroma bails out of the
     * whole call when the neighbour cannot absorb, which freezes the handle and
     * then jumps it.
     */
    function resizeTo(key, width) {
        var col = Catalog.entry(key);
        if (!col || !Catalog.isResizable(col) || widths[key] === undefined)
            return;
        if (_base[key] === undefined)
            beginResize();          // called outside a drag; snapshot now

        var chain = _absorbChain(key);
        var push = _pushChain(key);

        // What the absorb chain can hand over, and take back, from where the
        // drag started. Per-entry Math.max, because a chain member already
        // squeezed below its own floor by a narrow window must count as zero
        // rather than as a negative that eats into what the others offer.
        var give = 0, take = 0;
        for (var i = 0; i < chain.length; ++i) {
            give += Math.max(0, _baseOf(chain[i]) - _floorOf(chain[i]));
            take += Math.max(0, _ceilOf(chain[i]) - _baseOf(chain[i]));
        }

        // How far right the divider may go: this column's own slack, then
        // everything the columns beyond it are willing to give.
        var own = Math.max(0, _base[key] - _floorOf(key));
        var pushRoom = 0;
        for (var p = 0; p < push.length; ++p)
            pushRoom += Math.max(0, _baseOf(push[p]) - _floorOf(push[p]));

        var d = _base[key] - Math.round(width);
        d = Math.min(d, own + pushRoom, take);
        d = Math.max(d, -give);

        var next = {};
        for (var k in widths)
            next[k] = widths[k];
        next[key] = _base[key] - Math.min(d, own);

        // Everything below is rewritten from the snapshot, including the
        // entries this frame has no delta left for — that is what puts the far
        // end of a chain back when the cursor comes the other way.
        var spill = Math.max(0, d - own);
        for (var q = 0; q < push.length; ++q) {
            var room = Math.max(0, _baseOf(push[q]) - _floorOf(push[q]));
            var given = Math.min(spill, room);
            next[push[q]] = _baseOf(push[q]) - given;
            spill -= given;
        }

        var remaining = -d;
        for (var j = 0; j < chain.length; ++j) {
            var slack = remaining > 0
                        ? Math.max(0, _baseOf(chain[j]) - _floorOf(chain[j]))
                        : Math.min(0, _baseOf(chain[j]) - _ceilOf(chain[j]));
            var moved = remaining > 0 ? Math.min(remaining, slack)
                                      : Math.max(remaining, slack);
            if (chain[j] !== "")
                next[chain[j]] = _baseOf(chain[j]) - moved;
            remaining -= moved;
        }
        widths = next;
    }

    function endResize() {
        _base = ({});
        commit();
    }

    function toggle(key) {
        if (!Catalog.isAvailable(profile, key))
            return;
        var next = order.slice();
        var at = next.indexOf(key);
        var w = {};
        for (var k in widths)
            w[k] = widths[k];

        if (at === -1) {
            next.push(key);
            w[key] = Catalog.entry(key).pref;
        } else {
            next.splice(at, 1);
            delete w[key];
        }
        widths = w;
        order = next;     // triggers _refitDropped
        commit();
    }

    /* Move *key* so that it sits before the column currently at *toIndex* in
     * `order`; `order.length` means "past the end". */
    function moveColumn(key, toIndex) {
        var from = order.indexOf(key);
        if (from === -1)
            return;
        toIndex = Math.max(0, Math.min(toIndex, order.length));
        var insertAt = toIndex <= from ? toIndex : toIndex - 1;
        if (insertAt === from)
            return;

        var next = order.slice();
        next.splice(from, 1);
        next.splice(insertAt, 0, key);
        order = next;
        commit();
    }
}
