"""
Discovery Dialog — scan a real device via SNMP and save the result as a profile.

Layout
------
  [Scan form]           IP, community, port, version, options, [Scan] button
  [Result tabs]         System | Interfaces | CDP | LLDP | Bridge | Raw OIDs
  [Action buttons]      Save Profile… | Create Device… | Close
"""
from __future__ import annotations

import uuid
from typing import Optional

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from controller.core.config import load_discovery_profiles, save_discovery_profiles
from controller.core.discovery import DiscoveryResult, DiscoveryWorker


class DiscoveryDialog(QDialog):
    """
    Modal dialog for discovering a real device via SNMP.

    After a successful scan the user can:
      • Save the result as a named profile.
      • Open DeviceDialog pre-filled with the discovered data.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SNMP Device Discovery")
        self.setMinimumSize(820, 580)
        self._result: Optional[DiscoveryResult] = None
        self._worker: Optional[DiscoveryWorker] = None
        self._build_ui()

    # ------------------------------------------------------------------ #
    #  Build UI
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        root = QVBoxLayout(self)

        # ---- Scan form --------------------------------------------------
        scan_group = QGroupBox("Scan Target")
        scan_form  = QFormLayout(scan_group)

        self._ip_edit = QLineEdit()
        self._ip_edit.setPlaceholderText("e.g. 192.168.1.1")
        scan_form.addRow("Target IP:", self._ip_edit)

        self._community_edit = QLineEdit("public")
        scan_form.addRow("Community:", self._community_edit)

        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(161)
        scan_form.addRow("SNMP Port:", self._port_spin)

        self._version_combo = QComboBox()
        self._version_combo.addItem("SNMPv2c", "v2c")
        self._version_combo.addItem("SNMPv1",  "v1")
        scan_form.addRow("SNMP Version:", self._version_combo)

        opt_row = QWidget()
        opt_lay = QHBoxLayout(opt_row)
        opt_lay.setContentsMargins(0, 0, 0, 0)

        self._bridge_check = QCheckBox("Include BRIDGE-MIB")
        opt_lay.addWidget(self._bridge_check)

        opt_lay.addWidget(QLabel("  Timeout (s):"))
        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(1, 60)
        self._timeout_spin.setValue(5)
        opt_lay.addWidget(self._timeout_spin)

        opt_lay.addWidget(QLabel("  Retries:"))
        self._retries_spin = QSpinBox()
        self._retries_spin.setRange(0, 10)
        self._retries_spin.setValue(2)
        opt_lay.addWidget(self._retries_spin)
        opt_lay.addStretch()

        scan_form.addRow("Options:", opt_row)

        btn_row = QWidget()
        btn_lay = QHBoxLayout(btn_row)
        btn_lay.setContentsMargins(0, 0, 0, 0)
        self._scan_btn = QPushButton("🔍  Scan")
        self._scan_btn.clicked.connect(self._on_scan)
        btn_lay.addWidget(self._scan_btn)
        self._progress_label = QLabel("")
        self._progress_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        btn_lay.addWidget(self._progress_label)
        scan_form.addRow(btn_row)

        root.addWidget(scan_group)

        # ---- Result tabs ------------------------------------------------
        self._tabs = QTabWidget()
        self._tabs.setEnabled(False)

        # System tab
        sys_widget = QWidget()
        self._sys_form = QFormLayout(sys_widget)
        self._sys_fields: dict[str, QLabel] = {}
        for key in ("sysDescr", "sysObjectID", "sysName", "sysLocation",
                    "sysContact", "sysUpTime", "sysServices"):
            lbl = QLabel("—")
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._sys_form.addRow(f"{key}:", lbl)
            self._sys_fields[key] = lbl
        self._tabs.addTab(sys_widget, "System")

        # Interfaces tab
        self._ifc_table = self._make_table(
            ["ifIndex", "Name", "Type", "Speed", "AdminStatus", "OperStatus", "Alias"]
        )
        self._tabs.addTab(self._ifc_table, "Interfaces")

        # CDP tab
        self._cdp_table = self._make_table(
            ["Local If", "Remote Device", "Remote IP", "Platform", "Remote Port", "Version"]
        )
        self._tabs.addTab(self._cdp_table, "CDP")

        # LLDP tab
        self._lldp_table = self._make_table(
            ["Chassis ID", "Sys Name", "Port ID", "Port Desc", "Sys Desc"]
        )
        self._tabs.addTab(self._lldp_table, "LLDP")

        # Bridge tab (hidden until bridge data present)
        self._bridge_widget = QWidget()
        self._bridge_form   = QFormLayout(self._bridge_widget)
        self._bridge_fields: dict[str, QLabel] = {}
        for key in ("dot1dBaseBridgeAddress", "dot1dBaseNumPorts", "dot1dBaseType"):
            lbl = QLabel("—")
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._bridge_form.addRow(f"{key}:", lbl)
            self._bridge_fields[key] = lbl
        self._bridge_tab_idx = self._tabs.addTab(self._bridge_widget, "Bridge")

        # Raw OIDs tab
        self._raw_table = self._make_table(["OID", "Value"])
        self._raw_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._raw_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._tabs.addTab(self._raw_table, "Raw OIDs")

        root.addWidget(self._tabs, stretch=1)

        # ---- Bottom action bar ------------------------------------------
        action_row = QHBoxLayout()
        self._save_btn   = QPushButton("💾  Save Profile…")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save_profile)

        self._create_btn = QPushButton("➕  Create Device from this…")
        self._create_btn.setEnabled(False)
        self._create_btn.clicked.connect(self._on_create_device)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)

        action_row.addWidget(self._save_btn)
        action_row.addWidget(self._create_btn)
        action_row.addStretch()
        action_row.addWidget(close_btn)
        root.addLayout(action_row)

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setAlternatingRowColors(True)
        t.horizontalHeader().setStretchLastSection(True)
        t.setSortingEnabled(True)
        return t

    def _set_progress(self, msg: str):
        self._progress_label.setText(msg)

    # ------------------------------------------------------------------ #
    #  Scan
    # ------------------------------------------------------------------ #
    @Slot()
    def _on_scan(self):
        ip = self._ip_edit.text().strip()
        if not ip:
            QMessageBox.warning(self, "Validation", "Please enter a target IP address.")
            return

        # Disable scan button while running
        self._scan_btn.setEnabled(False)
        self._tabs.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._create_btn.setEnabled(False)
        self._result = None
        self._set_progress("Starting scan…")

        self._worker = DiscoveryWorker(
            ip=ip,
            community=self._community_edit.text().strip() or "public",
            port=self._port_spin.value(),
            version=self._version_combo.currentData(),
            include_bridge=self._bridge_check.isChecked(),
            timeout=self._timeout_spin.value(),
            retries=self._retries_spin.value(),
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.result.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    @Slot(str)
    def _on_progress(self, msg: str):
        self._set_progress(msg)

    @Slot(object)
    def _on_result(self, result: DiscoveryResult):
        self._result = result
        self._populate_tabs(result)
        self._scan_btn.setEnabled(True)
        self._tabs.setEnabled(True)
        self._save_btn.setEnabled(True)
        self._create_btn.setEnabled(True)
        oid_count = len(result.raw_oids)
        ifc_count = len(result.interfaces)
        self._set_progress(
            f"✅  Done — {ifc_count} interface(s), {oid_count} OID(s) collected."
        )

    @Slot(str)
    def _on_error(self, msg: str):
        self._scan_btn.setEnabled(True)
        self._set_progress(f"❌  Error: {msg}")
        QMessageBox.critical(self, "Discovery Failed", msg)

    # ------------------------------------------------------------------ #
    #  Populate result tabs
    # ------------------------------------------------------------------ #
    def _populate_tabs(self, r: DiscoveryResult):
        # System
        for key, lbl in self._sys_fields.items():
            lbl.setText(r.system.get(key, "—"))

        # Interfaces
        self._ifc_table.setRowCount(0)
        for ifc in r.interfaces:
            row = self._ifc_table.rowCount()
            self._ifc_table.insertRow(row)
            vals = [
                ifc.get("ifIndex", ""),
                ifc.get("ifName") or ifc.get("ifDescr", ""),
                ifc.get("ifType", ""),
                ifc.get("ifSpeed", ""),
                _admin_str(ifc.get("ifAdminStatus", "")),
                _oper_str(ifc.get("ifOperStatus", "")),
                ifc.get("ifAlias", ""),
            ]
            for col, v in enumerate(vals):
                self._ifc_table.setItem(row, col, QTableWidgetItem(str(v)))

        # CDP
        self._cdp_table.setRowCount(0)
        for nbr in r.cdp_neighbors:
            row = self._cdp_table.rowCount()
            self._cdp_table.insertRow(row)
            # extract local interface index from CDP row key if available
            vals = [
                nbr.get("_localIf", ""),
                nbr.get("cdpCacheDeviceId", ""),
                nbr.get("cdpCacheAddress", ""),
                nbr.get("cdpCachePlatform", ""),
                nbr.get("cdpCacheDevicePort", ""),
                nbr.get("cdpCacheVersion", ""),
            ]
            for col, v in enumerate(vals):
                self._cdp_table.setItem(row, col, QTableWidgetItem(str(v)))

        # LLDP
        self._lldp_table.setRowCount(0)
        for nbr in r.lldp_neighbors:
            row = self._lldp_table.rowCount()
            self._lldp_table.insertRow(row)
            vals = [
                nbr.get("lldpRemChassisId", ""),
                nbr.get("lldpRemSysName", ""),
                nbr.get("lldpRemPortId", ""),
                nbr.get("lldpRemPortDesc", ""),
                nbr.get("lldpRemSysDesc", ""),
            ]
            for col, v in enumerate(vals):
                self._lldp_table.setItem(row, col, QTableWidgetItem(str(v)))

        # Bridge
        has_bridge = bool(r.bridge)
        for key, lbl in self._bridge_fields.items():
            lbl.setText(r.bridge.get(key, "—"))
        # Show/hide bridge tab
        self._tabs.setTabVisible(self._bridge_tab_idx, has_bridge)

        # Raw OIDs
        self._raw_table.setSortingEnabled(False)
        self._raw_table.setRowCount(0)
        for oid, val in r.raw_oids.items():
            row = self._raw_table.rowCount()
            self._raw_table.insertRow(row)
            self._raw_table.setItem(row, 0, QTableWidgetItem(oid))
            self._raw_table.setItem(row, 1, QTableWidgetItem(val))
        self._raw_table.setSortingEnabled(True)

    # ------------------------------------------------------------------ #
    #  Save Profile
    # ------------------------------------------------------------------ #
    @Slot()
    def _on_save_profile(self):
        if not self._result:
            return

        from PySide6.QtWidgets import QInputDialog
        suggested = self._result.system.get("sysName") or self._result.target_ip
        name, ok = QInputDialog.getText(
            self, "Save Discovery Profile",
            "Profile name:", text=suggested
        )
        if not ok or not name.strip():
            return

        profiles = load_discovery_profiles()
        # Remove any existing profile with the same name
        profiles = [p for p in profiles if p.get("name") != name.strip()]
        entry = self._result.to_dict()
        entry["id"]   = str(uuid.uuid4())
        entry["name"] = name.strip()
        profiles.append(entry)
        save_discovery_profiles(profiles)
        QMessageBox.information(
            self, "Saved", f"Profile '{name.strip()}' saved successfully."
        )

    # ------------------------------------------------------------------ #
    #  Create Device
    # ------------------------------------------------------------------ #
    @Slot()
    def _on_create_device(self):
        if not self._result:
            return
        # Import here to avoid circular imports
        from controller.gui.device_dialog import DeviceDialog
        dlg = DeviceDialog(self, discovery=self._result)
        dlg.exec()


# ---------------------------------------------------------------------------
#  Tiny helper formatters
# ---------------------------------------------------------------------------
def _admin_str(val: str) -> str:
    mapping = {"1": "up", "2": "down", "3": "testing"}
    return mapping.get(str(val), str(val))


def _oper_str(val: str) -> str:
    mapping = {
        "1": "up", "2": "down", "3": "testing",
        "4": "unknown", "5": "dormant", "6": "notPresent", "7": "lowerLayerDown",
    }
    return mapping.get(str(val), str(val))
