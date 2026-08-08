"""
db.py
"""

import mysql.connector
import hashlib
import os
import re
import sys

# ── .env loading diagnostics ──────────────────────────────────────────────
# Filled in below regardless of outcome, so main.py's error dialog can show
# exactly what was tried instead of a bare, unhelpful MySQL error.
ENV_DEBUG = {
    "dotenv_installed": False,
    "checked_paths": [],
    "loaded_path": None,
}

try:
    from dotenv import load_dotenv
    ENV_DEBUG["dotenv_installed"] = True

    # In a frozen build, .env is bundled INTO the exe (via --add-data
    # ".env;." in build_exe.bat, same pattern as agent_config.txt for
    # SwiftAgent.exe) so it never appears as a visible/editable file next
    # to the exe -- PyInstaller unpacks bundled data to a temp folder at
    # runtime, reported as sys._MEIPASS. When NOT frozen (running
    # main.py directly), fall back to a plain .env next to this script or
    # in the current working directory, for local dev convenience.
    _candidate_dirs = []
    if getattr(sys, "frozen", False):
        _candidate_dirs.append(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable))))
    else:
        _candidate_dirs.append(os.path.dirname(os.path.abspath(__file__)))
        _candidate_dirs.append(os.getcwd())

    for _d in _candidate_dirs:
        _p = os.path.join(_d, ".env")
        ENV_DEBUG["checked_paths"].append(_p)
        if os.path.isfile(_p):
            load_dotenv(_p)
            ENV_DEBUG["loaded_path"] = _p
            break
    else:
        # Nothing found on disk -- still call load_dotenv() with no args
        # as a last resort (it searches upward from cwd on its own).
        load_dotenv()

except ImportError:
    # python-dotenv not installed -- fall back to whatever's already in
    # the environment (e.g. set manually via PowerShell/System settings).
    # In a frozen exe this usually means the build forgot to bundle
    # dotenv (see --hidden-import "dotenv" in build_exe.bat).
    pass

try:
    import bcrypt
    _HAS_BCRYPT = True
except ImportError:
    _HAS_BCRYPT = False

# MySQL Configuration Details.
# Values below are fallback defaults for this admin dashboard's own DB
# connection. They can be overridden with environment variables so the
# server IP/credentials don't have to be hardcoded and rebuilt into the
# app every time the DB server moves — set USB_DB_HOST / USB_DB_USER /
# USB_DB_PASSWORD / USB_DB_NAME before launching, or in a .env loaded by
# your process manager.
MYSQL_CONFIG = { "host": os.environ.get("USB_DB_HOST"), 
                 "user": os.environ.get("USB_DB_USER"), 
                 "password": os.environ.get("USB_DB_PASSWORD"), 
                 "database": os.environ.get("USB_DB_NAME"), 
                 "use_pure": True 
}

BRANCHES = ["Chennai", "Tindivanam", "Madurai", "Kanchipuram"]

# Roles: 'super_admin' (one, top-level — manages admins/employees across all
# branches), 'admin' (branch-scoped — manages employees in their own branch),
# 'user' (an employee/PC registered as a monitored "system").
ROLE_SUPER_ADMIN = "super_admin"
ROLE_ADMIN       = "admin"
ROLE_EMPLOYEE    = "user"


class DBManager:
    """The one and only place this app (User Dashboard, Admin Dashboard,
    login screen, manage_admin.py) opens a MySQL connection from.

    No connection pool. Every call to get_conn() opens a brand-new
    connection; every function below calls conn.close() when it's done,
    which now really does close the socket (open -> query -> commit ->
    close, every single time) instead of returning it to a pool. This
    dashboard process never holds a connection open between actions,
    even if the window itself stays open for hours.
    """

    def __init__(self, config):
        self._config = config

    def get_connection(self, select_db=True):
        config = self._config.copy()
        if not select_db:
            # no-database case, used only for first-run DB/table setup
            config.pop("database", None)
        return mysql.connector.connect(**config)


# Single shared instance -- every widget/page in this app calls get_conn()
# below, which goes through this one DBManager, instead of ever calling
# mysql.connector.connect() directly.
_DB_MANAGER = DBManager(MYSQL_CONFIG)


def get_conn(select_db=True, role="ui"):
    # `role` is kept as an accepted (ignored) parameter for backward
    # compatibility with any existing call sites -- this module is only
    # ever used by the UI-side apps; the agent has its own separate
    # agent_db.py with its own persistent connection.
    return _DB_MANAGER.get_connection(select_db=select_db)


# ── Password hashing ──────────────────────────────────────────────────────────

def hash_password(pw: str) -> str:
    if _HAS_BCRYPT:
        return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
    return hashlib.sha256(pw.encode()).hexdigest()


def _hash_password_legacy(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def _verify_password(pw: str, stored_hash: str, algo: str) -> bool:
    if algo == "bcrypt" and _HAS_BCRYPT:
        try:
            return bcrypt.checkpw(pw.encode(), stored_hash.encode())
        except Exception:
            return False
    return _hash_password_legacy(pw) == stored_hash


# ── Schema setup ─────────────────────────────────────────────────────────────

def _column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute("""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME   = %s
          AND COLUMN_NAME  = %s
    """, (table, column))
    return cursor.fetchone()[0] > 0


def _index_exists(cursor, table: str, index_name: str) -> bool:
    cursor.execute("""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME    = %s
          AND INDEX_NAME    = %s
    """, (table, index_name))
    return cursor.fetchone()[0] > 0


def init_db():
    try:
        conn = get_conn(select_db=False)
        c = conn.cursor()
        c.execute(f"CREATE DATABASE IF NOT EXISTS {MYSQL_CONFIG['database']}")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Database creation check failed/skipped: {e}")

    conn = get_conn(select_db=True)
    c = conn.cursor()

    # ── users ─────────────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id                  INT AUTO_INCREMENT PRIMARY KEY,
            username            VARCHAR(255) UNIQUE NOT NULL,
            password            VARCHAR(255) NOT NULL,
            role                VARCHAR(50)  NOT NULL,
            full_name           VARCHAR(255) DEFAULT '',
            password_algo       VARCHAR(20)  DEFAULT 'sha256',
            settings_changed_at TIMESTAMP    NULL DEFAULT NULL
        )
    """)

    # Migration: add password_algo if missing
    if not _column_exists(c, "users", "password_algo"):
        c.execute("ALTER TABLE users ADD COLUMN password_algo VARCHAR(20) DEFAULT 'sha256'")
        c.execute("UPDATE users SET password_algo = 'sha256' WHERE password_algo IS NULL OR password_algo = ''")
        conn.commit()

    # Migration: add settings_changed_at if missing (existing installs)
    if not _column_exists(c, "users", "settings_changed_at"):
        c.execute("ALTER TABLE users ADD COLUMN settings_changed_at TIMESTAMP NULL DEFAULT NULL")
        # Initialise to NOW() so agents pick up a baseline on first poll
        c.execute("UPDATE users SET settings_changed_at = NOW() WHERE settings_changed_at IS NULL")
        conn.commit()
        print("Added settings_changed_at column to users")

    # Migration: branch / active / forced-password-reset support for the
    # super-admin → admin → employee role hierarchy.
    if not _column_exists(c, "users", "branch"):
        c.execute("ALTER TABLE users ADD COLUMN branch VARCHAR(100) DEFAULT NULL")
        conn.commit()
        print("Added branch column to users")

    if not _column_exists(c, "users", "is_active"):
        c.execute("ALTER TABLE users ADD COLUMN is_active TINYINT(1) NOT NULL DEFAULT 1")
        conn.commit()
        print("Added is_active column to users")

    if not _column_exists(c, "users", "must_reset_password"):
        c.execute("ALTER TABLE users ADD COLUMN must_reset_password TINYINT(1) NOT NULL DEFAULT 0")
        conn.commit()
        print("Added must_reset_password column to users")

    if not _column_exists(c, "users", "prev_password"):
        c.execute("ALTER TABLE users ADD COLUMN prev_password VARCHAR(255) DEFAULT NULL")
        c.execute("ALTER TABLE users ADD COLUMN prev_password_algo VARCHAR(20) DEFAULT NULL")
        conn.commit()
        print("Added prev_password columns to users")

    # Migration: this app used to ship with exactly one 'admin' account.
    # Promote it to 'super_admin' so existing installs get a super admin
    # automatically instead of being locked out of the new admin/branch
    # management features. Only runs when no super_admin exists yet, and
    # only touches the single legacy admin row (safe to run every startup).
    c.execute("SELECT COUNT(*) FROM users WHERE role='super_admin'")
    if c.fetchone()[0] == 0:
        c.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1")
        legacy_admin = c.fetchone()
        if legacy_admin:
            c.execute("UPDATE users SET role='super_admin' WHERE id=%s", (legacy_admin[0],))
            conn.commit()
            print(f"Promoted existing admin (id={legacy_admin[0]}) to super_admin")

    c.execute("""
    CREATE TABLE IF NOT EXISTS usb_ports (
        id                  INT AUTO_INCREMENT PRIMARY KEY,
        user_id             INT NOT NULL,
        port_name           VARCHAR(100) NOT NULL,
        device_name         VARCHAR(255) DEFAULT 'Unknown',
        device_type         VARCHAR(50)  DEFAULT 'Storage',
        port_key            VARCHAR(255) DEFAULT '',
        port_enabled        INT          DEFAULT 1,
        keyboard_enabled    INT          DEFAULT 1,
        mouse_enabled       INT          DEFAULT 1,
        storage_enabled     INT          DEFAULT 0,
        transfer_expires_at DATETIME     NULL DEFAULT NULL,
        status              VARCHAR(50)  DEFAULT 'ON',
        device_id           VARCHAR(255) DEFAULT '',
        updated_by          VARCHAR(100) DEFAULT NULL,
        last_updated        TIMESTAMP    NULL DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        UNIQUE KEY uq_user_port_device (user_id, port_key, device_id)
    )
