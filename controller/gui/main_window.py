"""
Main window — device table, toolbar, trap log panel.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QAction, QColor, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from controller.core.agent_manager import AgentManager
from controller.core.config import load_devices, save_devices
from controller.core.ip_manager import add_ip_alias, is_elevated, remove_ip_alias
from controller.gui.device_dialog import DeviceDialog
from controller.gui.discovery_dialog import DiscoveryDialog
from controller.gui.trap_log import TrapLogPanel
from controller.models.device import Device

log = logging.getLogger(__name__)

# Table columns
COL_NAME    = 0
COL_IP      = 1
COL_TYPE    = 2
COL_STATUS  = 3
COL_CPU     = 4
COL_IFC_UP  = 5
COL_IFC_DN  = 6

HEADERS = ["Name", "IP", "Type", "Status", "CPU %", "IFC Up", "IFC Down"]

_TYPE_LABELS = {
    "cisco_router":   "Cisco Router",
    "cisco_switch":   "Cisco Switch",
    "windows_server": "Windows Server",
}

_STATUS_COLORS = {
    "running": "#2ecc71",
    "stopped": "#aaaaaa",
    "error":   "#e74c3c",
}


class _TrapSignalBridge(QThread):
    """Bridges agent-thread trap callbacks to the Qt main thread."""
    trap_received  = Signal(str, str, object)   # device_name, trap_type, ifc_index
    status_changed = Signal(str, str)            # device_id, status


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SNMP Simulator Controller")
        self.resize(1000, 640)

        self._devices: List[Device] = []
        self._bridge  = _TrapSignalBridge()
        self._manager = AgentManager(
            on_trap  =self._on_trap_thread,
            on_status=self._on_status_thread,
        )

        self._bridge.trap_received.connect(self._on_trap_ui)
        self._bridge.status_changed.connect(self._on_status_ui)

        self._build_ui()
        self._load()

        # Status bar privilege warning
        if not is_elevated():
            self.statusBar().showMessage(
                "Not running as root/admin — IP alias management disabled. "
                "Using existing IPs / high ports only."
            )

    # ------------------------------------------------------------------ #
    #  UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        toolbar = QToolBar("Main")
        self.addToolBar(toolbar)

        def act(label, tooltip, slot):
            a = QAction(label, self)
            a.setToolTip(tooltip)
            a.triggered.connect(slot)
            toolbar.addAction(a)
            return a

        act("➕ Add",       "Add a new simulated device",     self._on_add)
        act("✏️ Edit",      "Edit selected device",            self._on_edit)
        act("🗑️ Delete",   "Delete selected device",          self._on_delete)
        toolbar.addSeparator()
        act("▶ Start",     "Start selected device agent",     self._on_start)
        act("⏹ Stop",      "Stop selected device agent",      self._on_stop)
        toolbar.addSeparator()
        act("▶▶ Start All","Start all device agents",         self._on_start_all)
        act("⏹⏹ Stop All", "Stop all device agents",          self._on_stop_all)
        toolbar.addSeparator()
        act("↕ Toggle IFC","Toggle selected interface down/up",self._on_toggle_ifc)
        toolbar.addSeparator()
        act("🔍 Discover",  "Scan a real device via SNMP",       self._on_discover)

        # Splitter: table (top) + trap log (bottom)
        splitter = QSplitter(Qt.Vertical)

        self._table = QTableWidget(0, len(HEADERS))
        self._table.setHorizontalHeaderLabels(HEADERS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        self._table.setAlternatingRowColors(True)
        self._table.doubleClicked.connect(self._on_edit)
        splitter.addWidget(self._table)

        self._trap_log = TrapLogPanel()
        splitter.addWidget(self._trap_log)
        splitter.setSizes([400, 200])

        self.setCentralWidget(splitter)
        self.setStatusBar(QStatusBar())

    # ------------------------------------------------------------------ #
    #  Persistence
    # ------------------------------------------------------------------ #
    def _load(self):
        self._devices = load_devices()
        self._refresh_table()

    def _save(self):
        save_devices(self._devices)

    # ------------------------------------------------------------------ #
    #  Table management
    # ------------------------------------------------------------------ #
    def _refresh_table(self):
        self._table.setRowCount(0)
        for device in self._devices:
            self._insert_row(device)

    def _insert_row(self, device: Device):
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._update_row(row, device)

    def _update_row(self, row: int, device: Device):
        running = self._manager.is_running(device.id)
        status  = "running" if running else "stopped"
        ifc_up  = sum(1 for i in device.interfaces if i.oper_status == 1)
        ifc_dn  = sum(1 for i in device.interfaces if i.oper_status == 2)

        values = [
            device.name,
            f"{device.ip}:{device.port}",
            _TYPE_LABELS.get(device.device_type, device.device_type),
            status,
            f"{device.cpu_percent}%",
            str(ifc_up),
            str(ifc_dn),
        ]
        for col, val in enumerate(values):
            item = QTableWidgetItem(val)
            item.setData(Qt.UserRole, device.id)
            if col == COL_STATUS:
                item.setForeground(QColor(_STATUS_COLORS.get(status, "#aaaaaa")))
            self._table.setItem(row, col, item)

    def _row_for_device_id(self, device_id: str) -> int:
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item and item.data(Qt.UserRole) == device_id:
                return row
        return -1

    def _selected_device(self) -> Optional[Device]:
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 0)
        if not item:
            return None
        device_id = item.data(Qt.UserRole)
        return next((d for d in self._devices if d.id == device_id), None)

    # ------------------------------------------------------------------ #
    #  Toolbar actions
    # ------------------------------------------------------------------ #
    def _on_add(self):
        dlg = DeviceDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            device = dlg.result_device()
            self._devices.append(device)
            self._insert_row(device)
            self._save()

    def _on_edit(self):
        device = self._selected_device()
        if not device:
            return
        was_running = self._manager.is_running(device.id)
        dlg = DeviceDialog(self, device)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if was_running:
                self._manager.stop_device(device.id)
            row = self._row_for_device_id(device.id)
            self._update_row(row, device)
            if was_running:
                self._start_device(device)
            self._save()

    def _on_delete(self):
        device = self._selected_device()
        if not device:
            return
        reply = QMessageBox.question(
            self,
            "Delete Device",
            f"Delete '{device.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._manager.stop_device(device.id)
        self._try_remove_alias(device.ip)
        self._devices = [d for d in self._devices if d.id != device.id]
        row = self._row_for_device_id(device.id)
        if row >= 0:
            self._table.removeRow(row)
        self._save()

    def _on_start(self):
        device = self._selected_device()
        if device:
            self._start_device(device)

    def _on_stop(self):
        device = self._selected_device()
        if device:
            self._manager.stop_device(device.id)
            self._try_remove_alias(device.ip)

    def _on_start_all(self):
        for device in self._devices:
            if not self._manager.is_running(device.id):
                self._start_device(device)

    def _on_stop_all(self):
        for device in self._devices:
            if self._manager.is_running(device.id):
                self._manager.stop_device(device.id)
                self._try_remove_alias(device.ip)

    def _on_toggle_ifc(self):
        """Bring the first up interface down, or vice-versa."""
        device = self._selected_device()
        if not device or not device.interfaces:
            return
        # Find first interface that is up
        target = next((i for i in device.interfaces if i.oper_status == 1), None)
        if target is None:
            # all down — bring first one up
            target = device.interfaces[0]
            target.oper_status = 1
        else:
            target.oper_status = 2
        row = self._row_for_device_id(device.id)
        if row >= 0:
            self._update_row(row, device)

    def _on_discover(self):
        """Open the SNMP discovery dialog."""
        dlg = DiscoveryDialog(self)
        dlg.exec()

    # ------------------------------------------------------------------ #
    #  Agent lifecycle helpers
    # ------------------------------------------------------------------ #
    def _start_device(self, device: Device):
        if is_elevated():
            self._try_add_alias(device.ip)
        ok = self._manager.start_device(device)
        if not ok:
            QMessageBox.critical(
                self, "Start Failed",
                f"Could not start agent for '{device.name}'.\nCheck logs for details."
            )
        row = self._row_for_device_id(device.id)
        if row >= 0:
            self._update_row(row, device)

    def _try_add_alias(self, ip: str):
        try:
            add_ip_alias(ip)
        except Exception as exc:
            log.warning("Could not add IP alias %s: %s", ip, exc)

    def _try_remove_alias(self, ip: str):
        try:
            remove_ip_alias(ip)
        except Exception as exc:
            log.warning("Could not remove IP alias %s: %s", ip, exc)

    # ------------------------------------------------------------------ #
    #  Cross-thread callbacks
    # ------------------------------------------------------------------ #
    def _on_trap_thread(self, device: Device, trap_type: str, ifc_index):
        """Called from agent thread — emit signal to Qt main thread."""
        self._bridge.trap_received.emit(device.name, trap_type, ifc_index)

    def _on_status_thread(self, device_id: str, status: str):
        self._bridge.status_changed.emit(device_id, status)

    @Slot(str, str, object)
    def _on_trap_ui(self, device_name: str, trap_type: str, ifc_index):
        self._trap_log.add_trap(device_name, trap_type, ifc_index)

    @Slot(str, str)
    def _on_status_ui(self, device_id: str, status: str):
        row = self._row_for_device_id(device_id)
        if row < 0:
            return
        item = self._table.item(row, COL_STATUS)
        if item:
            item.setText(status)
            item.setForeground(QColor(_STATUS_COLORS.get(status, "#aaaaaa")))
        # Also refresh CPU/IFC columns periodically via the data engine callback
        device = next((d for d in self._devices if d.id == device_id), None)
        if device:
            self._update_row(row, device)

    # ------------------------------------------------------------------ #
    #  Close
    # ------------------------------------------------------------------ #
    def closeEvent(self, event):
        self._manager.stop_all()
        self._save()
        super().closeEvent(event)
