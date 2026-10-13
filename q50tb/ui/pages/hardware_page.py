"""Bench Testing > Hardware Control: ESP32 relays, hard-wired inputs and buttons."""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

from q50tb.hw.bench_io import KIND_OF, UI_CONTROLS
from q50tb.hw.esp32_bench import POWER
from q50tb.ui.pages.base import BasePage
from q50tb.ui.widgets.common import Led, blocked, button, confirm, mono_font, row


def _line(*widgets) -> QHBoxLayout:
    h = QHBoxLayout()
    for w in widgets:
        h.addWidget(w, 1 if isinstance(w, (QPushButton, QCheckBox)) else 0)
    return h


# Momentary buttons as left/right (down/up) pairs: name -> (row, column)
BUTTON_POS = {"STSW_B_LEFT": (0, 0), "STSW_B_RIGHT": (0, 1), "PADDLE_DOWN": (1, 0), "PADDLE_UP": (1, 1),
              "BRIGHT_DOWN": (2, 0), "BRIGHT_UP": (2, 1), "TRIP_RESET": (3, 0)}


class HardwarePage(BasePage):
    title = "Hardware"

    def __init__(self, ctx, bridge, parent=None):
        super().__init__(ctx, bridge, parent)
        self.io = ctx.bench
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        conn = QGroupBox("ESP32 bench controller (serial)")
        cl = QHBoxLayout(conn)
        self.port = QComboBox()
        self.port.setEditable(True)
        self.port.setMinimumWidth(180)
        cl.addWidget(QLabel("Port"))
        cl.addWidget(self.port)
        cl.addWidget(button("Refresh", self._fill_ports))
        self.connect_btn = button("Connect", self._connect)
        cl.addWidget(self.connect_btn)
        self.conn_led = Led("#20d020")
        cl.addWidget(self.conn_led)
        self.conn_lbl = QLabel("disconnected")
        cl.addWidget(self.conn_lbl, 1)
        lay.addWidget(conn)

        cl.addWidget(button("Read status", self.io.refresh))

        self.widgets: dict[str, tuple] = {}

        # power | latched warning inputs | momentary buttons
        groups = QHBoxLayout()
        power_box = QGroupBox("Power")
        pv = QVBoxLayout(power_box)
        inputs_box = QGroupBox("Warning inputs (latched)")
        ig = QGridLayout(inputs_box)
        buttons_box = QGroupBox("Buttons (momentary)")
        bg = QGridLayout(buttons_box)
        n_in = n_btn = 0
        for name, label, kind in UI_CONTROLS:
            pin = QLabel("")
            pin.setStyleSheet("color: #8a949c;")
            if KIND_OF.get(name) == POWER:
                led = Led("#20d020")
                w = QPushButton(label)
                w.setCheckable(True)
                w.setMinimumHeight(40)
                w.setStyleSheet("QPushButton:checked { background: #1b5e20; color: white; font-weight: bold; }")
                w.clicked.connect(lambda on, n=name: self._set(n, on))
                pv.addLayout(_line(led, w, pin))
            elif kind == "button":
                led = Led("#ffb300")
                w = QPushButton(label)
                w.pressed.connect(lambda n=name: self.io.hold(n, True))
                w.released.connect(lambda n=name: self.io.hold(n, False))
                r, c = BUTTON_POS.get(name, (4 + n_btn // 2, n_btn % 2))
                bg.addLayout(_line(led, w, pin), r, c)
                n_btn += 1
            else:
                led = Led("#20d020")
                w = QCheckBox(label)
                w.clicked.connect(lambda on, n=name: self._set(n, on))
                ig.addLayout(_line(led, w, pin), n_in // 2, n_in % 2)
                n_in += 1
            self.widgets[name] = (w, led, pin, kind)
        pv.addWidget(button("Power-cycle meter (5 s)", lambda: self.io.power_cycle(5.0)))
        all_off = button("ALL OFF", self._all_off)
        all_off.setStyleSheet("QPushButton { background: #8e1b1b; color: white; }")
        pv.addWidget(all_off)
        note = QLabel("Either B+ or IG alone keeps the meter on; a real reboot needs both off ~5 s and clears "
                      "page registration and latched faults. ACC is tied to IG on this bench, so IG also "
                      "enables AV communication.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #9aa5ae;")
        pv.addWidget(note)
        pv.addStretch(1)
        ig.setRowStretch(ig.rowCount(), 1)
        hold_note = QLabel("Held for as long as the mouse button is down (the ESP32 releases after 10 s at most).")
        hold_note.setWordWrap(True)
        hold_note.setStyleSheet("color: #9aa5ae;")
        bg.addWidget(hold_note, bg.rowCount(), 0, 1, 2)
        bg.setRowStretch(bg.rowCount(), 1)
        groups.addWidget(power_box, 2)
        groups.addWidget(inputs_box, 3)
        groups.addWidget(buttons_box, 3)
        lay.addLayout(groups)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(mono_font(9))
        self.log.setMaximumBlockCount(400)
        lay.addWidget(QLabel("Device / command log"))
        lay.addWidget(self.log, 1)
        self._log_n = 0
        self._fill_ports()

    def _fill_ports(self) -> None:
        cur = self.port.currentText() or self.ctx.settings.esp32_port
        self.port.clear()
        cands = set(self.io.candidates())
        for p in self.io.ports():
            self.port.addItem(f"{p}{'  (ESP32?)' if p in cands else ''}", p)
        if cur:
            i = self.port.findData(cur)
            if i >= 0:
                self.port.setCurrentIndex(i)
            else:
                self.port.setEditText(cur)

    def _port_value(self) -> str:
        data = self.port.currentData()
        return data if data and self.port.currentText().startswith(data) else self.port.currentText().split()[0]

    def _connect(self) -> None:
        if self.io.connected:
            self.io.disconnect()
            return
        port = self._port_value()
        if not port:
            return
        self.ctx.settings.esp32_port = port
        self.io.connect(port)

    def _set(self, name: str, on: bool) -> None:
        self.io.set(name, on)

    def _all_off(self) -> None:
        if confirm(self, "All off", "Turn every ESP32 output off? The meter will lose power."):
            self.io.all_off()

    def refresh(self) -> None:
        connected = self.io.connected
        self.conn_led.set_state(connected)
        self.conn_lbl.setText(f"connected to {self.io.port}" if connected else
                              f"disconnected{('  — ' + self.io.last_error) if self.io.last_error else ''}")
        self.connect_btn.setText("Disconnect" if connected else "Connect")
        states = self.io.states()
        info = self.io.channel_info()
        for name, (w, led, pin, kind) in self.widgets.items():
            st = states.get(name)
            ci = info.get(name)
            wired = ci is not None and ci.wired
            led.set_state(st if connected and wired else None)
            w.setEnabled(connected and wired)
            pin.setText(("GPIO " + str(ci.pin)) if wired else ("not wired" if ci else ("—" if connected else "")))
            if kind != "button":
                with blocked(w):
                    w.setChecked(bool(st))
        log = list(self.io.log)
        if log and (not self._log_n or log[-1] is not self._log_n):
            start = 0
            for i in range(len(log) - 1, -1, -1):
                if log[i] is self._log_n:
                    start = i + 1
                    break
            for t, lvl, text in log[start:]:
                self.log.appendPlainText(f"{t:9.2f} {lvl:7s} {text}")
            self._log_n = log[-1]
