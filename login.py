"""
login.py — Login window (light theme)
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QCheckBox, QDialog
)
from PyQt6.QtCore import Qt, pyqtSignal, QSettings, QTimer
from styles import (BG_WHITE, ACCENT_BLUE, TEXT_PRIMARY, TEXT_MUTED, BG_SIDEBAR)
import gui_utils
import db


class ForceSetPasswordDialog(QDialog):
    """Shown right after a successful login when the super admin has just
    reactivated this admin account. Must set a new password (different
    from the old one) before continuing into the dashboard."""

    def __init__(self, parent, user_id):
        super().__init__(parent)
        self.user_id = user_id
        self.setWindowTitle("Set a New Password")
        self.setFixedWidth(380)
        self.setStyleSheet(f"background-color: {BG_WHITE};")
        self.setModal(True)

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 22)
        v.setSpacing(10)

        title = QLabel("Set a New Password")
        title.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {TEXT_PRIMARY};")
        v.addWidget(title)

        info = QLabel("Your account was just reactivated by the super admin. "
                       "For security, please set a new password to continue. "
                       "It can't be the same as your old password.")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        v.addWidget(info)
        v.addSpacing(6)

        lbl1 = QLabel("New Password")
        lbl1.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {TEXT_MUTED};")
        v.addWidget(lbl1)
        self.inp_new = gui_utils.make_password_field("Enter new password", height=40)
        v.addWidget(self.inp_new)

        lbl2 = QLabel("Confirm New Password")
        lbl2.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {TEXT_MUTED};")
        v.addWidget(lbl2)
        self.inp_confirm = gui_utils.make_password_field("Re-enter new password", height=40)
        self.inp_confirm.returnPressed.connect(self._save)
        v.addWidget(self.inp_confirm)

        v.addSpacing(8)
        btn = QPushButton("Set Password & Continue")
        btn.setFixedHeight(42)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_BLUE}; color: white;
                border: none; border-radius: 8px;
                font-size: 13px; font-weight: 700;
            }}
            QPushButton:hover {{ background-color: #007DB8; }}
        """)
        btn.clicked.connect(self._save)
        v.addWidget(btn)

    def _save(self):
        new_pw = self.inp_new.text().strip()
        confirm = self.inp_confirm.text().strip()
        if not new_pw or not confirm:
            gui_utils.show_warning(self, "Validation", "Please fill in both fields.")
            return
        if new_pw != confirm:
            gui_utils.show_warning(self, "Validation", "Passwords don't match.")
            return
        if len(new_pw) < 6:
            gui_utils.show_warning(self, "Validation", "Password must be at least 6 characters.")
            return

        ok, msg = db.set_new_password(self.user_id, new_pw)
        if ok:
            self.new_password = new_pw
            self.accept()
        else:
            gui_utils.show_warning(self, "Can't Set Password", msg)


class LoginWindow(QWidget):
    login_success = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Swift Optimizer — Login")
        self.setFixedSize(440, 560)  # slightly taller
        self.setStyleSheet(f"background-color: {BG_WHITE};")
        self.settings = QSettings("SwiftProsys", "USBControlSystem")
        self._build_ui()
        
        saved_user = self.settings.value("login/username", "")
        saved_pass = self.settings.value("login/password", "")
        if saved_user and saved_pass:
            self.inp_user.setText(saved_user)
            self.inp_pass.setText(saved_pass)
            self.chk_remember.setChecked(True)
            QTimer.singleShot(100, self._do_login)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(0)

        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_WHITE};
                border: none;
            }}
        """)
        root.addWidget(card)

        v = QVBoxLayout(card)
        v.setContentsMargins(44, 44, 44, 44)
        v.setSpacing(0)

        # Blue top banner
        banner = QFrame()
        banner.setFixedHeight(64)
        banner.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_SIDEBAR};
                border-radius: 10px;
                border: none;
            }}
        """)
        bl = QVBoxLayout(banner)
        bl.setContentsMargins(0, 0, 0, 0)
        icon = QLabel("🔌  USB Control System")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 18px; font-weight: 700; color: white; background: transparent; border: none;")
        bl.addWidget(icon)
        v.addWidget(banner)
        v.addSpacing(28)

        sub = QLabel("Sign in to your account")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(f"font-size: 13px; color: {TEXT_MUTED}; background: transparent; border: none;")
        v.addWidget(sub)
        v.addSpacing(28)

        def field_label(text):
            l = QLabel(text)
            l.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 12px; font-weight: 600; background: transparent; border: none;")
            return l

        v.addWidget(field_label("Username"))
        v.addSpacing(6)
        self.inp_user = QLineEdit()
        self.inp_user.setPlaceholderText("Enter hostname")
        self.inp_user.setFixedHeight(40)
        v.addWidget(self.inp_user)
        v.addSpacing(16)

        v.addWidget(field_label("Password"))
        v.addSpacing(6)
        self.inp_pass = gui_utils.make_password_field("Enter password", height=40)
        self.inp_pass.returnPressed.connect(self._do_login)
        v.addWidget(self.inp_pass)
        v.addSpacing(16)
        
        self.chk_remember = QCheckBox("Remember Me (Auto-Login)")
        self.chk_remember.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 13px; font-weight: 500;")
        self.chk_remember.setCursor(Qt.CursorShape.PointingHandCursor)
        v.addWidget(self.chk_remember)
        v.addSpacing(28)

        btn = QPushButton("Sign In")
        btn.setFixedHeight(44)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_BLUE};
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 700;
            }}
            QPushButton:hover {{ background-color: #007DB8; }}
            QPushButton:pressed {{ background-color: #006FA3; }}
        """)
        btn.clicked.connect(self._do_login)
        v.addWidget(btn)
        v.addSpacing(20)

        v.addStretch()

    def _do_login(self):
        username = self.inp_user.text().strip()
        password = self.inp_pass.text().strip()
        if not username or not password:
            gui_utils.show_warning(self, "Login", "Please enter hostname and password.")
            return
        result = db.authenticate(username, password)

        if result == "INACTIVE":
            self.settings.remove("login/username")
            self.settings.remove("login/password")
            gui_utils.show_error(self, "Account Deactivated",
                                  "This account has been deactivated by the super admin. "
                                  "Contact your super admin for access.")
            self.inp_pass.clear()
            return

        user = result
        if user:
            # If the super admin just reactivated this account, force a new
            # password before allowing entry into the dashboard.
            if user.get("must_reset_password"):
                dlg = ForceSetPasswordDialog(self, user["id"])
                if dlg.exec() != QDialog.DialogCode.Accepted:
                    # User closed the dialog without saving — abort login.
                    self.inp_pass.clear()
                    return
                # Update the session's password to the newly set one so that
                # "Remember Me" (if checked) stores the correct credential.
                password = dlg.new_password

            if self.chk_remember.isChecked():
                self.settings.setValue("login/username", username)
                self.settings.setValue("login/password", password)
            else:
                self.settings.remove("login/username")
                self.settings.remove("login/password")
            self.login_success.emit(user)
        else:
            self.settings.remove("login/username")
            self.settings.remove("login/password")
            gui_utils.show_error(self, "Login Failed", "Invalid hostname or password.")
            self.inp_pass.clear()