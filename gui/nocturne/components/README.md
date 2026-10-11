# Nocturne component directory

Reusable QML lives in folders named for its responsibility. Complete pages live
in `../views/`, grouped by feature. `../main.qml` composes the window, shell,
and destinations. The `../Nocturne/` module owns shared theme, icons, formatting,
selection, clipboard, input helpers, and its existing public primitives.

| Folder | Contents |
| --- | --- |
| `artwork/` | `CoverArt`, `CornerMask`, `AmbientBackdrop` |
| `chips/` | `EntityChips`, artist/album pickers, `ChipStack`, `ContributorChips` |
| `controls/` | General buttons, fields, menus, labels, `ViewHeader`, `Toast` |
| `dialogs/` | Shared prompts and playlist, backup, export, consent, update dialogs |
| `editing/` | Metadata field/slider rows, external IDs, album split/unify dialogs |
| `importing/` | Import sources, split button, status legend, scan options dialog |
| `library/` | Collection grids/lists, cards/delegates, detail hero, badges, display options |
| `playback/` | Player bar, seek/volume, lyrics timeline, equalizer, focus handle |
| `settings/` | Searchable setting controls, page/tab layout, vault move guide |
| `shell/` | Title bar, navigation/history, sidebars/details, resize handles/grips |
| `tracks/` | Track table/delegates, column layouts/header/catalog, status cell, skeletons |

Keep a component with the feature that owns its behavior. A dialog used only by
metadata editing belongs in `editing/`; a generally reusable confirmation belongs
in `dialogs/`. Shared collection geometry belongs in `library/`, while track
selection, row gestures, and column arithmetic belong in `tracks/`.

Import `Nocturne` for the shared foundation. Types in the same directory resolve
automatically; import other component directories explicitly when using their
types. For example, `tracks/TrackTable.qml` imports `../artwork` for cover art
and `../library` for shared collection layouts. Views import the component groups
they use, and components never import views. There is no umbrella component
import or forwarding wrapper to maintain.

Keep JavaScript helpers beside their consumers: `tracks/TrackColumns.js` defines
the column catalog, and `importing/ImportSources.js` defines offered sources.
Resolve relative assets from the QML file's directory. `shell/TitleBar.qml` uses
`../../assets/icon.png`; image-provider URLs remain independent of file location.

The bundle compiler recursively includes this tree and fingerprints paths as
well as contents, so moving a file automatically invalidates the compiled GUI.
Directory imports work in both source checkouts and compiled resource bundles.
Moving files does not add visual objects, loaders, or forwarding components;
existing component bodies, delegates, caches, and shared mask ownership remain
the same.

See [the settings guide](settings/README.md) when adding controls or settings pages.
