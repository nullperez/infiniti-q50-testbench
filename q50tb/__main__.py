"""Start the Q50 TestBench GUI:  python -m q50tb"""

from __future__ import annotations

import os
import sys


def main() -> int:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from PySide6.QtWidgets import QApplication

    from q50tb.app_context import AppContext
    from q50tb.ui.main_window import MainWindow
    from q50tb.ui.theme import apply_theme

    app = QApplication(sys.argv)
    app.setApplicationName("Q50 TestBench")
    apply_theme(app)
    ctx = AppContext()
    win = MainWindow(ctx)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
