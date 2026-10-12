"""Dark Fusion theme (readable in a dim garage, consistent on Windows and Linux)."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory


def apply_theme(app: QApplication) -> None:
    app.setStyle(QStyleFactory.create("Fusion"))
    p = QPalette()
    base, alt, text, btn = QColor("#1d2329"), QColor("#252c33"), QColor("#e6ebef"), QColor("#2b333b")
    p.setColor(QPalette.Window, QColor("#171c21"))
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, alt)
    p.setColor(QPalette.ToolTipBase, QColor("#2b333b"))
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, btn)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor("#ff5252"))
    p.setColor(QPalette.Highlight, QColor("#2f6fb0"))
    p.setColor(QPalette.HighlightedText, QColor("white"))
    p.setColor(QPalette.PlaceholderText, QColor("#77818a"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor("#5d666e"))
    app.setPalette(p)
    app.setStyleSheet("""
        QGroupBox { border: 1px solid #333c45; border-radius: 4px; margin-top: 10px; padding-top: 6px; }
        QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #9fb3c4; }
        QListWidget { font-size: 13px; }
        QListWidget::item { padding: 4px; }
        QTableView, QTableWidget { gridline-color: #2c343c; }
        QToolTip { border: 1px solid #44505b; }
    """)
