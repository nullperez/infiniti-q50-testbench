"""Bench Testing > Hardware Control: ESP32 relays, hard-wired inputs and buttons."""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from q50tb.hw.bench_io import UI_CONTROLS
from q50tb.ui.pages.base import BasePage
from q50tb.ui.widgets.common import Led, blocked, button, confirm, mono_font, row


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

        grid_box = QGroupBox("Outputs (checkbox = latched, button = momentary press)")
        grid = QGridLayout(grid_box)
        self.press_ms = QSpinBox()
        self.press_ms.setRange(20, 10000)
        self.press_ms.setValue(self.io.press_ms)
        self.press_ms.setSuffix(" ms")
        self.press_ms.valueChanged.connect(lambda v: setattr(self.io, "press_ms", v))
        self.widgets: dict[str, tuple] = {}
        for i, (name, label, kind) in enumerate(UI_CONTROLS):
            r, c = i % 10, (i // 10) * 4
            led = Led("#20d020" if kind != "button" else "#ffb300")
            if kind == "button":
                w = QPushButton(label)
                w.pressed.connect(lambda n=name: self._press(n))
            else:
                w = QCheckBox(label)
                w.clicked.connect(lambda on, n=name: self._set(n, on))
            pin = QLabel("")
            pin.setStyleSheet("color: #8a949c;")
            grid.addWidget(led, r, c)
            grid.addWidget(w, r, c + 1)
            grid.addWidget(pin, r, c + 2)
            self.widgets[name] = (w, led, pin, kind)
        lay.addWidget(grid_box)
        power = QLabel("Either B+ or IG alone keeps the meter on; a real reboot needs both off ~5 s and clears "
                       "page registration and latched faults. ACC enables AV communication (needs its own relay; "
                       "older firmware has no ACC channel).")
        power.setWordWrap(True)
        power.setStyleSheet("color: #9aa5ae;")
        lay.addWidget(power)
        lay.addWidget(row("Press duration", self.press_ms,
                          button("Power-cycle meter (5 s)", lambda: self.io.power_cycle(5.0)),
                          button("ALL OFF", self._all_off), button("Read status", self.io.refresh)))
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

    def _press(self, name: str) -> None:
        self.io.press(name, self.press_ms.value())

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
