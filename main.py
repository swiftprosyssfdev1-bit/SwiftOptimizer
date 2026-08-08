"""
main.py — Swift Optimizer entry point
"""
import sys
import os
import ctypes
import builtins

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtCore import Qt

# db.py loads .env itself (frozen-exe aware -- see db.py), so importing it
# here is enough to have USB_DB_* available for the rest of this module.
import db   
from styles import APP_STYLESHEET, resource_path
from login import LoginWindow

QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)


def _install_crash_guard():

    import traceback

    def _hook(exc_type, exc_value, exc_tb):
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            log_path = os.path.join(os.path.expanduser("~"), "swiftprosys_error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(tb_text + "\n")
        except Exception:
            pass
        try:
            import gui_utils
            gui_utils.show_error(
                None,
                "Unexpected Error",
                f"Something went wrong and the action could not be completed.\n\n{exc_value}"
            )
        except Exception:
            pass

    sys.excepthook = _hook


def main():
    _install_crash_guard()
    if os.name == 'nt':

        myappid = 'swiftprosys.usbcontrol.agent.1_0'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

    app = QApplication(sys.argv)
    app.setApplicationName("Swift Optimizer")
    app.setStyleSheet(APP_STYLESHEET)

    icon_path = resource_path("assets", "icon.ico")
    app.setWindowIcon(QIcon(icon_path))

    font = QFont("Segoe UI", 10)
    app.setFont(font)

    try:
        db.init_db()    
    except BaseException as e:
        import gui_utils
        diag = db.ENV_DEBUG
        cfg = db.MYSQL_CONFIG
        diag_lines = [
            f"python-dotenv installed: {diag.get('dotenv_installed')}",
            f".env loaded from: {diag.get('loaded_path') or 'NOT FOUND (checked: ' + '; '.join(diag.get('checked_paths', [])) + ')'}",
            f"Resolved config -> host={cfg.get('host')!r}, user={cfg.get('user')!r}, database={cfg.get('database')!r}",
        ]
        gui_utils.show_error(
            None,
            "Database Connection Error",
            "Failed to connect to the MySQL database.\n\n"
            f"Error: {e}\n\n"
            + "\n".join(diag_lines) +
            "\n\nIf host/user/database above are blank or 'None', .env "
            "wasn't found -- for a dev run, make sure .env sits next to "
            "main.py; for the installed app, it should be bundled into "
            "SwiftOptimizer.exe at build time (--add-data \".env;.\" in "
            "build_exe.bat) -- rebuild the exe with a valid .env present."
        )
        sys.exit(1)

    login = LoginWindow()

    def on_login(user):
        login.close()
        if user["role"] in ("admin", "super_admin"):
            from admin_dashboard import AdminDashboard
            win = AdminDashboard(user)
        else:
            from user_dashboard import UserDashboard
            win = UserDashboard(user)
        win.show()
        # Keep reference
        app._main_window = win

    login.login_success.connect(on_login)
    login.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
