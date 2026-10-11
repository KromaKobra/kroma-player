# Nocturne

The official interface for [Kroma Player](https://github.com/KromaKobra/kroma-player),
shipped with the app and installed into your gui folder on first run.

Kroma means colour, so Nocturne takes its colour from the music: the playing
track's artwork sits blurred behind the whole window and cross-fades as the
queue moves. It runs dark or light, or follows whichever the desktop is on.

---

## Install

Nothing to do: the app copies this folder to
`~/Music/venomvault/.data/gui/nocturne/` on first run and loads it by default.

If you are running another interface and want to come back, open
**Settings → Appearance**, find the **Interface** section, click **Nocturne**,
and restart Kroma Player. Or, for one run without changing anything:

```bash
KROMA_GUI=nocturne python run.py
```

If you edit your installed copy, the app leaves it alone unless a newer version
ships — in which case your edits are moved to `nocturne.bak` first. To fork it,
copy the folder under a new name; the app never touches that one.

Requires GUI API version 13 (Kroma Player with `docs/GUI_API.md`).

---

## What it does

- **Now Playing** — large artwork with an accent-tinted glow, and beside it
  either time-synced lyrics that scroll with the playhead, or what is up next.
  Either half can take the whole page: move the pointer into one and a quiet
  handle fades in at the bottom of it, and pressing that gives that half the
  width and hides the other. Pressing it again brings the split back. The
  choice is remembered between launches, and a window too narrow to have two
  halves (under 900px) shows the artwork alone as it always has.
- **Queue** — the queue and play history behind one toggle, with the playing
  row marked by an animated level meter.
- **Browse** — your library, and whatever online sources the enabled plugins
  offer, in one place. Library filtering runs as you type; online searches wait
  for Enter, since each is a network round trip. With no plugin on there is no
  source picker at all. An empty box in library mode shows everything, so this doubles as the
  browse-all view.
- **Playlists** — a cover grid that opens into a detail page with its own hero.
- **Library** — what is stored and how much space it takes. The import buttons
  live in its header, but the run itself happens on a page of its own.
- **Settings** — seven tabs across the top: *Appearance* (dark, light or
  system, six accent themes, the ambient toggle, and the interface switcher),
  *Playback*, *Backing*, *Library* (the vault and import), *Plugins* (install,
  turn on — agreeing to a plugin's terms first — settings, and which plugin is
  asked first), *System* (updates, and the programs Kroma and its plugins need)
  and *About*. Switching one glides the page sideways, toward the side the tab
  you picked lives on.

Multi-select works throughout with click, Ctrl-click, and Shift-click; the
right-click menu acts on the whole selection.

---

## Keyboard

| Key | Action |
|---|---|
| `Space` | Play / pause |
| `→` / `←` | Seek ±5 seconds |
| `Ctrl + →` / `Ctrl + ←` | Next / previous track |
| `↑` / `↓` | Volume |
| `Ctrl + F` | Browse |
| `Ctrl + 1`…`6` | Jump to a section |
| `Ctrl + I` | Toggle the details panel |
| `Esc` | Close a dialog, clear the search box |

Transport keys are bound on a focus scope rather than as global shortcuts, so
typing a space into the search box types a space.

While a **retime** is running in Now Playing, four of them mean something else —
the one place in the interface where that is true:

| Key | Action |
|---|---|
| `Space` | Mark where the next line starts, and highlight it (the first press also starts the track) |
| `Backspace` | Take back the last mark and rewind to where the line before it began |
| `Enter` | Finish and save the new timings |
| `Esc` | Cancel; nothing is stored |

---

## Notes

**No shuffle or repeat buttons in the transport.** The Kroma backend exposes no
such modes, and a control that silently does nothing is worse than no control.
Shuffle instead appears where it can genuinely act — *Shuffle whole library* in
Library, and *Shuffle* on a playlist — both of which build a shuffled queue.

The MPRIS service follows the same rule for the same reason: `Shuffle` and
`LoopStatus` are optional in the spec and are left out, so the system media
widget greys those two buttons out rather than offering switches that do
nothing. That is an omission, not an oversight — `app/system/mpris.py` documents
what to change if the queue ever grows the modes.

**The window is frameless.** Nocturne draws its own title bar so it can be part
of the layout. Dragging, double-click-to-maximise, and all eight resize edges
are wired to the system window manager, and maximising respects the taskbar.

**Preferences are Nocturne's own, and the backend stores them.** The theme mode,
accent, ambient toggle, sort orders, how big the tiles on the artist and album
walls are (`artistTileSize`, `albumTileSize`), whether the album page shows
artwork on its rows (`albumArtwork`) and column widths go to `settings.json`;
the last section,
which settings tab was open, which half of Now Playing has the page
(`nowPlayingFocus`), the window's size and the details panel go to
`session.json`. Both under `gui.nocturne`, scoped by the backend so nothing
collides with another interface.

The two tile sizes are a target width rather than the column count the slider in
each wall's heading actually steps through: a grid fills its row, so a column
count only means something beside the window it was chosen in, while a width
survives a resize and travels to another machine. They are kept apart from each
other for the same reason `artistSort` and `albumSort` are.

Column widths are one key per profile — `columns.queue`, `columns.library`,
`columns.playlist`, `columns.album`, `columns.search`, `columns.webSearch`,
`columns.import`. The album keeps its own even though it draws the library's
columns: its rows can drop the artwork, which changes the width the columns have
to share out, and a layout object holds that budget for every table using it.

`Theme.qml` reaches them through `import Kroma.Settings 1.0` rather than
`settingsVM`, because a `pragma Singleton` cannot see root-context properties.
`Theme.prefs` stays a real `QtObject` with real properties — `SettingsStore.value()`
is a method, not a notifying property, so binding the interface to it directly
would leave nothing able to repaint on an accent change. The properties are seeded
from the store and written back on change, guarded until `Component.onCompleted`
so a first launch does not write every default straight back.

This replaced a `nocturne.ini` inside this folder. That location could not be
written in a packaged build, was never carried by a backup, and would have shipped
a developer's accent in any build cut from a dirty tree.

**Rows arrive and leave visibly.** A track list of near-identical rows changes
silently otherwise: something is there, and then it is not, and on a long list
neither which one nor how many is recoverable. So a row that leaves fades out
while the rows below close the gap, and a row that arrives fades in where it
landed while the rows below glide down to make room — `TrackTable`'s `remove`
and `add` transitions and their `*Displaced` partners. Position is the whole of
what an arrival has to say, so it is delivered there rather than travelling in
from an edge.

What a departure has to say is one thing more, and it is the difference between
being got rid of and being over. A row the user removed also flies off the
right-hand edge; a track the player simply moved on from does not. It leaves
queue row 0 through the same removal the context menu uses, so
`queueVM.advance_removed` names it on the way past and it goes without the
gesture — nobody sent it anywhere, so it has no direction to leave in.

The import list is the second half of that. It shows only rows that still have
work in them, so a track leaves the screen as it finishes importing — again not
because anyone threw it out, and again named first, this time by
`importVM.imported_removed`. Both signals reach the same `markAdvanceRemoved`,
which the `remove` transition asks about first.

That one is a departure and not a removal in a second sense too: the row stays in
the list, hidden, and comes back if the setting that hid it goes off. What the
table sees is the same `removeRows` either way, which is the point — the exit is
drawn from what the row is doing, not from what the model is keeping.

**And a row can be thrown out by hand.** The grip that reorders a list also
removes from it: pull a row far enough to the right and let go. Pulling it
meets a resistance that grows until the card will not come any further, and past
a boundary it snaps across and follows the pointer exactly — the snap being the
only warning that letting go now removes something. Coming back over the boundary
puts the resistance back, which is what makes an armed pull something the hand can
take back rather than commit to.

**A drag does one of those two things, never both.** Which one is read off the
first five pixels — rightward and more sideways than vertical removes, everything
else reorders — and then fixed until the button comes back up. Letting go and
grabbing again is what changes its mind. That is not a rule laid on top of the
gesture so much as the thing that keeps it simple: a removal displaces no rows and
a reorder moves no card sideways, so neither half ever has to be talked out of the
other's territory, and `_shiftFor` and `_cardShiftFor` each answer zero outright in
the mode that is not theirs.

**Artist chips drag too, and share none of that code.** A track's artists are a
credit order and the track editor lets you arrange it, but the chips sit in a
`Flow` so a track with six of them wraps — and none of `TrackTable`'s arithmetic
means anything once a list has two axes and rows of different widths. What
carries over is the rule rather than the implementation: the model is not
committed until the gesture ends, so `changed` reaches `editVM` exactly once
however far the chip travelled.

Where a chip would land is asked of the laid-out delegates rather than computed,
because a `Flow` decides for itself where its lines break and only it knows what
it did. The pointer's line is tested before its side, which is what makes a
wrapped row behave: a chip two lines down is nearer in raw distance than the one
beside it, and landing there is never what the hand meant. The chip being carried
keeps its slot in the `Flow` and is drawn offset from it through a `Translate`,
so the positioner is never fought for control of `x` — and the rest of the row
slides out of the way on the `Flow`'s own `move` transition, which is the whole
of the animation.

That is also why the chips are a `ListModel` and not the JS array they were:
reassigning an array makes a `Repeater` destroy every delegate and build them
again, which mid-drag destroys the chip the pointer is holding. A model move is
one item changing places, and the delegate survives its own gesture.

Underneath the card is a red bed with a trash glyph and the word REMOVE. Only the
strip the card has uncovered is drawn — an unselected row's backdrop is
`transparent`, so a full-width panel behind it would show through and redden a row
from the first pixel of a pull. Which makes the reveal the whole of the staging,
and it comes out in the right order for nothing: the trash clears the clip while
the pull is still being resisted, and the word finishes at the moment the card
snaps. The two axes live on different items for it — the delegate's `Translate`
carries the vertical drag and the horizontal one is on `rowCard` alone, so the bed
is a sibling the card slides off rather than a child it drags along.

Both ways of letting go glide, and neither is a special case. A pull that did not
go far enough returns to its row under `springBack`; one that did is simply
*removed*, then and there, exactly as the context menu removes it — so the rows
leave through `remove` and the list closes up through `removeDisplaced`, the same
departure from the same two transitions. The only thing the gesture adds is where
they start from: `_dismissedShift` keeps the offset the hand let go at, keyed by
uid because the row is out of the model by then, and the exit animates the
delegate's own `x` on top of it. The row carries on from the hand instead of being
yanked back to the left edge to begin again.

Both of those are explicit animations rather than Behaviors, and that is
load-bearing. A Behavior would have to be switched on at the same moment its
target changed, both reading one flag in one change propagation — and QML does not
order those two, so the card would sometimes be written before the Behavior was
armed and blink rather than glide.

**The queue and playlists both have handles**, because both are an order somebody
chose. `TrackTable` looks for `moveRows` and `removeAtRows` on whatever model it
was given rather than naming a view model, so the two pages share every line of
the gesture and a list that offers neither simply has no grip.

One rule is not shared, and it is worth saying which. Queue row 0 is what is
playing, and "queue[0] is what is playing" is an invariant the backend, the player
bar and the Now Playing page all read off the same index — so it is not a drop
target and it offers nothing to pull on. A playlist has no such row: playing a
playlist copies its ids into the queue and the two go their own way afterwards, so
every row of one has a handle, including whichever is currently playing. That row
keeps its meter at rest and swaps to a grip while you hover it. `_minDropRow`
holds the whole distinction — it is the first row that may be landed on, and
`canDrag` asks it the same question, so the row nothing may be dropped on is
exactly the row that cannot be picked up.

Making it work on playlists was mostly a backend change: the playlist model
answered every edit with a full reset, which reaches a view as "the list is now
something else" and leaves nothing to animate. It emits row signals now. The
awkward part is that the event bus is synchronous and the service announces each
removal as it makes it, so the model hears its own change while it is still
inside the `beginRemoveRows` describing it — hence the guard it holds while
writing.

**And a row can travel.** Playing a track from partway down the queue is neither
an arrival nor a departure for that row: it is the same row, somewhere else. The
backend says so — `rowsMoved`, not a reset — and the `move` and `moveDisplaced`
transitions carry it to the front while the rows it passes close up behind it.
Whatever was playing goes at the same time, fading out of row 0 if it was
listened to and gliding down a slot if it was not, since in that case it never
left the queue at all.

That pair is held to zero duration while `_committing`, which is a drag being
handed over to the model: the block is already where it is going, put there by
the delegate's own Translate offsets, and animating the model's version of the
same move would take it back and re-fly it. `_committing` outlives the commit by
a few frames on purpose — a view transition is not built until the next polish,
so a flag cleared synchronously reads false by the time it is asked.

Rows arriving at the end of the import list get one thing further, since an
append lands below the fold where a fade cannot be seen at all: `revealAdded`
scrolls them into view wearing an accent wash that fades.

**Settings glides sideways, but not far.** Its six tabs are laid out left to
right and the pages move as if they were: the one you are leaving slides off
toward its own side while the one you picked comes in from its own, under a
cross-fade. Which side is never tracked. A tab left of the current one waits off
the left edge and one to the right waits off the right — `index < currentIndex`
in `SettingsPane`, and that is the whole of it — so the direction falls out of
the two numbers and cannot get out of step with itself, including for a tab
restored from `session.json` rather than clicked.

The travel is one page margin, not one page width. A full-width slide at
`Theme.normal` is around five thousand pixels a second, which reads as a flinch
rather than a movement, and takes the direction — the only thing the gesture has
to say — down with it. A short offset under a fade is what the eye reads it
from. The fade itself is symmetric and unshaped, the same bare `duration` the
views cross-fade on: two pages whose opacities do not sum to one are both
half-there at once, and that overlap is a smear rather than one page replacing
another.

All six tabs stay alive, as the views do, so each keeps its scroll position and
the `Connections` declared inside one go on being heard while another is up.
What a tab loses while it is not the one showing is input — `enabled: current`,
which is also what stops six `SmoothWheel`s fighting over the same wheel — and
its refresh: each tab asks the backend for what it needs when it is looked at,
so opening Settings no longer probes audio devices nobody asked about.

---

## Layout

```
main.qml              Window, navigation, view switching, shortcuts, session
gui.json              Package manifest
Nocturne/             QML module — design tokens and primitives
  Theme.qml             dark and light palettes, spacing, type, motion, accents
  Icons.qml             ~40 icons as inline SVG; no image files ship
  Fmt.qml               duration/date/count formatting
  TrackSelection.qml    registry of the visible track list — see Selection
  Icon, IconButton, Pill, NocturneTooltip
  SmoothWheel.qml       browser-like wheel and trackpad scrolling for a Flickable
  WheelSteps.qml        the same input read as whole steps, for the rail and tabs
components/           Reusable pieces
  TrackTable.qml        the track list used by five of the six views
  PlayerBar, NavRail, TitleBar, InfoPanel, SeekBar, VolumeControl
  CoverArt, AmbientBackdrop, EqualizerBars, Toast, ResizeGrips
  NocturneMenu, MenuRow, ConfirmDialog, ChoiceDialog, NameDialog,
  AddToPlaylistDialog, VaultMoveGuide, ConsentDialog, AppButton,
  NocturneField, ToggleSwitch, CheckRow, ViewHeader, SectionLabel, InfoRow,
  EditFieldRow
  ExternalIdRows.qml    the id fields an enabled plugin declares, in an editor —
                        drawn from `externalIdFields`, never from a list here
  TileSizeSlider.qml    how many tiles fit across the artist and album walls;
                        its stops are column counts, its value is a width
  DetailHero.qml        the band atop a playlist, an artist or an album — one
                        component, three pages, and it names no view model
  EntityChips.qml       chips you pick, not a string you type; ArtistChips and
                        AlbumChips are it with round and square pictures
  ChipStack.qml         chips you only read, packed so the pictures overlap and
                        opening one at a time under the pointer; the prominent
                        ones draw half again as large
  ContributorChips.qml  it with an album's artists in it: who is on a record,
                        in the hero, with anyone over half its tracks drawn big
  SettingsTabBar.qml    underline tabs with a sliding accent indicator
  SettingsPane.qml      one settings tab: its scroll surface and its glide
  ImportButton.qml      split button: import files, or pick another source
  ImportSources.js      the sources it offers — add one here, and nowhere else
  ImportOptionsDialog.qml  what a scan found, and what to do about it
views/                One file per destination
  NowPlayingView, QueueView, BrowseView, PlaylistsView, LibraryView,
  SettingsView          the tab bar and the six pages below
  settings/             AppearanceTab, PlaybackTab, BackingTab,
                        LibraryTab, SystemTab, AboutTab
  BackingTrackView, TrackEditView, ImportView   work pages — below
```

`NavRail` follows the sidebar concept in `assets/gui-concept`: Home and listening
destinations, a Library group with Songs, Albums, Artists and Playlists, Tools
with Import, an empty Pinned heading, and Settings in the footer. Songs, Albums
and Artists select the existing Library page's corresponding face. Home is a
blank page. Open artist, album and track edit pages appear in an Open pages
group while relevant. The backing workbench remains reachable through track
actions; it has no sidebar button.

The sidebar toggle and **Ctrl+B** switch between a 64px icon rail and the user's
expanded width (200–360px, constrained by available content space). Drag the
right edge to resize; releasing below 156px snaps to the icon rail. Expanding
restores the previous expanded width. Width and collapsed state are saved with
window session state.

Both sidebars use `SidebarResizeHandle` for the same frame-synchronized,
critically damped spring and pointer handling. Retargeting preserves velocity;
grabbing interrupts at the displayed width and tracks the pointer directly.
Release hands velocity back to the spring, with a short momentum projection
for collapsing flicks. The frame callback stops when settled.

The right details panel expands to 240–520px within the available space. Drag
its left edge right and release below 156px to close it completely. Its previous
expanded width is retained; the transport's details toggle or **Ctrl+I** reopens
it. Width and open state persist in window session state. Details keep a minimum
content width while the outer panel clips them during collapse, avoiding cramped
text and artwork. A closed panel has no active resize edge or metadata reads.

Settings → Appearance → Reduce sidebar motion makes size changes immediate for
both panels. Navigation buttons support keyboard focus, accessible names and
collapsed tooltips.

Scrolling the rail moves one slot at a time, and so does scrolling the settings
tab strip — `WheelSteps` accumulates the deltas and reports whole steps, since a
tab change is discrete and a flick's worth of pixels is not. Both step by slot
position rather than by index, and both report the step through the same signal
a click uses, so neither bar starts owning the index it draws.

Import used to live inside the Library page, swapping that page's track list out
from under it whenever a run was in flight. It is its own destination now, so
neither feature has to make room for the other.

Icons are generated at runtime from path data in `Icons.qml`; sidebar paths are
copied from the concept and every glyph inherits its colour from the theme.

## Import states

One import list holds files, folders, pasted links and CSV rows together, and
every row in it is in one of six states — `importState`, which the backend
decides and this package only draws. What each is telling you to do is the whole
of the design:

| | | |
|---|---|---|
| ✓ | **in library** | nothing; it is done |
| ⬇ | **to download** | press Import — its audio is fetched |
| ⤓ | **to import** | press Import — your file is filed as it is |
| ⚠ | **tags only** | press Import, and know it arrives without the album, artwork or release date |
| ⌕ | **to identify** | find it in Browse and pair it with your file — it cannot import yet |
| ✕ | **unavailable** | remove it; nothing will ever import it |

Three surfaces say it, at three distances. The **legend** — a column of chips
down the left of the page, under the number of tracks the import brought in —
counts each state and, pressed, selects those rows, which is how you clear every
dead row in one gesture rather than hunting for them. The **badge** on the
artwork catches the two that want attention while you scan down the left edge.
The **status cell** on the right carries the glyph and the button, and explains
itself on hover.

The legend was a bar across the top and is a column because of what a user does
with it: a match run comes back a file at a time and those counts move for a
minute or more, and a bar puts them on the same axis as the list growing beneath
it — six chips wrapping and eliding over rows scrolling past. Held to one side at
a fixed width, nothing moves but the numbers.

**in library** is the chip whose rows may not be on the list at all. Tracks you
already have are hidden by default (Settings → Library), and they are hidden
rather than dropped precisely so this chip can go on counting them — over an
import of forty where you already had thirty-eight, it is the only thing on the
page that can tell you so.

A rule down the gutter separates the two. The rail carries the run's progress
bar, and the bar is the rail's width — so with no boundary a bar that stops a
third of the way across the page reads as a bar that is stuck rather than one
that has finished its own column.

## Waiting for a list

A scan and a match run leave the import list empty for a minute or more, and an
empty panel does not read as *working* — it reads as *finished, and there was
nothing*. So `SkeletonRows` fills it with the shape of the answer: one bar per
row, standing exactly where the row cards will land, drawn from the table's own
`rowHeight` and `sideMargin` so an arriving row lands on the bar that was holding
its place. Any list can ask for them; `TrackTable.loading` is the switch, and
`ImportView` is the only page that throws it today.

They are deliberately not a preview of the *columns*. One bar per row, all alike,
no cover square and no title stub. A skeleton that mimicked the column layout
would promise things it cannot keep — how wide the album column is, whether there
is artwork — and would have to be kept in step with a layout the user can reorder
and resize. "A row goes here" is the one part that is certainly true.

The bars brighten and dim together on one slow sine, because the alternative is a
crowd: one animation drives one number at the component root and every bar reads
it, the same arrangement the arrival flash uses. And nothing in the component
measures itself — the count comes from the height it was handed, never from a
positioner. An item that is not effectively visible does not lay out, and this one
is built hidden every single time, so a version that sized itself by measuring
would answer with whatever its unlaid content happened to be. That is how the
import chips once latched at zero height and stayed there.

Two rules hold it together. **Colour is severity, and never the accent** — the
accent is one of six hues you pick, two of which are a green and an amber, so a
state drawn in it would mean something different per theme. And **faded means
you cannot act**: the two states with no button are the two that dim, along with
rows already in the library, so what is left at full strength is exactly what
the Import button is about.

A seventh fact rides alongside rather than joining them, because it is
independent of all six: the **SOURCE** column says where a row's audio comes
from and what taking it in costs — `Opus`, `FLAC → Opus`, or the name the
plugin that will fetch it gives its source. Non-Opus
files are transcoded on import, and that arrow is the only place you can see it
coming.

## Selection

Selection is keyed on each row's `uid`, never `trackId` — the same track can
appear twice in a queue — and row identity is read through the models'
`uidAt()` / `trackIdAt()` slots, so no Qt role numbers appear anywhere in this
package.

Deselecting is deliberately easy, because what is selected drives the details
panel and there is no other way back to "nothing selected, so show what is
playing". Four gestures do it:

- **Clicking the one row that is selected.** Held back briefly, since clicking a
  selected row and clicking it again is how you play it; a double-click, a
  context menu or anything else that changes the selection calls it off. The
  wait is capped well below the platform's double-click interval, which is a
  generous upper bound rather than a delay worth sitting through — a slower
  double click is not lost, only deselected between its presses and re-selected
  on the second.
- **Pressing anywhere in the list that is not a row** — below the last one, above
  the first, or the strip either side of the cards. A row delegate spans the
  list, but the card it paints does not, and only the card hovers and selects;
  everything around it goes to the list's own catcher, which lives inside the
  ListView's `contentItem` because a Flickable takes for itself any press its
  content did not want.
- **Escape**, handled on the view focus scope in `main.qml` alongside the
  transport keys. A focused `NocturneField` clears its own text first and only
  passes Escape on once it is already empty.
- **Pressing the ambient background.** A catcher behind the whole window picks
  up every press that nothing in front of it claimed. Buttons, scroll bars and
  track rows take their own presses; the title bar, the transport and the
  details panel are chrome rather than background and each swallow their strays
  with a sink at the bottom of their stack.

A press lands on the row even while the list is still moving. A Flickable with
speed on filters the next press away from its children and spends it stopping
itself — the touch gesture for halting a flick, which on a mouse just loses the
click, and after a wheel scroll that is most of them. `pressDelay` makes it hold
the press and replay it to the row instead, and is switched on only while the
list moves: left on, it costs double click, because the replayed press arrives
after its own release and the double click never reaches the row.

Escape and the ambient press are window-level gestures, but the selection lives
in whichever `TrackTable` is on screen. The `TrackSelection` singleton is how
they meet: the table registers itself there while it is `active`, so nothing in
between has to forward a signal it has no other use for.
