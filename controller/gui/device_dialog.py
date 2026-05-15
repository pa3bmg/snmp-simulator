"""
Device dialog — add or edit a simulated device.
"""
from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from controller.models.device import Device, DEVICE_TYPES

# Import lazily to avoid heavy circular imports at module load time
_DiscoveryResult = None


def _get_discovery_result_cls():
    global _DiscoveryResult
    if _DiscoveryResult is None:
        from controller.core.discovery import DiscoveryResult
        _DiscoveryResult = DiscoveryResult
    return _DiscoveryResult

_DEVICE_LABELS = {
    "cisco_router":   "Cisco Router",
    "cisco_switch":   "Cisco Switch",
    "windows_server": "Windows Server",
}

_IP_RE = re.compile(
    r"^(\d{1,3}\.){3}\d{1,3}$"
)


class DeviceDialog(QDialog):
    """
    Modal dialog for creating or editing a Device.

    Usage
    -----
    dlg = DeviceDialog(parent)           # new device
    dlg = DeviceDialog(parent, device)   # edit existing
    if dlg.exec() == QDialog.Accepted:
        device = dlg.result_device()
    """

    def __init__(self, parent=None, device: Optional[Device] = None, discovery=None):
        super().__init__(parent)
        self._device = device
        self._result: Optional[Device] = None
        self._discovery = discovery          # optional DiscoveryResult for pre-fill
        self._pending_extra_oids: dict = {}
        self._pending_ifc_names:  list = []
        self.setWindowTitle("Edit Device" if device else "Add Device")
        self.setMinimumWidth(420)
        self._build_ui()
        if device:
            self._populate(device)
        elif discovery:
            self._populate_from_discovery(discovery)

    # ------------------------------------------------------------------ #
    #  Build UI
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        main_layout = QVBoxLayout(self)

        # "Load from Profile" banner (only shown when creating a new device)
        if not self._device:
            load_row = QHBoxLayout()
            load_btn = QPushButton("📂  Load from Profile…")
            load_btn.setToolTip("Pre-fill this form from a saved discovery profile")
            load_btn.clicked.connect(self._on_load_profile)
            load_row.addWidget(load_btn)
            load_row.addStretch()
            main_layout.addLayout(load_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        container = QWidget()
        form = QFormLayout(container)
        form.setLabelAlignment(Qt.AlignRight)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        scroll.setWidget(container)
        main_layout.addWidget(scroll)

        # Device type
        self._type_combo = QComboBox()
        for key in DEVICE_TYPES:
            self._type_combo.addItem(_DEVICE_LABELS[key], key)
        form.addRow("Device Type:", self._type_combo)

        # Name
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. router-01")
        form.addRow("Device Name:", self._name_edit)

        # IP
        self._ip_edit = QLineEdit()
        self._ip_edit.setPlaceholderText("e.g. 127.0.0.10")
        form.addRow("IP Address:", self._ip_edit)

        # Port
        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(161)
        form.addRow("SNMP Port:", self._port_spin)

        # Community
        self._community_edit = QLineEdit("public")
        form.addRow("Community String:", self._community_edit)

        # Number of interfaces
        self._ifc_spin = QSpinBox()
        self._ifc_spin.setRange(1, 48)
        self._ifc_spin.setValue(4)
        form.addRow("# Interfaces:", self._ifc_spin)

        # CPU range
        cpu_widget = QWidget()
        cpu_layout = QHBoxLayout(cpu_widget)
        cpu_layout.setContentsMargins(0, 0, 0, 0)
        self._cpu_min_spin = QSpinBox()
        self._cpu_min_spin.setRange(0, 99)
        self._cpu_min_spin.setValue(5)
        self._cpu_max_spin = QSpinBox()
        self._cpu_max_spin.setRange(1, 100)
        self._cpu_max_spin.setValue(75)
        cpu_layout.addWidget(QLabel("Min:"))
        cpu_layout.addWidget(self._cpu_min_spin)
        cpu_layout.addWidget(QLabel("  Max:"))
        cpu_layout.addWidget(self._cpu_max_spin)
        cpu_layout.addStretch()
        form.addRow("CPU Range (%):", cpu_widget)

        # Trap section
        trap_group = QGroupBox("SNMP Traps")
        trap_form  = QFormLayout(trap_group)

        self._trap_dest_edit = QLineEdit()
        self._trap_dest_edit.setPlaceholderText("IP of CA Spectrum server")
        trap_form.addRow("Trap Destination:", self._trap_dest_edit)

        self._trap_port_spin = QSpinBox()
        self._trap_port_spin.setRange(1, 65535)
        self._trap_port_spin.setValue(162)
        trap_form.addRow("Trap Port:", self._trap_port_spin)

        self._trap_community_edit = QLineEdit("public")
        trap_form.addRow("Trap Community:", self._trap_community_edit)

        form.addRow(trap_group)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        main_layout.addWidget(buttons)

    # ------------------------------------------------------------------ #
    #  Populate from existing device
    # ------------------------------------------------------------------ #
    def _populate(self, d: Device):
        idx = self._type_combo.findData(d.device_type)
        if idx >= 0:
            self._type_combo.setCurrentIndex(idx)
        self._name_edit.setText(d.name)
        self._ip_edit.setText(d.ip)
        self._port_spin.setValue(d.port)
        self._community_edit.setText(d.community)
        self._ifc_spin.setValue(d.num_interfaces)
        self._cpu_min_spin.setValue(d.cpu_min)
        self._cpu_max_spin.setValue(d.cpu_max)
        self._trap_dest_edit.setText(d.trap_destination)
        self._trap_port_spin.setValue(d.trap_port)
        self._trap_community_edit.setText(d.trap_community)

    # ------------------------------------------------------------------ #
    #  Validation + accept
    # ------------------------------------------------------------------ #
    def _on_accept(self):
        name = self._name_edit.text().strip()
        ip   = self._ip_edit.text().strip()

        if not name:
            QMessageBox.warning(self, "Validation", "Device name is required.")
            return
        if not _IP_RE.match(ip):
            QMessageBox.warning(self, "Validation", f"'{ip}' is not a valid IP address.")
            return
        if self._cpu_min_spin.value() >= self._cpu_max_spin.value():
            QMessageBox.warning(self, "Validation", "CPU min must be less than CPU max.")
            return

        device_type = self._type_combo.currentData()

        if self._device:
            # editing — preserve id and interfaces unless count changed
            d = self._device
            d.device_type      = device_type
            d.name             = name
            d.ip               = ip
            d.port             = self._port_spin.value()
            d.community        = self._community_edit.text().strip()
            d.cpu_min          = self._cpu_min_spin.value()
            d.cpu_max          = self._cpu_max_spin.value()
            d.trap_destination = self._trap_dest_edit.text().strip()
            d.trap_port        = self._trap_port_spin.value()
            d.trap_community   = self._trap_community_edit.text().strip()
            if self._ifc_spin.value() != d.num_interfaces:
                d.num_interfaces = self._ifc_spin.value()
                d.interfaces = d._default_interfaces()
            self._result = d
        else:
            self._result = Device(
                device_type      = device_type,
                name             = name,
                ip               = ip,
                port             = self._port_spin.value(),
                community        = self._community_edit.text().strip(),
                num_interfaces   = self._ifc_spin.value(),
                cpu_min          = self._cpu_min_spin.value(),
                cpu_max          = self._cpu_max_spin.value(),
                trap_destination = self._trap_dest_edit.text().strip(),
                trap_port        = self._trap_port_spin.value(),
                trap_community   = self._trap_community_edit.text().strip(),
                extra_oids       = self._pending_extra_oids,
            )
            # Apply discovered interface names if we have them
            if self._pending_ifc_names:
                for ifc, ifc_name in zip(
                    self._result.interfaces, self._pending_ifc_names
                ):
                    ifc.name = ifc_name

        self.accept()

    def result_device(self) -> Optional[Device]:
        return self._result

    # ------------------------------------------------------------------ #
    #  Load from discovery profile
    # ------------------------------------------------------------------ #
    def _on_load_profile(self):
        from controller.core.config import load_discovery_profiles
        from controller.core.discovery import DiscoveryResult

        profiles = load_discovery_profiles()
        if not profiles:
            QMessageBox.information(
                self, "No Profiles",
                "No discovery profiles saved yet.\n"
                "Use the 🔍 Discover button in the toolbar to scan a device first."
            )
            return

        # Simple list-picker dialog
        picker = QDialog(self)
        picker.setWindowTitle("Select Discovery Profile")
        picker.setMinimumWidth(420)
        layout = QVBoxLayout(picker)
        layout.addWidget(QLabel("Select a saved discovery profile to pre-fill the form:"))

        lst = QListWidget()
        for p in profiles:
            label = (
                f"{p.get('name', '(unnamed)')}  "
                f"—  {p.get('target_ip', '')}  "
                f"({p.get('timestamp', '')[:10]})"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, p)
            lst.addItem(item)
        lst.setCurrentRow(0)
        layout.addWidget(lst)

        btn_row = QHBoxLayout()
        ok_btn  = QPushButton("Load")
        ok_btn.clicked.connect(picker.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(picker.reject)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        selected_item = lst.currentItem()
        if not selected_item:
            return

        profile_dict = selected_item.data(Qt.UserRole)
        discovery = DiscoveryResult.from_dict(profile_dict)
        self._populate_from_discovery(discovery)

    def _populate_from_discovery(self, discovery) -> None:
        """Pre-fill the form from a DiscoveryResult (or compatible dict-like object)."""
        # Device type
        device_type = discovery.infer_device_type()
        idx = self._type_combo.findData(device_type)
        if idx >= 0:
            self._type_combo.setCurrentIndex(idx)

        # Name + IP + community
        sys_name = discovery.system.get("sysName", "").strip()
        if sys_name:
            self._name_edit.setText(sys_name)
        self._ip_edit.setText(discovery.target_ip)
        self._community_edit.setText(discovery.community)

        # Interface count — use actual discovered count capped at spin max
        ifc_count = len(discovery.interfaces)
        if ifc_count > 0:
            self._ifc_spin.setValue(min(ifc_count, self._ifc_spin.maximum()))

        # Store extra_oids on the dialog so _on_accept can attach them to the device
        self._pending_extra_oids = dict(discovery.raw_oids)
        self._pending_ifc_names  = [
            ifc.get("ifName") or ifc.get("ifDescr", f"ifc{i}")
            for i, ifc in enumerate(discovery.interfaces)
        ]