""")
    # Migration: rename registry_key → device_id ONLY if registry_key exists
    # AND device_id does not yet exist (safe on both MariaDB and MySQL 8+)
    if _column_exists(c, "usb_ports", "registry_key") and not _column_exists(c, "usb_ports", "device_id"):
        c.execute("ALTER TABLE usb_ports CHANGE COLUMN registry_key device_id VARCHAR(255) DEFAULT ''")
        conn.commit()
        print("Migrated usb_ports.registry_key → device_id")

    # Migration: add device_type if missing
    if not _column_exists(c, "usb_ports", "device_type"):
        c.execute("ALTER TABLE usb_ports ADD COLUMN device_type VARCHAR(50) DEFAULT 'Storage'")
        conn.commit()
        # Backfill based on device_name
        c.execute("""
            UPDATE usb_ports 
            SET device_type = CASE 
                WHEN LOWER(device_name) LIKE '%keyboard%' THEN 'Keyboard'
                WHEN LOWER(device_name) LIKE '%mouse%' THEN 'Mouse'
                ELSE 'Storage'
            END
            WHERE device_type IS NULL OR device_type = ''
        """)
        conn.commit()
        print("Added device_type column to usb_ports")

    # Migration: columns the app actually queries/writes (get_all_ports,
    # get_ports_by_user, update_field, the agent's port sync) but which the
    # CREATE TABLE above never declared. Without these, a brand-new install
    # creates a usb_ports table that crashes the first time the dashboard or
    # agent touches it — these must stay in sync with every SELECT/INSERT
    # that references them.
    if not _column_exists(c, "usb_ports", "port_key"):
        c.execute("ALTER TABLE usb_ports ADD COLUMN port_key VARCHAR(255) DEFAULT ''")
        conn.commit()
        print("Added port_key column to usb_ports")

    if not _column_exists(c, "usb_ports", "port_enabled"):
        c.execute("ALTER TABLE usb_ports ADD COLUMN port_enabled INT DEFAULT 1")
        conn.commit()
        print("Added port_enabled column to usb_ports")

    if not _column_exists(c, "usb_ports", "updated_by"):
        c.execute("ALTER TABLE usb_ports ADD COLUMN updated_by VARCHAR(100) DEFAULT NULL")
        conn.commit()
        print("Added updated_by column to usb_ports")

    if not _column_exists(c, "usb_ports", "last_updated"):
        c.execute("ALTER TABLE usb_ports ADD COLUMN last_updated TIMESTAMP NULL DEFAULT NULL "
                   "ON UPDATE CURRENT_TIMESTAMP")
        conn.commit()
        print("Added last_updated column to usb_ports")

    if not _column_exists(c, "usb_ports", "transfer_expires_at"):
        c.execute("ALTER TABLE usb_ports ADD COLUMN transfer_expires_at DATETIME NULL DEFAULT NULL")
        conn.commit()
        print("Added transfer_expires_at column to usb_ports")

    if not _column_exists(c, "usb_ports", "connected"):
        c.execute("ALTER TABLE usb_ports ADD COLUMN connected INT DEFAULT 1")
        conn.commit()
        print("Added connected column to usb_ports")

    # Migration: port_admin_set — was previously only added via a manual
    # one-off USB.sql run, so a fresh install's own runtime migrations
    # never created it. Marks whether an admin has ever explicitly decided
    # this physical port (vs it merely sitting at an auto-default).
    if not _column_exists(c, "usb_ports", "port_admin_set"):
        c.execute("ALTER TABLE usb_ports ADD COLUMN port_admin_set INT DEFAULT 0")
        conn.commit()
        c.execute("UPDATE usb_ports SET port_admin_set = 1 WHERE port_enabled = 0")
        conn.commit()
        print("Added port_admin_set column to usb_ports")

    # Migration: storage_admin_set — Storage Access is now its own
    # independent per-port policy, decoupled from the Port Access master
    # switch (which only governs keyboard/mouse/webcam). Defaults to 0
    # (blocked) on every port until an admin explicitly flips Storage
    # Access, and that decision persists across unplug/replug and across
    # whatever device type next occupies the port.
    if not _column_exists(c, "usb_ports", "storage_admin_set"):
        c.execute("ALTER TABLE usb_ports ADD COLUMN storage_admin_set INT DEFAULT 0")
        conn.commit()
        # BACKFILL — this is the actual fix for "Storage shows Allowed
        # without admin approval". Before this migration, storage_enabled
        # was NOT its own independent decision — on many rows it was left
        # over from the old (pre-decoupling) behavior where storage mirrored
        # the Port Access switch, or from other code paths that wrote
        # storage_enabled=1 without ever recording a genuine admin decision.
        # Adding storage_admin_set with DEFAULT 0 does NOT retroactively
        # make those old storage_enabled=1 rows admin-approved — it just
        # silently lets them keep showing "Allowed" forever with nothing on
        # record justifying it. Reset every row that has no recorded admin
        # decision back to the safe default (blocked) so "Allowed" only ever
        # means "an admin explicitly approved this port for storage".
        c.execute("UPDATE usb_ports SET storage_enabled = 0 WHERE storage_admin_set = 0")
        conn.commit()
        print("Added storage_admin_set column to usb_ports and reset "
              "un-admin-approved storage_enabled rows to blocked")

    # Invariant: storage_enabled can only ever be 1 alongside
    # storage_admin_set=1 — i.e. "Allowed" always means a real admin
    # decision is on record, never a leftover/inherited/default value. This
    # is enforced at the DB layer (not just in application code) so no
    # future code path — a bug, a new feature, a request-approval workflow,
    # a reconciliation pass — can silently resurrect "Allowed" without an
    # admin decision ever making it past this constraint.
    try:
        c.execute("""
            SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'usb_ports'
              AND CONSTRAINT_NAME = 'chk_storage_requires_admin_set'
        """)
        if not c.fetchone():
            c.execute("""
                ALTER TABLE usb_ports
                ADD CONSTRAINT chk_storage_requires_admin_set
                CHECK (storage_enabled = 0 OR storage_admin_set = 1)
            """)
            conn.commit()
            print("Added chk_storage_requires_admin_set constraint to usb_ports")
    except Exception as e:
        # MySQL < 8.0.16 parses CHECK but never enforces it; MariaDB support
        # varies by version. Not fatal either way — the application-level
        # fix (every write path below now sets both columns together) is
        # the primary guarantee; the constraint is defense-in-depth on
        # servers that support it.
        print(f"Could not add chk_storage_requires_admin_set constraint "
              f"(non-fatal, older MySQL/MariaDB may not support it): {e}")

    if not _column_exists(c, "devices", "connected"):
        c.execute("ALTER TABLE devices ADD COLUMN connected INT DEFAULT 1")
        conn.commit()
        print("Added connected column to devices")

    # Migration: devices.port_key — without this, update_port_toggle() and
    # _sync_devices_to_db() have no stable way to tell apart two physical
    # ports that happen to share the same displayed port_name (Windows
    # reuses/shifts port_name labels independently of the underlying
    # port_key). That mismatch is what causes a device's Device Details
    # status to disagree with its own Port Details row.
    if not _column_exists(c, "devices", "port_key"):
        c.execute("ALTER TABLE devices ADD COLUMN port_key VARCHAR(255) DEFAULT ''")
        conn.commit()
        print("Added port_key column to devices")
        # Best-effort backfill for rows that existed before this column did.
        c.execute("""
            UPDATE devices d
            JOIN usb_ports p
              ON d.user_id = p.user_id
             AND d.device_name = p.device_name
             AND d.device_type = p.device_type
            SET d.port_key = p.port_key
            WHERE d.port_key = '' OR d.port_key IS NULL
        """)
        conn.commit()
        print("Backfilled devices.port_key from usb_ports")

    # Migration: prevent true duplicate rows — the same device re-inserted
    # at the same port for the same user. Deliberately scoped to
    # (user_id, port_key, device_id) rather than just (user_id, port_key):
    # a single physical port can legitimately hold multiple rows when a
    # composite USB device (e.g. a wireless keyboard+mouse combo dongle)
    # registers more than one HID function at that port — usb_agent.py
    # inserts one row per such device_id, all sharing the same port_key,
    # by design. Only add the constraint once existing true duplicates are
    # cleared, otherwise the ALTER itself fails.
    c.execute("""
        SELECT user_id, port_key, device_id, COUNT(*) c
        FROM usb_ports
        WHERE port_key IS NOT NULL AND port_key != ''
        GROUP BY user_id, port_key, device_id
        HAVING c > 1
    """)
    dupes = c.fetchall()
    if dupes:
        print(f"WARNING: {len(dupes)} duplicate (user_id, port_key, device_id) group(s) found in usb_ports — "
              f"keeping the newest row per group, deleting the rest, before adding the unique key.")
        for user_id, port_key, device_id, _count in dupes:
            c.execute("""
                DELETE FROM usb_ports
                WHERE user_id=%s AND port_key=%s AND device_id=%s
                  AND id NOT IN (
                      SELECT id FROM (
                          SELECT id FROM usb_ports
                          WHERE user_id=%s AND port_key=%s AND device_id=%s
                          ORDER BY id DESC LIMIT 1
                      ) keep
                  )
            """, (user_id, port_key, device_id, user_id, port_key, device_id))
        conn.commit()

    if not _index_exists(c, "usb_ports", "uq_user_port_device") and not _index_exists(c, "usb_ports", "uq_user_port"):
        try:
            c.execute("ALTER TABLE usb_ports ADD UNIQUE KEY uq_user_port_device (user_id, port_key, device_id)")
            conn.commit()
            print("Added unique key uq_user_port_device (user_id, port_key, device_id) on usb_ports")
        except Exception as e:
            print(f"Could not add uq_user_port_device unique key (will retry on next startup): {e}")

    # Migration: cleanup legacy duplicate disconnected rows from before v4
    try:
        c.execute("""
            DELETE p1 FROM usb_ports p1 
            JOIN usb_ports p2 ON p1.user_id = p2.user_id 
                             AND p1.port_key = p2.port_key 
                             AND p1.device_type = p2.device_type 
            WHERE p1.connected = 0 AND p2.connected = 1
        """)
        if c.rowcount > 0:
            print(f"Cleaned up {c.rowcount} duplicate disconnected DB rows")
        conn.commit()
    except Exception as e:
        print(f"Failed to clean up duplicate DB rows: {e}")



    # ── devices ───────────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            user_id     INT NOT NULL,
            device_name VARCHAR(255) NOT NULL,
            device_type VARCHAR(100) NOT NULL,
            port_name   VARCHAR(100) NOT NULL,
            port_key    VARCHAR(255) DEFAULT '',
            status      VARCHAR(50)  NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # ── usb_requests ──────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS usb_requests (
            id           INT AUTO_INCREMENT PRIMARY KEY,
            user_id      INT NOT NULL,
            port_id      INT NOT NULL,
            reason       TEXT,
            status       VARCHAR(50) DEFAULT 'pending',
            requested_at TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
            resolved_at  TIMESTAMP   NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (port_id) REFERENCES usb_ports(id) ON DELETE CASCADE
        )
    """)

    # ── audit_logs ────────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id                   INT AUTO_INCREMENT PRIMARY KEY,
            user_id              INT NULL,
            user_name_snapshot   VARCHAR(255) DEFAULT '',
            action               VARCHAR(100) NOT NULL,
            old_value            VARCHAR(50)  DEFAULT '',
            new_value            VARCHAR(50)  DEFAULT '',
            changed_by_id        INT NULL,
            changed_by_name      VARCHAR(255) DEFAULT '',
            port_id              INT NULL,
            port_name_snapshot   VARCHAR(100) DEFAULT '',
            created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_audit_user    (user_id),
            INDEX idx_audit_created (created_at)
        )
    """)

    # ── agent_status ──────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS agent_status (
            user_id         INT NOT NULL PRIMARY KEY,
            hostname        VARCHAR(255) DEFAULT '',
            agent_version   VARCHAR(50)  DEFAULT '',
            last_checkin    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
            kbd_blocked     TINYINT      DEFAULT 0,
            mse_blocked     TINYINT      DEFAULT 0,
            hooks_installed TINYINT      DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # ── unregistered_agents ───────────────────────────────────────────────────
    # An agent checks in here when the username it resolved (Windows Full Name,
    # login name, or machine account like 'HOST$') doesn't match any row in
    # `users`. This lets the dashboard surface "a new/unknown PC is running the
    # agent" as an alert, instead of it only showing up as a WARNING buried in
    # that machine's local log file.
    c.execute("""
        CREATE TABLE IF NOT EXISTS unregistered_agents (
            id                INT AUTO_INCREMENT PRIMARY KEY,
            resolved_username VARCHAR(255) NOT NULL,
            hostname          VARCHAR(255) DEFAULT '',
            agent_version     VARCHAR(50)  DEFAULT '',
            first_seen        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen         TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            dismissed         TINYINT DEFAULT 0,
            UNIQUE KEY uq_unreg (resolved_username, hostname)
        )
    """)
    conn.commit()

    # ── system_settings ───────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            setting_key   VARCHAR(100) PRIMARY KEY,
            setting_value VARCHAR(255) DEFAULT ''
        )
    """)
    c.execute("""
        INSERT INTO system_settings (setting_key, setting_value)
        VALUES ('dry_run_mode', '0')
        ON DUPLICATE KEY UPDATE setting_key = setting_key
    """)
    c.execute("""
        INSERT INTO system_settings (setting_key, setting_value)
        VALUES ('fail_open_after_minutes', '5')
        ON DUPLICATE KEY UPDATE setting_key = setting_key
    """)
    conn.commit()

    # ── slot_requests ─────────────────────────────────────────────────────────
    # Created here (once, at startup) so every read/query function that touches
    # this table can simply SELECT/INSERT without repeating DDL on every call.
    c.execute("""
        CREATE TABLE IF NOT EXISTS slot_requests (
            id           INT AUTO_INCREMENT PRIMARY KEY,
            user_id      INT NOT NULL,
            slot_name    VARCHAR(100) NOT NULL,
            reason       TEXT,
            status       VARCHAR(50) DEFAULT 'pending',
            requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at  TIMESTAMP NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # ── available_usb_slots ───────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS available_usb_slots (
            id           INT AUTO_INCREMENT PRIMARY KEY,
            user_id      INT NOT NULL,
            slot_name    VARCHAR(100) NOT NULL,
            slot_id      VARCHAR(255) DEFAULT '',
            controller   VARCHAR(255) DEFAULT '',
            location     VARCHAR(255) DEFAULT '',
            updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.commit()

    # ── port_physical_slots ───────────────────────────────────────────────────
    # Permanent anchor table: one row per physical USB socket per user.
    # socket_id  = stable hardware identity (hub VID+PID+serial :: port position)
    # port_key   = current Windows LocationInformation string (updated on rename)
    # slot_number = the display number shown as "USB Port N" (immutable once set)
    #
    # This is the single source of truth for which physical socket is labelled
    # "USB Port 1", "USB Port 2", etc.  The agent writes here once per new socket
    # ever seen; subsequent reboots / port_key renames only UPDATE port_key.
    c.execute("""
        CREATE TABLE IF NOT EXISTS port_physical_slots (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            user_id     INT NOT NULL,
            socket_id   VARCHAR(512) NOT NULL,
            port_key    VARCHAR(255) NOT NULL DEFAULT '',
            slot_number INT NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_user_socket   (user_id, socket_id),
            UNIQUE KEY uq_user_slot     (user_id, slot_number),
            INDEX       idx_user_portkey (user_id, port_key),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    conn.commit()

    # One-time migration: backfill port_physical_slots from existing usb_ports
    # rows so machines upgrading keep their current display numbers without any
    # dashboard disruption.  We use port_key as socket_id for pre-existing rows
    # (best-effort — we have no hub info for old rows; the agent will update
    # socket_id to the real hardware identity on the first scan after upgrade).
    # INSERT IGNORE prevents overwriting rows the agent already wrote correctly.
    try:
        import re as _re_bp
        c.execute("""
            SELECT DISTINCT user_id, port_key, port_name
            FROM usb_ports
            WHERE port_key IS NOT NULL AND port_key != ''
              AND port_name LIKE 'USB Port %'
        """)
        backfill_rows = c.fetchall()
        for user_id_bp, port_key_bp, port_name_bp in backfill_rows:
            m = _re_bp.search(r'USB Port (\d+)$', port_name_bp)
            if not m:
                continue
            slot_num_bp = int(m.group(1))
            c.execute("""
                INSERT IGNORE INTO port_physical_slots
                    (user_id, socket_id, port_key, slot_number)
                VALUES (%s, %s, %s, %s)
            """, (user_id_bp, port_key_bp, port_key_bp, slot_num_bp))
        conn.commit()
        if backfill_rows:
            print(f"Backfilled port_physical_slots: {len(backfill_rows)} existing port row(s)")
    except Exception as _e_bp:
        print(f"port_physical_slots backfill (non-fatal): {_e_bp}")

    # Seed super admin user (brand-new installs only — existing installs are
    # handled by the "promote legacy admin" migration above)
    c.execute("SELECT id FROM users WHERE role IN ('admin','super_admin') LIMIT 1")
    if not c.fetchone():
        c.execute(
            "INSERT INTO users (username, password, role, full_name, password_algo) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("admin", hash_password("admin123"), "super_admin", "System Admin",
             "bcrypt" if _HAS_BCRYPT else "sha256")
        )
        conn.commit()

    conn.close()



# ── Authentication ───────────────────────────────────────────────────────────

def authenticate(username: str, password: str):
    """Returns:
      - None                    → invalid username/password
      - the string "INACTIVE"   → credentials are correct but the account
                                   has been deactivated by the super admin
      - a dict                  → successful login. Includes
                                   "must_reset_password": True when the
                                   super admin just reactivated this admin
                                   account and it needs a new password
                                   before use (caller must handle this).
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, username, role, full_name, password, password_algo, "
        "branch, is_active, must_reset_password "
        "FROM users WHERE LOWER(username)=LOWER(%s)",
        (username,)
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return None

    (user_id, db_username, role, full_name, stored_hash, algo,
     branch, is_active, must_reset_password) = row
    algo = algo or "sha256"

    if not _verify_password(password, stored_hash, algo):
        conn.close()
        return None

    if is_active is not None and int(is_active) == 0:
        conn.close()
        return "INACTIVE"

    # Transparent upgrade: re-hash legacy sha256 → bcrypt
    if algo != "bcrypt" and _HAS_BCRYPT:
        new_hash = hash_password(password)
        c.execute(
            "UPDATE users SET password=%s, password_algo='bcrypt' WHERE id=%s",
            (new_hash, user_id)
        )
        conn.commit()

    conn.close()
    return {
        "id": user_id, "username": db_username, "role": role,
        "full_name": full_name, "branch": branch,
        "must_reset_password": bool(must_reset_password),
    }


def set_new_password(user_id: int, new_password: str):
    """Used for the forced 'set a new password' step after a super admin
    reactivates an admin account. Rejects the new password if it's the
    same as the password the account currently has (the old password from
    before it was deactivated), otherwise updates it and clears the
    must_reset_password flag."""
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            "SELECT password, password_algo FROM users WHERE id=%s",
            (user_id,)
        )
        row = c.fetchone()
        if not row:
            conn.close()
            return False, "Account not found."

        stored_hash, algo = row
        algo = algo or "sha256"
        if _verify_password(new_password, stored_hash, algo):
            conn.close()
            return False, "New password can't be the same as your old password."

        new_hash = hash_password(new_password)
        new_algo = "bcrypt" if _HAS_BCRYPT else "sha256"
        c.execute(
            "UPDATE users SET password=%s, password_algo=%s, must_reset_password=0, "
            "prev_password=%s, prev_password_algo=%s WHERE id=%s",
            (new_hash, new_algo, stored_hash, algo, user_id)
        )
        conn.commit()
        conn.close()
        return True, "Password updated."
    except Exception as e:
        conn.rollback()
        conn.close()
        return False, f"Database error: {str(e)}"


# ── Super admin: manage admin accounts ────────────────────────────────────────

def add_admin(username, password, full_name, branch):
    """Creates a new branch admin account. Only meant to be called from a
    super_admin session (enforced in the UI layer, same pattern the rest
    of this app already uses for role gating)."""
    if branch not in BRANCHES:
        return False, "Please choose a valid branch."

    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("SELECT id FROM users WHERE LOWER(username)=LOWER(%s)", (username,))
        if c.fetchone():
            conn.close()
            return False, "Username already exists."

        c.execute(
            "INSERT INTO users (username, password, role, full_name, password_algo, branch, is_active) "
            "VALUES (%s, %s, 'admin', %s, %s, %s, 1)",
            (username, hash_password(password), full_name,
             "bcrypt" if _HAS_BCRYPT else "sha256", branch)
        )
        conn.commit()
        conn.close()
        return True, "Admin added successfully."
    except Exception as e:
        conn.rollback()
        conn.close()
        return False, f"Database error: {str(e)}"


def set_admin_active(user_id: int, active: bool):
    """Activate/deactivate an admin account. Reactivating an account requires
    the super admin to set a new password for them."""
    conn = get_conn()
    c = conn.cursor()
    try:
        if active:
            c.execute(
                "UPDATE users SET is_active=1 WHERE id=%s AND role='admin'",
                (user_id,)
            )
        else:
            c.execute(
                "UPDATE users SET is_active=0 WHERE id=%s AND role='admin'",
                (user_id,)
            )
        conn.commit()
        conn.close()
        return True, "Admin account updated successfully."
    except Exception as e:
        conn.rollback()
        conn.close()
        return False, f"Database error: {str(e)}"


# ── USB ports / devices ──────────────────────────────────────────────────────

def get_or_assign_slot_number(user_id: int, socket_id: str,
                               current_port_key: str = "") -> int:
    """
    Returns the permanent display slot number (the N in "USB Port N") for
    a given physical socket.

    - If socket_id already has a row in port_physical_slots, returns its
      slot_number and updates port_key if it changed (Windows renamed the
      LocationInformation string for the same physical socket).
    - If not, assigns MAX(slot_number)+1 for this user (or 1 if first)
      and inserts a new row.  Uses a transaction + FOR UPDATE to prevent
      duplicate slot numbers on a race between two concurrent first-sees.

    socket_id     : stable physical socket identity from registry_utils.get_socket_id()
    current_port_key : current Windows LocationInformation string for this socket
    """
    conn = get_conn()
    c = conn.cursor()
    try:
        # Serialise concurrent first-time assignments with a table-level advisory.
        c.execute("SELECT slot_number, port_key FROM port_physical_slots "
                  "WHERE user_id=%s AND socket_id=%s FOR UPDATE",
                  (user_id, socket_id))
        row = c.fetchone()
        if row:
            slot_num, stored_pk = row
            # Update port_key if Windows renamed the LocationInformation string.
            if current_port_key and stored_pk != current_port_key:
                c.execute(
                    "UPDATE port_physical_slots "
                    "SET port_key=%s WHERE user_id=%s AND socket_id=%s",
                    (current_port_key, user_id, socket_id)
                )
                conn.commit()
            return slot_num

        # New socket — assign the next available number.
        c.execute(
            "SELECT COALESCE(MAX(slot_number), 0) FROM port_physical_slots WHERE user_id=%s",
            (user_id,)
        )
        max_slot = c.fetchone()[0]
        new_slot = max_slot + 1
        c.execute(
            "INSERT INTO port_physical_slots (user_id, socket_id, port_key, slot_number) "
            "VALUES (%s, %s, %s, %s)",
            (user_id, socket_id, current_port_key or socket_id, new_slot)
        )
        conn.commit()
        return new_slot
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"get_or_assign_slot_number error (falling back to 0): {e}")
        return 0
    finally:
        conn.close()


def get_all_ports():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, port_name, device_name,
               keyboard_enabled, mouse_enabled,
               storage_enabled, status,
               port_key, port_enabled,
               transfer_expires_at
        FROM usb_ports
        ORDER BY id
    """)
    rows = c.fetchall()
    conn.close()
    return rows


