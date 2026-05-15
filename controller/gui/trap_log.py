"""
Trap log panel — live scrolling list of sent SNMP traps.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_TRAP_COLORS = {
    "linkDown": "#e74c3c",
    "linkUp":   "#2ecc71",
    "cpuHigh":  "#f39c12",
}
_MAX_ENTRIES = 500


class TrapLogPanel(QWidget):
    """A group-box widget with a scrolling list of trap events."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        box = QGroupBox("Trap Log")
        vbox = QVBoxLayout(box)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        vbox.addWidget(self._list)

        btn_bar = QHBoxLayout()
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self._list.clear)
        btn_bar.addStretch()
        btn_bar.addWidget(btn_clear)
        vbox.addLayout(btn_bar)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)

    def add_trap(self, device_name: str, trap_type: str, ifc_index=None):
        """Append a trap entry. Safe to call from any thread via Qt signal."""
        ts = datetime.now().strftime("%H:%M:%S")
        ifc_info = f"  ifc={ifc_index}" if ifc_index is not None else ""
        text = f"[{ts}]  {trap_type:<10}  {device_name}{ifc_info}"

        item = QListWidgetItem(text)
        color = _TRAP_COLORS.get(trap_type, "#aaaaaa")
        item.setForeground(QColor(color))
        item.setData(Qt.UserRole, trap_type)

        self._list.addItem(item)
        self._list.scrollToBottom()

        # Trim to max entries
        while self._list.count() > _MAX_ENTRIES:
            self._list.takeItem(0)
