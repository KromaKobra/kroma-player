"""Parsing and validation of a plugin's ``plugin.json``.

The manifest is everything the host needs to *show* a plugin without running
it: what it is called, what it can answer, which buttons and menu rows it adds,
what it needs installed, and what it asks the user to agree to. Nothing here
starts a process. A plugin that never runs still appears in Settings → Plugins
with its name, its programs and — when it cannot load — the reason why.

Mirrors ``app/gui/manifest.py`` deliberately: the same ``*ManifestError`` for
anything that makes the package unusable, the same ``is_compatible`` /
``incompatibility_reason()`` pair for anything that merely makes it unusable *by
this build*. The second is not an error, because an incompatible plugin should
be listed with a reason rather than vanish from the list.

What is checked, and why each one is a refusal rather than a warning:

- **The id** — ``^[a-z0-9]+(\\.[a-z0-9-]+)+$``. It names folders, settings
  scopes and every contributed id, so it has to be safe as all three, and it is
  permanent: the vault records which plugins are enabled by it.
- **Every contributed id is namespaced.** A bare ``"link"`` becomes
  ``"<id>/link"``; an id that names a namespace must name *this* plugin's.
  Otherwise two plugins' ``download`` actions collide, or one claims another's.
- **Every external-id kind is spelled as a key can be.** ``provider`` and
  ``kind`` become the ``"provider:kind"`` key the core stores them under — see
  :mod:`app.plugins.external_ids` — so a colon, or anything that cannot sit in
  a ``{entity/provider/kind}`` placeholder, is refused here, where the plugin
  author is the one who sees it.
- **A ``writeTag`` cannot be one of the core's own tags.** A plugin may have
  an id mirrored into the audio file, but not over the title, the lyrics or
  the ``LOCALID`` that ties a file to its row (see
  :mod:`app.importer.core_tags`).
- **Every regex compiles, with exactly one group.** ``parse`` and ``tagScan``
  run on the UI thread against whatever a user pasted; a pattern that throws
  there is a crash, and one with no group returns nothing useful.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.importer.core_tags import CORE_TAGS
from app.plugins import external_ids
from app.versions import MIN_PLUGIN_API_VERSION, PLUGIN_API_VERSION

MANIFEST_FILENAME = "plugin.json"

ID_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9-]+)+$")
_LOCAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

#: What a plugin may say it does. Shown at install, never enforced — see Trust
#: in the plugin design. An unknown word is refused so a typo is not shown to
#: the user as a capability.
DECLARATIONS = ("network", "runs-programs", "files-outside-vault")

#: Provider kinds a plugin can offer, by the name ``provides`` uses for them.
PROVIDER_KINDS = (
    "trackSources", "identity", "metadata", "artwork", "audio", "importSources",
)

ROLES = ("recording", "instrumental")
SETTING_TYPES = ("bool", "enum", "string", "int")
ACTION_CONTEXTS = ("track", "artist", "album")
INPUT_KINDS = ("text", "file")

# `{track/provider/kind}` — an entity, then the key's two halves.
_PLACEHOLDER_RE = re.compile(r"\{([a-z]+)/([a-z0-9.-]+)/([a-z0-9.-]+)\}")

# A Vorbis comment name as a `writeTag` may give it: upper case, so it reads
# like every tag the core writes, and nothing a comment name cannot hold.
_TAG_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class PluginManifestError(Exception):
    """Raised when a plugin.json is missing, malformed, or unsafe."""


@dataclass(frozen=True)
class Program:
    """One outside program a plugin needs on the machine."""

    name: str
    min_version: str = ""
    version_args: Tuple[str, ...] = ("--version",)
    #: What the plugin uses it for, said to the user in Settings → System.
    purpose: str = ""
    #: A command that installs it on Windows, where nothing comes with a
    #: package manager — shown with a copy button. Optional.
    install: str = ""


@dataclass(frozen=True)
class IdKind:
    """One kind of external id a plugin declares — what the core knows of it.

    Everything here is used *without* starting the plugin: normalising a pasted
    link, reading an id out of a file's tags, recognising a CSV column, drawing
    "Open in …". That is why it is data and not a method on the plugin.
    """

    plugin_id: str
    entity: str
    provider: str
    kind: str
    label: str
    role: str = ""
    source_label: str = ""
    url: str = ""
    parse: Tuple[str, ...] = ()
    tag_scan: Tuple[str, ...] = ()
    read_tags: Tuple[str, ...] = ()
    csv_columns: Tuple[str, ...] = ()
    #: The Vorbis comment the core mirrors an id of this kind into, in the
    #: track's own file. Tracks only. ``""`` keeps it out of the file.
    write_tag: str = ""
    #: A picture file inside the plugin, drawn beside an id of this kind.
    icon: str = ""

    @property
    def key(self) -> str:
        return external_ids.make_key(self.provider, self.kind)

    @property
    def unique(self) -> bool:
        """Whether one value may belong to only one row — see :mod:`app.plugins.external_ids`.

        Every kind names its entity, and so is unique, except an instrumental:
        that names an upload a second file came from, and one upload can back
        two tracks.
        """
        return self.role != "instrumental"

    def parse_value(self, text: str) -> str:
        """The id in *text*, or ``""`` when it holds none.

        Each pattern is tried against the whole stripped text and then searched
        for, in order; the first capture wins. A pattern that matches the whole
        text is what accepts a bare id, and one that matches inside it is what
        accepts a link.
        """
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        for pattern in self.parse:
            compiled = re.compile(pattern)
            found = compiled.fullmatch(cleaned) or compiled.search(cleaned)
            if found and found.group(1):
                return found.group(1)
        return ""

    def scan(self, text: str) -> str:
        """The id embedded anywhere in one tag value, or ``""``."""
        for pattern in self.tag_scan:
            found = re.search(pattern, str(text or ""))
            if found and found.group(1):
                return found.group(1)
        return ""

    def url_for(self, value: str) -> str:
        """``url`` with ``{value}`` filled in, or ``""`` when there is no template."""
        if not self.url or not value:
            return ""
        return self.url.replace("{value}", value)


@dataclass(frozen=True)
class TrackSourceDecl:
    id: str
    query: bool = False
    label: str = ""


@dataclass(frozen=True)
class ImportSourceDecl:
    id: str
    label: str
    icon: str = ""
    input: str = "text"
    prompt: str = ""
    filters: Tuple[str, ...] = ()
    #: The local id the plugin is asked to resolve, without the namespace.
    local_id: str = ""


@dataclass(frozen=True)
class ActionDecl:
    id: str
    label: str
    on: Tuple[str, ...]
    icon: str = ""
    open_url: str = ""
    copy: str = ""


@dataclass(frozen=True)
class PageDecl:
    id: str
    title: str
    icon: str = ""
    track_source: str = ""


@dataclass(frozen=True)
class SettingDecl:
    key: str
    type: str
    label: str
    default: Any = None
    values: Tuple[str, ...] = ()
    description: str = ""
    minimum: Optional[int] = None
    maximum: Optional[int] = None

    def coerce(self, value: Any) -> Any:
        """*value* as this setting's type, or the default when it cannot be."""
        try:
            if self.type == "bool":
                return bool(value)
            if self.type == "int":
                number = int(value)
                if self.minimum is not None:
                    number = max(self.minimum, number)
                if self.maximum is not None:
                    number = min(self.maximum, number)
                return number
            if self.type == "enum":
                return value if value in self.values else self.default
            return str(value if value is not None else "")
        except (TypeError, ValueError):
            return self.default