def _dedupe_and_sort_port_rows(rows):
    """
    Shared prep for the cosmetic port-name renumbering: dedupe to one row
    per port_key (safety net against stale duplicates surviving a
    migration) and naturally sort by port_name so "USB Port 2" sorts
    before "USB Port 18" (a plain SQL ORDER BY port_name does a string
    sort: "1" < "18" < "2"). Assumes index 1 = port_name, index 7 =
    port_key, matching get_ports_by_user's SELECT column order.
    """
    seen_keys = set()
    deduped = []
    for row in rows:
        port_key = row[7]  # index 7 = port_key
        if port_key and port_key not in seen_keys:
            seen_keys.add(port_key)
            deduped.append(row)
        elif not port_key:
            deduped.append(row)

    def _natural_key(row):
        name = row[1] or ""  # index 1 = port_name
        parts = re.split(r'(\d+)', name)
        return [int(p) if p.isdigit() else p.lower() for p in parts]

    deduped.sort(key=_natural_key)
    return deduped


def _renumber_for_display(rows):
    """
    Cosmetic renumbering for display only. On hardware where Windows'
    LocationInformation reporting is unstable (see agent_scanner.py /
    agent_db.py's port-reconciliation comments — the same physical socket
    can get relabeled from e.g. Port_#0017 to Port_#0018 between scans),
    every relabel mints a brand-new sequential name in the scanner even
    though the OLD name is what actually survives on the dashboard via
    DB-side reconciliation. The burned numbers are never reclaimed, so a
    machine with only 2-3 real sockets in active use can end up showing
    "USB Port 1, USB Port 6, USB Port 7" instead of "1, 2, 3" even though
    nothing about the actual ports is wrong.

    This does NOT touch port_key, the stored port_name in the DB, or
    anything authorization/grants/audit-log are keyed against — it only
    rewrites the label in THIS returned row set, every time it's fetched,
    so the visible numbering is always compact and sequential regardless
    of how many names got burned internally along the way. Assumes index
    1 = port_name, matching get_ports_by_user's SELECT column order.
    """
    phys_idx = 1
    unk_idx = 1
    out = []
    for row in rows:
        row = list(row)
        name = row[1] or ""  # index 1 = port_name
        if name.startswith("Unknown Port"):
            row[1] = f"Unknown Port {unk_idx}"
            unk_idx += 1
        elif name.startswith("USB Port"):
            row[1] = f"USB Port {phys_idx}"
            phys_idx += 1
        out.append(tuple(row))
    return out


