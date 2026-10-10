"""What a plugin.json may say, and what it is refused for.

The manifest is read without running anything, so everything it is checked for
is something that would otherwise go wrong *later* — in a process, on a user's
machine, against their library — where the plugin's author is no longer the one
looking. Each refusal here is one of those, moved to the moment of install.
"""
from __future__ import annotations

import copy
import json

import pytest

from app.plugins.manifest import (
    PluginManifestError,
    fill,
    load_manifest,
    parse,
    placeholders,
)
from app.versions import MIN_PLUGIN_API_VERSION, PLUGIN_API_VERSION
from tests.fakes.providers import MANIFEST


def _parse(**changes):
    data = copy.deepcopy(MANIFEST)
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return parse(json.dumps(data))


def _with_kind(**entry):
    data = copy.deepcopy(MANIFEST)
    data["externalIds"] = [{**data["externalIds"][0], **entry}]
    data["provides"].pop("audio")
    return parse(json.dumps(data))


# -- the shape ---------------------------------------------------------------


def test_the_fake_manifest_is_valid():
    manifest = _parse()

    assert manifest.id == "test.fake"
    assert manifest.is_compatible
    assert [k.key for k in manifest.external_ids] == [
        "fake:video", "fake:instrumental-video", "fake:channel", "fake:release",
    ]


@pytest.mark.parametrize("raw", ["not json", "[]", "3"])
def test_what_is_not_an_object_is_refused(raw):
    with pytest.raises(PluginManifestError):
        parse(raw)


@pytest.mark.parametrize("plugin_id", [
    "", "fake", "Test.Fake", "test.fake/", "../evil", "test..fake", "test.fake.", "test fake",
])
def test_an_id_that_could_not_name_a_folder_is_refused(plugin_id):
    with pytest.raises(PluginManifestError, match="id"):
        _parse(id=plugin_id)


@pytest.mark.parametrize("plugin_id", ["kroma.fetch-audio", "a.b", "author.plugin-2.x"])
def test_ordinary_ids_are_accepted(plugin_id):
    assert _parse(id=plugin_id).id == plugin_id


@pytest.mark.parametrize("missing", ["name", "version", "api"])
def test_required_fields_are_required(missing):
    with pytest.raises(PluginManifestError):
        _parse(**{missing: None})


def test_another_runtime_is_refused_rather_than_mistaken_for_a_crash():
    with pytest.raises(PluginManifestError, match="runtime"):
        _parse(backend={"runtime": "node", "main": "index.js"})


@pytest.mark.parametrize("main", ["/etc/passwd", "../main.py", "lib\\main.py"])
def test_the_entry_point_must_be_inside_the_plugin(main):
    with pytest.raises(PluginManifestError, match="main"):
        _parse(backend={"runtime": "python", "main": main})


def test_an_unknown_declaration_is_refused():
    """A typo would be shown to the user as a capability."""
    with pytest.raises(PluginManifestError, match="declaration"):
        _parse(declares=["netwrok"])


# -- the API range --------------------------------------------------------------


def test_a_plugin_from_the_future_is_listed_but_not_loadable():
    manifest = _parse(api=PLUGIN_API_VERSION + 1)

    assert not manifest.is_compatible
    assert "update Kroma Player" in manifest.incompatibility_reason()


def test_a_plugin_from_before_the_floor_is_listed_but_not_loadable():
    manifest = _parse(api=MIN_PLUGIN_API_VERSION - 1)

    assert not manifest.is_compatible
    assert "no longer supports" in manifest.incompatibility_reason()


# -- namespacing -----------------------------------------------------------------


def test_a_bare_contributed_id_is_namespaced_by_the_plugin():
    manifest = _parse()

    assert manifest.import_sources[0].id == "test.fake/link"
    assert manifest.import_sources[0].local_id == "link"
    assert {a.id for a in manifest.actions} == {"test.fake/open", "test.fake/copy"}


