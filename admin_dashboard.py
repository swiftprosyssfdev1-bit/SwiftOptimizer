"""
admin_dashboard.py — Admin view (light theme, sidebar wired to tabs, MySQL backend)
"""
import os
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QStackedWidget,
    QComboBox, QLineEdit, QCheckBox, QDialog
)
import gui_utils
from PyQt6.QtCore import Qt, QTimer, QThread, QObject, pyqtSignal, QSize, QRectF, QPointF
from PyQt6.QtGui import QPixmap, QColor, QFont, QIcon, QPainter, QPen, QPainterPath

from styles import (
    BG_PAGE, BG_WHITE, ACCENT_BLUE,
    ACCENT_BLUE2, ACCENT_BLUE_DARK, ACCENT_BLUE_LIGHT, ACCENT_BLUE_PALE, ACCENT_RED,
    DANGER_RED, DANGER_RED_HOVER, SUCCESS_GREEN,
    SUCCESS_GREEN_HOVER, TEXT_PRIMARY,
    TEXT_MUTED, BORDER, ToggleSwitch, status_badge, apply_shadow, resource_path,
    HEADER_BAR_STYLE,
)
import db
# NOTE: registry_utils IS used by admin_dashboard — see _ToggleWorker.run()
# below, which does an "instant local apply" via registry_utils when an
# admin toggles a port on their own PC (imported lazily there so the GUI
# still loads fine on a machine where wmi/pywin32 aren't available). The
# USB agent (usb_agent.py) on each employee PC is what actually enforces
# the setting for THAT PC — this local call is only for the admin's own
# workstation, as an instant-feedback nicety, not the primary enforcement
# path.


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


def _make_password_field(placeholder, height=42):
    return gui_utils.make_password_field(placeholder, height=height)


def make_icon(kind: str, color: str = "#FFFFFF", size: int = 18) -> QIcon:
    """Draws a small vector icon (eye / pencil / trash / plus / x) directly
    with QPainter instead of relying on a font having that glyph — emoji
    and symbol-font coverage varies a lot machine to machine, so buttons
    that used text glyphs were rendering as blank boxes on some PCs. This
    always renders identically everywhere."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(size * 0.11)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    s = size

    if kind == "view":
        # Eye: outer almond shape + filled pupil
        rect = QRectF(s * 0.08, s * 0.30, s * 0.84, s * 0.40)
        p.drawArc(rect, 0, 180 * 16)
        p.drawArc(rect, 180 * 16, 180 * 16)
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(s * 0.5, s * 0.5), s * 0.10, s * 0.10)

    elif kind == "edit":
        # Outlined box + pencil, matching the reference "edit" glyph:
        # three sides of a square (open at the top-right) with a pencil
        # crossing through the gap.
        box = QPainterPath()
        box.moveTo(s * 0.56, s * 0.28)
        box.lineTo(s * 0.18, s * 0.28)
        box.lineTo(s * 0.18, s * 0.80)
        box.lineTo(s * 0.74, s * 0.80)
        box.lineTo(s * 0.74, s * 0.44)
        p.drawPath(box)

        # Pencil, drawn along local x-axis then rotated -45° into place.
        p.save()
        p.translate(s * 0.62, s * 0.34)
        p.rotate(-45)
        L = s * 0.56      # shaft length
        w = s * 0.15      # shaft half-height (thickness)
        notch = s * 0.14  # tip length
        pencil = QPainterPath()
        pencil.moveTo(-L / 2, -w / 2)
        pencil.lineTo(L / 2 - notch, -w / 2)
        pencil.lineTo(L / 2, 0)
        pencil.lineTo(L / 2 - notch, w / 2)
        pencil.lineTo(-L / 2, w / 2)
        pencil.closeSubpath()
        p.drawPath(pencil)
        p.restore()

    elif kind == "delete":
        # Trash can: lid + body + ribs
        p.drawLine(QPointF(s * 0.20, s * 0.28), QPointF(s * 0.80, s * 0.28))
        p.drawLine(QPointF(s * 0.38, s * 0.28), QPointF(s * 0.42, s * 0.16))
        p.drawLine(QPointF(s * 0.42, s * 0.16), QPointF(s * 0.58, s * 0.16))
        p.drawLine(QPointF(s * 0.58, s * 0.16), QPointF(s * 0.62, s * 0.28))
        body = QPainterPath()
        body.moveTo(s * 0.26, s * 0.34)
        body.lineTo(s * 0.30, s * 0.84)
        body.lineTo(s * 0.70, s * 0.84)
        body.lineTo(s * 0.74, s * 0.34)
        p.drawPath(body)
        p.drawLine(QPointF(s * 0.40, s * 0.42), QPointF(s * 0.42, s * 0.76))
        p.drawLine(QPointF(s * 0.50, s * 0.42), QPointF(s * 0.50, s * 0.76))
        p.drawLine(QPointF(s * 0.60, s * 0.42), QPointF(s * 0.58, s * 0.76))

    elif kind == "add":
        p.drawLine(QPointF(s * 0.5, s * 0.18), QPointF(s * 0.5, s * 0.82))
        p.drawLine(QPointF(s * 0.18, s * 0.5), QPointF(s * 0.82, s * 0.5))

    elif kind == "dismiss":
        p.drawLine(QPointF(s * 0.24, s * 0.24), QPointF(s * 0.76, s * 0.76))
        p.drawLine(QPointF(s * 0.76, s * 0.24), QPointF(s * 0.24, s * 0.76))

    p.end()
    return QIcon(pm)


def _icon_button(kind, tooltip, bg, hover_bg, fg="#FFFFFF", border=None, size=(36, 32), icon_size=17):
    btn = QPushButton()
    btn.setIcon(make_icon(kind, color=fg, size=icon_size))
    btn.setIconSize(QSize(icon_size, icon_size))
    btn.setToolTip(tooltip)
    btn.setFixedSize(*size)
    border_css = f"border: 1px solid {border};" if border else "border: none;"
    btn.setStyleSheet(f"""
        QPushButton {{ background-color: {bg}; {border_css} border-radius: 6px; }}
        QPushButton:hover {{ background-color: {hover_bg}; }}
    """)
    return btn


class AdminInfoDialog(QDialog):
    """Read-only 'View' for an admin account. Admin accounts don't run the
    USB agent, so there's no port/device dashboard to open for them the
    way there is for an employee — this just shows their account details."""

    def __init__(self, parent, full_name, username, branch, is_active):
        super().__init__(parent)
        self.setWindowTitle("Admin Details")
        self.setFixedWidth(340)
        self.setStyleSheet(f"background-color: {BG_WHITE};")

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 22)
        v.setSpacing(10)

        title = QLabel("Admin Details")
        title.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {TEXT_PRIMARY};")
        v.addWidget(title)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {BORDER}; border: none;")
        v.addWidget(sep)
        v.addSpacing(4)

        def row(label, value, color=None):
            l = QLabel(label)
            l.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {TEXT_MUTED}; text-transform: uppercase; letter-spacing: 0.5px;")
            v.addWidget(l)
            val = QLabel(str(value))
            val.setStyleSheet(f"font-size: 14px; color: {color or TEXT_PRIMARY}; font-weight: 600;")
            v.addWidget(val)
            v.addSpacing(8)

        row("Full Name", full_name)
        row("Username", username)
        row("Branch", branch or "—")
        row("Status", "Active" if is_active else "Deactivated",
            color=(SUCCESS_GREEN if is_active else DANGER_RED))

        v.addSpacing(4)
        btn_close = QPushButton("Close")
        btn_close.setFixedHeight(38)
        btn_close.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PAGE}; color: {TEXT_PRIMARY};
                border: 1px solid {BORDER}; border-radius: 8px;
                font-size: 13px; font-weight: 600;
            }}
            QPushButton:hover {{ background-color: {BORDER}; }}
        """)
        btn_close.clicked.connect(self.accept)
        v.addWidget(btn_close)