def _display_port_name_map(user_id: int) -> dict:
    """
    Builds the same port_key -> cosmetic-display-name mapping the USB
    Port Details tab uses (get_ports_by_user), so any other tab can show
    an identical, stable "USB Port 1 … N" label for the same physical
    port instead of the raw, internally-escalating port_name stored on
    its own row.
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id,
               port_name,
               device_name,
               keyboard_enabled,
               mouse_enabled,
               storage_enabled,
               status,
               port_key,
               port_enabled,
               transfer_expires_at,
               connected,
               device_type,
               storage_admin_set
        FROM usb_ports
        WHERE user_id = %s
          AND port_key NOT IN ('VIRTUAL_KBD', 'VIRTUAL_MSE')
        ORDER BY id
    """, (user_id,))
    rows = c.fetchall()
    conn.close()

    deduped = _dedupe_and_sort_port_rows(rows)
    displayed = _renumber_for_display(deduped)
    return {row[7]: row[1] for row in displayed}  # port_key -> display name


def get_ports_by_user(user_id: int):
    """
    Returns one row per physical port_key for this user, including empty
    (no device connected) external USB ports so that Storage, Keyboard,
    Mouse, WebCam and Printer slots are always visible in the dashboard
    even when nothing is currently plugged in.

    The agent uses a DELETE+re-INSERT strategy on every sync cycle, so
    there is at most one row per (user_id, port_key) at any time. Rows
    are ordered by port_name for natural "USB Port 1 … N" sequencing.
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id,
               port_name,
               device_name,
               keyboard_enabled,
               mouse_enabled,
               storage_enabled,
               status,
               port_key,
               port_enabled,
               transfer_expires_at,
               connected,
               device_type,
               storage_admin_set
        FROM usb_ports
        WHERE user_id = %s
          AND port_key NOT IN ('VIRTUAL_KBD', 'VIRTUAL_MSE')
        ORDER BY id
    """, (user_id,))
    rows = c.fetchall()
    conn.close()

    deduped = _dedupe_and_sort_port_rows(rows)
    return _renumber_for_display(deduped)


