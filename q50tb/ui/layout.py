"""Split-pane page layout: up to four pages side by side, like editor groups in VS Code.

Each page widget exists once, so a page shows in at most one pane; choosing a page that is already open in
another pane swaps the two. The sidebar opens pages in the active pane (the last one clicked).
"""

from __future__ import annotations

from typing import Optional, Union

from PySide6.QtCore import QByteArray, QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QScrollArea, QSplitter,
                               QVBoxLayout, QWidget)

Spec = Union[int, tuple]

# name -> (menu text, split tree). Leaves are pane numbers; ("h", [...]) splits left/right, ("v", [...]) top/bottom.
LAYOUTS: dict[str, tuple[str, Spec]] = {
    "single": ("Single page", 0),
    "cols2": ("2 columns", ("h", [0, 1])),
    "rows2": ("2 rows", ("v", [0, 1])),
    "cols3": ("3 columns", ("h", [0, 1, 2])),
    "top2_bottom1": ("2 top, 1 bottom", ("v", [("h", [0, 1]), 2])),
    "top1_bottom2": ("1 top, 2 bottom", ("v", [0, ("h", [1, 2])])),
    "left1_right2": ("1 left, 2 right", ("h", [0, ("v", [1, 2])])),
    "left2_right1": ("2 left, 1 right", ("h", [("v", [0, 1]), 2])),
    "grid4": ("2 x 2 grid", ("v", [("h", [0, 1]), ("h", [2, 3])])),
}

ACTIVE_BORDER = "#3d8bfd"
IDLE_BORDER = "#2a3540"


def _leaves(spec: Spec) -> int:
    return 1 if isinstance(spec, int) else sum(_leaves(k) for k in spec[1])


class Pane(QFrame):
    def __init__(self, area: "PaneArea", index: int):
        super().__init__()
        self.area, self.index = area, index
        self.page_index: Optional[int] = None
        self.setObjectName("pane")
        v = QVBoxLayout(self)
        v.setContentsMargins(1, 1, 1, 1)
        v.setSpacing(0)
        self.header = QWidget()
        h = QHBoxLayout(self.header)
        h.setContentsMargins(6, 2, 6, 2)
        num = QLabel(str(index + 1))
        num.setStyleSheet("color: #8a949c; font-weight: bold;")
        self.combo = QComboBox()
        self.combo.setToolTip("Page shown in this pane (a page open in another pane swaps places)")
        for i, page in enumerate(area.pages):
            self.combo.addItem(page.title, i)
        self.combo.activated.connect(lambda _i: area.assign(self.index, self.combo.currentData()))
        h.addWidget(num)
        h.addWidget(self.combo)
        h.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        v.addWidget(self.header)
        v.addWidget(self.scroll, 1)

    def set_page(self, idx: int) -> None:
        page = self.area.pages[idx]
        self.scroll.setWidget(page)
        page.show()
        self.page_index = idx
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(self.combo.findData(idx))
        self.combo.blockSignals(False)

    def take_page(self) -> None:
        w = self.scroll.takeWidget()
        if w is not None:
            w.hide()
            w.setParent(self.area.parking)
        self.page_index = None

    def set_active(self, on: bool, framed: bool) -> None:
        color = ACTIVE_BORDER if on else IDLE_BORDER
        self.setStyleSheet(f"QFrame#pane {{ border: 1px solid {color}; }}" if framed else "")


class PaneArea(QWidget):
    """Holds the panes. ``changed`` fires when the active pane or a pane's page changes."""
    changed = Signal()

    def __init__(self, pages: list, defaults: list[int], parent=None):
        super().__init__(parent)
        self.pages = pages
        self.defaults = defaults
        self.parking = QWidget(self)
        self.parking.hide()
        for p in pages:
            p.setParent(self.parking)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self.root: Optional[QWidget] = None
        self.panes: list[Pane] = []
        self.splitters: list[QSplitter] = []
        self.active = 0
        self.layout_name = ""
        QApplication.instance().installEventFilter(self)

    # ── layout ──────────────────────────────────────────────────────
    def set_layout(self, name: str, page_indices: Optional[list[int]] = None,
                   sizes: Optional[list[str]] = None) -> None:
        if name not in LAYOUTS:
            name = "single"
        keep = [p.page_index for p in self.panes] if page_indices is None else list(page_indices)
        for p in self.panes:
            p.take_page()
        if self.root is not None:
            self._lay.removeWidget(self.root)
            self.root.deleteLater()
        spec = LAYOUTS[name][1]
        self.panes = [Pane(self, i) for i in range(_leaves(spec))]
        self.splitters = []
        self.root = self._build(spec)
        self._lay.addWidget(self.root)
        self.layout_name = name
        chosen: list[int] = []
        for idx in keep + self.defaults + list(range(len(self.pages))):
            if idx is not None and 0 <= idx < len(self.pages) and idx not in chosen:
                chosen.append(idx)
        for pane, idx in zip(self.panes, chosen):
            pane.set_page(idx)
            pane.header.setVisible(len(self.panes) > 1)
        for sp, state in zip(self.splitters, sizes or []):
            try:
                sp.restoreState(QByteArray.fromHex(state.encode()))
            except Exception:
                pass
        self.set_active(min(self.active, len(self.panes) - 1))

    def _build(self, spec: Spec) -> QWidget:
        if isinstance(spec, int):
            return self.panes[spec]
        orient, kids = spec
        sp = QSplitter(Qt.Horizontal if orient == "h" else Qt.Vertical)
        sp.setChildrenCollapsible(False)
        sp.setHandleWidth(6)
        self.splitters.append(sp)
        for k in kids:
            sp.addWidget(self._build(k))
        sp.setSizes([10_000] * len(kids))  # equal shares
        return sp

    def splitter_states(self) -> list[str]:
        return [bytes(sp.saveState().toHex()).decode() for sp in self.splitters]

    # ── pages ───────────────────────────────────────────────────────
    def assign(self, pane_index: int, page_index: int) -> None:
        """Show a page in a pane; if another pane shows it, the two panes swap pages."""
        target = self.panes[pane_index]
        if target.page_index != page_index:
            other = next((p for p in self.panes if p.page_index == page_index), None)
            old = target.page_index
            target.take_page()
            if other is not None:
                other.take_page()
            target.set_page(page_index)
            if other is not None:
                shown = {p.page_index for p in self.panes}
                fill = old if old is not None else next(i for i in range(len(self.pages)) if i not in shown)
                other.set_page(fill)
            for p in self.panes:
                if p.page_index is not None:
                    self.pages[p.page_index].refresh()
        self.set_active(pane_index)

    def show_in_active(self, page_index: int) -> None:
        self.assign(self.active, page_index)

    def set_active(self, index: int) -> None:
        self.active = index
        for p in self.panes:
            p.set_active(p.index == index, framed=len(self.panes) > 1)
        self.changed.emit()

    def active_page_index(self) -> Optional[int]:
        return self.panes[self.active].page_index if self.panes else None

    def visible_pages(self) -> list:
        return [self.pages[p.page_index] for p in self.panes if p.page_index is not None]

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        # a click anywhere inside a pane makes it the active one (where the sidebar opens pages)
        if ev.type() == QEvent.MouseButtonPress and isinstance(obj, QWidget) and len(self.panes) > 1:
            w = obj
            while w is not None:
                if isinstance(w, Pane) and w in self.panes:
                    if w.index != self.active:
                        self.set_active(w.index)
                    break
                w = w.parentWidget()
        return False