@dataclass(frozen=True)
class PluginManifest:
    """A validated plugin manifest."""

    id: str
    name: str
    version: str
    api: int
    author: str = ""
    description: str = ""
    homepage: str = ""
    runtime: str = "python"
    main: str = "main.py"
    programs: Tuple[Program, ...] = ()
    consent: str = ""
    declares: Tuple[str, ...] = ()
    provides: Mapping[str, Any] = field(default_factory=dict)
    external_ids: Tuple[IdKind, ...] = ()
    track_sources: Tuple[TrackSourceDecl, ...] = ()
    import_sources: Tuple[ImportSourceDecl, ...] = ()
    actions: Tuple[ActionDecl, ...] = ()
    pages: Tuple[PageDecl, ...] = ()
    settings: Tuple[SettingDecl, ...] = ()
    icon: str = ""

    @property
    def is_compatible(self) -> bool:
        """True when this build can run the plugin."""
        return MIN_PLUGIN_API_VERSION <= self.api <= PLUGIN_API_VERSION

    def incompatibility_reason(self) -> str:
        """Human-readable explanation of why the plugin cannot run, or ""."""
        if self.is_compatible:
            return ""
        if self.api > PLUGIN_API_VERSION:
            return (
                f"needs plugin API level {self.api}, but this build provides "
                f"{PLUGIN_API_VERSION} — update Kroma Player"
            )
        return (
            f"targets plugin API level {self.api}, which this build no longer "
            f"supports (minimum {MIN_PLUGIN_API_VERSION})"
        )

    def provides_kind(self, kind: str) -> bool:
        return kind in self.provides

    def declared_keys(self, entity: str) -> frozenset:
        """The keys of every kind this plugin declares for *entity*."""
        return frozenset(k.key for k in self.external_ids if k.entity == entity)

    def recording_keys(self) -> frozenset:
        """The track kinds this plugin names a recording by — never an instrumental's.

        The tracks an artist or an album can be identified from are the ones
        holding one of these: an id the plugin cannot read is no help to it.
        """
        return frozenset(
            k.key for k in self.external_ids if k.entity == "track" and k.role == "recording"
        )

    def entities_for(self, kind: str) -> Tuple[str, ...]:
        """Which entities a provider kind answers for, e.g. ``("artist", "album")``."""
        spec = self.provides.get(kind)
        if not isinstance(spec, Mapping):
            return ()
        return tuple(spec.get("entities") or ())

    @property
    def audio_roles(self) -> Tuple[str, ...]:
        spec = self.provides.get("audio")
        if not isinstance(spec, Mapping):
            return ()
        return tuple(spec.get("roles") or ("recording",))

    @property
    def audio_accepts(self) -> Tuple[str, ...]:
        """The track keys this plugin can fetch audio for."""
        spec = self.provides.get("audio")
        if not isinstance(spec, Mapping):
            return ()
        return tuple(spec.get("accepts") or ())

    @property
    def album_tracks(self) -> bool:
        spec = self.provides.get("metadata")
        return isinstance(spec, Mapping) and bool(spec.get("albumTracks"))

    def setting(self, key: str) -> Optional[SettingDecl]:
        return next((s for s in self.settings if s.key == key), None)