def get_devices_by_user(user_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, device_name, device_type, port_name, status, port_key
        FROM devices
        WHERE user_id=%s
          AND port_name NOT IN ('System Keyboard', 'System Mouse')
        ORDER BY id
    """, (user_id,))
    rows = c.fetchall()
    conn.close()

    # Show the exact same cosmetic "USB Port 1 … N" label the USB Port
    # Details tab shows for the same physical port_key, instead of this
    # row's own raw port_name — which can carry an internally-escalating
    # or collision-suffixed name (e.g. "USB Port 10 (2)") left over from
    # port-key churn on unstable hardware. Falls back to the raw
    # port_name when a device's port_key has no match in usb_ports (e.g.
    # a device row from a cycle that hasn't synced to usb_ports yet).
    display_map = _display_port_name_map(user_id)
    out = []
    for row in rows:
        dev_id, dev_name, dev_type, port_name, status, port_key = row
        display_name = display_map.get(port_key, port_name)
        out.append((dev_id, dev_name, dev_type, display_name, status))
    return out


def update_port_toggle(port_id: int, field: str, value: int,
                        changed_by_id=None, changed_by_name: str = "") -> bool:
    """
    Returns True if the toggle was applied, False if it was rejected
    (bad field) or the port_id no longer exists (stale row, e.g. after
    a device resync deleted/reinserted usb_ports). Callers should check
    this return value and refresh their data + re-show the failure to
    the user instead of assuming the write succeeded.
    """
    # Explicit allowlist maps caller-supplied field name → verified column name.
    # Never interpolate field directly into SQL — use this dict so the column
    # name in the query is always a trusted literal, not caller-controlled input.
    _SAFE_COLUMNS = {
        "keyboard_enabled": "keyboard_enabled",
        "mouse_enabled":    "mouse_enabled",
        "storage_enabled":  "storage_enabled",
        "status":           "status",
        "port_enabled":     "port_enabled",
    }
    if field not in _SAFE_COLUMNS:
        return False
    col = _SAFE_COLUMNS[field]

    conn = get_conn()
    c = conn.cursor()

    c.execute(
        f"SELECT {col}, user_id, port_name, port_key FROM usb_ports WHERE id=%s",
        (port_id,)
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    old_value, user_id, port_name, port_key = row

    if field == "port_enabled":
        # A physical port can have more than one usb_ports ROW (composite
        # devices register multiple HID/USB entities at the same port_key,
        # and an empty-port placeholder is also one row). Apply the toggle
        # to every row sharing this port_key, not just the clicked row, so
        # the dashboard reflects one switch per physical port as intended.
        #
        # Port Access now governs keyboard/mouse/webcam only. Storage is
        # deliberately NOT touched here — it has its own independent
        # policy (see the "storage_enabled" branch below), so turning a
        # port on/off for input devices never grants or revokes storage
        # access on that same port.
        if port_key:
            c.execute(
                """UPDATE usb_ports
                   SET port_enabled=%s, port_admin_set=1, status=%s,
                       keyboard_enabled = CASE WHEN device_type='Keyboard' THEN %s ELSE keyboard_enabled END,
                       mouse_enabled    = CASE WHEN device_type='Mouse'    THEN %s ELSE mouse_enabled END
                   WHERE user_id=%s AND port_key=%s""",
                (value, "ON" if value else "OFF", value, value, user_id, port_key)
            )
            # Match by port_key, not port_name: two different physical ports
            # (different port_key) can share the same displayed port_name,
            # so matching on port_name here would also flip devices that
            # sit on a different port than the one actually toggled.
            c.execute(
                "UPDATE devices SET status=%s WHERE user_id=%s AND port_key=%s AND device_type != 'Storage'",
                ("Allowed" if value else "Blocked", user_id, port_key)
            )
        else:
            c.execute("""UPDATE usb_ports
                         SET port_enabled=%s, port_admin_set=1, status=%s,
                             keyboard_enabled = CASE WHEN device_type='Keyboard' THEN %s ELSE keyboard_enabled END,
                             mouse_enabled    = CASE WHEN device_type='Mouse'    THEN %s ELSE mouse_enabled END
                         WHERE id=%s""",
                      (value, "ON" if value else "OFF", value, value, port_id))
    elif field == "storage_enabled":
        # Storage Access is its own independent, persistent per-port
        # policy — decoupled from Port Access entirely. Setting
        # storage_admin_set=1 marks that an admin has made a real,
        # explicit decision about storage on this physical port; the
        # agent sync (agent_db.py) carries this forward unchanged across
        # unplug/replug and regardless of what device type next occupies
        # the port, instead of resetting to the type default. Applies to
        # every row sharing this port_key (including the empty-port
        # placeholder), so the grant is already in effect before a device
        # is even plugged in. No expiry — this stays ON until an admin
        # explicitly turns it off.
        if port_key:
            c.execute(
                """UPDATE usb_ports
                   SET storage_enabled=%s, storage_admin_set=1, transfer_expires_at=NULL
                   WHERE user_id=%s AND port_key=%s""",
                (value, user_id, port_key)
            )
            c.execute(
                "UPDATE devices SET status=%s WHERE user_id=%s AND port_key=%s AND device_type='Storage'",
                ("Allowed" if value else "Blocked", user_id, port_key)
            )
        else:
            c.execute(
                """UPDATE usb_ports
                   SET storage_enabled=%s, storage_admin_set=1, transfer_expires_at=NULL
                   WHERE id=%s""",
                (value, port_id)
            )
    else:
        c.execute(f"UPDATE usb_ports SET {col}=%s WHERE id=%s", (value, port_id))

    if field in ("keyboard_enabled", "mouse_enabled", "storage_enabled"):
        c.execute("""
            SELECT keyboard_enabled, mouse_enabled, storage_enabled, user_id, port_name, port_key
            FROM usb_ports WHERE id=%s
        """, (port_id,))
        r = c.fetchone()
        if r:
            kbd_en, mse_en, stor_en, user_id, port_name, port_key = r
            overall = "ON" if (kbd_en or mse_en or stor_en) else "OFF"
            c.execute("UPDATE usb_ports SET status=%s WHERE id=%s", (overall, port_id))

            if port_key:
                c.execute(
                    "SELECT id, device_type FROM devices WHERE user_id=%s AND port_key=%s",
                    (user_id, port_key)
                )
            else:
                c.execute(
                    "SELECT id, device_type FROM devices WHERE user_id=%s AND port_name=%s",
                    (user_id, port_name)
                )
            for dev_id, dev_type in c.fetchall():
                if dev_type == "Keyboard":
                    dev_status = "Allowed" if kbd_en else "Blocked"
                elif dev_type == "Mouse":
                    dev_status = "Allowed" if mse_en else "Blocked"
                else:
                    dev_status = "Allowed" if stor_en else "Blocked"
                c.execute("UPDATE devices SET status=%s WHERE id=%s", (dev_status, dev_id))
    

    c.execute("SELECT username FROM users WHERE id=%s", (user_id,))
    u_row = c.fetchone()
    user_name_snapshot = u_row[0] if u_row else ""

    c.execute("""
        INSERT INTO audit_logs
            (user_id, user_name_snapshot, action, old_value, new_value,
             changed_by_id, changed_by_name, port_id, port_name_snapshot)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        user_id, user_name_snapshot, field,
        str(old_value), str(value),
        changed_by_id, changed_by_name or "",
        port_id, port_name
    ))

    c.execute(
        "UPDATE users SET settings_changed_at = NOW() WHERE id = %s",
        (user_id,)
    )
    conn.commit()   # single commit covers all writes above — one round-trip saved
    conn.close()
    return True


