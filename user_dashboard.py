"""
user_dashboard.py — User view
"""
import os
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QTableWidget, QTableWidgetItem,
    QHeaderView, QTabWidget, QAbstractItemView, QDialog,
    QTextEdit, QComboBox
)
import gui_utils
from PyQt6.QtCore import Qt, QTimer, QThread, QObject, pyqtSignal
from PyQt6.QtGui import QPixmap
from datetime import datetime

from styles import (
    BG_PAGE, BG_WHITE, ACCENT_BLUE, ACCENT_BLUE2, ACCENT_BLUE_DARK,
    ACCENT_BLUE_LIGHT, ACCENT_BLUE_PALE, ACCENT_GREEN,
    ACCENT_ORANGE, BORDER, TEXT_PRIMARY,
    TEXT_MUTED, ToggleSwitch, status_badge, apply_shadow, resource_path,
)
import db

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOGO_CANDIDATES = [
    # resource_path() resolves correctly both in dev mode AND inside a
    # PyInstaller-built .exe (via sys._MEIPASS) — try it first.
    resource_path("assets", "logo.png"),
    resource_path("logo.png"),
    os.path.join(_HERE, "assets", "logo.png"),
    os.path.join(_HERE, "logo.png"),
    os.path.join(os.getcwd(), "assets", "logo.png"),
    os.path.join(os.getcwd(), "logo.png"),
]
LOGO_PATH = next((p for p in _LOGO_CANDIDATES if os.path.isfile(p)), _LOGO_CANDIDATES[0])


# ── Background DB worker for user dashboard ─────────────────────────────────
class _UserDbWorker(QObject):
    """Fetches ALL data for one refresh cycle off the main UI thread —
    devices, ports (+ pending markers), and my requests —
    so _on_refresh_data() never has to call db.* itself on the UI thread."""
    result_ready = pyqtSignal(object, object, object, object, object, object)
    # devices, ports, pending_port_ids, pending_slot_names, device_reqs, slot_reqs
    finished     = pyqtSignal()

    def __init__(self, user_id):
        super().__init__()
        self.user_id = user_id

    def run(self):
        try:
            devices            = db.get_devices_by_user(self.user_id)
            ports              = db.get_ports_by_user(self.user_id)
            pending_port_ids   = db.get_pending_port_ids_for_user(self.user_id)
            pending_slot_names = db.get_pending_slot_names_for_user(self.user_id)
            device_reqs        = db.get_user_requests(self.user_id)
            slot_reqs          = db.get_slot_requests_by_user(self.user_id)
        except Exception:
            devices, ports, pending_port_ids, pending_slot_names, device_reqs, slot_reqs = \
                [], [], set(), set(), [], []
        self.result_ready.emit(devices, ports, pending_port_ids, pending_slot_names,
                                device_reqs, slot_reqs)
        self.finished.emit()


class RequestDialog(QDialog):
    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Request USB File Transfer Access")
        self.setStyleSheet(f"QDialog {{ background-color: {BG_WHITE}; }}")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self._build(items)

    def _build(self, items):
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        title = QLabel("Request USB Access")
        title.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {TEXT_PRIMARY}; border: none; padding: 0; margin: 0;")
        v.addWidget(title)

        desc = QLabel("Select the port and provide a reason for file transfer access.")
        desc.setStyleSheet(f"font-size: 12px; color: {TEXT_MUTED}; border: none; padding: 0; margin: 0;")
        v.addWidget(desc)

        pl = QLabel("Select Port")
        pl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {TEXT_PRIMARY}; border: none; padding: 0; margin: 0;")
        v.addWidget(pl)

        self.port_combo = QComboBox()
        self.port_combo.setFixedHeight(34)
        for kind, item_id, label in items:
            self.port_combo.addItem(label, userData=(kind, item_id))
        v.addWidget(self.port_combo)

        rl = QLabel("Reason for Request")
        rl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {TEXT_PRIMARY}; border: none; padding: 0; margin: 0;")
        v.addWidget(rl)

        self.reason_edit = QTextEdit()
        self.reason_edit.setPlaceholderText("Describe why you need USB file transfer access...")
        self.reason_edit.setFixedHeight(90)
        v.addWidget(self.reason_edit)

        btn_h = QHBoxLayout()
        btn_h.setSpacing(10)
        btn_h.setContentsMargins(0, 0, 0, 0)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(36)
        cancel_btn.clicked.connect(self.reject)

        submit_btn = QPushButton("Submit Request")
        submit_btn.setFixedHeight(36)
        submit_btn.clicked.connect(self.accept)

        btn_h.addWidget(cancel_btn)
        btn_h.addWidget(submit_btn)
        v.addLayout(btn_h)

        self.setFixedSize(480, 380)

    def get_data(self):
        kind, item_id = self.port_combo.currentData()
        return (kind, item_id, self.reason_edit.toPlainText().strip())