# ----------------------------------------------------------------------
# Field helpers
# ----------------------------------------------------------------------

def _require_str(data: Mapping, key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PluginManifestError(f"{where}: '{key}' must be a non-empty string")
    return value.strip()


def _opt_str(data: Mapping, key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def _str_list(data: Mapping, key: str, where: str) -> Tuple[str, ...]:
    value = data.get(key, [])
    if value in (None, ""):
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PluginManifestError(f"{where}: '{key}' must be a list of strings")
    return tuple(v for v in value if v.strip())


def _regexes(data: Mapping, key: str, where: str) -> Tuple[str, ...]:
    patterns = _str_list(data, key, where)
    for pattern in patterns:
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise PluginManifestError(f"{where}: '{key}' pattern {pattern!r} ({exc})") from exc
        if compiled.groups != 1:
            raise PluginManifestError(
                f"{where}: '{key}' pattern {pattern!r} must have exactly one group"
            )
    return patterns


def _namespaced(raw: str, plugin_id: str, where: str) -> str:
    """``"<plugin>/<local>"`` for a contributed id, refusing someone else's namespace."""
    text = (raw or "").strip()
    if "/" in text:
        namespace, _, local = text.partition("/")
        if namespace != plugin_id:
            raise PluginManifestError(
                f"{where}: id {text!r} is namespaced under {namespace!r}, "
                f"not this plugin ({plugin_id!r})"
            )
    else:
        local = text
    if not _LOCAL_ID_RE.match(local):
        raise PluginManifestError(f"{where}: id {text!r} is not a valid identifier")
    return f"{plugin_id}/{local}"


def _local(namespaced: str) -> str:
    return namespaced.partition("/")[2]


# ----------------------------------------------------------------------
# Sections
# ----------------------------------------------------------------------

def _programs(data: Mapping, where: str) -> Tuple[Program, ...]:
    requires = data.get("requires") or {}
    if not isinstance(requires, Mapping):
        raise PluginManifestError(f"{where}: 'requires' must be an object")
    raw = requires.get("programs") or []
    if not isinstance(raw, list):
        raise PluginManifestError(f"{where}: 'requires.programs' must be a list")
    programs: List[Program] = []
    for entry in raw:
        # The design's shorthand, a bare name, is accepted alongside the full
        # form: most programs have no floor worth stating.
        if isinstance(entry, str):
            entry = {"name": entry}
        if not isinstance(entry, Mapping):
            raise PluginManifestError(f"{where}: each required program must be an object")
        name = _require_str(entry, "name", f"{where}: requires.programs")
        if "/" in name or "\\" in name:
            raise PluginManifestError(
                f"{where}: program {name!r} must be a name, not a path"
            )
        args = entry.get("versionArgs")
        if args is not None and (
            not isinstance(args, list) or not all(isinstance(a, str) for a in args)
        ):
            raise PluginManifestError(f"{where}: '{name}' versionArgs must be strings")
        programs.append(Program(
            name=name,
            min_version=_opt_str(entry, "minVersion"),
            version_args=tuple(args) if args else ("--version",),
            purpose=_opt_str(entry, "purpose"),
            install=_opt_str(entry, "install"),
        ))
    return tuple(programs)


def _external_ids(data: Mapping, plugin_id: str, where: str) -> Tuple[IdKind, ...]:
    raw = data.get("externalIds") or []
    if not isinstance(raw, list):
        raise PluginManifestError(f"{where}: 'externalIds' must be a list")
    kinds: List[IdKind] = []
    seen: set = set()
    for index, entry in enumerate(raw):
        at = f"{where}: externalIds[{index}]"
        if not isinstance(entry, Mapping):
            raise PluginManifestError(f"{at} must be an object")
        entity = _require_str(entry, "entity", at)
        provider = _require_str(entry, "provider", at)
        kind = _require_str(entry, "kind", at)
        if entity not in external_ids.ENTITIES:
            raise PluginManifestError(f"{at}: unknown entity {entity!r}")
        for part, text in (("provider", provider), ("kind", kind)):
            if not external_ids.PART_RE.match(text):
                raise PluginManifestError(
                    f"{at}: {part} {text!r} must be lower case letters, digits, "
                    "dots and dashes, at most 64 characters"
                )
        if (entity, provider, kind) in seen:
            raise PluginManifestError(f"{at}: {entity}/{provider}/{kind} is declared twice")
        seen.add((entity, provider, kind))
        role = _opt_str(entry, "role")
        if entity == "track":
            role = role or "recording"
            if role not in ROLES:
                raise PluginManifestError(f"{at}: role must be one of {', '.join(ROLES)}")
        elif role:
            raise PluginManifestError(f"{at}: only track ids carry a role")
        url = _opt_str(entry, "url")
        if url and "{value}" not in url:
            raise PluginManifestError(f"{at}: 'url' must contain {{value}}")
        if url and not re.match(r"^https?://", url):
            raise PluginManifestError(f"{at}: 'url' must be an http(s) URL")
        icon = _opt_str(entry, "icon")
        if icon:
            icon = _relative_file(icon, at, "icon")
        write_tag = _opt_str(entry, "writeTag")
        if write_tag:
            if entity != "track":
                raise PluginManifestError(f"{at}: only track ids can be written to a file")
            if not _TAG_RE.match(write_tag):
                raise PluginManifestError(
                    f"{at}: writeTag {write_tag!r} must be upper case letters, digits "
                    "and underscores"
                )
            if write_tag in CORE_TAGS:
                raise PluginManifestError(
                    f"{at}: writeTag {write_tag!r} is one of Kroma's own tags"
                )
        kinds.append(IdKind(
            plugin_id=plugin_id,
            entity=entity,
            provider=provider,
            kind=kind,
            label=_require_str(entry, "label", at),
            role=role,
            source_label=_opt_str(entry, "sourceLabel"),
            url=url,
            parse=_regexes(entry, "parse", at),
            tag_scan=_regexes(entry, "tagScan", at),
            read_tags=_str_list(entry, "readTags", at),
            csv_columns=_str_list(entry, "csvColumns", at),
            write_tag=write_tag,
            icon=icon,
        ))
    return tuple(kinds)


def _provides(data: Mapping, plugin_id: str, kinds: Sequence[IdKind], where: str):
    raw = data.get("provides") or {}
    if not isinstance(raw, Mapping):
        raise PluginManifestError(f"{where}: 'provides' must be an object")
    for kind in raw:
        if kind not in PROVIDER_KINDS:
            raise PluginManifestError(f"{where}: unknown provider kind {kind!r}")

    declared_keys = {k.key for k in kinds if k.entity == "track"}
    audio = raw.get("audio")
    if audio is not None:
        if not isinstance(audio, Mapping):
            raise PluginManifestError(f"{where}: 'provides.audio' must be an object")
        for role in audio.get("roles") or ["recording"]:
            if role not in ROLES:
                raise PluginManifestError(f"{where}: audio role {role!r} is unknown")
        for key in audio.get("accepts") or []:
            if key not in declared_keys:
                raise PluginManifestError(
                    f"{where}: audio accepts {key!r}, which no externalIds entry declares"
                )
    for kind in ("identity", "metadata", "artwork"):
        spec = raw.get(kind)
        if spec is None:
            continue
        if not isinstance(spec, Mapping):
            raise PluginManifestError(f"{where}: 'provides.{kind}' must be an object")
        for entity in spec.get("entities") or []:
            if entity not in external_ids.ENTITIES:
                raise PluginManifestError(f"{where}: {kind} entity {entity!r} is unknown")

    sources: List[TrackSourceDecl] = []
    for index, entry in enumerate(raw.get("trackSources") or []):
        at = f"{where}: provides.trackSources[{index}]"
        if not isinstance(entry, Mapping):
            raise PluginManifestError(f"{at} must be an object")
        local = _require_str(entry, "id", at)
        if not _LOCAL_ID_RE.match(local):
            raise PluginManifestError(f"{at}: id {local!r} is not a valid identifier")
        sources.append(TrackSourceDecl(
            id=local, query=bool(entry.get("query")), label=_opt_str(entry, "label"),
        ))
    return dict(raw), tuple(sources)


def _contributes(
    data: Mapping, plugin_id: str, sources: Sequence[TrackSourceDecl],
    kinds: Sequence[IdKind], where: str,
):
    raw = data.get("contributes") or {}
    if not isinstance(raw, Mapping):
        raise PluginManifestError(f"{where}: 'contributes' must be an object")

    imports: List[ImportSourceDecl] = []
    for index, entry in enumerate(raw.get("importSources") or []):
        at = f"{where}: contributes.importSources[{index}]"
        if not isinstance(entry, Mapping):
            raise PluginManifestError(f"{at} must be an object")
        ident = _namespaced(_require_str(entry, "id", at), plugin_id, at)
        kind = _opt_str(entry, "input") or "text"
        if kind not in INPUT_KINDS:
            raise PluginManifestError(f"{at}: input must be one of {', '.join(INPUT_KINDS)}")
        imports.append(ImportSourceDecl(
            id=ident,
            label=_require_str(entry, "label", at),
            icon=_opt_str(entry, "icon"),
            input=kind,
            prompt=_opt_str(entry, "prompt"),
            filters=_str_list(entry, "filters", at),
            local_id=_local(ident),
        ))

    actions: List[ActionDecl] = []
    for index, entry in enumerate(raw.get("actions") or []):
        at = f"{where}: contributes.actions[{index}]"
        if not isinstance(entry, Mapping):
            raise PluginManifestError(f"{at} must be an object")
        ident = _namespaced(_require_str(entry, "id", at), plugin_id, at)
        on = _str_list(entry, "on", at) or ("track",)
        for context in on:
            if context not in ACTION_CONTEXTS:
                raise PluginManifestError(f"{at}: unknown context {context!r}")
        open_url = _opt_str(entry, "openUrl")
        copy = _opt_str(entry, "copy")
        # Declarative only, and exactly one of the two. An action that runs
        # code in the plugin is a later API level; until then "do nothing" is
        # not an action worth drawing.
        if bool(open_url) == bool(copy):
            raise PluginManifestError(f"{at}: give exactly one of 'openUrl' or 'copy'")
        template = open_url or copy
        declared = {(k.entity, k.provider, k.kind) for k in kinds}
        for entity, provider, kind in _PLACEHOLDER_RE.findall(template):
            if (entity, provider, kind) not in declared:
                raise PluginManifestError(
                    f"{at}: placeholder {{{entity}/{provider}/{kind}}} names no id "
                    "this plugin declares"
                )
        if open_url and not re.match(r"^https?://", open_url):
            raise PluginManifestError(f"{at}: 'openUrl' must be an http(s) URL")
        actions.append(ActionDecl(
            id=ident,
            label=_require_str(entry, "label", at),
            on=on,
            icon=_opt_str(entry, "icon"),
            open_url=open_url,
            copy=copy,
        ))

    known_sources = {s.id for s in sources}
    pages: List[PageDecl] = []
    for index, entry in enumerate(raw.get("pages") or []):
        at = f"{where}: contributes.pages[{index}]"
        if not isinstance(entry, Mapping):
            raise PluginManifestError(f"{at} must be an object")
        source = _opt_str(entry, "trackSource")
        if source and source not in known_sources:
            raise PluginManifestError(f"{at}: trackSource {source!r} is not in provides")
        pages.append(PageDecl(
            id=_namespaced(_require_str(entry, "id", at), plugin_id, at),
            title=_require_str(entry, "title", at),
            icon=_opt_str(entry, "icon"),
            track_source=source,
        ))

    settings: List[SettingDecl] = []
    raw_settings = raw.get("settings") or {}
    if not isinstance(raw_settings, Mapping):
        raise PluginManifestError(f"{where}: 'contributes.settings' must be an object")
    for key, entry in raw_settings.items():
        at = f"{where}: contributes.settings.{key}"
        if not _LOCAL_ID_RE.match(str(key)):
            raise PluginManifestError(f"{at}: not a valid key")
        if not isinstance(entry, Mapping):
            raise PluginManifestError(f"{at} must be an object")
        kind = _require_str(entry, "type", at)
        if kind not in SETTING_TYPES:
            raise PluginManifestError(f"{at}: type must be one of {', '.join(SETTING_TYPES)}")
        values = _str_list(entry, "values", at)
        if kind == "enum" and not values:
            raise PluginManifestError(f"{at}: an enum needs 'values'")
        default = entry.get("default")
        if kind == "enum" and default not in values:
            default = values[0]
        decl = SettingDecl(
            key=str(key),
            type=kind,
            label=_require_str(entry, "label", at),
            default=default,
            values=values,
            description=_opt_str(entry, "description"),
            minimum=entry.get("min") if isinstance(entry.get("min"), int) else None,
            maximum=entry.get("max") if isinstance(entry.get("max"), int) else None,
        )
        settings.append(replace(decl, default=decl.coerce(default)))

    return tuple(imports), tuple(actions), tuple(pages), tuple(settings)


def _relative_file(value: str, where: str, key: str) -> str:
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in value:
        raise PluginManifestError(f"{where}: '{key}' must be a relative path inside the plugin")
    return candidate.as_posix()


def parse(raw: str | bytes, source: str = MANIFEST_FILENAME) -> PluginManifest:
    """Parse and validate manifest text, reporting problems against *source*.

    Compatibility is *not* checked here, exactly as for a GUI: read
    ``is_compatible`` on the result, so an incompatible plugin can still be
    listed with a reason instead of vanishing.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PluginManifestError(f"{source}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise PluginManifestError(f"{source}: must contain a JSON object")

    plugin_id = _require_str(data, "id", source)
    if not ID_RE.match(plugin_id):
        raise PluginManifestError(
            f"{source}: id {plugin_id!r} must look like 'author.name' — lower case, "
            "digits and dashes, at least one dot"
        )

    api = data.get("api")
    if not isinstance(api, int) or isinstance(api, bool):
        raise PluginManifestError(f"{source}: 'api' must be an integer")

    backend = data.get("backend") or {}
    if not isinstance(backend, Mapping):
        raise PluginManifestError(f"{source}: 'backend' must be an object")
    runtime = _opt_str(backend, "runtime") or "python"
    if runtime != "python":
        # The only runtime this build can start. Anything else is a later API
        # level, and saying so beats starting nothing and calling it a crash.
        raise PluginManifestError(f"{source}: backend runtime {runtime!r} is not supported")
    main = _relative_file(_opt_str(backend, "main") or "main.py", source, "backend.main")

    declares = _str_list(data, "declares", source)
    for word in declares:
        if word not in DECLARATIONS:
            raise PluginManifestError(f"{source}: unknown declaration {word!r}")

    consent = data.get("consent") or ""
    if consent and not isinstance(consent, str):
        raise PluginManifestError(f"{source}: 'consent' must name a file")
    if consent:
        consent = _relative_file(consent, source, "consent")

    icon = _opt_str(data, "icon")
    if icon:
        icon = _relative_file(icon, source, "icon")

    kinds = _external_ids(data, plugin_id, source)
    provides, sources = _provides(data, plugin_id, kinds, source)
    imports, actions, pages, settings = _contributes(data, plugin_id, sources, kinds, source)

    return PluginManifest(
        id=plugin_id,
        name=_require_str(data, "name", source),
        version=_require_str(data, "version", source),
        api=api,
        author=_opt_str(data, "author"),
        description=_opt_str(data, "description"),
        homepage=_opt_str(data, "homepage"),
        runtime=runtime,
        main=main,
        programs=_programs(data, source),
        consent=consent,
        declares=declares,
        provides=provides,
        external_ids=kinds,
        track_sources=sources,
        import_sources=imports,
        actions=actions,
        pages=pages,
        settings=settings,
        icon=icon,
    )


def load_manifest(directory: Path) -> PluginManifest:
    """Load and validate ``<directory>/plugin.json``.

    Also checks the files the manifest names are really there — the backend's
    entry point and the consent text. A plugin whose agreement is missing would
    otherwise be enabled with nothing to agree to, which is worse than refusing
    it.
    """
    path = Path(directory) / MANIFEST_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PluginManifestError(f"{path}: no {MANIFEST_FILENAME} found") from exc
    except OSError as exc:
        raise PluginManifestError(f"{path}: cannot be read ({exc})") from exc

    manifest = parse(raw, str(path))
    for label, relative in (("backend.main", manifest.main), ("consent", manifest.consent)):
        if relative and not (Path(directory) / relative).is_file():
            raise PluginManifestError(f"{path}: {label} file '{relative}' does not exist")
    return manifest


def placeholders(template: str) -> List[Tuple[str, str]]:
    """``[(entity, key)]`` for every ``{entity/provider/kind}`` in *template*."""
    return [
        (entity, external_ids.make_key(provider, kind))
        for entity, provider, kind in _PLACEHOLDER_RE.findall(template or "")
    ]


def fill(template: str, ids_by_entity: Mapping[str, Mapping[str, str]]) -> str:
    """*template* with every placeholder filled, or ``""`` if one cannot be.

    An action whose template cannot be filled is hidden, which is the whole
    condition language for now: "only on tracks that have this id" is the
    condition almost every action wants, and it falls out of the template.
    """
    missing = False

    def replace(match: "re.Match[str]") -> str:
        nonlocal missing
        entity, provider, kind = match.groups()
        value = (ids_by_entity.get(entity) or {}).get(external_ids.make_key(provider, kind))
        if not value:
            missing = True
            return ""
        return str(value)

    filled = _PLACEHOLDER_RE.sub(replace, template or "")
    return "" if missing else filled