def test_its_own_namespace_written_out_is_accepted():
    data = copy.deepcopy(MANIFEST)
    data["contributes"]["importSources"][0]["id"] = "test.fake/link"
    assert parse(json.dumps(data)).import_sources[0].id == "test.fake/link"


@pytest.mark.parametrize("section", ["importSources", "actions", "pages"])
def test_claiming_another_plugins_namespace_is_refused(section):
    data = copy.deepcopy(MANIFEST)
    data["contributes"][section][0]["id"] = "someone.else/thing"
    with pytest.raises(PluginManifestError, match="namespaced"):
        parse(json.dumps(data))


# -- external ids ---------------------------------------------------------------


def test_any_catalogue_can_be_declared():
    """The core keeps whatever kinds a plugin declares; it has no list of its own."""
    data = copy.deepcopy(MANIFEST)
    data["externalIds"] = [{**data["externalIds"][0], "provider": "acme.music", "kind": "recording-2"}]
    data["provides"].pop("audio")
    data["contributes"].pop("actions")

    assert parse(json.dumps(data)).external_ids[0].key == "acme.music:recording-2"


@pytest.mark.parametrize("field, value", [
    ("provider", "Acme"),
    ("provider", "acme:music"),
    ("kind", "rec ording"),
    ("kind", "x" * 65),
])
def test_a_kind_that_cannot_be_a_key_is_refused(field, value):
    """A colon would make ``provider:kind`` ambiguous, and a placeholder could not name it."""
    with pytest.raises(PluginManifestError, match=field):
        _with_kind(**{field: value})


def test_an_unknown_entity_is_refused():
    with pytest.raises(PluginManifestError, match="entity"):
        _with_kind(entity="playlist")


def test_an_instrumental_kind_is_the_one_that_is_not_unique():
    kinds = {k.key: k for k in _parse().external_ids}

    assert kinds["fake:video"].unique
    assert kinds["fake:channel"].unique
    assert not kinds["fake:instrumental-video"].unique
    assert _parse().recording_keys() == {"fake:video"}


def test_a_write_tag_is_read_off_the_manifest():
    assert _parse().external_ids[0].write_tag == "FAKEID"


@pytest.mark.parametrize("tag", ["TITLE", "LOCALID", "LYRICS", "METADATA_BLOCK_PICTURE"])
def test_a_write_tag_cannot_overwrite_kromas_own(tag):
    with pytest.raises(PluginManifestError, match="Kroma's own"):
        _with_kind(writeTag=tag)


@pytest.mark.parametrize("tag", ["fakeid", "FAKE=ID", "FAKE ID", ""])
def test_a_write_tag_must_be_a_plain_comment_name(tag):
    if not tag:
        assert _with_kind(writeTag=tag).external_ids[0].write_tag == ""
        return
    with pytest.raises(PluginManifestError, match="writeTag"):
        _with_kind(writeTag=tag)


def test_only_a_track_id_can_be_written_to_a_file():
    data = copy.deepcopy(MANIFEST)
    data["externalIds"][2]["writeTag"] = "CHANNELID"
    with pytest.raises(PluginManifestError, match="file"):
        parse(json.dumps(data))


def test_a_kind_declared_twice_is_refused():
    data = copy.deepcopy(MANIFEST)
    data["externalIds"].append(dict(data["externalIds"][0]))
    with pytest.raises(PluginManifestError, match="twice"):
        parse(json.dumps(data))


@pytest.mark.parametrize("pattern", ["[unclosed", "no-group", "(two)(groups)"])
def test_a_parse_rule_must_compile_with_exactly_one_group(pattern):
    with pytest.raises(PluginManifestError, match="parse"):
        _with_kind(parse=[pattern])


def test_only_track_ids_carry_a_role():
    data = copy.deepcopy(MANIFEST)
    data["externalIds"][2]["role"] = "recording"
    with pytest.raises(PluginManifestError, match="role"):
        parse(json.dumps(data))


def test_a_url_template_must_have_somewhere_to_put_the_id():
    with pytest.raises(PluginManifestError, match="value"):
        _with_kind(url="https://example.com/watch")