class UserDashboard(QMainWindow):
    def __init__(self, user: dict, admin_view: bool = False, parent_dashboard=None):
        super().__init__()
        self.user = user
        self.admin_view = admin_view
        if admin_view:
            self.setWindowTitle(f"Swift Optimizer — {self.user['full_name']}'s Dashboard (Admin View)")
        else:
            self.setWindowTitle("Swift Optimizer — User Portal")
        self.parent_dashboard = parent_dashboard
        self.setMinimumSize(900, 600)
        self._build_ui()
        self._load_ports()
        self._load_my_requests()

        self._bg_running = False
        self._active_thread = None
        self._closing = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1000)

    def _refresh(self):
        """Non-blocking background DB refresh — never freezes the UI."""
        if getattr(self, '_bg_running', False) or getattr(self, '_closing', False):
            return
        self._bg_running = True
        thread = QThread(self)
        self._active_worker = _UserDbWorker(self.user["id"])
        self._active_worker.moveToThread(thread)
        self._active_thread = thread
        thread.started.connect(self._active_worker.run)
        self._active_worker.result_ready.connect(self._on_refresh_data)
        self._active_worker.finished.connect(thread.quit)
        self._active_worker.finished.connect(self._active_worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._reset_bg_flag)
        thread.finished.connect(self._clear_active_thread)
        thread.start()

    def _reset_bg_flag(self):
        self._bg_running = False

    def _clear_active_thread(self):
        self._active_thread = None

    def closeEvent(self, event):
        """Stop the refresh timer and safely wind down any in-flight
        background thread before this window is destroyed. Without this,
        closing the window while a QThread is still running causes Qt to
        fatally abort the whole application ('QThread: Destroyed while
        thread is still running'), which is what made the app randomly
        close when re-opening 'View Dashboard' from the admin side."""
        self._closing = True
        try:
            self._timer.stop()
        except Exception:
            pass

        thread = getattr(self, '_active_thread', None)
        if thread is not None:
            try:
                if thread.isRunning():
                    thread.quit()
                    thread.wait(3000)  # give the worker up to 3s to finish
            except RuntimeError:
                pass  # underlying C++ object already gone

        super().closeEvent(event)

    def _on_refresh_data(self, devices, ports, pending_port_ids, pending_slot_names,
                          device_reqs, slot_reqs):
        """Called on the UI thread with data fetched in the background.
        Renders directly from these args — must NOT call db.* here, that
        would put a blocking DB round trip back on the UI thread every tick."""
        current_hash = hash(str(devices) + str(ports) + str(pending_port_ids) + str(pending_slot_names)
                             + str(device_reqs) + str(slot_reqs))
        if getattr(self, "_last_data_hash", None) == current_hash:
            return
        self._last_data_hash = current_hash

        self._render_devices_table(devices)
        self._render_ports_table(ports, pending_port_ids, pending_slot_names)
        self._render_my_requests_table(device_reqs, slot_reqs)
        if self.parent_dashboard:
            self.parent_dashboard._periodic_refresh()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        central.setStyleSheet(f"background-color: {BG_PAGE};")

        main_v = QVBoxLayout(central)
        main_v.setContentsMargins(0, 0, 0, 0)
        main_v.setSpacing(0)

        # ── Top bar ──────────────────────────────────────────────────────────
        topbar = QFrame()
        topbar.setFixedHeight(64)
        topbar.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_WHITE};
                border-bottom: 1px solid {BORDER};
            }}
        """)
        tb_h = QHBoxLayout(topbar)
        tb_h.setContentsMargins(24, 0, 24, 0)

        lc_h = QHBoxLayout()
        logo = QLabel()
        pix = QPixmap()
        try:
            with open(LOGO_PATH, "rb") as f:
                pix.loadFromData(f.read())
        except Exception:
            pass
            
        if not pix.isNull():
            logo.setPixmap(pix.scaledToHeight(46, Qt.TransformationMode.SmoothTransformation))
        else:
            logo.setText("⚙  USB Control System")
            logo.setStyleSheet("color: #0099DC; font-size: 15px; font-weight: 700;")
        logo.setStyleSheet(logo.styleSheet() + "background: transparent; border: none;")
        lc_h.addWidget(logo)
        tb_h.addLayout(lc_h)
        tb_h.addStretch()

        user_lbl = QLabel(f"👤  {self.user['full_name']}")
        user_lbl.setStyleSheet(f"color: {ACCENT_BLUE_DARK}; font-size: 13px; font-weight: 600; background: transparent; border: none;")
        tb_h.addWidget(user_lbl)
        tb_h.addSpacing(16)

        if self.admin_view:
            close_btn = QPushButton("✕  Close")
            close_btn.setFixedHeight(32)
            close_btn.setStyleSheet(f"QPushButton {{ background-color: {BG_PAGE}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER}; border-radius: 6px; padding: 0 14px; font-size: 12px; font-weight: 600; }} QPushButton:hover {{ background-color: #e2e8f0; }}")
            close_btn.clicked.connect(self.close)
            tb_h.addWidget(close_btn)
        else:
            logout_btn = QPushButton("↩  Logout")
            logout_btn.setFixedHeight(32)
            logout_btn.setStyleSheet(f"QPushButton {{ background-color: {BG_PAGE}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER}; border-radius: 6px; padding: 0 14px; font-size: 12px; font-weight: 600; }} QPushButton:hover {{ background-color: #e2e8f0; }}")
            logout_btn.clicked.connect(self._logout)
            tb_h.addWidget(logout_btn)

        main_v.addWidget(topbar)

        # ── Content ───────────────────────────────────────────────────────────
        content = QWidget()
        content.setStyleSheet(f"background-color: {BG_PAGE};")
        cv = QVBoxLayout(content)
        cv.setContentsMargins(32, 24, 32, 24)
        cv.setSpacing(16)

        # Header
        hdr_row = QHBoxLayout()
        hv = QVBoxLayout()
        hv.setSpacing(2)
        h1 = QLabel("USB Device Status")
        h1.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {TEXT_PRIMARY};")
        sub_text = (f"Admin View — {self.user['full_name']}'s system (editable)"
                    if self.admin_view
                    else f"Hello, {self.user['full_name']} — view your device status and request file transfer access")
        sub = QLabel(sub_text)
        sub.setStyleSheet(f"font-size: 12px; color: {TEXT_MUTED};")
        hv.addWidget(h1)
        hv.addWidget(sub)
        hdr_row.addLayout(hv)
        hdr_row.addStretch()
        if not self.admin_view:
            req_btn = QPushButton("＋  Request File Transfer")
            req_btn.setFixedHeight(36)
            req_btn.setStyleSheet(
                f"QPushButton {{ background-color: {ACCENT_BLUE}; color: white; border: none; border-radius: 7px; "
                f"font-size: 13px; font-weight: 600; padding: 0 18px; }} "
                f"QPushButton:hover {{ background-color: {ACCENT_BLUE2}; }}"
            )
            req_btn.clicked.connect(lambda: self._open_request_dialog())
            hdr_row.addWidget(req_btn)
        cv.addLayout(hdr_row)

        # Info banner
        if self.admin_view:
            banner = QFrame()
            banner.setStyleSheet(f"QFrame {{ background-color: {ACCENT_BLUE_PALE}; border: 1.5px solid {ACCENT_BLUE_LIGHT}; border-radius: 8px; }}")
            bl = QHBoxLayout(banner)
            bl.setContentsMargins(14, 8, 14, 8)
            bl.addWidget(QLabel("🔐"))
            t = QLabel(f"<b>Admin View</b> — Viewing <b>{self.user['full_name']}</b>'s system. Toggles are editable.")
            t.setStyleSheet(f"color: {ACCENT_BLUE_DARK}; font-size: 12px; background: transparent; border: none;")
            bl.addWidget(t, 1)
            cv.addWidget(banner)
        else:
            banner = QFrame()
            banner.setStyleSheet(f"QFrame {{ background-color: {ACCENT_BLUE_PALE}; border: 1.5px solid {ACCENT_BLUE_LIGHT}; border-radius: 8px; }}")
            bl = QHBoxLayout(banner)
            bl.setContentsMargins(14, 8, 14, 8)
            bl.addWidget(QLabel("ℹ️"))
            t = QLabel("Port access (keyboard, mouse and storage) is controlled by the admin. To request USB file transfer access for a storage device, click “+ Request File Transfer” at the top-right.")
            t.setWordWrap(True)
            t.setStyleSheet(f"color: {ACCENT_BLUE2}; font-size: 12px; background: transparent; border: none;")
            bl.addWidget(t, 1)
            cv.addWidget(banner)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"QWidget {{ background-color: {BG_WHITE}; }}")
        apply_shadow(self.tabs, blur=22, y=4, alpha=22)

        # Tab 1: Device Details
        tab_devices = QWidget()
        tab_devices.setStyleSheet(f"background-color: {BG_WHITE};")
        td = QVBoxLayout(tab_devices)
        td.setContentsMargins(16, 16, 16, 16)
        self.device_table = self._make_device_table()
        td.addWidget(self.device_table)
        self.tabs.addTab(tab_devices, "  Device Details  ")

        # Tab 2: USB Port Details
        tab_ports = QWidget()
        tab_ports.setStyleSheet(f"background-color: {BG_WHITE};")
        tp = QVBoxLayout(tab_ports)
        tp.setContentsMargins(16, 16, 16, 16)
        
        self.port_count_lbl = QLabel("")
        self.port_count_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 13px; font-weight: 600; margin-bottom: 4px;")
        tp.addWidget(self.port_count_lbl)

        if not self.admin_view:
            slots_desc = QLabel("🔌 All ports (occupied and empty) are shown below. Use the “+ Request File Transfer” button above to request storage access for a port.")
            slots_desc.setWordWrap(True)
            slots_desc.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; border: none; background: transparent; padding: 4px 0 8px 0;")
            tp.addWidget(slots_desc)

        self.port_table = self._make_port_table()
        tp.addWidget(self.port_table)
        self.tabs.addTab(tab_ports, "  USB Port Details  ")

        # Tab 3: My Requests (user only)
        if not self.admin_view:
            tab_req = QWidget()
            tab_req.setStyleSheet(f"background-color: {BG_WHITE};")
            tr = QVBoxLayout(tab_req)
            tr.setContentsMargins(16, 16, 16, 16)
            self.my_req_table = self._make_my_request_table()
            tr.addWidget(self.my_req_table)
            self.tabs.addTab(tab_req, "  My Requests  ")

        cv.addWidget(self.tabs)
        main_v.addWidget(content, 1)

    # ── Table factories ───────────────────────────────────────────────────────

    def _make_device_table(self):
        cols = ["Device Name", "Device Type", "Connected Port", "Status"]
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.setAlternatingRowColors(True)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.setShowGrid(False)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        t.setColumnWidth(1, 150)
        t.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        t.setColumnWidth(2, 130)
        t.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        t.setColumnWidth(3, 140)
        return t

    def _make_port_table(self):
        cols = ["Port", "Connected Device", "Port Access", "File Transfer", "Status"]
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.setAlternatingRowColors(True)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.setShowGrid(False)
        
        # Responsive sizing: Fixed small cols, Interactive for Device, Stretch for Status
        # Stretching the last column prevents the right edge from clipping under the scrollbar
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        t.setColumnWidth(0, 100)
        
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        t.setColumnWidth(1, 230)
        
        for col in [2, 3]:
            t.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            t.setColumnWidth(col, 120)
            
        t.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        return t

    def _make_my_request_table(self):
        cols = ["Port", "Device", "Reason", "Status", "Requested"]
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.setAlternatingRowColors(True)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.setShowGrid(False)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        t.setColumnWidth(0, 120)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        t.setColumnWidth(1, 240)
        t.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        t.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        t.setColumnWidth(3, 140)
        t.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        t.setColumnWidth(4, 150)
        return t

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_devices(self):
        """Fetch from the DB (blocking) and render. Only for explicit user
        actions — the timer path uses _render_devices_table() instead."""
        devices = db.get_devices_by_user(self.user["id"])
        self._render_devices_table(devices)

    def _render_devices_table(self, devices):
        """Pure render — no DB calls."""
        t = self.device_table
        t.setRowCount(0)
        for i, dev in enumerate(devices):
            dev_id, dev_name, dev_type, port_name, status = dev
            t.insertRow(i)
            t.setRowHeight(i, 48)
            t.setItem(i, 0, self._cell(dev_name, bold=True))

            type_lbl = QLabel(dev_type)
            type_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if dev_type == "Keyboard":
                type_lbl.setStyleSheet(f"background-color: {ACCENT_BLUE_PALE}; color: {ACCENT_BLUE_DARK}; border: 1px solid {ACCENT_BLUE_LIGHT}; border-radius: 10px; padding: 2px 10px; font-size: 11px; font-weight: 700;")
            elif dev_type == "Mouse":
                type_lbl.setStyleSheet(f"background-color: {BG_WHITE}; color: {ACCENT_BLUE2}; border: 1.5px solid {ACCENT_BLUE2}; border-radius: 10px; padding: 2px 10px; font-size: 11px; font-weight: 700;")
            elif dev_type == "Webcam":
                type_lbl.setStyleSheet(f"background-color: #f3e5f5; color: #6a1b9a; border: 1px solid #ce93d8; border-radius: 10px; padding: 2px 10px; font-size: 11px; font-weight: 700;")
            else:
                type_lbl.setStyleSheet(f"background-color: {ACCENT_BLUE}; color: white; border: 1px solid {ACCENT_BLUE}; border-radius: 10px; padding: 2px 10px; font-size: 11px; font-weight: 700;")
            t.setCellWidget(i, 1, self._center_widget(type_lbl))
            t.setItem(i, 2, self._cell(port_name))
            t.setCellWidget(i, 3, self._center_widget(status_badge(status.upper())))

    def _load_ports(self):
        """Fetch from the DB (blocking) and render. Only for explicit user
        actions — the timer path uses _render_ports_table() instead."""
        self._load_devices()
        pending_port_ids   = db.get_pending_port_ids_for_user(self.user["id"])
        pending_slot_names = db.get_pending_slot_names_for_user(self.user["id"])
        ports              = db.get_ports_by_user(self.user["id"])
        self._render_ports_table(ports, pending_port_ids, pending_slot_names)

    def _render_ports_table(self, ports, pending_port_ids, pending_slot_names):
        """Pure render — no DB calls."""
        t = self.port_table
        t.setRowCount(0)
        row_idx = 0

        # ── Occupied ports ────────────────────────────────────────────────────
        for port in ports:
            (pid, port_name, device_name, kbd_en, mse_en, stor_en, status,
             port_key, port_en, transfer_expires_at, connected, device_type,
             storage_admin_set) = port

            # If a port is disabled (port_en == 0), the hardware was forcefully removed from the PnP tree,
            # so WMI reports it as disconnected. We still want to show the preserved device name.
            is_empty = device_name in ("- Empty -", "Empty Slot", "", None)

            display_name = device_name

            t.insertRow(row_idx)
            t.setRowHeight(row_idx, 52)

            t.setItem(row_idx, 0, self._cell(port_name, bold=True))

            if is_empty:
                from PyQt6.QtWidgets import QTableWidgetItem as _QTW
                dn_item = _QTW(display_name)
                from PyQt6.QtGui import QColor as _QC
                dn_item.setForeground(_QC("#8aabca"))
                _f = dn_item.font(); _f.setItalic(True); dn_item.setFont(_f)
                t.setItem(row_idx, 1, dn_item)
            else:
                t.setItem(row_idx, 1, self._cell(display_name))

            # Port Access toggle — admin can toggle all ports including empty ones.
            # Regular users see the toggle as read-only.
            port_t = ToggleSwitch(bool(port_en))
            port_t.setEnabled(self.admin_view)
            if self.admin_view:
                port_t.setToolTip("Enable/Disable this physical port")
                port_t.toggled.connect(lambda val, p=pid: self._on_toggle(p, "port_enabled", val))
            else:
                port_t.setToolTip("Controlled by admin")
            t.setCellWidget(row_idx, 2, self._center_widget(port_t))

            # File Transfer column — Storage Access is a per-port policy now,
            # so an empty port shows it too (pre-authorized ports display
            # "Allowed" even with nothing plugged in yet). Only genuinely
            # N/A when the port is occupied by a non-storage device.
            is_storage = (device_type or "").strip().lower() == "storage"
            if is_empty or is_storage:
                if self.admin_view:
                    stor_t = ToggleSwitch(bool(stor_en))
                    stor_t.setToolTip("Allow storage devices (pen drives, HDDs) on this port")
                    stor_t.toggled.connect(lambda val, p=pid: self._on_toggle(p, "storage_enabled", val))
                    t.setCellWidget(row_idx, 3, self._center_widget(stor_t))
                else:
                    t.setCellWidget(row_idx, 3, self._center_widget(
                        self._transfer_status_label(stor_en, transfer_expires_at, pid, pending_port_ids)
                    ))
            else:
                na_lbl = QLabel("—")
                na_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px; background: transparent;")
                t.setCellWidget(row_idx, 3, self._center_widget(na_lbl))

            # Status badge: an empty port is always Off — nothing is
            # currently connected for the toggle to be actively allowing.
            # The toggle itself stays ON to show the port is authorized
            # (so a device plugged in next gets access immediately), but
            # the badge only reflects what's happening right now. Occupied
            # ports still show Allowed/Blocked based on the admin's toggle.
            if is_empty:
                badge_status = "OFF"
            elif is_storage:
                # Storage devices are governed by the File Transfer toggle
                # (storage_enabled), not Port Access (port_en) — port_en
                # only controls keyboard/mouse/webcam. Using port_en here
                # made a storage device with File Transfer ON but Port
                # Access OFF show "Blocked" even though it was allowed.
                badge_status = "Allowed" if stor_en else "Blocked"
            else:
                badge_status = "Allowed" if port_en else "Blocked"
            t.setCellWidget(row_idx, 4, self._center_widget(status_badge(badge_status)))

            if not self.admin_view:
                if is_empty or not is_storage:
                    # Users can request access to an empty port (or it's not a storage device)
                    if not is_empty and port_name in pending_slot_names:
                        lbl = QLabel("⏳  Pending")
                        lbl.setStyleSheet(f"color: {ACCENT_ORANGE}; font-weight: 700; font-size: 12px; background: transparent;")
                    else:
                        lbl = QLabel("—")
                        lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px; background: transparent;")
                    t.setCellWidget(row_idx, 5, self._center_widget(lbl))
                else:
                    t.setCellWidget(row_idx, 5, self._center_widget(
                        self._transfer_action_widget(pid, stor_en, pending_port_ids, port_en)
                    ))

            row_idx += 1

        total_ports = len(ports)
        plugged_in = sum(1 for p in ports if p[2] not in ("- Empty -", "Empty Slot", "", None) and p[10])
        self.port_count_lbl.setText(f"Total External Ports: {total_ports} ({plugged_in} plugged in)")

        # NOTE: Empty ports are now included in the ports list above
        # (get_ports_by_user returns connected=1 rows including device_type='None'
        # placeholders). The old available_usb_slots rendering loop is removed
        # to prevent duplicate rows.

    def _transfer_status_label(self, stor_en, expires_at, pid, pending_port_ids) -> QLabel:
        """Read-only File Transfer status: Allowed (+ time remaining if the
        grant is time-boxed), Pending, or Denied."""
        if stor_en:
            text = "✓ Allowed"
            if expires_at:
                remaining = (expires_at - datetime.now()).total_seconds()
                if remaining > 0:
                    h, rem = divmod(int(remaining), 3600)
                    m = rem // 60
                    time_str = f"{h}h {m}m left" if h else f"{m}m left"
                    text = f"✓ Allowed ({time_str})"
            color = ACCENT_GREEN
        elif pid in pending_port_ids:
            text = "⏳ Pending"
            color = ACCENT_ORANGE
        else:
            text = "Denied"
            color = TEXT_MUTED
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {color}; font-weight: 700; font-size: 12px; background: transparent;")
        return lbl

    def _transfer_action_widget(self, pid, stor_en, pending_port_ids, port_en) -> QWidget:
        """Per-row quick action: a Request button when eligible, else a dash."""
        if stor_en or pid in pending_port_ids or not port_en:
            lbl = QLabel("—")
            lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px; background: transparent;")
            return lbl
        btn = QPushButton("Request")
        btn.setFixedHeight(28)
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT_BLUE}; color: white; border: none; "
            f"border-radius: 6px; font-size: 11px; font-weight: 600; padding: 0 12px; }} "
            f"QPushButton:hover {{ background-color: {ACCENT_BLUE2}; }}"
        )
        btn.clicked.connect(lambda _, p=pid: self._open_request_dialog(preselected_port_id=p))
        return btn

    def _load_my_requests(self):
        """Fetch from the DB (blocking) and render. Only for explicit user
        actions — the timer path uses _render_my_requests_table() instead."""
        if self.admin_view:
            return
        device_reqs = db.get_user_requests(self.user["id"])
        slot_reqs   = db.get_slot_requests_by_user(self.user["id"])
        self._render_my_requests_table(device_reqs, slot_reqs)

    def _render_my_requests_table(self, device_reqs, slot_reqs):
        """Pure render — no DB calls."""
        if self.admin_view:
            return

        t = self.my_req_table
        t.setRowCount(0)
        row_idx = 0

        for r in device_reqs:
            rid, port_name, device_name, reason, status, req_at = r
            t.insertRow(row_idx)
            t.setRowHeight(row_idx, 44)
            t.setItem(row_idx, 0, self._cell(f"🔌 {port_name}", bold=True))
            t.setItem(row_idx, 1, self._cell(device_name))
            t.setItem(row_idx, 2, self._cell(reason or "—"))
            t.setCellWidget(row_idx, 3, self._center_widget(status_badge(status.upper())))
            t.setItem(row_idx, 4, self._cell((req_at or "")[:16]))
            row_idx += 1

        for r in slot_reqs:
            rid, slot_name, reason, status, req_at = r
            t.insertRow(row_idx)
            t.setRowHeight(row_idx, 44)
            t.setItem(row_idx, 0, self._cell(f"🕳️ {slot_name}", bold=True))
            t.setItem(row_idx, 1, self._cell("(Empty Port)"))
            t.setItem(row_idx, 2, self._cell(reason or "—"))
            t.setCellWidget(row_idx, 3, self._center_widget(status_badge(status.upper())))
            t.setItem(row_idx, 4, self._cell((req_at or "")[:16]))
            row_idx += 1

        pending = (sum(1 for r in device_reqs if r[4] == "pending") +
                   sum(1 for r in slot_reqs   if r[3] == "pending"))
        self.tabs.setTabText(2, f"  My Requests {'🔴' if pending else ''}  ")

    # ── Toggle handlers ───────────────────────────────────────────────────────

    def _on_toggle(self, port_id, field, value):
        """Run DB update off the UI thread so the toggle animation is smooth."""
        from PyQt6.QtCore import QThread, QObject, pyqtSignal as _sig

        user_id   = self.user["id"]
        user_name = self.user["full_name"]

        thread = QThread(self)
        parent = self

        class _W(QObject):
            done = _sig(bool)
            def run(self_):
                ok = db.update_port_toggle(port_id, field, int(value),
                                           changed_by_id=user_id,
                                           changed_by_name=user_name)
                self_.done.emit(ok)

        worker = _W()
        worker.moveToThread(thread)

        def _finish(ok):
            if not ok:
                gui_utils.show_warning(
                    parent, "Toggle Failed",
                    "Could not update the database — the port may have been resynced. "
                    "Refreshing the table, please try again."
                )
            parent._last_data_hash = None
            parent._load_ports()
            parent._load_my_requests()
            if parent.parent_dashboard:
                parent.parent_dashboard._refresh_all()
            thread.quit()
            thread.wait()

        worker.done.connect(_finish)
        thread.started.connect(worker.run)
        parent._toggle_thread = thread
        parent._toggle_worker = worker
        thread.start()


    # ── Request dialog ────────────────────────────────────────────────────────

    def _open_request_dialog(self, preselected_port_id=None):
        # All ports for this user — both occupied (device plugged in) and
        # empty — so the dropdown always reflects every physical port,
        # not just the empty ones.
        try:
            all_user_ports      = db.get_ports_by_user(self.user["id"])
            pending_port_ids    = db.get_pending_port_ids_for_user(self.user["id"])
            all_slots           = db.get_available_slots_by_user(self.user["id"])
            pending_slot_names  = db.get_pending_slot_names_for_user(self.user["id"])
        except Exception as e:
            # A DB hiccup here used to raise uncaught inside this button's
            # slot, which PyQt6 treats as fatal and silently closes the
            # whole app. Catch it and show a normal error dialog instead.
            gui_utils.show_error(self, "Request Access",
                f"Could not load your ports right now. Please try again.\n\n{e}")
            return

        eligible_ports = [p for p in all_user_ports if p[0] not in pending_port_ids]

        items = []
        for p in eligible_ports:
            pid, port_name, device_name = p[0], p[1], p[2]
            is_empty = device_name in ("- Empty -", "Empty Slot", "", None)
            label = f"{port_name} — (Empty Port)" if is_empty else f"{port_name} — {device_name}"
            
            if is_empty:
                items.append(("slot", port_name, label))
            else:
                items.append(("port", pid, label))

        # Also surface any not-yet-synced empty slots (detected by the agent
        # but not yet present as a row in usb_ports) so those still show up.
        known_port_names = {p[1] for p in all_user_ports}
        for s in all_slots:
            slot_name = s[1]
            if slot_name not in known_port_names and slot_name not in pending_slot_names:
                items.append(("slot", slot_name, f"{slot_name} — (Empty Port)"))

        if not items:
            gui_utils.show_info(self, "Request Access",
                "All your ports already have file transfer access enabled or have a pending request!")
            return

        dlg = RequestDialog(items, self)
        if preselected_port_id:
            index = dlg.port_combo.findData(("port", preselected_port_id))
            if index >= 0:
                dlg.port_combo.setCurrentIndex(index)

        if dlg.exec():
            kind, item_id, reason = dlg.get_data()
            if not reason:
                gui_utils.show_warning(self, "Request", "Please provide a reason for the request.")
                return
            try:
                if kind == "port":
                    ok, msg = db.submit_request(self.user["id"], item_id, reason)
                else:
                    ok, msg = db.submit_slot_request(self.user["id"], item_id, reason)
            except Exception as e:
                # Same reasoning as above: never let a DB exception escape
                # this slot uncaught, or the whole app disappears.
                gui_utils.show_error(self, "Request Failed",
                    f"Could not submit your request right now. Please try again.\n\n{e}")
                return
            if ok:
                gui_utils.show_info(self, "Request Submitted",
                    "Your request has been submitted. An admin will review it shortly.")
                self._last_data_hash = None
                self._load_ports()
                self._load_my_requests()
            else:
                gui_utils.show_warning(self, "Request Failed", msg)

    # ── Logout ────────────────────────────────────────────────────────────────

    def _logout(self):
        from PyQt6.QtCore import QSettings
        settings = QSettings("SwiftProsys", "USBControlSystem")
        settings.remove("login/username")
        settings.remove("login/password")
        
        from login import LoginWindow
        self._lw = LoginWindow()
        self._lw.login_success.connect(self._on_relogin)
        self._lw.show()
        self.close()

    def _on_relogin(self, user):
        self._lw.close()
        from admin_dashboard import AdminDashboard
        if user["role"] in ("admin", "super_admin"):
            w = AdminDashboard(user)
        else:
            w = UserDashboard(user)
            
        from PyQt6.QtWidgets import QApplication
        QApplication.instance()._main_window = w
        w.show()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _cell(self, text: str, bold=False, tooltip=None) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        if tooltip is not None:
            item.setToolTip(tooltip)
        if bold:
            f = item.font()
            f.setBold(True)
            item.setFont(f)
        return item

    def _center_widget(self, widget: QWidget) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(widget)
        return w
