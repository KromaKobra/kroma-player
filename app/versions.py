from __future__ import annotations

# ---------------------------------------------------------------------------
# Identity versions — semver strings, ordered comparison only
# ---------------------------------------------------------------------------

# The most recent release. Bumped in the same commit that adds
# ``docs/changelog/v<APP_VERSION>.md``; that commit lands on ``main``, and the
# tag ``v<APP_VERSION>`` is applied to it. The rule is not bookkeeping — the
# updater in :mod:`app.update` compares this against the newest tag on GitHub,
# so a value that trails the tags offers every user an update to the version
# they are already running. ``tests/test_versions.py`` enforces the changelog
# half of it, which is the half that can be checked offline.
APP_VERSION = "1.3.0"

GUI_API_VERSION = 13
MIN_GUI_API_VERSION = 13

PLUGIN_API_VERSION = GUI_API_VERSION
MIN_PLUGIN_API_VERSION = MIN_GUI_API_VERSION

VAULT_VERSION = 1
MIN_VAULT_VERSION = 1

KPI_FORMAT_VERSION = 1
MIN_KPI_FORMAT_VERSION = 1

KPP_FORMAT_VERSION = 1
MIN_KPP_FORMAT_VERSION = 1


def version_report() -> dict:
    """Return every version in the system as one flat mapping.

    Aggregated rather than centralised. The file-format versions gathered below
    stay in the modules that read and write those files: that is where a format
    change is made, and so it is where forgetting to bump one would be noticed.
    This exists so a diagnostics view or a backup manifest can report the whole
    set without knowing where each constant lives.

    The imports are inside the function deliberately. ``app.gui.gui_paths``
    imports this module, and that happens very early; pulling the settings,
    session, database and backup packages in at import time would turn this into
    a hub that most of the application transitively depends on.
    """
    from app.backup.identity import IDENTITY_VERSION
    from app.backup.manifest import MANIFEST_VERSION
    from app.config.kord_config import CONFIG_VERSION
    from app.database.schema import SCHEMA_VERSION
    from app.session import SESSION_VERSION
    from app.settings.file import SETTINGS_VERSION

    return {
        # Identity
        "app": APP_VERSION,
        # Contracts owned here
        "gui_api": GUI_API_VERSION,
        "min_gui_api": MIN_GUI_API_VERSION,
        "vault": VAULT_VERSION,
        "min_vault": MIN_VAULT_VERSION,
        "kpi_format": KPI_FORMAT_VERSION,
        "min_kpi_format": MIN_KPI_FORMAT_VERSION,
        "plugin_api": PLUGIN_API_VERSION,
        "min_plugin_api": MIN_PLUGIN_API_VERSION,
        "kpp_format": KPP_FORMAT_VERSION,
        "min_kpp_format": MIN_KPP_FORMAT_VERSION,
        # File formats, owned by their readers and writers
        "schema": SCHEMA_VERSION,
        "session": SESSION_VERSION,
        "settings": SETTINGS_VERSION,
        "config": CONFIG_VERSION,
        "identity": IDENTITY_VERSION,
        "backup_manifest": MANIFEST_VERSION,
    }
