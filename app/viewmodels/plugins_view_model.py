"""PluginsViewModel — Settings → Plugins, and every slot a plugin fills.

Two jobs in one object, because they read the same manifests:

- **Managing plugins.** What is installed and what is missing, which are on,
  installing a ``.kpp`` (with the screen that says what it is and what it
  declares before anything is written), the terms a plugin asks the user to
  agree to, its settings form, the programs it needs, and — when two plugins
  offer the same kind of answer — which one is asked first.
- **The slots.** Import sources for the Import menu, actions for the track
  context menu, whether there is an audio source or an identity provider at
  all — the questions a GUI asks to decide what to draw. All of them are
  answered from manifests; none starts a process.

Consent is the one thing here a GUI cannot grant by itself. ``setEnabled``
refuses a plugin with terms and emits ``consent_required`` instead; the GUI
shows the text and calls ``acceptConsent``, which is what records agreement —
against a hash of the text, so changed terms ask again. Nothing else can write
the ``plugins`` scope of the settings store: like ``app``, ``settingsVM`` has no
path to it.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from app.plugins import external_ids, installer, programs
from app.plugins.icons import icon_url
from app.plugins.manifest import PROVIDER_KINDS, fill

logger = logging.getLogger(__name__)

#: Where the upgrade notice's dismissal is remembered, in the ``app`` scope.
NOTICE_DISMISSED_KEY = "plugins_notice_dismissed"

_PROVIDER_LABELS = {
    "trackSources": "Online search",
    "identity": "Identifying tracks",
    "metadata": "Track, artist and album details",
    "artwork": "Artwork",
    "audio": "Fetching audio",
    "importSources": "Import sources",
}


def _to_local_path(url: Any) -> str:
    if isinstance(url, QUrl):
        return url.toLocalFile()
    text = str(url or "")
    if text.startswith("file:"):
        return QUrl(text).toLocalFile()
    return text


def _icon(plugin_id: str, value: str) -> Dict[str, str]:
    """A manifest icon as ``{iconName, iconUrl}`` — a theme glyph, or a file it ships."""
    value = str(value or "")
    if not value:
        return {"iconName": "", "iconUrl": ""}
    if "/" in value or "." in value:
        return {"iconName": "", "iconUrl": icon_url(plugin_id, value)}
    return {"iconName": value, "iconUrl": ""}


class PluginsViewModel(QObject):
    """What plugins there are, and what they add."""

    plugins_changed = Signal()
    jobs_changed = Signal()
    notice_changed = Signal()
    error_occurred = Signal(str)
    #: A plugin has terms to agree to before it can be turned on.
    consent_required = Signal(str, str, str)   # plugin id, name, text
    #: An install finished. The id, and the version now installed.
    installed = Signal(str, str)
    #: A plugin crashed; ``(name, reason)``. For a toast, not a dialog.
    plugin_crashed = Signal(str, str)

    def __init__(
        self,
        host: Any,
        registry: Any,
        store: Any,
        track_service: Any = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._registry = registry
        self._store = store
        self._track_service = track_service
        self._notice = ""
        if host is not None:
            host.plugins_changed.connect(self._on_plugins_changed)
            host.jobs_changed.connect(self.jobs_changed)
            host.plugin_crashed.connect(self._on_plugin_crashed)
        self._refresh_notice()

    # ==================================================================
    # The list
    # ==================================================================

    @Property(bool, constant=True)
    def safeMode(self) -> bool:  # noqa: N802
        """Whether Kroma was started with every plugin off."""
        return bool(self._host is not None and self._host.safe_mode)

    @Property("QVariantList", notify=plugins_changed)
    def plugins(self) -> List[Dict[str, Any]]:
        """Every plugin the host knows about, installed or missing, by id.

        ``{id, name, version, author, description, homepage, source, enabled,
        active, running, error, disabledReason, needsConsent, hasTerms,
        declares, provides, programs, settings, iconUrl}``. ``source`` is
        ``installed``, ``dev`` (from ``$KROMA_PLUGIN_DIR``) or ``missing`` —
        enabled in this library and not on this computer.
        """
        if self._host is None:
            return []
        out = []
        for record in self._host.records():
            manifest = record.manifest
            entry: Dict[str, Any] = {
                "id": record.id,
                "name": record.name,
                "version": manifest.version if manifest else "",
                "author": manifest.author if manifest else "",
                "description": manifest.description if manifest else "",
                "homepage": manifest.homepage if manifest else "",
                "source": record.source,
                "enabled": self._host.is_enabled(record.id),
                "active": self._host.usable(record.id),
                "running": self._host.running(record.id),
                "error": record.error,
                "disabledReason": self._host.disabled_reason(record.id),
                "needsConsent": self._host.needs_consent(record.id),
                "hasTerms": bool(manifest and manifest.consent),
                "declares": list(manifest.declares) if manifest else [],
                "provides": [
                    _PROVIDER_LABELS.get(kind, kind)
                    for kind in PROVIDER_KINDS if manifest and manifest.provides_kind(kind)
                ],
                "programs": [
                    programs.status(
                        p.name, minimum=p.min_version, version_args=p.version_args,
                    ).as_dict()
                    for p in (manifest.programs if manifest else ())
                ],
                "settings": self._settings_form(record.id),
                "iconUrl": icon_url(record.id, manifest.icon) if manifest and manifest.icon else "",
            }
            out.append(entry)
        return out

    def _settings_form(self, plugin_id: str) -> List[Dict[str, Any]]:
        record = self._host.record(plugin_id)
        if record is None or record.manifest is None:
            return []
        values = self._host.settings(plugin_id)
        return [
            {
                "key": decl.key,
                "type": decl.type,
                "label": decl.label,
                "description": decl.description,
                "values": list(decl.values),
                "min": decl.minimum,
                "max": decl.maximum,
                "value": values.get(decl.key, decl.default),
            }
            for decl in record.manifest.settings
        ]

    @Slot()
    def refresh(self) -> None:
        """Look at the disk again — for a plugin installed or edited outside Kroma."""
        if self._host is not None:
            self._host.discover()
        programs.clear_cache()

    @Slot()
    def _on_plugins_changed(self) -> None:
        self._refresh_notice()
        self.plugins_changed.emit()

    @Slot(str, str)
    def _on_plugin_crashed(self, plugin_id: str, reason: str) -> None:
        record = self._host.record(plugin_id) if self._host is not None else None
        self.plugin_crashed.emit(record.name if record else plugin_id, reason)

    # ==================================================================
    # Installing and removing
    # ==================================================================

    @Slot("QVariant", result="QVariantMap")
    def inspect(self, url: Any) -> Dict[str, Any]:
        """What a ``.kpp`` is, before it is installed — the install screen's content.

        ``{id, name, version, author, description, homepage, declares,
        programs, provides, installedVersion, files, size, hasTerms, error}``.
        Nothing is written. ``error`` is non-empty, and the rest empty, for a
        package that cannot be installed.
        """
        path = _to_local_path(url)
        try:
            found = installer.inspect(path)
        except installer.InstallError as exc:
            return {"error": str(exc)}
        manifest = found.manifest
        return {
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "author": manifest.author,
            "description": manifest.description,
            "homepage": manifest.homepage,
            "declares": list(manifest.declares),
            "programs": [p.name for p in manifest.programs],
            "provides": [
                _PROVIDER_LABELS.get(kind, kind)
                for kind in PROVIDER_KINDS if manifest.provides_kind(kind)
            ],
            "installedVersion": found.installed_version,
            "files": found.files,
            "size": found.size,
            "hasTerms": bool(manifest.consent),
            "compatible": manifest.is_compatible,
            "error": manifest.incompatibility_reason(),
        }

    @Slot("QVariant", result=bool)
    def install(self, url: Any) -> bool:
        """Install a ``.kpp``. True on success; ``error_occurred`` says why not.

        Installing does not turn a plugin on. That is a separate, deliberate
        step — and for a plugin with terms, the step where they are shown.
        """
        if self._host is None:
            return False
        try:
            manifest = installer.install(_to_local_path(url), stop=self._host.stop)
        except installer.InstallError as exc:
            self.error_occurred.emit(str(exc))
            return False
        self._host.discover()
        self.installed.emit(manifest.id, manifest.version)
        return True

    @Slot(str, result=bool)
    def uninstall(self, plugin_id: str) -> bool:
        """Remove a plugin's code. Its settings, consent and the ids it wrote stay."""
        if self._host is None:
            return False
        try:
            removed = installer.uninstall(str(plugin_id), stop=self._host.stop)
        except installer.InstallError as exc:
            self.error_occurred.emit(str(exc))
            return False
        self._host.discover()
        return removed

    # ==================================================================
    # Enabled and consent
    # ==================================================================

    @Slot(str, bool)
    def setEnabled(self, plugin_id: str, enabled: bool) -> None:  # noqa: N802
        """Turn a plugin on or off. A plugin with unagreed terms asks first."""
        if self._host is None:
            return
        answer = self._host.set_enabled(str(plugin_id), bool(enabled))
        if answer == "consent":
            record = self._host.record(plugin_id)
            self.consent_required.emit(
                str(plugin_id), record.name if record else str(plugin_id),
                self._host.consent_text(plugin_id),
            )
        elif answer:
            self.error_occurred.emit(answer)

    @Slot(str, result=str)
    def consentText(self, plugin_id: str) -> str:  # noqa: N802
        return self._host.consent_text(str(plugin_id)) if self._host is not None else ""

    @Slot(str)
    def acceptConsent(self, plugin_id: str) -> None:  # noqa: N802
        """Record agreement to the plugin's current terms, and turn it on."""
        if self._host is None:
            return
        if not self._host.accept_consent(str(plugin_id)):
            self.error_occurred.emit("That plugin's terms could not be read")
            return
        answer = self._host.set_enabled(str(plugin_id), True)
        if answer:
            self.error_occurred.emit(answer)

    @Slot(str, str, "QVariant")
    def setSetting(self, plugin_id: str, key: str, value: Any) -> None:  # noqa: N802
        if self._host is not None:
            self._host.set_setting(str(plugin_id), str(key), value)

    # ==================================================================
    # Provider order
    # ==================================================================

    @Property("QVariantList", notify=plugins_changed)
    def contestedKinds(self) -> List[Dict[str, Any]]:  # noqa: N802
        """Provider kinds two or more enabled plugins offer, with their order.

        ``[{kind, label, plugins: [{id, name}]}]``, the plugins in the order
        they are asked. Empty — and the section hidden — while no two plugins
        compete, which with one official plugin is always.
        """
        if self._host is None:
            return []
        out = []
        for kind in PROVIDER_KINDS:
            offering = self._host.providers(kind)
            if len(offering) < 2:
                continue
            out.append({
                "kind": kind,
                "label": _PROVIDER_LABELS.get(kind, kind),
                "plugins": [{"id": m.id, "name": m.name} for m in offering],
            })
        return out

    @Slot(str, "QVariantList")
    def setProviderOrder(self, kind: str, ids: list) -> None:  # noqa: N802
        if self._host is not None and kind in PROVIDER_KINDS:
            self._host.set_provider_order(str(kind), [str(i) for i in ids or []])

    # ==================================================================
    # Slots a GUI fills
    # ==================================================================

    @Property(bool, notify=plugins_changed)
    def hasAudioSource(self) -> bool:  # noqa: N802
        """Whether anything can fetch audio — what CSV import and Download need."""
        return bool(self._registry is not None and self._registry.has_audio_source())

    @Property(bool, notify=plugins_changed)
    def hasIdentity(self) -> bool:  # noqa: N802
        """Whether anything can identify a local file — what the matching options need."""
        return bool(self._registry is not None and self._registry.has_identity())

    @Property("QVariantList", notify=plugins_changed)
    def importSources(self) -> List[Dict[str, Any]]:  # noqa: N802
        """The Import menu's plugin entries: ``[{key, label, input, prompt,
        filters, plugin, pluginName, iconName, iconUrl}]``.

        ``key`` is what ``importVM.importFromSource`` takes. ``input`` is
        ``text`` — ask for a line of text, with ``prompt`` — or ``file`` — pick a
        file matching ``filters``.
        """
        if self._registry is None:
            return []
        return [
            {
                "key": decl.id,
                "label": decl.label,
                "input": decl.input,
                "prompt": decl.prompt,
                "filters": list(decl.filters),
                "plugin": provider.plugin_id,
                "pluginName": provider.name,
                **_icon(provider.plugin_id, decl.icon),
            }
            for provider, decl in self._registry.import_sources()
        ]

    @Slot(str, result="QVariantMap")
    def idKind(self, key: str) -> Dict[str, Any]:  # noqa: N802
        """What an id key is called and drawn with: ``{label, sourceLabel, iconUrl}``."""
        if self._registry is None:
            return {}
        for kind in self._registry.declarations():
            if kind.key == key:
                return {
                    "label": kind.label,
                    "sourceLabel": kind.source_label or kind.label,
                    "iconUrl": icon_url(kind.plugin_id, kind.icon) if kind.icon else "",
                }
        return {}

    @Slot(str, "QVariant", "QVariant", result="QVariantList")
    def actionsFor(self, context: str, list_vm: Any, uid: Any) -> List[Dict[str, Any]]:  # noqa: N802
        """The plugin actions for one row, resolved: ``[{id, label, kind, value,
        iconName, iconUrl}]``.

        *context* is ``track``, ``artist`` or ``album``. For a track, *list_vm*
        is the list the row is in and *uid* the row's uid; for an artist or an
        album, *uid* is its id and *list_vm* is unused. ``kind`` is ``openUrl``
        or ``copy`` and ``value`` the URL or text with every placeholder filled
        in — an action whose placeholders this row cannot fill is left out,
        which is the whole condition language at this API level.
        """
        if self._host is None:
            return []
        ids = self._ids_for(str(context), list_vm, uid)
        if not ids:
            return []
        out: List[Dict[str, Any]] = []
        for manifest in self._host.active():
            for action in manifest.actions:
                if context not in action.on:
                    continue
                template = action.open_url or action.copy
                value = fill(template, {context: ids})
                if not value:
                    continue
                out.append({
                    "id": action.id,
                    "label": action.label,
                    "kind": "openUrl" if action.open_url else "copy",
                    "value": value,
                    **_icon(manifest.id, action.icon),
                })
        return out

    def _ids_for(self, context: str, list_vm: Any, uid: Any) -> Dict[str, str]:
        try:
            number = int(uid)
        except (TypeError, ValueError):
            return {}
        if context == external_ids.TRACK:
            reader = getattr(list_vm, "external_ids_for_uid", None)
            return dict(reader(number) or {}) if callable(reader) else {}
        if self._track_service is None:
            return {}
        if context == external_ids.ARTIST:
            return external_ids.read(context, self._track_service.artist_repo.get_by_id(number))
        if context == external_ids.ALBUM:
            return external_ids.read(context, self._track_service.album_repo.get_by_id(number))
        return {}

    # ==================================================================
    # Jobs
    # ==================================================================

    @Property("QVariantList", notify=jobs_changed)
    def jobs(self) -> List[Dict[str, Any]]:
        """Long requests in flight: ``[{plugin, pluginName, label, stage, percent}]``."""
        if self._host is None:
            return []
        out = []
        for job in self._host.jobs():
            record = self._host.record(job.plugin_id)
            out.append({
                "plugin": job.plugin_id,
                "pluginName": record.name if record else job.plugin_id,
                "label": job.label,
                "stage": job.stage,
                "percent": job.percent,
            })
        return out

    # ==================================================================
    # The upgrade notice
    # ==================================================================

    @Property(str, notify=notice_changed)
    def notice(self) -> str:
        """The one-time notice for a library whose online features moved out, or "".

        Shown when the library holds ids that no enabled plugin declares — a
        vault from before plugins, opened with none installed — and until the
        user dismisses it. Nothing has been deleted, and it says so.
        """
        return self._notice

    @Slot()
    def dismissNotice(self) -> None:  # noqa: N802
        if self._store is not None:
            self._store.app_set(NOTICE_DISMISSED_KEY, True)
        self._notice = ""
        self.notice_changed.emit()

    def _refresh_notice(self) -> None:
        text = ""
        try:
            text = self._notice_text()
        except Exception:  # noqa: BLE001 — a notice is never worth a failure
            logger.debug("Could not decide the plugin notice", exc_info=True)
        if text != self._notice:
            self._notice = text
            self.notice_changed.emit()

    def _notice_text(self) -> str:
        if self._store is None or self._track_service is None:
            return ""
        if self._store.app_get(NOTICE_DISMISSED_KEY, False):
            return ""
        declared = set()
        if self._registry is not None:
            declared = {(k.entity, k.key) for k in self._registry.declarations()}
        orphaned = [
            held for held in external_ids.held(self._track_service.track_repo.session)
            if held not in declared
        ]
        if not orphaned:
            return ""
        # Not the provider's name: the core has none to give. A plugin is what
        # names its catalogue, and the plugin is exactly what is missing here.
        return "Downloading and online matching moved to plugins"

    @Property(str, notify=notice_changed)
    def noticeDetail(self) -> str:  # noqa: N802
        if not self._notice:
            return ""
        return (
            "This library has tracks, artists or albums identified online. Those "
            "ids are still here — nothing has been deleted. Install and turn on "
            "the plugin that understands them to search, fetch and refresh from "
            "them again."
        )
