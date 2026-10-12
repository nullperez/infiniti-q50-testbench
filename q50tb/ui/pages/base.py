"""Page base class and the signal-binding helper used by control pages."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

from q50tb.signals.model import SignalDef
from q50tb.ui.widgets.common import banner, blocked


class BasePage(QWidget):
    title = "Page"
    tx_page = False          # transmits -> disabled in monitor mode

    def __init__(self, ctx, bridge, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.bridge = bridge
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(8, 8, 8, 8)
        self._monitor_banner = banner("MONITORING MODE - this page transmits and is disabled. "
                                      "Nothing is sent or acknowledged on the bus.")
        self._monitor_banner.setVisible(False)
        self.outer.addWidget(self._monitor_banner)
        self.body = QWidget()
        self.outer.addWidget(self.body, 1)

    def refresh(self) -> None:
        """Called by the main window timer while the page is visible."""

    def on_bus_state(self) -> None:
        mon = self.ctx.buses.running and self.ctx.buses.monitor_only
        if self.tx_page:
            if mon:
                # software-only monitoring (adapter without hardware listen-only): the app sends nothing, but the
                # adapter's controller still ACKs frames
                soft = [ch.role for ch in self.ctx.buses.channels.values() if not ch.config.listen_only]
                self._monitor_banner.setText(
                    "MONITORING MODE - this page transmits and is disabled. Nothing is sent on the bus; "
                    f"the {'/'.join(soft)} adapter still acknowledges frames (software-only monitoring)." if soft else
                    "MONITORING MODE - this page transmits and is disabled. "
                    "Nothing is sent or acknowledged on the bus.")
            self._monitor_banner.setVisible(mon)
            self.body.setEnabled(not mon)

    def status(self, text: str) -> None:
        win = self.window()
        if hasattr(win, "statusBar"):
            win.statusBar().showMessage(text, 6000)


def signal_tooltip(s: SignalDef, extra: str = "") -> str:
    m = s.message
    rng = s.range()
    lines = [f"<b>{s.label}</b> ({s.name})", f"0x{m.arbitration_id:03X} {m.name} - {s.position_text()}",
             f"factor {s.factor:g} offset {s.offset:g} {s.unit}  range {rng[0]:g}..{rng[1]:g}",
             f"confidence: <b>{s.confidence}</b>"]
    if s.notes:
        lines.append(s.notes)
    if m.notes:
        lines.append(f"<i>{m.notes}</i>")
    if extra:
        lines.append(extra)
    return "<br>".join(lines)


class SignalBinder:
    """Creates widgets bound to background-message signals and keeps them in sync."""

    def __init__(self, ctx, on_error: Optional[Callable[[str], None]] = None):
        self.ctx = ctx
        self.on_error = on_error
        self._bound: list[tuple[str, QWidget, Callable]] = []

    def sdef(self, path: str) -> SignalDef:
        return self.ctx.db.signal(path)

    def set(self, path: str, value: float) -> None:
        s = self.sdef(path)
        try:
            self.ctx.background.set_signal(s.message.bus, s.message.arbitration_id, s.name, value)
        except Exception as exc:
            if self.on_error:
                self.on_error(f"{path}: {exc}")

    def current(self, path: str) -> Optional[float]:
        s = self.sdef(path)
        return self.ctx.background.signal_value(s.message.bus, s.message.arbitration_id, s.name)

    def checkbox(self, path: str, label: Optional[str] = None) -> QCheckBox:
        s = self.sdef(path)
        w = QCheckBox(label or s.label)
        w.setToolTip(signal_tooltip(s))
        w.toggled.connect(lambda on: self.set(path, 1 if on else 0))

        def sync():
            v = self.current(path)
            with blocked(w):
                w.setChecked(bool(v))
        self._bound.append((path, w, sync))
        sync()
        return w

    def combo(self, path: str, items: Optional[list[tuple[str, int]]] = None) -> QComboBox:
        s = self.sdef(path)
        w = QComboBox()
        for text, raw in items or [(f"{v} ({k:#x})", k) for k, v in sorted((s.enum or {}).items())]:
            w.addItem(text, raw)
        w.setToolTip(signal_tooltip(s))
        w.currentIndexChanged.connect(lambda _i: self._set_raw(path, w.currentData()))

        def sync():
            v = self.current(path)
            if v is None:
                return
            raw = s.phys_to_raw(v)
            i = w.findData(raw)
            with blocked(w):
                if i >= 0:
                    w.setCurrentIndex(i)
        self._bound.append((path, w, sync))
        sync()
        return w

    def _set_raw(self, path: str, raw: int) -> None:
        s = self.sdef(path)
        try:
            self.ctx.background.set_signal_raw(s.message.bus, s.message.arbitration_id, s.name, int(raw))
        except Exception as exc:
            if self.on_error:
                self.on_error(f"{path}: {exc}")

    def spin(self, path: str, lo: Optional[float] = None, hi: Optional[float] = None, step: Optional[float] = None,
             decimals: Optional[int] = None, suffix: Optional[str] = None,
             to_phys: Callable[[float], float] = lambda x: x, from_phys: Callable[[float], float] = lambda x: x):
        s = self.sdef(path)
        rlo, rhi = s.range()
        lo = from_phys(rlo) if lo is None else lo
        hi = from_phys(rhi) if hi is None else hi
        if decimals is None:
            decimals = 0 if float(s.factor).is_integer() else 2
        w = QDoubleSpinBox() if decimals else QSpinBox()
        if decimals:
            w.setDecimals(decimals)
        w.setRange(lo if decimals else int(lo), hi if decimals else int(hi))
        w.setSingleStep(step or (abs(s.factor) if s.factor and not float(s.factor).is_integer() else 1))
        w.setSuffix(f" {suffix if suffix is not None else s.unit}" if (suffix or s.unit) else "")
        w.setKeyboardTracking(False)
        w.setToolTip(signal_tooltip(s))
        w.valueChanged.connect(lambda v: self.set(path, to_phys(v)))

        def sync():
            v = self.current(path)
            if v is not None:
                with blocked(w):
                    w.setValue(from_phys(v) if decimals else int(round(from_phys(v))))
        self._bound.append((path, w, sync))
        sync()
        return w

    def slider(self, path: str, lo: float, hi: float, step: float = 1, decimals: int = 0, suffix: str = "",
               to_phys: Callable[[float], float] = lambda x: x, from_phys: Callable[[float], float] = lambda x: x,
               extra_targets: Optional[list[tuple[str, Callable[[float], float]]]] = None) -> QWidget:
        """Slider + spin box. ``extra_targets`` are other signals set from the same UI value."""
        s = self.sdef(path)
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        scale = 10 ** decimals
        sl = QSlider(Qt.Horizontal)
        sl.setRange(int(lo * scale), int(hi * scale))
        sl.setSingleStep(int(step * scale) or 1)
        sp = QDoubleSpinBox() if decimals else QSpinBox()
        if decimals:
            sp.setDecimals(decimals)
        sp.setRange(lo, hi) if decimals else sp.setRange(int(lo), int(hi))
        sp.setSingleStep(step)
        if suffix:
            sp.setSuffix(" " + suffix)
        sp.setKeyboardTracking(False)
        sp.setMinimumWidth(100)
        lay.addWidget(sl, 1)
        lay.addWidget(sp)
        box.setToolTip(signal_tooltip(s, "also drives: " + ", ".join(p for p, _ in extra_targets or [])
                                      if extra_targets else ""))

        def apply(v: float):
            self.set(path, to_phys(v))
            for p, fn in extra_targets or []:
                self.set(p, fn(v))

        def from_slider(iv):
            v = iv / scale
            with blocked(sp):
                sp.setValue(v if decimals else int(v))
            apply(v)

        def from_spin(v):
            with blocked(sl):
                sl.setValue(int(round(v * scale)))
            apply(v)
        sl.valueChanged.connect(from_slider)
        sp.valueChanged.connect(from_spin)

        def sync():
            v = self.current(path)
            if v is None:
                return
            u = from_phys(v)
            with blocked(sl), blocked(sp):
                sl.setValue(int(round(u * scale)))
                sp.setValue(u if decimals else int(round(u)))
        self._bound.append((path, box, sync))
        sync()
        box.slider, box.spin = sl, sp
        return box

    def sync_all(self, skip_focused: bool = False) -> None:
        for _p, w, fn in self._bound:
            if skip_focused and _busy(w):
                continue
            try:
                fn()
            except Exception:
                pass


def _busy(w: QWidget) -> bool:
    """True while the user is interacting with the widget (keyboard focus inside it or a slider drag)."""
    fw = QApplication.focusWidget()
    if fw is not None and (fw is w or w.isAncestorOf(fw)):
        return True
    sl = getattr(w, "slider", None)
    return bool(sl is not None and sl.isSliderDown())
