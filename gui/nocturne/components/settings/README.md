# Nocturne settings components

Settings live in `gui/nocturne/views/settings/`, with category pages in `tabs/`.
`SettingsView.qml` owns category selection, search, and navigation. Reusable
controls live beside this guide in `gui/nocturne/components/settings/`.
Pages stay instantiated to retain their scroll
position and backend signal connections. Refresh expensive data only when a
page's `active` property becomes true.

## Add a setting

Place a control inside a `SettingsGroup` on the appropriate page. Give it a short
label describing the choice, a description explaining the effect, and optional
search synonyms. Bind its value to the owner and handle its intent signal:

```qml
import QtQuick
import Nocturne
import "../../../components/settings"

SettingsGroup {
    width: parent.width
    title: "Queue & history"

    NumberSetting {
        width: parent.width
        label: "History length"
        description: "Number of played tracks remembered by the Previous button."
        keywords: "recent previous played"
        minimum: 10
        maximum: 1000
        step: 10
        value: playerVM.maxHistorySize
        onCommitted: function(value) { playerVM.maxHistorySize = value }
    }
}
```

Search discovers groups and controls through their `settingsSearchText`, `label`,
and `description` properties. No separate search index needs maintaining.
Results open the category, scroll to the setting, and briefly highlight it.
Set a conditional group's `searchAvailable` to the same condition as `visible`:
effective visibility alone cannot be used because inactive pages are hidden.
Plugin cards and manifest-defined settings participate in the same search.

## Control types

| Component | Value / options | User intent | Use |
| --- | --- | --- | --- |
| `ToggleSwitch` | `checked` | `toggled(bool)` | Independent on/off choice |
| `ChoiceSetting` | `options`, `currentIndex` | `selected(index)` | Dropdown; `segmented: true` for a few short choices |
| `NumberSetting` | `value`, `minimum`, `maximum`, `step`, `decimals`, `suffix` | `committed(value)` | Exact typed values with increment/decrement buttons |
| `SliderSetting` | Same numeric properties | `committed(value)` | Continuous adjustment, with live value and range labels |
| `TextSetting` | `text`, `placeholder` | `edited(text)` | Free text; emitted only for user edits |
| `PathSetting` | `path`, `placeholder`, `actionText`, `secondaryText` | `requested()`, `secondaryRequested()` | Selectable read-only path with picker/open/clear actions |
| `SettingButton` | `text`, `enabled` | `clicked()` | Keyboard-accessible settings actions |

`SettingRow` supplies labels, descriptions, disabled presentation, highlighting,
and responsive editor placement. Its default child goes in the editor area;
`controlWidth` sets the desktop width, and `stacked: true` puts a wide editor
below its label. `SettingsGroup` supplies a padded card and section heading.
Use these two primitives for specialised settings without duplicating layout.

Controls emit intent without overwriting their public value bindings. The
backend may accept, normalise, or reject a change; it remains authoritative.
Numeric editors support typing, Up/Down, bounds, and Escape to discard an edit.
Switches, buttons, choices, and sliders support native keyboard interaction.
Descriptions are also exposed to accessibility APIs. Setting transitions and
switch motion respect `Theme.prefs.reducedMotion`.

Keep a restart requirement or prerequisite close to the setting it affects.
Do not add controls for behaviour the backend does not implement. GUI-only
preferences belong to `Theme.prefs`; playback, import, plugins, and system
settings go through their existing view models.

## Verification

`tests/test_settings_controls.py` exercises keyboard edits, bounds, and value
ownership. `tests/test_settings_tabs.py` covers category navigation, discovery,
search result navigation, and compact layout. The existing import, plugin,
update, and wheel-navigation tests cover the integrated workflows.
