import sys
import logging
import configparser
from pathlib import Path

if getattr(sys, 'frozen', False):
    _EXE_DIR = Path(sys.executable).parent
else:
    _EXE_DIR = Path(__file__).parent.resolve()

IS_WINDOWS = sys.platform == "win32"

# NOTE: This agent used to mark its config and log files Hidden+System so
# they wouldn't show up in a normal Explorer window. That's been removed —
# this is disclosed, visible endpoint software now, so its files stay
# normal and inspectable like any other installed app's.
def _set_hidden(path: Path):
    """No-op kept only so existing call sites don't need to change."""
    return

if getattr(sys, 'frozen', False):
    # Frozen (PyInstaller) build: read the config straight out of the
    # bundle PyInstaller unpacked to (_MEIPASS). We never copy it out to
    # a plain file sitting next to SwiftAgent.exe, so there's simply no
    # separate agent_config.txt for anyone to see in that folder.
    _CONFIG_FILE = Path(getattr(sys, "_MEIPASS", _EXE_DIR)) / "agent_config.txt"
else:
    # Running from source (dev/test): read the file next to this script.
    _CONFIG_FILE = _EXE_DIR / "agent_config.txt"

_cfg = configparser.ConfigParser()
_cfg.read(str(_CONFIG_FILE), encoding="utf-8")

DB_HOST     = _cfg.get("mysql", "host").strip()
DB_USER     = _cfg.get("mysql", "user").strip()
DB_PASSWORD = _cfg.get("mysql", "password").strip()
DB_NAME     = _cfg.get("mysql", "database").strip()
_CFG_USERNAME = _cfg.get("agent", "username").strip()

AGENT_VERSION = "3.6"
_REG_KEY_PATH = r"SOFTWARE\USBControlAgent"
_REG_VALUE    = "username"


import os as _os
_LOG_DIR = Path("C:\\SwiftAgent")
LOG_FILE  = _LOG_DIR / "usb_agent.log"
POLL_INTERVAL = 2

def _setup_logging():
    from logging.handlers import RotatingFileHandler

    class _HiddenRotatingFileHandler(RotatingFileHandler):
        """Same as RotatingFileHandler, but keeps the active log file (and
        any rotated .1/.2/.3 backups) marked Hidden+System after every
        write and after every rollover."""
        def emit(self, record):
            super().emit(record)
            _set_hidden(Path(self.baseFilename))

        def doRollover(self):
            super().doRollover()
            _set_hidden(Path(self.baseFilename))
            for i in range(1, (self.backupCount or 0) + 1):
                backup = Path(f"{self.baseFilename}.{i}")
                if backup.exists():
                    _set_hidden(backup)

    logger = logging.getLogger("USBAgent")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            fh = _HiddenRotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))
            logger.addHandler(fh)
            _set_hidden(LOG_FILE)
        except Exception as e:
            # Running as a Windows Service, sys.stdout/stderr may be None
            # (no console attached at all) — never assume print()/stdout
            # works here, or a failure creating the log file silently
            # becomes a hard crash at import time with zero log output.
            try:
                sys.stderr.write(f"WARNING: Could not create log file: {e}\n")
            except Exception:
                pass

        # Only attach a console StreamHandler if a real console stream
        # exists. Under a Windows Service (or --noconsole with no console
        # allocated), sys.stdout/sys.stderr can be None or lack a usable
        # .buffer — skip this handler entirely rather than wrapping None.
        if sys.stdout is not None and hasattr(sys.stdout, "buffer"):
            import io
            sh = logging.StreamHandler(
                io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            )
            sh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))
            logger.addHandler(sh)
    return logger

log = _setup_logging()

if IS_WINDOWS:
    try:
        import winreg
        _HAS_WINREG = True
    except ImportError:
        _HAS_WINREG = False
    try:
        import wmi
        _HAS_WMI = True
    except ImportError:
        _HAS_WMI = False
        log.error("wmi not installed")
else:
    _HAS_WINREG = False
    _HAS_WMI = False
