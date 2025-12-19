# app/style.py
from PySide6.QtWidgets import QApplication
from typing import Any

def apply_global_style(app: QApplication, theme: Any):
    """Apply formatted global stylesheet using theme values."""
    tpl = f"""
    QWidget#central_widget {{
        background: {theme.bg_color};
    }}

    /* Root background for central board */
    .board {{
        background: {theme.board_color};
        border-radius: {theme.border_radius}px;
    }}

    /* Generic dock card style (for inner widgets if needed) */
    .dock-card {{
        background: {theme.dock_color};
        color: {theme.text};
        border-radius: {theme.inner_radius}px;
        padding: 8px;
    }}

    QLabel, QToolButton, QPushButton {{
        color: {theme.text};
    }}

    /* small subtle text */
    .subtle {{
        color: {theme.subtle};
    }}

    /* accent button */
    .dock-accent {{
        background: {theme.accent};
        color: white;
        border-radius: 8px;
        padding: 6px 12px;
    }}
    """
    app.setStyleSheet(tpl)
