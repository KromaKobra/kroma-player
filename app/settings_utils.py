# app/settings_utils.py
from PySide6.QtCore import QByteArray, QSettings

def safe_restore_geometry_and_state(window, org: str, app_name: str,
                                    geom_key="mainwindow/geometry",
                                    state_key="mainwindow/state"):
    """
    Safely restore geometry and state for `window` from QSettings(org, app_name).
    Handles several possible stored types and corrupted values.
    Returns a tuple (restored_geometry_success, restored_state_success).
    """
    settings = QSettings(org, app_name)

    def _to_qbytearray(val):
        # If already QByteArray, leave it.
        if isinstance(val, QByteArray):
            return val
        # If bytes, wrap directly
        if isinstance(val, (bytes, bytearray)):
            return QByteArray(bytes(val))
        # If a Python string, try several decodings:
        if isinstance(val, str):
            # Try interpreting as base64 (common if someone serialised)
            try:
                # PySide6 QByteArray has fromBase64 and fromHex classmethods
                ba = QByteArray.fromBase64(val.encode("utf-8"))
                if ba and len(ba) > 0:
                    return ba
            except Exception:
                pass
            # Try hex decode
            try:
                ba = QByteArray.fromHex(val.encode("utf-8"))
                if ba and len(ba) > 0:
                    return ba
            except Exception:
                pass
            # As a last resort, try to encode the string directly (may be wrong)
            try:
                return QByteArray(val.encode("utf-8"))
            except Exception:
                pass
        # Unknown / unsupported type
        return None

    geom_ok = False
    state_ok = False

    # --- geometry ---
    raw_geom = settings.value(geom_key)
    if raw_geom is not None:
        ba = _to_qbytearray(raw_geom)
        if ba is not None:
            try:
                window.restoreGeometry(ba)
                geom_ok = True
            except Exception as e:
                # corrupted or incompatible geometry: remove it to avoid repeated errors
                settings.remove(geom_key)
                # optionally log
                print(f"[settings] Failed to restore geometry: {e}. Key removed.")
        else:
            # not convertible -> delete bad key
            settings.remove(geom_key)
            print("[settings] Geometry value present but not convertible -> removed.")

    # --- state ---
    raw_state = settings.value(state_key)
    if raw_state is not None:
        ba = _to_qbytearray(raw_state)
        if ba is not None:
            try:
                window.restoreState(ba)
                state_ok = True
            except Exception as e:
                settings.remove(state_key)
                print(f"[settings] Failed to restore state: {e}. Key removed.")
        else:
            settings.remove(state_key)
            print("[settings] State value present but not convertible -> removed.")

    return geom_ok, state_ok
