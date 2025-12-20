# app/style.py
from PySide6.QtWidgets import QApplication
from typing import Any

def apply_global_style(app: QApplication, theme: Any):
    """Apply formatted global stylesheet using theme values."""
    tpl = f"""
    QWidget#central_widget {{
        background: {theme.tmp};
    }}
    """
    app.setStyleSheet(tpl)
