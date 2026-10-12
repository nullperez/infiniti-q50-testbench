"""Main window: connection bar, page navigation and the refresh loop."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPushButton, QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from q50tb.can.adapters.base import ListenOnlyUnsupported
from q50tb.control.api import BenchAPI
from q50tb.control.server import ControlServer
from q50tb.core.bus_manager import AV, MAIN
from q50tb.ui.pages.av_debug_page import AvDebugPage
from q50tb.ui.pages.av_page import AvPage
from q50tb.ui.pages.background_page import BackgroundPage
from q50tb.ui.pages.controls_page import ControlsPage
from q50tb.ui.pages.dashboard_page import DashboardPage
from q50tb.ui.pages.hardware_page import HardwarePage
from q50tb.ui.pages.log_page import LogPage
from q50tb.ui.pages.replay_page import ReplayPage
from q50tb.ui.pages.send_page import SendPage
from q50tb.ui.pages.settings_page import SettingsPage
from q50tb.ui.pages.signals_page import SignalsPage
from q50tb.ui.pages.tracker_page import TrackerPage
from q50tb.ui.widgets.common import Bridge, Led, error_box

NAV = [
    ("BENCH TESTING", [ControlsPage, AvPage, HardwarePage, SendPage, LogPage, BackgroundPage, ReplayPage]),
    ("MONITORING", [DashboardPage, TrackerPage, SignalsPage, AvDebugPage]),
    ("", [SettingsPage]),
]
BITRATES = [500_000, 250_000, 125_000, 1_000_000]


class ConnectionBar(QFrame):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        ctx = win.ctx
        self.setFrameShape(QFrame.StyledPanel)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        self.main = QComboBox()
        self.main.setMinimumWidth(240)
        self.av = QComboBox()
        self.av.setMinimumWidth(220)
        self.bitrate = QComboBox()
        self.bitrate.setEditable(True)
        for b in BITRATES:
            self.bitrate.addItem(f"{b}", b)
        self.bitrate.setCurrentText(str(ctx.settings.bitrate))
        self.fd = QCheckBox("CAN FD")
        self.fd.setChecked(ctx.settings.fd)
        self.monitor = QCheckBox("MONITORING MODE ONLY")
        self.monitor.setToolTip("Listen-only: the adapter neither transmits nor acknowledges frames. "
                                "Use this on a real vehicle.")
        self.monitor.setStyleSheet("QCheckBox { color: #ff5252; font-weight: bold; }")
        self.monitor.setChecked(ctx.settings.monitor_mode)
        self.start = QPushButton("Start bus")
        self.start.setMinimumWidth(110)
        self.start.clicked.connect(self._toggle)
        refresh = QPushButton("⟳")
        refresh.setToolTip("Find CAN adapters")
        refresh.setFixedWidth(30)
        refresh.clicked.connect(self.fill)
        self.led_main = Led("#20d020")
        self.led_av = Led("#20d020")
        self.state = QLabel("")
        for w in (QLabel("Main bus"), self.main, refresh, QLabel("AV bus"), self.av, QLabel("bps"), self.bitrate,
                  self.fd, self.monitor, self.start, self.led_main, self.led_av, self.state):
            lay.addWidget(w)
        lay.addStretch(1)
        self.fill()

    def fill(self) -> None:
        ctx = self.win.ctx
        adapters = ctx.refresh_adapters()
        cur_main = self.main.currentData() or ctx.settings.main_adapter
        cur_av = self.av.currentData() if self.av.count() else ctx.settings.av_adapter
        self.main.clear()
        self.av.clear()
        self.av.addItem("— none —", "")
        for a in adapters:
            self.main.addItem(a.describe(), a.key)
            self.av.addItem(a.describe(), a.key)
        if self.main.findData(cur_main) >= 0:
            self.main.setCurrentIndex(self.main.findData(cur_main))
        i = self.av.findData(cur_av)
        if i >= 0:
            self.av.setCurrentIndex(i)
        elif self.main.count() > 1 and self.av.count() > 2:
            self.av.setCurrentIndex(2 if self.main.currentIndex() == 0 else 1)
        if not adapters:
            self.state.setText("no CAN adapters found")

    def _toggle(self) -> None:
        ctx = self.win.ctx
        if ctx.buses.running:
            ctx.stop_buses()
            return
        main = self.main.currentData()
        av = self.av.currentData() or None
        if not main:
            error_box(self, "Start bus", "Choose a main-bus adapter.")
            return
        if av == main:
            error_box(self, "Start bus", "The AV bus must use a different adapter channel than the main bus.")
            return
        mon = self.monitor.isChecked()
        try:
            br = int(self.bitrate.currentText())
        except ValueError:
            error_box(self, "Start bus", "Bitrate must be a number")
            return
        if not mon:
            ans = QMessageBox.warning(
                self, "Bench mode", "Start in BENCH mode? The app will transmit background messages "
                "and acknowledge frames.\n\nNever do this on a real vehicle - tick MONITORING MODE ONLY there.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return
        try:
            ctx.start_buses(main, av, monitor=mon, bitrate=br, fd=self.fd.isChecked())
        except ListenOnlyUnsupported as exc:
            error_box(self, "Monitoring mode refused", str(exc))
        except Exception as exc:
            error_box(self, "Start bus", f"{type(exc).__name__}: {exc}")

    def sync(self) -> None:
        ctx = self.win.ctx
        running = ctx.buses.running
        self.start.setText("Stop bus" if running else "Start bus")
        self.start.setStyleSheet("QPushButton { background: #8e1b1b; color: white; }" if running else
                                 "QPushButton { background: #1b5e20; color: white; }")
        for w in (self.main, self.av, self.bitrate, self.fd, self.monitor):
            w.setEnabled(not running)

    def refresh(self) -> None:
        ctx = self.win.ctx
        ctx.buses.update_rates()
        parts = []
        for role, led in ((MAIN, self.led_main), (AV, self.led_av)):
            ch = ctx.buses.channels.get(role)
            led.set_state(None if ch is None else ch.adapter.stats.bus_state in ("error-active", "listen-only"))
            led.setToolTip(f"{role}: {ch.adapter.stats.bus_state if ch else 'closed'}")
            if ch:
                parts.append(f"{role}: {ch.adapter.stats.bus_state} RX {ch.stats.rx_fps:.0f}/s TX {ch.stats.tx_fps:.0f}/s"
                             + (f" refused {ch.stats.refused}" if ch.stats.refused else "")
                             + (f" err {ch.stats.tx_errors}" if ch.stats.tx_errors else ""))
        if ctx.buses.running:
            mode = "MONITOR" if ctx.buses.monitor_only else "BENCH"
            self.state.setText(f"<b style='color:{'#ff5252' if mode == 'MONITOR' else '#66bb6a'}'>{mode}</b>  " + "  •  ".join(parts))
        else:
            self.state.setText("stopped")


class MainWindow(QMainWindow):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("Q50 TestBench — Infiniti Q50 combination meter")
        self.resize(1500, 950)
        self.bridge = Bridge()
        self.bridge.changed.connect(self._bridge)
        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(4, 4, 4, 4)
        self.conn = ConnectionBar(self)
        v.addWidget(self.conn)
        split = QSplitter(Qt.Horizontal)
        self.nav = QListWidget()
        self.nav.setMaximumWidth(210)
        self.stack = QStackedWidget()
        self.pages = []
        bold = QFont()
        bold.setBold(True)
        for header, classes in NAV:
            if header:
                it = QListWidgetItem(header)
                it.setFlags(Qt.NoItemFlags)
                it.setFont(bold)
                it.setForeground(QColor("#8a949c"))
                self.nav.addItem(it)
            for cls in classes:
                page = cls(ctx, self.bridge)
                self.pages.append(page)
                self.stack.addWidget(page)
                it = QListWidgetItem("   " + page.title)
                it.setData(Qt.UserRole, len(self.pages) - 1)
                self.nav.addItem(it)
        self.nav.currentItemChanged.connect(self._nav_changed)
        split.addWidget(self.nav)
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)
        v.addWidget(split, 1)
        self.setCentralWidget(central)
        self.nav.setCurrentRow(1)

        ctx.buses.add_state_listener(lambda: self.bridge.changed.emit("bus-state"))
        ctx.buses.add_status_listener(lambda lvl, msg: self.bridge.changed.emit(f"status|{lvl}|{msg}"))
        ctx.hu.listeners.append(lambda: self.bridge.changed.emit("hu"))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(ctx.settings.ui_refresh_ms)
        self.server = None
        self.apply_settings()
        self._bus_state()
        if ctx.settings.esp32_autoconnect and ctx.settings.esp32_port:
            ctx.bench.connect(ctx.settings.esp32_port)
        act = QAction("Quit", self)
        act.setShortcut("Ctrl+Q")
        act.triggered.connect(self.close)
        self.addAction(act)

    def apply_settings(self) -> None:
        s = self.ctx.settings
        self.timer.setInterval(s.ui_refresh_ms)
        if self.server:
            self.server.stop()
            self.server = None
        if s.control_server:
            try:
                self.server = ControlServer(BenchAPI(self.ctx), s.control_port, s.control_token)
                self.server.start()
                self.statusBar().showMessage(f"control server on 127.0.0.1:{s.control_port} (MCP ready)", 8000)
            except OSError as exc:
                self.statusBar().showMessage(f"control server not started: {exc}", 10000)
                self.server = None

    def _nav_changed(self, cur, _prev) -> None:
        if cur is None:
            return
        idx = cur.data(Qt.UserRole)
        if idx is not None:
            self.stack.setCurrentIndex(idx)
            self.pages[idx].refresh()

    def _bridge(self, msg: str) -> None:
        if msg == "bus-state":
            self._bus_state()
        elif msg.startswith("status|"):
            _, lvl, text = msg.split("|", 2)
            self.statusBar().showMessage(text, 10000 if lvl in ("error", "warning") else 5000)
        else:
            for p in self.pages:
                if hasattr(p, "handle_bridge"):
                    p.handle_bridge(msg)

    def _bus_state(self) -> None:
        self.conn.sync()
        for p in self.pages:
            p.on_bus_state()

    def _tick(self) -> None:
        self.conn.refresh()
        page = self.stack.currentWidget()
        if page is not None:
            try:
                page.refresh()
            except Exception as exc:
                self.statusBar().showMessage(f"{page.title}: {exc!r}", 5000)

    def closeEvent(self, ev) -> None:
        self.timer.stop()
        if self.server:
            self.server.stop()
        try:
            self.ctx.settings.save()
        except OSError:
            pass
        self.ctx.shutdown()
        ev.accept()
