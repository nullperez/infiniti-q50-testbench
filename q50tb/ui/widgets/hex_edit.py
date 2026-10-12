"""Hex input widgets with validation and automatic byte separation."""

from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QLineEdit

from q50tb.ui.widgets.common import mono_font


class _HexBytesValidator(QValidator):
    def __init__(self, max_bytes: int, parent=None):
        super().__init__(parent)
        self.max_bytes = max_bytes

    def validate(self, text: str, pos: int):
        digits = re.sub(r"[\s,:]", "", text)
        if digits and not re.fullmatch(r"[0-9a-fA-F]*", digits):
            return QValidator.Invalid, text, pos
        if len(digits) > self.max_bytes * 2:
            return QValidator.Invalid, text, pos
        if len(digits) % 2:
            return QValidator.Intermediate, text, pos
        return QValidator.Acceptable, text, pos


class HexBytesEdit(QLineEdit):
    """Payload editor: '0102aB' becomes '01 02 AB' while typing; max_bytes limits length."""
    bytesChanged = Signal(bytes)

    def __init__(self, max_bytes: int = 8, parent=None):
        super().__init__(parent)
        self._validator = _HexBytesValidator(max_bytes, self)
        self.setValidator(self._validator)
        self.setFont(mono_font())
        self.setPlaceholderText("e.g. 01 02 03 04")
        self.textEdited.connect(self._reformat)

    def set_max_bytes(self, n: int) -> None:
        self._validator.max_bytes = n
        if len(self.data()) > n:
            self.set_data(self.data()[:n])

    def _reformat(self, text: str) -> None:
        digits = re.sub(r"[^0-9a-fA-F]", "", text).upper()
        pairs = [digits[i:i + 2] for i in range(0, len(digits), 2)]
        new = " ".join(pairs)
        if new != text:
            cur = self.cursorPosition()
            digits_before = len(re.sub(r"[^0-9a-fA-F]", "", text[:cur]))
            self.setText(new)
            # after k digits the cursor sits at k plus one space per completed pair before it
            self.setCursorPosition(digits_before + max(0, (digits_before - 1) // 2))
        valid = len(digits) % 2 == 0
        self.setStyleSheet("" if valid else "QLineEdit { background: #5a3a00; }")
        if valid:
            self.bytesChanged.emit(self.data())

    def data(self) -> bytes:
        digits = re.sub(r"[^0-9a-fA-F]", "", self.text())
        if len(digits) % 2:
            digits = digits[:-1]
        return bytes.fromhex(digits)

    def is_complete(self) -> bool:
        return len(re.sub(r"[^0-9a-fA-F]", "", self.text())) % 2 == 0

    def set_data(self, b: bytes) -> None:
        self.setText(bytes(b).hex(" ").upper())


class HexIdEdit(QLineEdit):
    """CAN ID editor (hex). Extended limit when ``extended`` is set."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.extended = False
        self.setFont(mono_font())
        self.setPlaceholderText("hex ID, e.g. 180")
        self.setMaxLength(8)
        self.textChanged.connect(self._check)

    def value(self) -> Optional[int]:
        t = self.text().strip().lower().replace("0x", "")
        if not t or not re.fullmatch(r"[0-9a-f]+", t):
            return None
        v = int(t, 16)
        return v if v <= (0x1FFFFFFF if self.extended else 0x7FF) else None

    def _check(self, _t: str) -> None:
        self.setStyleSheet("" if self.value() is not None or not self.text() else "QLineEdit { background: #5a1a1a; }")