# ── Audit log ─────────────────────────────────────────────────────────────────



def get_audit_logs(user_id=None, limit: int = 200):
    conn = get_conn()
    c = conn.cursor()
    if user_id is not None:
        c.execute("""
            SELECT a.id, COALESCE(NULLIF(u.full_name, ''), a.user_name_snapshot), a.action, a.old_value, a.new_value,
                   a.changed_by_name, a.port_name_snapshot, a.created_at
            FROM audit_logs a
            LEFT JOIN users u ON a.user_id = u.id
            WHERE a.user_id=%s
            ORDER BY a.created_at DESC
            LIMIT %s
        """, (user_id, limit))
    else:
        c.execute("""
            SELECT a.id, COALESCE(NULLIF(u.full_name, ''), a.user_name_snapshot), a.action, a.old_value, a.new_value,
                   a.changed_by_name, a.port_name_snapshot, a.created_at
            FROM audit_logs a
            LEFT JOIN users u ON a.user_id = u.id
            ORDER BY a.created_at DESC
            LIMIT %s
        """, (limit,))
    rows = c.fetchall()
    conn.close()
    formatted = []
    for row in rows:
        r = list(row)
        if r[7]:
            r[7] = r[7].strftime("%Y-%m-%d %H:%M:%S")
        formatted.append(r)
    return formatted

def clear_audit_logs():
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("DELETE FROM audit_logs")
        conn.commit()
    except Exception as e:
        print(f"clear_audit_logs error: {e}")
        conn.rollback()
    finally:
        conn.close()


# ── Agent heartbeat / status ────────────────────────────────────────────────









# ── System settings (dry-run / fail-open) ───────────────────────────────────













# ── USB requests ─────────────────────────────────────────────────────────────

def submit_request(user_id: int, port_id: int, reason: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id FROM usb_requests WHERE user_id=%s AND port_id=%s AND status='pending'",
        (user_id, port_id)
    )
    if c.fetchone():
        conn.close()
        return False, "You already have a pending request for this port."
    c.execute(
        "INSERT INTO usb_requests (user_id, port_id, reason) VALUES (%s, %s, %s)",
        (user_id, port_id, reason)
    )
    conn.commit()
    conn.close()
    return True, "Request submitted successfully."


def get_pending_requests(viewer_role=None, viewer_branch=None):
    conn = get_conn()
    c = conn.cursor()
    if viewer_role == 'admin' and viewer_branch:
        # Normal admin: only see requests from users in their own branch
        c.execute("""
            SELECT r.id, u.full_name, u.username, p.port_name, p.device_name, r.reason, r.requested_at
            FROM usb_requests r
            JOIN users u ON r.user_id=u.id
            JOIN usb_ports p ON r.port_id=p.id
            WHERE r.status='pending'
              AND u.branch=%s
            ORDER BY r.requested_at DESC
        """, (viewer_branch,))
    else:
        # Super admin: see all pending requests
        c.execute("""
            SELECT r.id, u.full_name, u.username, p.port_name, p.device_name, r.reason, r.requested_at
            FROM usb_requests r
            JOIN users u ON r.user_id=u.id
            JOIN usb_ports p ON r.port_id=p.id
            WHERE r.status='pending'
            ORDER BY r.requested_at DESC
        """)
    rows = c.fetchall()
    conn.close()
    formatted = []
    for row in rows:
        r = list(row)
        if r[6]:
            r[6] = r[6].strftime("%Y-%m-%d %H:%M:%S")
        formatted.append(r)
    return formatted


def get_all_requests(viewer_role=None, viewer_branch=None):
    conn = get_conn()
    c = conn.cursor()
    if viewer_role == 'admin' and viewer_branch:
        c.execute("""
            SELECT r.id, u.full_name, p.port_name, r.reason, r.status, r.requested_at, r.resolved_at
            FROM usb_requests r
            JOIN users u ON r.user_id=u.id
            JOIN usb_ports p ON r.port_id=p.id
            WHERE u.branch=%s
            ORDER BY r.requested_at DESC
            LIMIT 50
        """, (viewer_branch,))
    else:
        c.execute("""
            SELECT r.id, u.full_name, p.port_name, r.reason, r.status, r.requested_at, r.resolved_at
            FROM usb_requests r
            JOIN users u ON r.user_id=u.id
            JOIN usb_ports p ON r.port_id=p.id
            ORDER BY r.requested_at DESC
            LIMIT 50
        """)
    rows = c.fetchall()
    conn.close()
    formatted = []
    for row in rows:
        r = list(row)
        if r[5]:
            r[5] = r[5].strftime("%Y-%m-%d %H:%M:%S")
        if r[6]:
            r[6] = r[6].strftime("%Y-%m-%d %H:%M:%S")
        formatted.append(r)
    return formatted


_DEFAULT_TRANSFER_HOURS = 8  # default grant duration when admin approves without specifying