def test_audio_cannot_accept_a_kind_nobody_declared():
    data = copy.deepcopy(MANIFEST)
    data["provides"]["audio"]["accepts"] = ["fake:mystery"]
    with pytest.raises(PluginManifestError, match="accepts"):
        parse(json.dumps(data))


def test_parse_rules_read_a_bare_id_and_a_link():
    kind = _parse().external_ids[0]

    assert kind.parse_value("Xq7Lm2Pz9Ka") == "Xq7Lm2Pz9Ka"
    assert kind.parse_value("  https://fake.example/watch?v=Xq7Lm2Pz9Ka&t=3 ") == "Xq7Lm2Pz9Ka"
    assert kind.parse_value("not an id") == ""
    assert kind.url_for("Xq7Lm2Pz9Ka") == "https://fake.example/watch?v=Xq7Lm2Pz9Ka"


# -- actions ---------------------------------------------------------------------


def test_an_action_does_exactly_one_thing():
    data = copy.deepcopy(MANIFEST)
    data["contributes"]["actions"][0]["copy"] = "{track/fake/video}"
    with pytest.raises(PluginManifestError, match="exactly one"):
        parse(json.dumps(data))


def test_an_action_placeholder_must_name_an_id_this_plugin_declares():
    data = copy.deepcopy(MANIFEST)
    data["contributes"]["actions"][1]["copy"] = "{track/other/track}"
    with pytest.raises(PluginManifestError, match="placeholder"):
        parse(json.dumps(data))


def test_an_open_action_must_open_the_web():
    data = copy.deepcopy(MANIFEST)
    data["contributes"]["actions"][0]["openUrl"] = "file:///etc/passwd?{track/fake/video}"
    with pytest.raises(PluginManifestError, match="http"):
        parse(json.dumps(data))


def test_a_template_is_filled_or_the_action_is_hidden():
    template = "https://fake.example/watch?v={track/fake/video}"

    assert placeholders(template) == [("track", "fake:video")]
    assert fill(template, {"track": {"fake:video": "abc"}}) == "https://fake.example/watch?v=abc"
    assert fill(template, {"track": {}}) == ""


# -- settings ---------------------------------------------------------------------


def test_settings_are_typed_and_defaulted():
    data = copy.deepcopy(MANIFEST)
    data["contributes"]["settings"] = {
        "quality": {"type": "enum", "label": "Quality", "values": ["best", "good"]},
        "retries": {"type": "int", "label": "Retries", "default": 9, "min": 0, "max": 5},
        "verbose": {"type": "bool", "label": "Verbose", "default": True},
    }
    settings = {s.key: s for s in parse(json.dumps(data)).settings}

    assert settings["quality"].default == "best", "an enum defaults to its first value"
    assert settings["retries"].default == 5, "clamped"
    assert settings["quality"].coerce("terrible") == "best"
    assert settings["retries"].coerce("3") == 3
    assert settings["verbose"].coerce(0) is False


def test_an_unknown_setting_type_is_refused():
    data = copy.deepcopy(MANIFEST)
    data["contributes"]["settings"] = {"x": {"type": "colour", "label": "X"}}
    with pytest.raises(PluginManifestError, match="type"):
        parse(json.dumps(data))


# -- on disk ----------------------------------------------------------------------


def test_files_the_manifest_names_must_exist(tmp_path):
    data = copy.deepcopy(MANIFEST)
    data["consent"] = "agreement.md"
    (tmp_path / "plugin.json").write_text(json.dumps(data))
    (tmp_path / "main.py").write_text("")

    with pytest.raises(PluginManifestError, match="consent"):
        load_manifest(tmp_path)

    (tmp_path / "agreement.md").write_text("Terms.")
    assert load_manifest(tmp_path).consent == "agreement.md"


def test_the_shipped_fixture_plugin_is_valid():
    from tests.fakes.plugin_env import FIXTURE

    assert load_manifest(FIXTURE).id == "test.echo"
