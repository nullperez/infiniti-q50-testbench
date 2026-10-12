"""Settings page: every AppSettings field, grouped, with Save/Apply."""

from __future__ import annotations

import os
import sys

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QLabel,
                               QLineEdit, QPlainTextEdit, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from q50tb.core.config import ROOT, AppSettings
from q50tb.ui.pages.base import BasePage
from q50tb.ui.widgets.common import banner, button, confirm, mono_font, row

BITRATES = [1_000_000, 800_000, 500_000, 250_000, 125_000, 100_000, 83_333, 50_000]
DATA_BITRATES = [1_000_000, 2_000_000, 4_000_000, 8_000_000]


class SettingsPage(BasePage):
    title = "Settings"

    def __init__(self, ctx, bridge, parent=None):
        super().__init__(ctx, bridge, parent)
        s = ctx.settings
        outer = QVBoxLayout(self.body)
        outer.setContentsMargins(0, 0, 0, 0)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        sa.setWidget(inner)
        outer.addWidget(sa, 1)
        outer.addWidget(row(button("Save && apply", self._save), button("Restore defaults", self._defaults),
                            QLabel(f"Settings file: {AppSettings.path()}")))

        g = QGroupBox("CAN buses (applied on next bus start)")
        f = QFormLayout(g)
        self.bitrate = QComboBox()
        self.bitrate.setEditable(True)
        for b in BITRATES:
            self.bitrate.addItem(str(b), b)
        self.bitrate.setCurrentText(str(s.bitrate))
        self.fd = QCheckBox("Open buses as CAN FD")
        self.fd.setChecked(s.fd)
        self.dbitrate = QComboBox()
        for b in DATA_BITRATES:
            self.dbitrate.addItem(str(b), b)
        self.dbitrate.setCurrentText(str(s.data_bitrate))
        self.sock = QCheckBox("Let the app configure SocketCAN links (ip link set … bitrate/listen-only; needs sudo/pkexec)")
        self.sock.setChecked(s.socketcan_configure_link)
        f.addRow("Bitrate (default 500k)", self.bitrate)
        f.addRow("", self.fd)
        f.addRow("CAN FD data bitrate", self.dbitrate)
        f.addRow("", self.sock)
        lay.addWidget(g)

        g = QGroupBox("Safety")
        f = QFormLayout(g)
        self.prot_main = QLineEdit(", ".join(s.protected_main))
        self.prot_av = QLineEdit(", ".join(s.protected_av))
        self.allow_prot = QCheckBox("Allow transmitting protected IDs (meter-originated). Dangerous.")
        self.allow_prot.setChecked(s.allow_protected_tx)
        self.soft_mon = QCheckBox("Allow MONITOR mode on adapters without hardware listen-only "
                                  "(TX blocked in software, but the controller will still ACK frames)")
        self.soft_mon.setChecked(s.allow_monitor_without_hw_listen_only)
        f.addRow("Protected IDs - main", self.prot_main)
        f.addRow("Protected IDs - AV", self.prot_av)
        f.addRow("", self.allow_prot)
        f.addRow("", self.soft_mon)
        lay.addWidget(g)

        g = QGroupBox("Background messages")
        f = QFormLayout(g)
        self.profile = QLineEdit(s.background_profile)
        self.autostart = QCheckBox("Transmit background messages automatically when the bus starts")
        self.autostart.setChecked(s.background_autostart)
        f.addRow("Default profile", row(self.profile, button("Browse…", self._browse_profile), stretch=False))
        f.addRow("", self.autostart)
        lay.addWidget(g)

        g = QGroupBox("Logging / display")
        f = QFormLayout(g)
        self.log_max = QSpinBox()
        self.log_max.setRange(10_000, 5_000_000)
        self.log_max.setSingleStep(50_000)
        self.log_max.setValue(s.log_max_frames)
        self.ui_ms = QSpinBox()
        self.ui_ms.setRange(30, 2000)
        self.ui_ms.setValue(s.ui_refresh_ms)
        self.ui_ms.setSuffix(" ms")
        self.stale_ms = QSpinBox()
        self.stale_ms.setRange(50, 60000)
        self.stale_ms.setValue(s.tracker_stale_ms)
        self.stale_ms.setSuffix(" ms")
        f.addRow("Raw log buffer (frames)", self.log_max)
        f.addRow("UI refresh", self.ui_ms)
        f.addRow("Tracker stale after", self.stale_ms)
        lay.addWidget(g)

        g = QGroupBox("AV head-unit emulation")
        f = QFormLayout(g)
        self.av_auto = QCheckBox("Start HU emulation automatically when the AV bus opens (bench mode)")
        self.av_auto.setChecked(s.av_auto_start)
        self.bus_auto = QCheckBox("Start the last-used buses at launch (same adapters and mode, no bench-mode prompt; "
                                  "never enable this for a real vehicle unless monitoring mode is saved)")
        self.bus_auto.setChecked(s.buses_autostart)
        self.av_answer = QSpinBox()
        self.av_answer.setRange(0, 10000)
        self.av_answer.setValue(s.av_answer_delay_ms)
        self.av_answer.setSuffix(" ms")
        self.av_ack = QSpinBox()
        self.av_ack.setRange(50, 10000)
        self.av_ack.setValue(s.av_ack_timeout_ms)
        self.av_ack.setSuffix(" ms")
        self.av_retry = QSpinBox()
        self.av_retry.setRange(0, 10)
        self.av_retry.setValue(s.av_retries)
        self.av_peer = QDoubleSpinBox()
        self.av_peer.setRange(0.5, 60)
        self.av_peer.setValue(s.av_peer_timeout_s)
        self.av_peer.setSuffix(" s")
        self.av_read = QCheckBox("Read meter settings automatically after the settings channel opens")
        self.av_read.setChecked(s.av_auto_read_settings)
        f.addRow("", self.av_auto)
        f.addRow("", self.bus_auto)
        f.addRow("Answer delay after announce", self.av_answer)
        f.addRow("ACK timeout", self.av_ack)
        f.addRow("Retries", self.av_retry)
        f.addRow("Peer lost after", self.av_peer)
        f.addRow("", self.av_read)
        lay.addWidget(g)

        g = QGroupBox("ESP32 bench I/O")
        f = QFormLayout(g)
        self.esp_port = QLineEdit(s.esp32_port)
        self.esp_auto = QCheckBox("Connect automatically at start-up (this port, else the auto-detected ESP32)")
        self.esp_auto.setChecked(s.esp32_autoconnect)
        self.press = QSpinBox()
        self.press.setRange(20, 10000)
        self.press.setValue(s.press_ms)
        self.press.setSuffix(" ms")
        f.addRow("Serial port", self.esp_port)
        f.addRow("", self.esp_auto)
        f.addRow("Default press", self.press)
        lay.addWidget(g)

        g = QGroupBox("AI agent access (control server + MCP)")
        f = QFormLayout(g)
        self.ctl = QCheckBox("Run the local control server (127.0.0.1 only; needed by the MCP server)")
        self.ctl.setChecked(s.control_server)
        self.ctl_port = QSpinBox()
        self.ctl_port.setRange(1024, 65535)
        self.ctl_port.setValue(s.control_port)
        self.ctl_token = QLineEdit(s.control_token)
        self.ctl_token.setPlaceholderText("optional shared token")
        f.addRow("", self.ctl)
        f.addRow("Port", self.ctl_port)
        f.addRow("Token", self.ctl_token)
        py = os.path.join(ROOT, ".venv", "Scripts" if sys.platform.startswith("win") else "bin",
                          "python.exe" if sys.platform.startswith("win") else "python")
        help_text = QPlainTextEdit(
            "Register the MCP server with Claude Code (run in a terminal):\n"
            f"  claude mcp add q50-bench -- \"{py}\" -m q50tb.control.mcp_server\n\n"
            "Other MCP clients (JSON config):\n"
            f'  {{"command": "{py.replace(chr(92), "/")}", "args": ["-m", "q50tb.control.mcp_server"], '
            f'"cwd": "{ROOT.replace(chr(92), "/")}"}}\n\n'
            "With this GUI running, the agent drives the same bench you see. Add --headless to run without the GUI.")
        help_text.setReadOnly(True)
        help_text.setFont(mono_font(9))
        help_text.setMaximumHeight(140)
        f.addRow(help_text)
        lay.addWidget(g)
        lay.addStretch(1)

    def _browse_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Default profile", os.path.join(ROOT, "profiles"), "JSON (*.json)")
        if path:
            self.profile.setText(path)

    def _defaults(self) -> None:
        if confirm(self, "Defaults", "Restore all settings to defaults? (takes effect after Save)"):
            d = AppSettings()
            self.ctx.settings.__dict__.update(d.__dict__)
            self.ctx.settings.save()
            self.status("defaults restored - restart the app to reload every page")

    def _save(self) -> None:
        s = self.ctx.settings
        try:
            s.bitrate = int(self.bitrate.currentText())
        except ValueError:
            pass
        s.fd = self.fd.isChecked()
        s.data_bitrate = int(self.dbitrate.currentText())
        s.socketcan_configure_link = self.sock.isChecked()
        s.protected_main = [x.strip() for x in self.prot_main.text().split(",") if x.strip()]
        s.protected_av = [x.strip() for x in self.prot_av.text().split(",") if x.strip()]
        s.allow_protected_tx = self.allow_prot.isChecked()
        s.allow_monitor_without_hw_listen_only = self.soft_mon.isChecked()
        s.background_profile = self.profile.text()
        s.background_autostart = self.autostart.isChecked()
        s.log_max_frames = self.log_max.value()
        s.ui_refresh_ms = self.ui_ms.value()
        s.tracker_stale_ms = self.stale_ms.value()
        s.av_auto_start = self.av_auto.isChecked()
        s.buses_autostart = self.bus_auto.isChecked()
        s.av_answer_delay_ms = self.av_answer.value()
        s.av_ack_timeout_ms = self.av_ack.value()
        s.av_retries = self.av_retry.value()
        s.av_peer_timeout_s = self.av_peer.value()
        s.av_auto_read_settings = self.av_read.isChecked()
        s.esp32_port = self.esp_port.text()
        s.esp32_autoconnect = self.esp_auto.isChecked()
        s.press_ms = self.press.value()
        s.control_server = self.ctl.isChecked()
        s.control_port = self.ctl_port.value()
        s.control_token = self.ctl_token.text()
        s.save()
        c = self.ctx
        c.log.set_maxlen(s.log_max_frames)
        c.tracker.stale_s = s.tracker_stale_ms / 1000.0
        c.bench.press_ms = s.press_ms
        c.apply_av_settings()
        for ch in c.buses.channels.values():
            ch.allow_protected = s.allow_protected_tx
            ch.protected_ids = s.protected(ch.role)
        win = self.window()
        if hasattr(win, "apply_settings"):
            win.apply_settings()
        self.status("settings saved")