def resolve_request(request_id: int, action: str,
                     changed_by_id=None, changed_by_name: str = "",
                     expiry_hours: int = _DEFAULT_TRANSFER_HOURS):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT port_id, user_id FROM usb_requests WHERE id=%s", (request_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return
    port_id, user_id = row
    c.execute(
        "UPDATE usb_requests SET status=%s, resolved_at=NOW() WHERE id=%s",
        (action, request_id)
    )

    c.execute("SELECT storage_enabled, port_name, port_key FROM usb_ports WHERE id=%s", (port_id,))
    p_row = c.fetchone()
    old_storage = p_row[0] if p_row else None
    port_name   = p_row[1] if p_row else ""
    port_key    = p_row[2] if p_row else ""

    if action == "approved":
        # Set transfer_expires_at so the agent's _expire_stale_transfer_grants()
        # automatically revokes the grant after expiry_hours. Without this the
        # grant was permanent and would never be cleaned up by the expiry logic.
        c.execute("""
            UPDATE usb_ports
               SET storage_enabled=1,
                   storage_admin_set=1,
                   status='ON',
                   transfer_expires_at = DATE_ADD(NOW(), INTERVAL %s HOUR)
             WHERE id=%s
        """, (expiry_hours, port_id))
        if port_key:
            c.execute("""
                UPDATE devices SET status='Allowed'
                WHERE user_id=%s AND port_key=%s AND device_type NOT IN ('Keyboard', 'Mouse')
            """, (user_id, port_key))
        else:
            c.execute("""
                UPDATE devices SET status='Allowed'
                WHERE user_id=%s AND port_name=%s AND device_type NOT IN ('Keyboard', 'Mouse')
            """, (user_id, port_name))
        new_storage = 1
    elif action == "rejected":
        c.execute("UPDATE usb_ports SET storage_enabled=0, storage_admin_set=1 WHERE id=%s", (port_id,))
        if port_key:
            c.execute("""
                UPDATE devices SET status='Blocked'
                WHERE user_id=%s AND port_key=%s AND device_type NOT IN ('Keyboard', 'Mouse')
            """, (user_id, port_key))
        else:
            c.execute("""
                UPDATE devices SET status='Blocked'
                WHERE user_id=%s AND port_name=%s AND device_type NOT IN ('Keyboard', 'Mouse')
            """, (user_id, port_name))
        new_storage = 0
    else:
        new_storage = old_storage

    # ✅ ADD THIS: Sync device_type from devices to usb_ports
    c.execute("""
        UPDATE usb_ports u
        JOIN devices d ON u.user_id = d.user_id AND u.port_name = d.port_name
        SET u.device_type = d.device_type
        WHERE u.id = %s
    """, (port_id,))

    c.execute("SELECT username FROM users WHERE id=%s", (user_id,))
    u_row = c.fetchone()
    user_name_snapshot = u_row[0] if u_row else ""

    c.execute("""
        INSERT INTO audit_logs
            (user_id, user_name_snapshot, action, old_value, new_value,
             changed_by_id, changed_by_name, port_id, port_name_snapshot)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        user_id, user_name_snapshot, f"storage_request_{action}",
        str(old_storage), str(new_storage),
        changed_by_id, changed_by_name or "",
        port_id, port_name
    ))

    conn.commit()
    # Bump so the agent re-applies the new storage setting promptly
    c.execute("UPDATE users SET settings_changed_at = NOW() WHERE id = %s", (user_id,))
    conn.commit()
    conn.close()

def get_pending_port_ids_for_user(user_id: int) -> set:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT port_id FROM usb_requests WHERE user_id=%s AND status='pending'",
        (user_id,)
    )
    rows = c.fetchall()
    conn.close()
    return {r[0] for r in rows}


def get_user_requests(user_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT r.id, p.port_name, p.device_name, r.reason, r.status, r.requested_at
        FROM usb_requests r
        JOIN usb_ports p ON r.port_id=p.id
        WHERE r.user_id=%s
        ORDER BY r.requested_at DESC
    """, (user_id,))
    rows = c.fetchall()
    conn.close()
    formatted = []
    for row in rows:
        r = list(row)
        if r[5]:
            r[5] = r[5].strftime("%Y-%m-%d %H:%M:%S")
        formatted.append(r)
    return formatted


# ── Unregistered agent alerts ────────────────────────────────────────────────

def get_unregistered_agents(include_dismissed=False):
    """Returns agents that have checked in but don't match any user in `users`,
    most-recently-seen first. Used to power the sidebar alert badge + panel."""
    conn = get_conn()
    c = conn.cursor(dictionary=True)
    where = "" if include_dismissed else "WHERE dismissed = 0"
    c.execute(f"""
        SELECT id, resolved_username, hostname, agent_version, first_seen, last_seen
        FROM unregistered_agents
        {where}
        ORDER BY last_seen DESC
    """)
    rows = c.fetchall()
    conn.close()
    return rows


def count_unregistered_agents():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM unregistered_agents WHERE dismissed = 0")
    n = c.fetchone()[0]
    conn.close()
    return n


def dismiss_unregistered_agent(entry_id):
    """Hides one alert (e.g. after you've added it as a system, or it's a
    stray/decommissioned machine you don't care about)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE unregistered_agents SET dismissed = 1 WHERE id = %s", (entry_id,))
    conn.commit()
    conn.close()


def delete_unregistered_agent(entry_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM unregistered_agents WHERE id = %s", (entry_id,))
    conn.commit()
    conn.close()


# ── User / system management ────────────────────────────────────────────────

def add_system(username, password, full_name, branch=None):
    """
    Creates a new user account (an employee/monitored PC). Does NOT seed
    default/dummy USB ports — the agent's first-run scan on that user's
    PC will populate their real device list in usb_ports/devices.

    branch must be one of BRANCHES. A normal admin should only ever pass
    their own branch here (enforced in the UI by locking the branch
    field); a super admin may pass any branch.
    """
    if branch not in BRANCHES:
        return False, "Please choose a valid branch."

    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            "SELECT id FROM users WHERE LOWER(username)=LOWER(%s)",
            (username,)
        )
        if c.fetchone():
            conn.close()
            return False, "Username already exists."

        c.execute(
            "INSERT INTO users (username, password, role, full_name, password_algo, branch) "
            "VALUES (%s, %s, 'user', %s, %s, %s)",
            (username, hash_password(password), full_name,
             "bcrypt" if _HAS_BCRYPT else "sha256", branch)
        )
        user_id = c.lastrowid

        # If this username was previously flagged as an unregistered agent,
        # clear that alert now that it's a real registered system.
        c.execute(
            "DELETE FROM unregistered_agents WHERE LOWER(resolved_username)=LOWER(%s)",
            (username,)
        )

        conn.commit()
        conn.close()
        return True, "System added successfully."
    except Exception as e:
        conn.rollback()
        conn.close()
        return False, f"Database error: {str(e)}"


def update_system(user_id, username, full_name, password=None, branch=None):
    """
    Updates an existing system's full name and system username, and
    optionally its password and branch. Pass password=None (or "") to
    leave the existing password unchanged; pass branch=None to leave the
    existing branch unchanged. Editing is a super-admin-only action,
    enforced in the UI layer (Edit is hidden for normal admins).
    """
    if branch is not None and branch not in BRANCHES:
        return False, "Please choose a valid branch."

    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            "SELECT id FROM users WHERE LOWER(username)=LOWER(%s) AND id<>%s",
            (username, user_id)
        )
        if c.fetchone():
            conn.close()
            return False, "Username already exists."

        sets, params = ["username=%s", "full_name=%s"], [username, full_name]
        if password:
            sets += ["password=%s", "password_algo=%s"]
            params += [hash_password(password), "bcrypt" if _HAS_BCRYPT else "sha256"]
        if branch is not None:
            sets.append("branch=%s")
            params.append(branch)
        params.append(user_id)

        c.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=%s", params)

        conn.commit()
        conn.close()
        return True, "System updated successfully."
    except Exception as e:
        conn.rollback()
        conn.close()
        return False, f"Database error: {str(e)}"


def get_all_systems(viewer_role=None, viewer_branch=None, viewer_id=None):
    """Returns registered accounts (admins + employees - never the
    super_admin row itself) for the 'Registered Systems' table and the
    Port Controls system selector.

    Scoping:
      - viewer_role == 'super_admin' (or None, for back-compat callers):
        every admin + employee account, across all branches.
      - viewer_role == 'admin': only employees ('user' role) in that
        admin's own branch - an admin never sees other admins or other
        branches' employees.
    """
    conn = get_conn()
    c = conn.cursor()
    params = []
    
    if viewer_role == ROLE_ADMIN:
        query = (
            "SELECT id, username, full_name, role, branch, is_active "
            "FROM users WHERE (role='user' AND branch=%s)"
        )
        params.append(viewer_branch)
        if viewer_id:
            query += " OR id=%s"
            params.append(viewer_id)
        query += " ORDER BY id"
    else:
        query = (
            "SELECT id, username, full_name, role, branch, is_active "
            "FROM users WHERE role IN ('admin','user')"
        )
        if viewer_id:
            query += " OR id=%s"
            params.append(viewer_id)
        query += " ORDER BY role DESC, id"
        
    if params:
        c.execute(query, tuple(params))
    else:
        c.execute(query)
    rows = c.fetchall()
    conn.close()
    return [
        {"id": r[0], "username": r[1], "full_name": r[2], "role": r[3],
         "branch": r[4], "is_active": bool(r[5]) if r[5] is not None else True}
        for r in rows
    ]


def get_all_admins():
    """Super-admin-only: every admin account, for account management
    (activate/deactivate)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, username, full_name, branch, is_active "
        "FROM users WHERE role='admin' ORDER BY branch, id"
    )
    rows = c.fetchall()
    conn.close()
    return [
        {"id": r[0], "username": r[1], "full_name": r[2], "branch": r[3],
         "is_active": bool(r[4]) if r[4] is not None else True}
        for r in rows
    ]


