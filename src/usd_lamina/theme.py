"""Built-in dark Fusion theme."""

from __future__ import annotations

import os
from typing import Any

_PALETTE = {
    "Window": "#2b2b2b",
    "WindowText": "#dddddd",
    "Base": "#252525",
    "AlternateBase": "#2f2f2f",
    "Text": "#dddddd",
    "Button": "#3d3d3d",
    "ButtonText": "#eeeeee",
    "Highlight": "#3a6ea5",
    "HighlightedText": "#ffffff",
    "ToolTipBase": "#3a3a3a",
    "ToolTipText": "#eeeeee",
    "Link": "#5aa0dc",
}


def _qss_path() -> str:
    return os.path.join(os.path.dirname(__file__), "resources", "dark.qss")


def apply(target: Any, name: str = "dark") -> None:  # noqa: ARG001
    """Apply the bundled dark Fusion theme to a QApplication or QWidget."""
    QtGui = target.__class__.__module__.split(".", 1)[0]
    import importlib

    QtGui_mod = importlib.import_module(QtGui + ".QtGui")
    QtWidgets = importlib.import_module(QtGui + ".QtWidgets")

    is_app = isinstance(target, QtWidgets.QApplication)
    if is_app:
        target.setStyle("Fusion")
    else:
        style = QtWidgets.QStyleFactory.create("Fusion")
        if style is not None:
            target.setStyle(style)

    pal = QtGui_mod.QPalette()
    roles = getattr(QtGui_mod.QPalette, "ColorRole", QtGui_mod.QPalette)
    for role, value in _PALETTE.items():
        pal.setColor(getattr(roles, role), QtGui_mod.QColor(value))
    target.setPalette(pal)

    qss_file = _qss_path()
    if os.path.isfile(qss_file):
        with open(qss_file, encoding="utf-8") as handle:
            target.setStyleSheet(handle.read())