class EditSystemDialog(QDialog):
    """Admin dialog to edit a registered system's full name, system
    username, and (optionally) password."""

    def __init__(self, parent, user_id, full_name, username, branch=None):
        super().__init__(parent)
        self.user_id = user_id
        self.setWindowTitle("Edit System")
        self.setFixedWidth(360)
        self.setStyleSheet(f"background-color: {BG_WHITE};")

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 22)
        v.setSpacing(10)

        title = QLabel("Edit System")
        title.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {TEXT_PRIMARY};")
        v.addWidget(title)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {BORDER}; border: none;")
        v.addWidget(sep)
        v.addSpacing(2)

        def field_lbl(txt):
            l = QLabel(txt)
            l.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {TEXT_MUTED}; text-transform: uppercase; letter-spacing: 0.5px;")
            return l

        v.addWidget(field_lbl("Full Name"))
        self.inp_name = QLineEdit(full_name)
        self.inp_name.setFixedHeight(42)
        v.addWidget(self.inp_name)

        v.addWidget(field_lbl("Hostname"))
        self.inp_user = QLineEdit(username)
        self.inp_user.setFixedHeight(42)
        v.addWidget(self.inp_user)
        v.addSpacing(2)

        v.addWidget(field_lbl("Branch"))
        self.inp_branch = QComboBox()
        self.inp_branch.setFixedHeight(42)
        self.inp_branch.addItems(db.BRANCHES)
        if branch in db.BRANCHES:
            self.inp_branch.setCurrentText(branch)
        v.addWidget(self.inp_branch)

        v.addWidget(field_lbl("New Password"))
        self.inp_pass = _make_password_field("Leave blank to keep current password")
        v.addWidget(self.inp_pass)

        v.addSpacing(8)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setFixedHeight(38)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PAGE}; color: {TEXT_PRIMARY};
                border: 1px solid {BORDER}; border-radius: 8px;
                font-size: 13px; font-weight: 600;
            }}
            QPushButton:hover {{ background-color: {BORDER}; }}
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("Save Changes")
        btn_save.setFixedHeight(38)
        btn_save.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_BLUE}; color: white;
                border: none; border-radius: 8px;
                font-size: 13px; font-weight: 700;
            }}
            QPushButton:hover {{ background-color: #007DB8; }}
        """)
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_save)

        v.addLayout(btn_row)

    def _save(self):
        name = self.inp_name.text().strip()
        user = self.inp_user.text().strip()
        password = self.inp_pass.text().strip()

        if not name or not user:
            gui_utils.show_warning(self, "Validation", "Full name and system username are required.")
            return

        ok, msg = db.update_system(self.user_id, user, name, password or None,
                                    branch=self.inp_branch.currentText())
        if ok:
            self.accept()
        else:
            gui_utils.show_warning(self, "Error", msg)


# ── Background DB worker (keeps UI thread free) ──────────────────────────────
class _DbWorker(QObject):
    """Runs ALL heavy DB fetches for one refresh cycle off the UI thread and
    signals the results back. Everything the periodic refresh needs to
    render — including the currently-selected system's ports/logs and the
    systems list — is fetched here so _on_refresh_data() never has to call
    db.* itself on the UI thread."""
    result_ready = pyqtSignal(object, object, object, object, object, object, object)
    # ports, reqs, slots, user_ports, logs, systems, unregistered
    finished     = pyqtSignal()

    def __init__(self, user_id=None, viewer_role=None, viewer_branch=None, viewer_id=None):
        super().__init__()
        self.user_id = user_id
        self.viewer_role = viewer_role
        self.viewer_branch = viewer_branch
        self.viewer_id = viewer_id

    def run(self):
        try:
            ports        = db.get_all_ports()
            reqs         = db.get_pending_requests(self.viewer_role, self.viewer_branch)
            slots        = db.get_pending_slot_requests(self.viewer_role, self.viewer_branch)
            user_ports   = db.get_ports_by_user(self.user_id) if self.user_id is not None else []
            logs         = db.get_audit_logs(user_id=self.user_id, limit=5)
            systems      = db.get_all_systems(self.viewer_role, self.viewer_branch, self.viewer_id)
            unregistered = db.get_unregistered_agents()
        except Exception:
            ports, reqs, slots, user_ports, logs, systems, unregistered = [], [], [], [], [], [], []
        self.result_ready.emit(ports, reqs, slots, user_ports, logs, systems, unregistered)
        self.finished.emit()


# ── Toggle worker (runs DB + registry off the UI thread) ─────────────────────
class _ToggleWorker(QObject):
    """Executes a port-toggle DB write and optional registry call on a worker
    thread, then signals success/failure back to the UI thread."""
    finished = pyqtSignal(bool)   # True = db update succeeded

    def __init__(self, port_id, field, value, user_id, user_name):
        super().__init__()
        self._port_id   = port_id
        self._field     = field
        self._value     = value
        self._user_id   = user_id
        self._user_name = user_name

    def run(self):
        ok = db.update_port_toggle(
            self._port_id, self._field, int(self._value),
            changed_by_id=self._user_id,
            changed_by_name=self._user_name
        )
        try:
            conn = db.get_conn()
            c    = conn.cursor()
            c.execute("SELECT port_key, device_id FROM usb_ports WHERE id=%s", (self._port_id,))
            row  = c.fetchone()
            conn.close()
            if row:
                port_key, dev_ids_str = row
                device_ids = dev_ids_str.split(",") if dev_ids_str else []
                import registry_utils
                if self._field == "port_enabled":
                    registry_utils.set_port_enabled(port_key, bool(int(self._value)), device_ids)
                elif self._field == "storage_enabled":
                    registry_utils.set_storage_enabled(port_key, bool(int(self._value)), device_ids)
        except Exception as e:
            print(f"Instant local apply skipped (expected if not running as admin): {e}")
        self.finished.emit(ok)


NAV_ACTIVE_STYLE = """
    QPushButton {
        background-color: #e3f4fc;
        color: #005a82;
        border: none;
        border-left: 4px solid #0099DC;
        border-radius: 0px;
        border-top-right-radius: 8px;
        border-bottom-right-radius: 8px;
        text-align: left;
        padding: 0 0 0 18px;
        font-size: 13px;
        font-weight: 700;
        margin: 0 10px 0 0;
    }
"""
NAV_IDLE_STYLE = """
    QPushButton {
        background-color: transparent;
        color: #5b7892;
        border: none;
        border-left: 4px solid transparent;
        border-radius: 0px;
        border-top-right-radius: 8px;
        border-bottom-right-radius: 8px;
        text-align: left;
        padding: 0 0 0 18px;
        font-size: 13px;
        font-weight: 500;
        margin: 0 10px 0 0;
    }
    QPushButton:hover {
        background-color: #f3f8fd;
        color: #007DB8;
        border-left-color: #dff1fb;
    }
"""


class ViewAllActivityDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Global Activity Log")
        self.resize(700, 540)
        self.setStyleSheet(f"background-color: {BG_PAGE};")
        
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 20)
        
        lbl = QLabel(f"Global Activity Log (All Users)")
        lbl.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {TEXT_PRIMARY};")
        v.addWidget(lbl)

        # --- Date filter + sort controls -----------------------------------
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        date_lbl = QLabel("Date:")
        date_lbl.setStyleSheet(f"font-size: 13px; color: {TEXT_MUTED};")
        filter_row.addWidget(date_lbl)

        self.date_filter = QComboBox()
        self.date_filter.setFixedWidth(150)
        self.date_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        filter_row.addWidget(self.date_filter)

        sort_lbl = QLabel("Sort:")
        sort_lbl.setStyleSheet(f"font-size: 13px; color: {TEXT_MUTED}; margin-left: 10px;")
        filter_row.addWidget(sort_lbl)

        self.sort_order = QComboBox()
        self.sort_order.addItems(["Newest First", "Oldest First"])
        self.sort_order.setFixedWidth(150)
        self.sort_order.setCursor(Qt.CursorShape.PointingHandCursor)
        filter_row.addWidget(self.sort_order)

        filter_row.addStretch()
        v.addLayout(filter_row)
        
        t = QTableWidget(0, 3)
        t.setHorizontalHeaderLabels(["Time", "User", "Action"])
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        t.setColumnWidth(0, 150)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        t.setColumnWidth(1, 200)
        
        t.setShowGrid(False)
        t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.verticalHeader().hide()
        t.setStyleSheet("QTableWidget { border: 1px solid " + BORDER + "; border-radius: 8px; background: " + BG_WHITE + "; }")
        self.table = t

        # Fetch + pre-parse all logs once; filtering/sorting happens in-memory.
        from datetime import datetime
        self._logs = []
        for log_row in db.get_audit_logs(user_id=None, limit=200):
            log_id, username, action, old_v, new_v, by_name, port_name, created_at = log_row
            try:
                dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
            except Exception:
                dt = None
            self._logs.append({
                "dt": dt,
                "created_at": created_at,
                "username": username,
                "action": action,
                "new_v": new_v,
                "port_name": port_name,
            })

        # Populate the date filter with unique dates found in the logs (newest first).
        unique_dates = sorted(
            {log["dt"].date() for log in self._logs if log["dt"] is not None},
            reverse=True,
        )
        self.date_filter.addItem("All Dates", None)
        for d in unique_dates:
            self.date_filter.addItem(d.strftime("%d-%b-%Y"), d)

        self.date_filter.currentIndexChanged.connect(self._refresh_table)
        self.sort_order.currentIndexChanged.connect(self._refresh_table)

        self._refresh_table()
        v.addWidget(t)
        
        btn = QPushButton("Close")
        btn.setFixedHeight(36)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"background: {ACCENT_BLUE}; color: white; border-radius: 6px; font-weight: bold; padding: 0 20px;")
        btn.clicked.connect(self.accept)
        
        h = QHBoxLayout()
        h.addStretch()
        h.addWidget(btn)
        v.addLayout(h)

    def _refresh_table(self):
        """Re-render the table according to the selected date filter + sort order."""
        import datetime as _dt
        t = self.table

        selected_date = self.date_filter.currentData()
        newest_first = (self.sort_order.currentIndex() == 0)

        rows = self._logs
        if selected_date is not None:
            rows = [log for log in rows if log["dt"] is not None and log["dt"].date() == selected_date]

        rows = sorted(
            rows,
            key=lambda log: log["dt"] if log["dt"] is not None else _dt.datetime.min,
            reverse=newest_first,
        )

        t.setRowCount(len(rows))
        for i, log in enumerate(rows):
            dt = log["dt"]
            if dt is not None:
                time_str = dt.strftime("%d-%b ") + dt.strftime("%I:%M %p").lstrip("0")
            else:
                time_str = log["created_at"]

            action = log["action"]
            new_v = log["new_v"]
            port_name = log["port_name"]
            action_str = action
            if action == "port_enabled" or action == "port_enable":
                action_str = f"{port_name} Enabled" if new_v == "1" else f"{port_name} Disabled"
            elif action == "storage_request_approved":
                action_str = "Storage Request Approved"
            elif action == "storage_request_denied":
                action_str = "Storage Request Denied"
            elif action == "slot_request_approved":
                action_str = "Slot Request Approved"
            elif action == "slot_request_denied":
                action_str = "Slot Request Denied"
            elif action == "storage_grant_expired":
                action_str = "Storage Access Expired"

            t.setRowHeight(i, 38)

            item_time = QTableWidgetItem(time_str)
            item_time.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_time.setForeground(QColor(TEXT_MUTED))

            # Since my db update, username is now the full name!
            item_user = QTableWidgetItem(log["username"])
            item_user.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            item_action = QTableWidgetItem(action_str)
            item_action.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_action.setForeground(QColor(TEXT_PRIMARY))
            f = item_action.font(); f.setBold(True); item_action.setFont(f)

            t.setItem(i, 0, item_time)
            t.setItem(i, 1, item_user)
            t.setItem(i, 2, item_action)

class AdminDashboard(QMainWindow):
    def __init__(self, user: dict):
        super().__init__()
        self.user = user
        self.setWindowTitle("Swift Optimizer — Admin")
        self.setMinimumSize(1100, 680)
        self._nav_buttons = []
        self._build_ui()
        self._load_systems_selector()
        self._load_ports()
        self._load_requests()
        self._load_systems()
        self._load_unregistered()

        self._bg_thread  = None   # background QThread
        self._bg_running = False  # guard: only one poll at a time

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._periodic_refresh)
        self._timer.start(1000)

        # If the window is dragged to a monitor with a different display
        # scaling than the one it was opened on (e.g. moved from a laptop
        # screen to an external monitor), Qt can leave stale column/font
        # metrics behind and rows overlap. Force a full re-layout + repaint
        # of every table whenever the window's screen actually changes.
        QTimer.singleShot(0, self._connect_screen_changed)

    def _connect_screen_changed(self):
        handle = self.windowHandle()
        if handle is not None:
            handle.screenChanged.connect(self._on_screen_changed)

    def _on_screen_changed(self, _screen):
        for table in self.findChildren(QTableWidget):
            table.resizeColumnsToContents()
            table.horizontalHeader().resizeSections(QHeaderView.ResizeMode.ResizeToContents)
            table.updateGeometry()
            table.viewport().update()
            table.repaint()
        self.repaint()

    def _periodic_refresh(self):
        """Fire a background DB fetch; never blocks the UI thread."""
        if self._bg_running:
            return   # previous poll still in progress — skip this tick

        self._bg_running = True
        thread = QThread(self)
        user_id = self.system_selector.currentData() if self.system_selector.currentIndex() >= 0 else None
        self._active_worker = _DbWorker(user_id, self.user.get("role"), self.user.get("branch"), self.user.get("id"))
        self._active_worker.moveToThread(thread)
        thread.started.connect(self._active_worker.run)
        self._active_worker.result_ready.connect(self._on_refresh_data)
        self._active_worker.finished.connect(thread.quit)
        self._active_worker.finished.connect(self._active_worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._reset_bg_flag)
        self._bg_thread = thread
        thread.start()

    def _reset_bg_flag(self):
        self._bg_running = False

    def _on_refresh_data(self, ports, reqs, slots, user_ports, logs, systems, unregistered=None):
        """Called on the UI thread with fresh data fetched in the background.
        Renders directly from these args — must NOT call db.* here, that
        would put a blocking DB round trip back on the UI thread every tick."""
        unregistered = unregistered or []
        current_hash = hash(str(ports) + str(reqs) + str(slots) + str(user_ports) + str(logs) + str(systems) + str(unregistered))
        if getattr(self, "_last_data_hash", None) == current_hash:
            return
        self._last_data_hash = current_hash

        self._update_alerts_badge(len(unregistered))
        self._render_unregistered_table(unregistered)

        total      = len(ports)
        active     = sum(1 for p in ports if p[6] == "ON")
        storage_on = sum(1 for p in ports if p[5])
        pending    = len(reqs) + len(slots)

        if "Total Ports" in self._stat_labels:
            self._stat_labels["Total Ports"].setText(str(total))
        if "Active Ports" in self._stat_labels:
            self._stat_labels["Active Ports"].setText(str(active))
        if "Pending Requests" in self._stat_labels:
            self._stat_labels["Pending Requests"].setText(str(pending))
        if "Storage Enabled" in self._stat_labels:
            self._stat_labels["Storage Enabled"].setText(str(storage_on))
            self._stat_labels["Storage Enabled"].setStyleSheet(
                f"font-size: 26px; font-weight: 700; color: {ACCENT_RED if storage_on else TEXT_MUTED}; background: transparent; border: none;"
            )

        self._render_ports_table(user_ports, logs)
        self._render_requests_table(reqs, slots)
        self._render_systems_table(systems)

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        central.setStyleSheet(f"background-color: {BG_PAGE};")
        main_h = QHBoxLayout(central)
        main_h.setContentsMargins(0, 0, 0, 0)
        main_h.setSpacing(0)

        main_h.addWidget(self._make_sidebar())

        # Right side: stacked pages
        right = QWidget()
        right.setStyleSheet(f"background-color: {BG_PAGE};")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        # Top bar (always visible)
        rv.addWidget(self._make_topbar())

        # Stacked pages
        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"background-color: {BG_PAGE};")
        self.stack.addWidget(self._make_page_ports())      # 0
        self.stack.addWidget(self._make_page_requests())   # 1
        self.stack.addWidget(self._make_page_systems())    # 2  ← Add Systems
        self.stack.addWidget(self._make_page_history())    # 3
        self.stack.addWidget(self._make_page_alerts())     # 4  ← Unregistered Agent Alerts
        rv.addWidget(self.stack, 1)

        main_h.addWidget(right, 1)

    def _make_topbar(self):
        bar = QFrame()
        bar.setFixedHeight(58)
        bar.setStyleSheet(HEADER_BAR_STYLE)
        h = QHBoxLayout(bar)
        h.setContentsMargins(24, 0, 24, 0)

        self.lbl_title = QLabel("")  # hidden but kept for _switch_page compatibility
        self.lbl_title.setVisible(False)

        h.addStretch()

        # Separator pipe
        sep_lbl = QLabel("|")
        sep_lbl.setStyleSheet("color: rgba(255,255,255,0.3); font-size: 16px; background: transparent; border: none;")

        user_lbl = QLabel(self.user['full_name'])
        user_lbl.setStyleSheet(
            "color: white; font-size: 13px; font-weight: 600; "
            "background: transparent; border: none; letter-spacing: 0.2px;"
        )
        h.addWidget(user_lbl)
        h.addSpacing(12)
        h.addWidget(sep_lbl)
        h.addSpacing(12)

        logout_btn = QPushButton("Logout")
        logout_btn.setFixedHeight(30)
        logout_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255,255,255,0.15);
                color: white;
                border: 1px solid rgba(255,255,255,0.25);
                border-radius: 6px;
                padding: 0 14px;
                font-size: 12px;
                font-weight: 600;
                letter-spacing: 0.3px;
            }
            QPushButton:hover { background-color: rgba(255,255,255,0.27); }
            QPushButton:pressed { background-color: rgba(255,255,255,0.12); }
        """)
        logout_btn.clicked.connect(self._logout)
        h.addWidget(logout_btn)
        return bar

    # ── Sidebar ──────────────────────────────────────────────────────────────
    def _make_sidebar(self):
        sb = QFrame()
        sb.setFixedWidth(240)
        sb.setStyleSheet(f"background-color: {BG_WHITE}; border-right: 1px solid {BORDER};")
        v = QVBoxLayout(sb)
        v.setContentsMargins(0, 24, 0, 0)
        v.setSpacing(10)

        # Logo
        logo_lbl = QLabel()
        logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_lbl.setStyleSheet("background: transparent; border: none;")
        pix = QPixmap()
        try:
            with open(LOGO_PATH, "rb") as f:
                pix.loadFromData(f.read())
        except Exception:
            pass
            
        if not pix.isNull():
            logo_lbl.setPixmap(pix.scaledToWidth(180, Qt.TransformationMode.SmoothTransformation))
        else:
            logo_lbl.setText("⚙  Swift Optimizer")
            logo_lbl.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 800; font-size: 18px; border: none;")
        v.addWidget(logo_lbl)
        v.addSpacing(32)

        is_super_admin = self.user.get("role") == "super_admin"

        nav_items = [
            ("Port Controls",   0),
            ("Access Requests", 1),
            ("Add Systems",     2),
            ("Request History", 3),
        ]
        if is_super_admin:
            # Agent Alerts is super-admin-only.
            nav_items.append(("Agent Alerts", 4))

        self._nav_buttons = []
        self._nav_base_labels = {}
        self._nav_idx_to_button = {}
        for text, idx in nav_items:
            btn = QPushButton(text)
            btn.setFixedHeight(40)
            btn.setStyleSheet(NAV_ACTIVE_STYLE if idx == 0 else NAV_IDLE_STYLE)
            btn.clicked.connect(lambda _, i=idx: self._switch_page(i))
            v.addWidget(btn)
            v.addSpacing(1)
            self._nav_buttons.append(btn)
            self._nav_base_labels[idx] = text
            self._nav_idx_to_button[idx] = btn
        self._alerts_nav_idx = 4

        v.addStretch()

        # User chip
        user_frame = QFrame()
        user_frame.setStyleSheet(f"background-color: transparent; border-top: 1px solid {BORDER}; border-right: none;")
        uf = QHBoxLayout(user_frame)
        uf.setContentsMargins(14, 10, 14, 10)
        avatar = QLabel("A")
        avatar.setFixedSize(34, 34)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet("background-color: #0099DC; color: white; border-radius: 17px; font-weight: 700; font-size: 14px;")
        uf.addWidget(avatar)
        uf.addSpacing(8)
        role_label = "Super Admin" if self.user.get("role") == "super_admin" else "Admin"
        uname = QLabel(f"{self.user['full_name']}\n{role_label}")
        uname.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 11px; background: transparent; border: none;")
        uf.addWidget(uname)
        uf.addStretch()
        v.addWidget(user_frame)
        return sb

    def _switch_page(self, idx: int):
        self.stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._nav_buttons):
            btn.setStyleSheet(NAV_ACTIVE_STYLE if i == idx else NAV_IDLE_STYLE)
        if idx == 0:
            self._load_systems_selector()
            self._load_ports()
        elif idx == 1:
            self._load_requests()
        elif idx == 2:
            self._load_systems()
        elif idx == 3:
            self._load_history()
        elif idx == 4:
            self._load_unregistered()

    # ── Pages ────────────────────────────────────────────────────────────────
    def _make_page_ports(self):
        page = QWidget()
        page.setStyleSheet(f"background-color: {BG_PAGE};")
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # (Banner removed as per user request)

        # Page Content
        content = QWidget()
        cv = QVBoxLayout(content)
        cv.setContentsMargins(32, 20, 32, 20)
        cv.setSpacing(12)

        cv.addLayout(self._make_stat_cards())

        # Filter: select system
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(10)
        lbl_select = QLabel("Select System / User:")
        lbl_select.setStyleSheet(f"font-weight: 600; font-size: 13px; color: {TEXT_PRIMARY};")
        
        self.system_selector = QComboBox()
        self.system_selector.setFixedHeight(34)
        self.system_selector.setMinimumWidth(220)
        self.system_selector.currentIndexChanged.connect(self._on_system_selected)
        
        filter_layout.addWidget(lbl_select)
        filter_layout.addWidget(self.system_selector)
        filter_layout.addStretch()
        
        self.port_count_lbl = QLabel("")
        self.port_count_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 13px; font-weight: 600;")
        filter_layout.addWidget(self.port_count_lbl)
        
        self.show_empty_cb = QCheckBox("Show empty / unplugged ports")
        self.show_empty_cb.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 13px; font-weight: 500; spacing: 8px;")
        self.show_empty_cb.setChecked(False)
        self.show_empty_cb.toggled.connect(self._load_ports)
        filter_layout.addWidget(self.show_empty_cb)
        
        cv.addLayout(filter_layout)

        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background-color: {BG_WHITE}; border: 1px solid {BORDER}; border-radius: 14px; }}")
        apply_shadow(card, blur=20, y=3, alpha=22)
        card_v = QVBoxLayout(card)
        card_v.setContentsMargins(20, 16, 20, 16)
        card_v.setSpacing(10)

        desc = QLabel("Toggle the physical USB port itself on/off for the selected system. "
                      "Whatever is plugged into a disabled port — keyboard, mouse, or storage — "
                      "is blocked; empty ports can be pre-disabled too.")
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setStyleSheet(f"border: none; color: {TEXT_MUTED}; font-size: 12px;")
        card_v.addWidget(desc)

        self.port_table = self._make_table(["Port", "Device Name", "Port Access", "Storage Access", "Status"])
        self.port_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.port_table.setColumnWidth(0, 100)
        self.port_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.port_table.setColumnWidth(1, 280)
        self.port_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.port_table.setColumnWidth(2, 110)
        self.port_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.port_table.setColumnWidth(3, 130)
        self.port_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        card_v.addWidget(self.port_table)
        
        cv.addWidget(card)
        
        # --- Recent Activity Card ---
        act_card = QFrame()
        act_card.setStyleSheet(f"QFrame {{ background-color: {BG_WHITE}; border: 1px solid {BORDER}; border-radius: 14px; }}")
        apply_shadow(act_card, blur=20, y=3, alpha=22)
        act_v = QVBoxLayout(act_card)
        act_v.setContentsMargins(20, 16, 20, 16)
        act_v.setSpacing(10)
        
        act_hdr = QHBoxLayout()
        act_hdr.setContentsMargins(0,0,0,0)
        act_lbl = QLabel("Recent Activity")
        act_lbl.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {TEXT_PRIMARY}; border: none; background: transparent;")
        act_hdr.addWidget(act_lbl)
        act_hdr.addStretch()
        
        clear_log_btn = QPushButton("Clear History")
        clear_log_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_log_btn.setStyleSheet(f"border: none; background: transparent; color: {TEXT_MUTED}; font-weight: 600; font-size: 12px; margin-right: 15px;")
        clear_log_btn.clicked.connect(self._clear_audit_logs)
        act_hdr.addWidget(clear_log_btn)
        
        view_all_btn = QPushButton("View All >")
        view_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        view_all_btn.setStyleSheet(f"border: none; background: transparent; color: {ACCENT_BLUE}; font-weight: 700; font-size: 12px;")
        view_all_btn.clicked.connect(self._show_all_activity)
        act_hdr.addWidget(view_all_btn)
        
        act_v.addLayout(act_hdr)
        
        self.activity_table = self._make_table(["Time", "User", "Action"])
        self.activity_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.activity_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.activity_table.setColumnWidth(0, 150)
        self.activity_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.activity_table.setColumnWidth(1, 200)
        # Limit height so it doesn't push everything up too much
        self.activity_table.setFixedHeight(180) 
        
        act_v.addWidget(self.activity_table)
        cv.addWidget(act_card)
        
        cv.addStretch()

        v.addWidget(content, 1)

        return page

    def _make_page_requests(self):
        page = QWidget()
        page.setStyleSheet(f"background-color: {BG_PAGE};")
        v = QVBoxLayout(page)
        v.setContentsMargins(32, 20, 32, 20)
        v.setSpacing(12)

        # ── Device Access Requests ──
        hdr = QLabel("Pending Access Requests")
        hdr.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {TEXT_PRIMARY};")
        v.addWidget(hdr)

        self.pending_lbl = QLabel("Review and act on user requests for USB file transfer access.")
        self.pending_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        v.addWidget(self.pending_lbl)

        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background-color: {BG_WHITE}; border: 1px solid {BORDER}; border-radius: 14px; }}")
        apply_shadow(card, blur=20, y=3, alpha=22)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(20, 16, 20, 16)

        self.req_table = self._make_table(["User", "Username", "Port", "Device", "Reason", "Requested", "Actions"])
        self.req_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.req_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.req_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.req_table.setColumnWidth(6, 185)
        cv.addWidget(self.req_table)
        v.addWidget(card)

        # ── Slot Access Requests ──
        v.addSpacing(16)
        
        slot_hdr = QLabel("Pending Slot Access Requests")
        slot_hdr.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {TEXT_PRIMARY};")
        v.addWidget(slot_hdr)
        
        slot_desc = QLabel("Users requesting access to empty/available USB ports.")
        slot_desc.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        v.addWidget(slot_desc)
        
        slot_card = QFrame()
        slot_card.setStyleSheet(f"QFrame {{ background-color: {BG_WHITE}; border: 1px solid {BORDER}; border-radius: 14px; }}")
        apply_shadow(slot_card, blur=20, y=3, alpha=22)
        slot_cv = QVBoxLayout(slot_card)
        slot_cv.setContentsMargins(20, 16, 20, 16)
        
        self.slot_req_table = self._make_table(["User", "Username", "Slot", "Reason", "Requested", "Actions"])
        self.slot_req_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.slot_req_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.slot_req_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.slot_req_table.setColumnWidth(5, 185)
        slot_cv.addWidget(self.slot_req_table)
        v.addWidget(slot_card)

        return page

    def _make_page_systems(self):
        """Add Systems page: left form to add, right list of registered systems with View Dashboard button."""
        page = QWidget()
        page.setStyleSheet(f"background-color: {BG_PAGE};")
        main_v = QVBoxLayout(page)
        main_v.setContentsMargins(32, 20, 32, 20)
        main_v.setSpacing(16)

        # Page header
        hdr_row = QHBoxLayout()
        hdr = QLabel("System Management")
        hdr.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {TEXT_PRIMARY};")
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()
        main_v.addLayout(hdr_row)

        sub = QLabel("Add new systems and manage existing ones. Click 'View Dashboard' to open a system's user portal.")
        sub.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        main_v.addWidget(sub)

        layout = QHBoxLayout()
        layout.setSpacing(20)

        # ── Left: Add System Form ──
        left_card = QFrame()
        left_card.setFixedWidth(310)
        left_card.setStyleSheet(f"QFrame {{ background-color: {BG_WHITE}; border: 1px solid {BORDER}; border-radius: 14px; }}")
        apply_shadow(left_card, blur=20, y=3, alpha=22)
        left_v = QVBoxLayout(left_card)
        left_v.setContentsMargins(22, 22, 22, 22)
        left_v.setSpacing(12)

        form_title = QLabel("Add New System")
        form_title.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {TEXT_PRIMARY}; border: none; background: transparent;")
        left_v.addWidget(form_title)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {BORDER}; border: none;")
        left_v.addWidget(sep)
        left_v.addSpacing(4)

        def field_lbl(txt):
            l = QLabel(txt)
            l.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {TEXT_MUTED}; border: none; background: transparent; text-transform: uppercase; letter-spacing: 0.5px;")
            return l

        is_super_admin = self.user.get("role") == "super_admin"

        # Account Type — super admin only. Normal admins always add employees.
        self.inp_sys_type = QComboBox()
        self.inp_sys_type.setFixedHeight(42)
        if is_super_admin:
            left_v.addWidget(field_lbl("Account Type"))
            self.inp_sys_type.addItems(["Employee", "Admin"])
            self.inp_sys_type.currentTextChanged.connect(self._on_sys_type_changed)
            left_v.addWidget(self.inp_sys_type)
        else:
            self.inp_sys_type.addItem("Employee")

        left_v.addWidget(field_lbl("Full Name"))
        self.inp_sys_name = QLineEdit()
        self.inp_sys_name.setPlaceholderText("Enter Name")
        self.inp_sys_name.setFixedHeight(42)
        left_v.addWidget(self.inp_sys_name)

        left_v.addWidget(field_lbl("Branch"))
        self.inp_sys_branch = QComboBox()
        self.inp_sys_branch.setFixedHeight(42)
        self.inp_sys_branch.addItems(db.BRANCHES)
        if not is_super_admin:
            # Normal admins can only add to their own branch — default to
            # it and lock the field so it can't be changed.
            own_branch = self.user.get("branch")
            if own_branch in db.BRANCHES:
                self.inp_sys_branch.setCurrentText(own_branch)
            self.inp_sys_branch.setEnabled(False)
        left_v.addWidget(self.inp_sys_branch)

        self.lbl_sys_user = field_lbl("Hostname")
        left_v.addWidget(self.lbl_sys_user)
        self.inp_sys_user = QLineEdit()
        self.inp_sys_user.setPlaceholderText("Enter Hostname")
        self.inp_sys_user.setFixedHeight(42)
        left_v.addWidget(self.inp_sys_user)

        left_v.addSpacing(4)
        left_v.addWidget(field_lbl("Password"))
        self.inp_sys_pass = _make_password_field("Set a password")
        left_v.addWidget(self.inp_sys_pass)

        left_v.addSpacing(6)
        btn_add = QPushButton("＋  Add System")
        btn_add.setFixedHeight(40)
        btn_add.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_BLUE};
                color: white; border: none; border-radius: 8px;
                font-size: 13px; font-weight: 700;
            }}
            QPushButton:hover {{ background-color: #007DB8; }}
        """)
        btn_add.clicked.connect(self._add_system)
        left_v.addWidget(btn_add)

        # Info note
        left_v.addSpacing(12)
        note = QLabel("ℹ️  After adding a system, install and run the USB Agent on that PC.\n\nThe agent will auto-detect real USB devices and register them here. Keyboard and mouse are allowed by default. Storage is blocked.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; background: #f0f4fa; border-radius: 6px; padding: 10px; border: none;")
        left_v.addWidget(note)

        left_v.addStretch()
        layout.addWidget(left_card)

        # ── Right: Systems List ──
        right_card = QFrame()
        right_card.setStyleSheet(f"QFrame {{ background-color: {BG_WHITE}; border: 1px solid {BORDER}; border-radius: 14px; }}")
        apply_shadow(right_card, blur=20, y=3, alpha=22)
        right_v = QVBoxLayout(right_card)
        right_v.setContentsMargins(20, 20, 20, 20)
        right_v.setSpacing(12)

        list_title_row = QHBoxLayout()
        list_title = QLabel("Registered Systems")
        list_title.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {TEXT_PRIMARY}; border: none; background: transparent;")
        list_title_row.addWidget(list_title)
        list_title_row.addStretch()
        
        self.sys_search = QLineEdit()
        self.sys_search.setPlaceholderText("🔍 Search systems...")
        self.sys_search.setFixedWidth(170)
        self.sys_search.setFixedHeight(32)
        self.sys_search.setStyleSheet(f"""
            QLineEdit {{
                background-color: #f0f4f8;
                border: 1px solid #d1d9e6;
                border-radius: 17px;
                padding: 0 16px;
                font-size: 13px;
                color: {TEXT_PRIMARY};
            }}
            QLineEdit:focus {{
                background-color: {BG_WHITE};
                border: 1.5px solid {ACCENT_BLUE};
            }}
        """)
        self.sys_search.textChanged.connect(self._filter_systems)
        list_title_row.addWidget(self.sys_search)

        # Branch filter dropdown — super admin only. Same height, same
        # pill radius, and same background/border as the search box next
        # to it, so the two read as one matched control group instead of
        # a rounded field butted up against a square one.
        self.sys_branch_filter = None
        if is_super_admin:
            # Wider than a typical 8-10px gap on purpose: at fractional DPI
            # scale factors (125%/150%, common on external monitors) Qt can
            # round a small addSpacing value down to almost nothing, which is
            # what made these two controls look like one merged pill on a
            # scaled monitor while looking fine on a 100%-scale laptop panel.
            list_title_row.addSpacing(24)
            self.sys_branch_filter = QComboBox()
            self.sys_branch_filter.setFixedHeight(32)
            self.sys_branch_filter.setFixedWidth(160)
            self.sys_branch_filter.setStyleSheet(f"""
                QComboBox {{
                    background-color: #f0f4f8;
                    border: 1px solid #d1d9e6;
                    border-radius: 17px;
                    padding: 0 16px;
                    font-size: 13px;
                    color: {TEXT_PRIMARY};
                }}
                QComboBox:focus {{
                    background-color: {BG_WHITE};
                    border: 1.5px solid {ACCENT_BLUE};
                }}
                QComboBox::drop-down {{
                    border: none;
                    width: 26px;
                }}
                QComboBox QAbstractItemView {{
                    background: {BG_WHITE};
                    border: 1px solid {BORDER};
                    selection-background-color: {ACCENT_BLUE};
                    selection-color: white;
                    font-size: 13px;
                }}
            """)
            self.sys_branch_filter.addItem("All Branches")
            self.sys_branch_filter.currentIndexChanged.connect(self._filter_systems_branch)
            list_title_row.addWidget(self.sys_branch_filter)

        list_title_row.addSpacing(14)

        self.systems_count_lbl = QLabel("")
        self.systems_count_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px; border: none; background: transparent;")
        list_title_row.addWidget(self.systems_count_lbl)
        right_v.addLayout(list_title_row)

        if is_super_admin:
            cols = ["Full Name", "Username", "Role", "Branch", "Actions"]
        else:
            cols = ["Full Name", "Hostname", "View"]
        self._sys_col = {name: i for i, name in enumerate(cols)}

        self.systems_table = self._make_table(cols)
        header = self.systems_table.horizontalHeader()

        # Full Name auto-sizes to fit its own content — people's names are
        # usually short, so this doesn't need to compete for space.
        # Username/Hostname is the ONLY stretch column, so it gets all of
        # whatever's left over — these tend to be the longest values
        # (e.g. "DESKTOP-B6PTH6Q"), so they're the ones that actually need
        # the room.
        stretch_cols = ["Username" if is_super_admin else "Hostname"]
        for name in cols:
            mode = (QHeaderView.ResizeMode.Stretch if name in stretch_cols
                    else QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(self._sys_col[name], mode)

        header.setMinimumSectionSize(60)
        header.setStretchLastSection(False)

        # Long usernames/hostnames (e.g. "DESKTOP-B6PTH6Q") wrap onto a
        # second line instead of getting cut off with "..." — paired with
        # the taller row height in _render_systems_table() so the second
        # line has room to show.
        self.systems_table.setWordWrap(True)

        right_v.addWidget(self.systems_table)

        layout.addWidget(right_card, 1)
        main_v.addLayout(layout, 1)
        return page

    def _make_page_history(self):
        page = QWidget()
        page.setStyleSheet(f"background-color: {BG_PAGE};")
        v = QVBoxLayout(page)
        v.setContentsMargins(32, 20, 32, 20)
        v.setSpacing(12)

        hdr_row = QHBoxLayout()
        hdr = QLabel("Request History")
        hdr.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {TEXT_PRIMARY};")
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()

        clear_btn = QPushButton("Clear History")
        clear_btn.setFixedHeight(34)
        clear_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {DANGER_RED};
                color: white; border: none; border-radius: 8px;
                font-size: 12px; font-weight: 600; padding: 0 16px;
            }}
            QPushButton:hover {{ background-color: {DANGER_RED_HOVER}; }}
        """)
        clear_btn.clicked.connect(self._clear_history)
        hdr_row.addWidget(clear_btn)
        v.addLayout(hdr_row)

        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background-color: {BG_WHITE}; border: 1px solid {BORDER}; border-radius: 14px; }}")
        apply_shadow(card, blur=20, y=3, alpha=22)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(20, 16, 20, 16)

        self.hist_table = self._make_table(["User", "Port", "Reason", "Status", "Requested", "Resolved"])
        self.hist_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.hist_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.hist_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.hist_table.setColumnWidth(3, 140)
        cv.addWidget(self.hist_table)
        v.addWidget(card)
        return page

    def _make_page_alerts(self):
        """Alerts page: agents that have checked in with a username that
        doesn't match any registered system — usually a new/repaired PC
        whose account name doesn't match the DB yet, or a WMI/username
        resolution failure on the agent side."""
        page = QWidget()
        page.setStyleSheet(f"background-color: {BG_PAGE};")
        v = QVBoxLayout(page)
        v.setContentsMargins(32, 20, 32, 20)
        v.setSpacing(12)

        hdr_row = QHBoxLayout()
        hdr = QLabel("Unregistered Agent Alerts")
        hdr.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {TEXT_PRIMARY};")
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()
        v.addLayout(hdr_row)

        sub = QLabel("These PCs are running the USB Agent but their hostname doesn't match "
                     "any registered system. Add them as a system (using the exact hostname shown), "
                     "or dismiss the alert if it's a stray/decommissioned machine.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        v.addWidget(sub)

        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background-color: {BG_WHITE}; border: 1px solid {BORDER}; border-radius: 14px; }}")
        apply_shadow(card, blur=20, y=3, alpha=22)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(20, 16, 20, 16)

        self.unreg_table = self._make_table(
            ["Hostname", "First Seen", "Last Seen", "Add", "Dismiss"]
        )
        header = self.unreg_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.unreg_table.setColumnWidth(3, 56)
        self.unreg_table.setColumnWidth(4, 72)
        cv.addWidget(self.unreg_table)
        v.addWidget(card)
        return page

    # ── Stat cards ───────────────────────────────────────────────────────────
    def _make_stat_cards(self):
        h = QHBoxLayout()
        h.setSpacing(16)
        ports = db.get_all_ports()
        total = len(ports)
        active = sum(1 for p in ports if p[6] == "ON")
        pending = len(db.get_pending_requests(self.user.get("role"), self.user.get("branch")))
        storage_on = sum(1 for p in ports if p[5])

        # All cards stay blue/white — meaning is carried by shade, not hue.
        cards = [
            ("Total Ports",      str(total),      ACCENT_BLUE),
            ("Active Ports",     str(active),     ACCENT_BLUE_DARK),
            ("Pending Requests", str(pending),    ACCENT_BLUE2),
            ("Storage Enabled",  str(storage_on), ACCENT_BLUE_DARK if storage_on else TEXT_MUTED),
        ]
        self._stat_labels = {}
        for label, val, color in cards:
            card = QFrame()
            card.setStyleSheet(f"""
                QFrame {{
                    background-color: {BG_WHITE};
                    border: 1px solid {BORDER};
                    border-left: 4px solid {color};
                    border-radius: 12px;
                }}
            """)
            apply_shadow(card, blur=18, y=3, alpha=28)
            cv = QVBoxLayout(card)
            cv.setContentsMargins(20, 16, 20, 16)
            cv.setSpacing(6)
            # Small colored square indicator bar at top
            indicator = QLabel()
            indicator.setFixedSize(28, 4)
            indicator.setStyleSheet(
                f"background-color: {color}; border-radius: 2px; border: none;"
            )
            cv.addWidget(indicator)
            cv.addSpacing(4)
            val_lbl = QLabel(val)
            val_lbl.setStyleSheet(
                f"font-size: 28px; font-weight: 700; color: {color}; "
                f"background: transparent; border: none; letter-spacing: -0.5px;"
            )
            cv.addWidget(val_lbl)
            name_lbl = QLabel(label)
            name_lbl.setStyleSheet(
                f"font-size: 11px; font-weight: 600; color: {TEXT_MUTED}; "
                f"background: transparent; border: none; letter-spacing: 0.3px;"
            )
            cv.addWidget(name_lbl)
            self._stat_labels[label] = val_lbl
            h.addWidget(card)
        return h

    # ── Table factory ────────────────────────────────────────────────────────
    def _make_table(self, cols):
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.setAlternatingRowColors(True)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.setShowGrid(False)
        t.setMinimumHeight(200)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        return t

    # ── Data ─────────────────────────────────────────────────────────────────
    def _load_systems_selector(self):
        curr_idx = self.system_selector.currentIndex()
        curr_data = self.system_selector.currentData() if curr_idx >= 0 else None
        
        self.system_selector.blockSignals(True)
        self.system_selector.clear()
        systems = db.get_all_systems(self.user.get("role"), self.user.get("branch"), self.user.get("id"))
        for sys_item in systems:
            role_tag = "  [Admin]" if sys_item.get("role") == "admin" else ""
            self.system_selector.addItem(
                f"{sys_item['full_name']} ({sys_item['username']}){role_tag}",
                userData=sys_item['id']
            )
        
        if curr_data:
            index = self.system_selector.findData(curr_data)
            if index >= 0:
                self.system_selector.setCurrentIndex(index)
            elif self.system_selector.count() > 0:
                self.system_selector.setCurrentIndex(0)
        elif self.system_selector.count() > 0:
            self.system_selector.setCurrentIndex(0)
            
        self.system_selector.blockSignals(False)

    def _on_system_selected(self):
        self._load_ports()

    def _clear_audit_logs(self):
        reply = gui_utils.confirm_action(self, "Clear History", "Are you sure you want to delete all activity history?\n\nThis cannot be undone.")
        if reply:
            db.clear_audit_logs()
            self._load_ports()

    def _show_all_activity(self):
        dialog = ViewAllActivityDialog(self)
        dialog.exec()

    def _load_ports(self, *args):
        """Fetch from the DB (blocking) and render. Only call this from
        explicit user actions (init, selector change, after approve/reject) —
        never from the 1-second timer path, which uses _render_ports_table()
        with data already fetched on a background thread."""
        curr_idx = self.system_selector.currentIndex()
        if curr_idx < 0:
            self.port_table.setRowCount(0)
            return
        user_id = self.system_selector.currentData()
        ports = db.get_ports_by_user(user_id)
        logs = db.get_audit_logs(user_id=user_id, limit=5)
        self._render_ports_table(ports, logs)

    def _render_ports_table(self, ports, logs):
        """Pure render — no DB calls. Safe to call from the UI thread with
        data that was fetched on a background thread."""
        t = self.port_table
        t.setRowCount(0)

        curr_row = 0
        for port in ports:
            pid, port_name, device_name, kbd_en, mse_en, stor_en, status, port_key, port_en, transfer_expires_at, connected, device_type, storage_admin_set = port
            
            is_empty = device_name in ("- Empty -", "Empty Slot", "", None)
            
            # Hide empty/unplugged ports unless the user explicitly checks the box to show them.
            if is_empty and not self.show_empty_cb.isChecked():
                continue

            t.insertRow(curr_row)
            t.setRowHeight(curr_row, 52)
            t.setItem(curr_row, 0, self._cell(port_name, bold=True))

            if is_empty:
                display_name = "— no device connected —"
                name_item = self._cell(display_name)
                f = name_item.font()
                f.setItalic(True)
                name_item.setFont(f)
                name_item.setForeground(QColor("#8aabca"))
            else:
                display_name = device_name
                name_item = self._cell(display_name)

            t.setItem(curr_row, 1, name_item)

            # Admin can toggle ALL ports (empty and occupied).
            port_t = ToggleSwitch(bool(port_en))
            port_t.setProperty("port_id", pid)
            port_t.setToolTip("Enable/Disable this physical port")
            port_t.toggled.connect(lambda val, _pid=pid: self._on_toggle(
                _pid, "port_enabled", val, "port"
            ))
            t.setCellWidget(curr_row, 2, self._center(port_t))

            # Storage Access toggle — a persistent per-port policy, so it's
            # available on empty ports too (to pre-authorize before a
            # device is plugged in) as well as on occupied ports. It's
            # only hidden when the port is occupied by a non-storage
            # device, since flipping it there wouldn't affect anything
            # currently connected (it still applies to a storage device
            # plugged in later — this policy lives on the port, not the
            # occupant).
            is_storage = (device_type or "").strip().lower() == "storage"
            if is_empty or is_storage:
                stor_t = ToggleSwitch(bool(stor_en))
                stor_t.setProperty("port_id", pid)
                stor_t.setToolTip("Allow storage devices (pen drives, HDDs) on this port")
                stor_t.toggled.connect(lambda val, _pid=pid: self._on_toggle(
                    _pid, "storage_enabled", val, "storage"
                ))
                t.setCellWidget(curr_row, 3, self._center(stor_t))
            else:
                na_lbl = QLabel("—")
                na_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px; background: transparent;")
                t.setCellWidget(curr_row, 3, self._center(na_lbl))

            # An empty port is always "Off" — nothing there to allow or
            # block. A plugged-in device shows Allowed/Blocked based on the
            # admin's toggle instead of a bare On/Off.
            if is_empty:
                badge_status = "OFF"
            else:
                badge_status = "Allowed" if port_en else "Blocked"
            t.setCellWidget(curr_row, 4, self._center(status_badge(badge_status)))
            
            curr_row += 1

        total_ports = len(ports)
        plugged_in = sum(1 for p in ports if p[2] not in ("- Empty -", "Empty Slot", "", None) and p[10])
        self.port_count_lbl.setText(f"Total External Ports: {total_ports} ({plugged_in} plugged in)")

        # Load recent activity table
        act_t = self.activity_table
        act_t.setShowGrid(False) # Remove messy grid lines
        act_t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        act_t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        act_t.verticalHeader().hide()
        act_t.setStyleSheet(act_t.styleSheet() + " QTableWidget { border: none; }")
        
        # We don't need a scrollbar for 5 items, let's size it perfectly
        act_t.setFixedHeight(230) 
        act_t.setRowCount(0)
        
        # Get the full name from the selector instead of the raw hostname
        sys_text = self.system_selector.currentText()
        user_display_name = sys_text.split(" (")[0] if " (" in sys_text else sys_text

        for i, log_row in enumerate(logs):
            log_id, username, action, old_v, new_v, by_name, port_name, created_at = log_row
            
            # Format time as '27-Jun 2:04 PM'
            from datetime import datetime
            try:
                dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
                time_str = dt.strftime("%d-%b ") + dt.strftime("%I:%M %p").lstrip("0")
            except Exception:
                time_str = created_at
                
            # Format action nicely
            action_str = action
            if action == "port_enabled" or action == "port_enable":
                action_str = f"{port_name} Enabled" if new_v == "1" else f"{port_name} Disabled"
            elif action == "storage_request_approved":
                action_str = "Storage Request Approved"
            elif action == "storage_request_denied":
                action_str = "Storage Request Denied"
            elif action == "slot_request_approved":
                action_str = "Slot Request Approved"
            elif action == "slot_request_denied":
                action_str = "Slot Request Denied"
            elif action == "storage_grant_expired":
                action_str = "Storage Access Expired"
                
            act_t.insertRow(i)
            act_t.setRowHeight(i, 38)
            
            time_item = self._cell(time_str)
            time_item.setForeground(QColor(TEXT_MUTED))
            time_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            
            user_item = self._cell(user_display_name)
            user_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            
            action_item = self._cell(action_str, bold=True)
            action_item.setForeground(QColor(TEXT_PRIMARY))
            action_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            
            act_t.setItem(i, 0, time_item)
            act_t.setItem(i, 1, user_item)
            act_t.setItem(i, 2, action_item)

    def _load_requests(self):
        """Fetch from the DB (blocking) and render. Only for explicit user
        actions — the timer path uses _render_requests_table() instead."""
        reqs = db.get_pending_requests(self.user.get("role"), self.user.get("branch"))
        slot_reqs = db.get_pending_slot_requests(self.user.get("role"), self.user.get("branch"))
        self._render_requests_table(reqs, slot_reqs)

    def _render_requests_table(self, reqs, slot_reqs):
        """Pure render — no DB calls."""
        t = self.req_table
        t.setRowCount(0)
        for row_idx, req in enumerate(reqs):
            req_id, full_name, username, port_name, device_name, reason, req_at = req
            t.insertRow(row_idx)
            t.setRowHeight(row_idx, 52)
            t.setItem(row_idx, 0, self._cell(full_name, bold=True))
            t.setItem(row_idx, 1, self._cell(username))
            t.setItem(row_idx, 2, self._cell(port_name))
            t.setItem(row_idx, 3, self._cell(device_name))
            t.setItem(row_idx, 4, self._cell(reason or "—"))
            t.setItem(row_idx, 5, self._cell(req_at[:16]))

            act_w = QWidget()
            act_w.setStyleSheet("background: transparent;")
            ah = QHBoxLayout(act_w)
            ah.setContentsMargins(4, 4, 4, 4)
            ah.setSpacing(6)

            approve_btn = QPushButton("✓ Approve")
            approve_btn.setFixedHeight(30)
            approve_btn.setStyleSheet(f"""
                QPushButton {{ background-color: {SUCCESS_GREEN}; color: white; border: none; border-radius: 6px; font-size: 12px; font-weight: 600; padding: 0 10px; }}
                QPushButton:hover {{ background-color: {SUCCESS_GREEN_HOVER}; }}
            """)
            approve_btn.clicked.connect(lambda _, rid=req_id: self._resolve(rid, "approved"))

            reject_btn = QPushButton("✕ Reject")
            reject_btn.setFixedHeight(30)
            reject_btn.setStyleSheet(f"""
                QPushButton {{ background-color: {DANGER_RED}; color: white; border: none; border-radius: 6px; font-size: 12px; font-weight: 600; padding: 0 10px; }}
                QPushButton:hover {{ background-color: {DANGER_RED_HOVER}; }}
            """)
            reject_btn.clicked.connect(lambda _, rid=req_id: self._resolve(rid, "rejected"))

            ah.addWidget(approve_btn)
            ah.addWidget(reject_btn)
            t.setCellWidget(row_idx, 6, act_w)

        # ── Render slot requests ──
        self._render_slot_requests_table(slot_reqs)

        count = len(reqs) + len(slot_reqs)
        badge = f" ({count})" if count else ""
        self._nav_buttons[1].setText(f"Access Requests{badge}")
        if "Pending Requests" in self._stat_labels:
            self._stat_labels["Pending Requests"].setText(str(count))
        # NOTE: _load_history() intentionally NOT called here — it's its
        # own tab and loads its own data when selected (see _on_nav / tab
        # switch handler). Calling it on every _load_requests() refresh
        # (init, timer tick, after every approve/reject) was firing an
        # extra, unnecessary history query each time.

    def _load_slot_requests(self):
        """Fetch from the DB (blocking) and render, returning the count.
        Only for explicit user actions."""
        slot_reqs = db.get_pending_slot_requests(self.user.get("role"), self.user.get("branch"))
        self._render_slot_requests_table(slot_reqs)
        return len(slot_reqs)

    def _render_slot_requests_table(self, slot_reqs):
        """Pure render — no DB calls."""
        t = self.slot_req_table
        t.setRowCount(0)

        for row_idx, req in enumerate(slot_reqs):
            req_id, full_name, username, slot_name, reason, req_at = req
            t.insertRow(row_idx)
            t.setRowHeight(row_idx, 50)
            t.setItem(row_idx, 0, self._cell(full_name, bold=True))
            t.setItem(row_idx, 1, self._cell(username))
            t.setItem(row_idx, 2, self._cell(slot_name))
            t.setItem(row_idx, 3, self._cell(reason or "—"))
            t.setItem(row_idx, 4, self._cell(req_at[:16] if req_at else "—"))
            
            act_w = QWidget()
            act_w.setStyleSheet("background: transparent;")
            ah = QHBoxLayout(act_w)
            ah.setContentsMargins(4, 4, 4, 4)
            ah.setSpacing(6)
            
            approve_btn = QPushButton("✓ Approve")
            approve_btn.setFixedHeight(30)
            approve_btn.setStyleSheet(f"""
                QPushButton {{ background-color: {SUCCESS_GREEN}; color: white; border: none; border-radius: 6px; font-size: 12px; font-weight: 600; padding: 0 10px; }}
                QPushButton:hover {{ background-color: {SUCCESS_GREEN_HOVER}; }}
            """)
            approve_btn.clicked.connect(lambda _, rid=req_id: self._resolve_slot_request(rid, "approved"))
            
            reject_btn = QPushButton("✕ Reject")
            reject_btn.setFixedHeight(30)
            reject_btn.setStyleSheet(f"""
                QPushButton {{ background-color: {DANGER_RED}; color: white; border: none; border-radius: 6px; font-size: 12px; font-weight: 600; padding: 0 10px; }}
                QPushButton:hover {{ background-color: {DANGER_RED_HOVER}; }}
            """)
            reject_btn.clicked.connect(lambda _, rid=req_id: self._resolve_slot_request(rid, "rejected"))
            
            ah.addWidget(approve_btn)
            ah.addWidget(reject_btn)
            t.setCellWidget(row_idx, 5, act_w)

        return len(slot_reqs)

    def _resolve_slot_request(self, request_id, action):
        """Resolve a slot access request."""
        reply = gui_utils.confirm_action(self, "Confirm", 
            f"Are you sure you want to {action} this slot request?")
        if reply:
            db.resolve_slot_request(request_id, action, 
                                   changed_by_id=self.user["id"],
                                   changed_by_name=self.user["full_name"])
            self._load_slot_requests()
            self._load_requests()
            gui_utils.show_info(self, "Done", f"Slot request {action}.")

    def _load_systems(self):
        """Fetch from the DB (blocking) and render. Only for explicit user
        actions — the timer path uses _render_systems_table() instead."""
        systems = db.get_all_systems(self.user.get("role"), self.user.get("branch"), self.user.get("id"))
        self._render_systems_table(systems)

    def _render_systems_table(self, systems):
        """Pure render — no DB calls."""
        t = self.systems_table
        col = self._sys_col
        is_super_admin = self.user.get("role") == "super_admin"

        # Admins always appear before employees (0 = admin/super_admin, 1 = user)
        systems = sorted(systems, key=lambda s: 0 if s.get("role") in ("admin", "super_admin") else 1)

        t.setRowCount(0)
        for i, sys_item in enumerate(systems):
            t.insertRow(i)
            t.setRowHeight(i, 58)
            t.setItem(i, col["Full Name"], self._cell(sys_item['full_name'], bold=True))
            t.setItem(i, col["Username" if is_super_admin else "Hostname"], self._cell(sys_item['username']))

            is_active = sys_item.get('is_active', True)
            is_admin_row = sys_item.get('role') in ('admin', 'super_admin')
            is_own_row = sys_item['id'] == self.user.get('id')

            if is_super_admin:
                t.setItem(i, col["Role"], self._cell("Admin" if is_admin_row else "Employee"))
                t.setItem(i, col["Branch"], self._cell(sys_item.get('branch') or "—"))

            # View — everyone gets the USB dashboard view now
            # (including admins/super_admins, so super admins can configure
            # other admins' USB ports if they install the agent).
            view_btn = _icon_button("view", "View Dashboard", BG_PAGE, BORDER, fg=TEXT_PRIMARY, border=BORDER, icon_size=18, size=(32, 28))
            view_btn.clicked.connect(lambda _, uid=sys_item['id'], uname=sys_item['full_name'], uuser=sys_item['username']:
                                     self._open_user_dashboard(uid, uname, uuser))

            if not is_super_admin:
                t.setCellWidget(i, col["View"], self._center(view_btn))
                continue  # normal admins never get Edit/Delete/Activate

            # Edit — super admin only, for both admins and employees.
            btn_edit = _icon_button("edit", "Edit", BG_PAGE, BORDER, fg=TEXT_PRIMARY, border=BORDER, icon_size=18, size=(32, 28))
            btn_edit.clicked.connect(lambda _, uid=sys_item['id'], uname=sys_item['full_name'], uuser=sys_item['username'], ubranch=sys_item.get('branch'):
                                     self._edit_system(uid, uname, uuser, ubranch))

            # Delete — super admin only, for both admins and employees.
            btn_del = _icon_button("delete", "Delete", BG_PAGE, BORDER, fg=TEXT_PRIMARY, border=BORDER, icon_size=18, size=(32, 28))
            btn_del.clicked.connect(lambda _, uid=sys_item['id']: self._delete_system(uid))

            action_widgets = [view_btn, btn_edit, btn_del]

            # Activate/Deactivate — admin accounts only. Employee rows
            # don't get a spacer in its place: with only 3 real buttons,
            # centering just those 3 (rather than centering 3 buttons +
            # an invisible 4th slot) is what actually looks centered in
            # the Actions column.
            if is_admin_row:
                if is_active:
                    btn_toggle = _icon_button("dismiss", "Deactivate", BG_PAGE, BORDER, fg=TEXT_PRIMARY, border=BORDER, icon_size=18, size=(34, 28))
                else:
                    btn_toggle = _icon_button("add", "Activate", BG_PAGE, BORDER, fg=TEXT_PRIMARY, border=BORDER, icon_size=18, size=(34, 28))
                btn_toggle.clicked.connect(lambda _, uid=sys_item['id'], uname=sys_item['full_name'], active=is_active:
                                           self._toggle_admin_active(uid, uname, active))
                action_widgets.append(btn_toggle)

            t.setCellWidget(i, col["Actions"], self._action_group(action_widgets))


        count = len(systems)
        self.systems_count_lbl.setText(f"{count} system{'s' if count != 1 else ''} registered")

        # Repopulate branch filter dropdown with branches from current data
        if self.sys_branch_filter is not None:
            current_branch = self.sys_branch_filter.currentText()
            self.sys_branch_filter.blockSignals(True)
            self.sys_branch_filter.clear()
            self.sys_branch_filter.addItem("All Branches")
            branches_seen = []
            for s in systems:
                b = s.get("branch") or ""
                if b and b not in branches_seen:
                    branches_seen.append(b)
            branches_seen.sort()
            for b in branches_seen:
                self.sys_branch_filter.addItem(b)
            # Restore previous selection if still valid
            idx = self.sys_branch_filter.findText(current_branch)
            if idx >= 0:
                self.sys_branch_filter.setCurrentIndex(idx)
            self.sys_branch_filter.blockSignals(False)

    # ── Unregistered agent alerts ───────────────────────────────────────────
    def _load_unregistered(self):
        """Fetch from the DB (blocking) and render. Only for explicit user
        actions — the timer path uses _render_unregistered_table() instead."""
        rows = db.get_unregistered_agents()
        self._render_unregistered_table(rows)
        self._update_alerts_badge(len(rows))

    def _update_alerts_badge(self, count):
        """Puts a red '(N)' count on the Alerts nav button so unregistered
        agents are visible without having to open the page."""
        btn = getattr(self, "_nav_idx_to_button", {}).get(self._alerts_nav_idx)
        if btn is None:
            return  # normal admin — no Agent Alerts nav item to badge
        base = self._nav_base_labels.get(self._alerts_nav_idx, "Agent Alerts")
        btn.setText(f"{base} ({count})" if count else base)

    def _render_unregistered_table(self, rows):
        """Pure render — no DB calls."""
        if not hasattr(self, "unreg_table"):
            return
        t = self.unreg_table
        t.setRowCount(0)
        for i, r in enumerate(rows):
            t.insertRow(i)
            t.setRowHeight(i, 50)
            hostname = r["hostname"] or r["resolved_username"] or "—"
            t.setItem(i, 0, self._cell(hostname, bold=True))
            t.setItem(i, 1, self._cell(str(r["first_seen"])))
            t.setItem(i, 2, self._cell(str(r["last_seen"])))

            btn_add = _icon_button("add", "Add as System", BG_PAGE, BORDER, fg=TEXT_PRIMARY, border=BORDER, icon_size=18, size=(40, 32))
            btn_add.clicked.connect(lambda _, uname=hostname: self._prefill_add_system(uname))
            t.setCellWidget(i, 3, self._center(btn_add))

            btn_dismiss = _icon_button("dismiss", "Dismiss", BG_PAGE, BORDER, fg=TEXT_PRIMARY, border=BORDER, size=(40, 32))
            btn_dismiss.clicked.connect(lambda _, eid=r["id"]: self._dismiss_unregistered(eid))
            t.setCellWidget(i, 4, self._center(btn_dismiss))



    def _prefill_add_system(self, username):
        """Jump to the Add Systems page with the username pre-filled, so the
        admin just needs to add the full name + password."""
        self._switch_page(2)
        self.inp_sys_user.setText(username)
        self.inp_sys_name.setFocus()

    def _dismiss_unregistered(self, entry_id):
        db.dismiss_unregistered_agent(entry_id)
        self._load_unregistered()

    def _filter_systems(self, text=""):
        """Filter by text search and optional branch dropdown."""
        text = (text or self.sys_search.text()).lower()
        branch_filter = ""
        if self.sys_branch_filter is not None:
            sel = self.sys_branch_filter.currentText()
            if sel != "All Branches":
                branch_filter = sel.lower()

        t = self.systems_table
        visible_count = 0
        # Branch column index for super admin (index 3 in the super-admin column set)
        branch_col = self._sys_col.get("Branch", -1)
        for i in range(t.rowCount()):
            # Text match (Full Name or Username/Hostname columns)
            text_match = not text
            if not text_match:
                for j in range(2):
                    item = t.item(i, j)
                    if item and text in item.text().lower():
                        text_match = True
                        break

            # Branch match
            branch_match = True
            if branch_filter and branch_col >= 0:
                b_item = t.item(i, branch_col)
                branch_match = b_item is not None and branch_filter in b_item.text().lower()

            match = text_match and branch_match
            t.setRowHidden(i, not match)
            if match:
                visible_count += 1

        self.systems_count_lbl.setText(f"{visible_count} system{'s' if visible_count != 1 else ''} registered")

    def _filter_systems_branch(self, _index=0):
        """Called when branch dropdown selection changes."""
        self._filter_systems()

    def _view_admin(self, full_name, username, branch, is_active):
        dlg = AdminInfoDialog(self, full_name, username, branch, is_active)
        dlg.exec()

    def _open_user_dashboard(self, user_id: int, full_name: str, username: str):
        """Open a view of the user's dashboard from admin side (editable)."""
        from user_dashboard import UserDashboard
        
        # Prevent multiple windows by closing the existing one if it's open.
        # UserDashboard.closeEvent() stops its refresh timer and waits for
        # any in-flight background QThread to finish before returning, so
        # it's now safe to close() it synchronously right here rather than
        # relying on deleteLater() to clean it up asynchronously later.
        if hasattr(self, '_user_win') and self._user_win is not None:
            try:
                self._user_win.close()
            except RuntimeError:
                pass  # C++ object might have been deleted already
            finally:
                self._user_win = None

        user_data = {"id": user_id, "full_name": full_name, "username": username, "role": "user"}
        self._user_win = UserDashboard(user_data, admin_view=True, parent_dashboard=self)
        self._user_win.setWindowTitle(f"Swift Optimizer — {full_name}'s Dashboard (Admin View)")
        self._user_win.show()

    def _on_sys_type_changed(self, type_text):
        """Toggle the username-field label between the employee (hostname)
        and admin (login username) framing."""
        is_admin_type = (type_text == "Admin")
        self.lbl_sys_user.setText("Username" if is_admin_type else "Hostname")
        self.inp_sys_user.setPlaceholderText("Enter Username" if is_admin_type else "Enter Hostname")

    def _add_system(self):
        name = self.inp_sys_name.text().strip()
        user = self.inp_sys_user.text().strip()
        password = self.inp_sys_pass.text().strip()
        branch = self.inp_sys_branch.currentText()
        account_type = self.inp_sys_type.currentText()

        if not name or not user or not password:
            gui_utils.show_warning(self, "Validation", "All fields are required.")
            return

        if account_type == "Admin":
            ok, msg = db.add_admin(user, password, name, branch)
            noun = "Admin"
        else:
            ok, msg = db.add_system(user, password, name, branch)
            noun = "System"

        if ok:
            gui_utils.show_info(self, "Success", f"{noun} '{name}' added successfully!\n\nUsername: {user}\nBranch: {branch}")
            self.inp_sys_name.clear()
            self.inp_sys_user.clear()
            self.inp_sys_pass.clear()
            self._load_systems()
            self._load_systems_selector()
            self._refresh_all()
        else:
            gui_utils.show_warning(self, "Error", msg)

    def _toggle_admin_active(self, user_id, full_name, currently_active):
        if currently_active:
            reply = gui_utils.confirm_action(
                self, "Deactivate Admin",
                f"Deactivate {full_name}'s admin account? They won't be able to log in until a super admin reactivates it."
            )
            if not reply:
                return
            ok, msg = db.set_admin_active(user_id, False)
        else:
            reply = gui_utils.confirm_action(
                self, "Activate Admin",
                f"Reactivate {full_name}'s admin account? You will need to set a new password for them now."
            )
            if not reply:
                return
                
            from login import ForceSetPasswordDialog
            from PyQt6.QtWidgets import QLabel, QDialog
            dlg = ForceSetPasswordDialog(self, user_id)
            for widget in dlg.findChildren(QLabel):
                if "reactivated" in widget.text():
                    widget.setText(f"You are reactivating {full_name}'s account. Please set a new password for them.")
                    break
                    
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
                
            ok, msg = db.set_admin_active(user_id, True)

        if ok:
            gui_utils.show_info(self, "Success", msg)
            self._load_systems()
            self._load_systems_selector()
            self._refresh_all()
        else:
            gui_utils.show_warning(self, "Error", msg)

    def _edit_system(self, user_id, full_name, username, branch=None):
        dlg = EditSystemDialog(self, user_id, full_name, username, branch)
        if dlg.exec():
            gui_utils.show_info(self, "Success", "System updated successfully.")
            self._load_systems()
            self._load_systems_selector()
            self._refresh_all()

    def _delete_system(self, user_id):
        reply = gui_utils.confirm_action(self, "Confirm Delete", 
            "Are you sure you want to delete this system?\n\nAll associated ports, devices, and requests will be permanently removed.")
        if reply:
            ok, msg = db.delete_system(user_id)
            if ok:
                gui_utils.show_info(self, "Success", msg)
                self._load_systems()
                self._load_systems_selector()
                self._refresh_all()
            else:
                gui_utils.show_warning(self, "Error", msg)

    def _load_history(self):
        # Merge port-based requests and slot-based requests, sorted by requested_at desc
        port_rows = db.get_all_requests(self.user.get("role"), self.user.get("branch"))
        slot_rows = db.get_all_slot_requests(self.user.get("role"), self.user.get("branch"))

        all_rows = port_rows + slot_rows
        # Sort combined list by requested_at descending (index 5)
        all_rows.sort(key=lambda r: r[5] or "", reverse=True)

        t = self.hist_table
        t.setRowCount(0)
        for i, r in enumerate(all_rows):
            rid, user, port, reason, status, req_at, res_at = r
            t.insertRow(i)
            t.setRowHeight(i, 44)
            t.setItem(i, 0, self._cell(user, bold=True))
            t.setItem(i, 1, self._cell(port))
            t.setItem(i, 2, self._cell(reason or "—"))
            t.setCellWidget(i, 3, self._center(status_badge(status.upper())))
            t.setItem(i, 4, self._cell((req_at or "")[:16]))
            t.setItem(i, 5, self._cell((res_at or "—")[:16]))

    # ── Handlers ─────────────────────────────────────────────────────────────
    def _on_toggle(self, port_id, field, value, kind):
        """Run the heavy DB + registry work on a background thread so the
        toggle animation is never blocked by I/O."""
        thread = QThread(self)
        worker = _ToggleWorker(port_id, field, value, self.user["id"], self.user["full_name"])
        worker.moveToThread(thread)

        def _on_done(ok):
            if not ok:
                gui_utils.show_warning(self, "Update Failed",
                    "Failed to update database. The device list may have resynced — refreshing now.")
            self._last_data_hash = None
            self._load_ports()
            thread.quit()
            thread.wait()

        worker.finished.connect(_on_done)
        thread.started.connect(worker.run)
        # Keep references alive until the thread finishes
        self._toggle_thread = thread
        self._toggle_worker = worker
        thread.start()

    def _resolve(self, req_id, action):
        label = "approve" if action == "approved" else "reject"
        reply = gui_utils.confirm_action(self, "Confirm", f"Are you sure you want to {label} this request?")
        if reply:
            # Update DB only. The agent on the user's PC applies the registry change.
            db.resolve_request(req_id, action,
                               changed_by_id=self.user["id"],
                               changed_by_name=self.user["full_name"])
            self._load_requests()
            self._load_ports()
            self._refresh_all()
            gui_utils.show_info(self, "Done",
                f"Request {'approved - USB storage will be enabled on the user\'s PC within ~10 seconds' if action == 'approved' else 'rejected'}.")

    def _refresh_all(self):
        ports = db.get_all_ports()
        total      = len(ports)
        active     = sum(1 for p in ports if p[6] == "ON")
        storage_on = sum(1 for p in ports if p[5])
        pending    = len(db.get_pending_requests(self.user.get("role"), self.user.get("branch"))) + len(db.get_pending_slot_requests(self.user.get("role"), self.user.get("branch")))

        if "Total Ports" in self._stat_labels:
            self._stat_labels["Total Ports"].setText(str(total))
        if "Active Ports" in self._stat_labels:
            self._stat_labels["Active Ports"].setText(str(active))
        if "Pending Requests" in self._stat_labels:
            self._stat_labels["Pending Requests"].setText(str(pending))
        if "Storage Enabled" in self._stat_labels:
            self._stat_labels["Storage Enabled"].setText(str(storage_on))
            self._stat_labels["Storage Enabled"].setStyleSheet(
                f"font-size: 26px; font-weight: 700; color: {ACCENT_RED if storage_on else TEXT_MUTED}; background: transparent; border: none;"
            )

        self._load_ports()
        self._load_requests()
        self._load_systems()
        self._load_unregistered()

    def _clear_history(self):
        reply = gui_utils.confirm_action(self, "Clear History", "Are you sure you want to delete all resolved request history?\n\nPending requests will not be affected.")

        if reply:
            db.clear_resolved_requests()
            self._load_history()
            gui_utils.show_info(self, "Done", "Request history cleared.")

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
        if user["role"] in ("admin", "super_admin"):
            w = AdminDashboard(user)
        else:
            from user_dashboard import UserDashboard
            w = UserDashboard(user)
            
        from PyQt6.QtWidgets import QApplication
        QApplication.instance()._main_window = w
        w.show()

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _cell(self, text, bold=False):
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        if bold:
            f = item.font(); f.setBold(True); item.setFont(f)
        return item

    def _center(self, widget, margin=2):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(margin, 0, margin, 0)
        h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(widget)
        return w

    def _action_group(self, widgets, spacing=5, margin=8):
        """Lay several action buttons (View/Edit/Delete/Activate) out in a
        single row so they can all live under one 'Actions' table column
        instead of each needing its own column. Symmetric margins so the
        group is genuinely centered in the column, not biased to one side."""
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(margin, 0, margin, 0)
        h.setSpacing(spacing)
        h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for widget in widgets:
            h.addWidget(widget)
        return w