def get_all_slot_requests(viewer_role=None, viewer_branch=None):
    """Return all slot requests (pending + resolved) for Request History."""
    conn = get_conn()
    c = conn.cursor()
    try:
        if viewer_role == 'admin' and viewer_branch:
            c.execute("""
                SELECT sr.id, u.full_name, sr.slot_name, sr.reason, sr.status,
                       sr.requested_at, sr.resolved_at
                FROM slot_requests sr
                JOIN users u ON sr.user_id = u.id
                WHERE u.branch = %s
                ORDER BY sr.requested_at DESC
                LIMIT 50
            """, (viewer_branch,))
        else:
            c.execute("""
                SELECT sr.id, u.full_name, sr.slot_name, sr.reason, sr.status,
                       sr.requested_at, sr.resolved_at
                FROM slot_requests sr
                JOIN users u ON sr.user_id = u.id
                ORDER BY sr.requested_at DESC
                LIMIT 50
            """)
        rows = c.fetchall()
    except Exception:
        rows = []
    conn.close()
    formatted = []
    for row in rows:
        r = list(row)
        if r[5]:
            r[5] = r[5].strftime("%Y-%m-%d %H:%M:%S")
        if r[6]:
            r[6] = r[6].strftime("%Y-%m-%d %H:%M:%S")
        formatted.append(r)
    return formatted


def clear_resolved_requests():
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM usb_requests WHERE status IN ('approved', 'rejected')")
    try:
        c.execute("DELETE FROM slot_requests WHERE status IN ('approved', 'rejected')")
    except Exception:
        pass
    conn.commit()
    conn.close()


def delete_system(user_id: int):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("SELECT username, role FROM users WHERE id=%s", (user_id,))
        u_row = c.fetchone()
        if u_row and u_row[1] == ROLE_SUPER_ADMIN:
            conn.close()
            return False, "The super admin account can't be deleted."
        if u_row:
            c.execute("""
                UPDATE audit_logs SET user_name_snapshot=%s
                WHERE user_id=%s AND (user_name_snapshot='' OR user_name_snapshot IS NULL)
            """, (u_row[0], user_id))

        c.execute("UPDATE audit_logs SET user_id=NULL WHERE user_id=%s", (user_id,))
        c.execute("DELETE FROM users WHERE id=%s", (user_id,))
        conn.commit()
        conn.close()
        return True, "System deleted successfully."
    except Exception as e:
        conn.rollback()
        conn.close()
        return False, f"Database error: {str(e)}"


# ── Available (unused) USB port slots ────────────────────────────────────────



def get_available_slots_by_user(user_id: int):
    """Returns list of available (empty) USB slots for the user."""
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT id, slot_name, slot_id, controller, location, updated_at
            FROM available_usb_slots
            WHERE user_id=%s
            ORDER BY slot_name
        """, (user_id,))
        rows = c.fetchall()
    except Exception:
        rows = []
    conn.close()
    return rows

def resolve_slot_request(request_id: int, action: str, changed_by_id=None, changed_by_name: str = "", expiry_hours: int = _DEFAULT_TRANSFER_HOURS):
    """Resolve a slot request (approve/reject)."""
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("SELECT user_id, slot_name FROM slot_requests WHERE id=%s", (request_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return
        
        user_id, slot_name = row
        c.execute("UPDATE slot_requests SET status=%s, resolved_at=NOW() WHERE id=%s", (action, request_id))
        
        if action == "approved":
            c.execute("""
                UPDATE usb_ports
                   SET storage_enabled=1,
                       storage_admin_set=1,
                       port_enabled=1,
                       port_admin_set=1,
                       status='ON',
                       transfer_expires_at = DATE_ADD(NOW(), INTERVAL %s HOUR)
                 WHERE user_id=%s AND port_name=%s
            """, (expiry_hours, user_id, slot_name))
        elif action == "rejected":
            c.execute("UPDATE usb_ports SET storage_enabled=0, storage_admin_set=1 WHERE user_id=%s AND port_name=%s", (user_id, slot_name))

        # Log the action
        c.execute("SELECT username FROM users WHERE id=%s", (user_id,))
        u_row = c.fetchone()
        user_name = u_row[0] if u_row else ""
        
        c.execute("""
            INSERT INTO audit_logs
                (user_id, user_name_snapshot, action, old_value, new_value,
                 changed_by_id, changed_by_name, port_name_snapshot)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            user_id, user_name, f"slot_request_{action}",
            slot_name, action,
            changed_by_id, changed_by_name or "",
            slot_name
        ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error resolving slot request: {e}")
    finally:
        conn.close()


def submit_slot_request(user_id: int, slot_id_str: str, reason: str):
    """
    Submit a file-transfer access request for an empty/available USB slot.
    Stores slot identifier in a dedicated table.
    Table is guaranteed to exist after init_db() runs at startup.
    """
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            "SELECT id FROM slot_requests WHERE user_id=%s AND slot_name=%s AND status='pending'",
            (user_id, slot_id_str)
        )
        if c.fetchone():
            conn.close()
            return False, "You already have a pending request for this port slot."
        c.execute(
            "INSERT INTO slot_requests (user_id, slot_name, reason) VALUES (%s, %s, %s)",
            (user_id, slot_id_str, reason)
        )
        conn.commit()
        conn.close()
        return True, "Request submitted. An admin will review it shortly."
    except Exception as e:
        conn.close()
        return False, f"Error: {e}"


def get_slot_requests_by_user(user_id: int):
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT id, slot_name, reason, status, requested_at
            FROM slot_requests
            WHERE user_id=%s
            ORDER BY requested_at DESC
        """, (user_id,))
        rows = c.fetchall()
    except Exception:
        rows = []
    conn.close()
    formatted = []
    for row in rows:
        r = list(row)
        if r[4]:
            r[4] = r[4].strftime("%Y-%m-%d %H:%M:%S")
        formatted.append(r)
    return formatted


def get_pending_slot_names_for_user(user_id: int) -> set:
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            "SELECT slot_name FROM slot_requests WHERE user_id=%s AND status='pending'",
            (user_id,)
        )
        rows = c.fetchall()
    except Exception:
        rows = []
    conn.close()
    return {r[0] for r in rows}


def get_pending_slot_requests(viewer_role=None, viewer_branch=None):
    """For admin panel — pending slot requests scoped by viewer role."""
    conn = get_conn()
    c = conn.cursor()
    try:
        if viewer_role == 'admin' and viewer_branch:
            # Normal admin: only see slot requests from users in their own branch
            c.execute("""
                SELECT sr.id, u.full_name, u.username, sr.slot_name, sr.reason, sr.requested_at
                FROM slot_requests sr
                JOIN users u ON sr.user_id = u.id
                WHERE sr.status = 'pending'
                  AND u.branch = %s
                ORDER BY sr.requested_at DESC
            """, (viewer_branch,))
        else:
            # Super admin: see all pending slot requests
            c.execute("""
                SELECT sr.id, u.full_name, u.username, sr.slot_name, sr.reason, sr.requested_at
                FROM slot_requests sr
                JOIN users u ON sr.user_id = u.id
                WHERE sr.status = 'pending'
                ORDER BY sr.requested_at DESC
            """)
        rows = c.fetchall()
    except Exception:
        rows = []
    conn.close()
    formatted = []
    for row in rows:
        r = list(row)
        if r[5]:
            r[5] = r[5].strftime("%Y-%m-%d %H:%M:%S")
        formatted.append(r)
    return formatted

