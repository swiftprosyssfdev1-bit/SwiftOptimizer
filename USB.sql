SET SQL_SAFE_UPDATES = 0;

DROP DATABASE IF EXISTS swiftoptimizer;
CREATE DATABASE IF NOT EXISTS swiftoptimizer;
USE swiftoptimizer;

-- ── Core tables ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    username            VARCHAR(255) UNIQUE NOT NULL,
    password            VARCHAR(255) NOT NULL,
    role                VARCHAR(50)  NOT NULL,
    full_name           VARCHAR(255) DEFAULT '',
    password_algo       VARCHAR(20)  DEFAULT 'sha256',
    settings_changed_at TIMESTAMP    NULL DEFAULT NULL,
    branch              VARCHAR(100) DEFAULT NULL,
    is_active           TINYINT(1)   NOT NULL DEFAULT 1,
    must_reset_password TINYINT(1)   NOT NULL DEFAULT 0,
    prev_password       VARCHAR(255) DEFAULT NULL,
    prev_password_algo  VARCHAR(20)  DEFAULT NULL
);

-- usb_ports: every column here is one db.py / usb_agent.py actually
-- SELECTs or INSERTs. port_key is the physical-port identity (stable
-- across replug/reconnect); the UNIQUE KEY below is what stops the
-- same physical port from ending up as two rows ("USB Port 1" twice).
CREATE TABLE IF NOT EXISTS usb_ports (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    user_id             INT NOT NULL,
    port_name           VARCHAR(100) NOT NULL,
    device_name         VARCHAR(255) DEFAULT 'Unknown',
    device_type         VARCHAR(50)  DEFAULT 'Storage',
    port_key            VARCHAR(255) DEFAULT '',
    port_enabled        INT DEFAULT 1,
    keyboard_enabled    INT DEFAULT 1,
    mouse_enabled       INT DEFAULT 1,
    storage_enabled     INT DEFAULT 0,
    transfer_expires_at TIMESTAMP NULL DEFAULT NULL,
    status              VARCHAR(50)  DEFAULT 'ON',
    device_id           VARCHAR(255) DEFAULT '',
    updated_by          VARCHAR(100) DEFAULT NULL,
    last_updated        TIMESTAMP NULL DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
    connected           INT DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    -- Scoped to device_id, not just (user_id, port_key): a composite USB
    -- device (e.g. a wireless keyboard+mouse combo dongle) can register
    -- more than one HID function at the SAME physical port, and the
    -- agent legitimately inserts one row per device_id sharing that
    -- port_key. Constraining on port_key alone would reject the second
    -- device's insert and break the whole sync.
    UNIQUE KEY uq_user_port_device (user_id, port_key, device_id)
);

CREATE TABLE IF NOT EXISTS devices (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT NOT NULL,
    device_name VARCHAR(255) NOT NULL,
    device_type VARCHAR(100) NOT NULL,
    port_name   VARCHAR(100) NOT NULL,
    port_key    VARCHAR(255) DEFAULT '',
    status      VARCHAR(50)  NOT NULL,
    connected   INT DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

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
);

-- ── Empty/available physical slots (separate from occupied usb_ports) ──

CREATE TABLE IF NOT EXISTS available_usb_slots (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    user_id      INT NOT NULL,
    slot_name    VARCHAR(100) NOT NULL,
    slot_id      VARCHAR(255) DEFAULT '',
    controller   VARCHAR(255) DEFAULT '',
    location     VARCHAR(255) DEFAULT '',
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS slot_requests (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    user_id      INT NOT NULL,
    slot_name    VARCHAR(100) NOT NULL,
    reason       TEXT,
    status       VARCHAR(50) DEFAULT 'pending',
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at  TIMESTAMP NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ── Audit log ─────────────────────────────────────────────────

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
);

-- ── Agent heartbeat / last-seen ───────────────────────────────

CREATE TABLE IF NOT EXISTS agent_status (
    user_id         INT NOT NULL PRIMARY KEY,
    hostname        VARCHAR(255) DEFAULT '',
    agent_version   VARCHAR(50)  DEFAULT '',
    last_checkin    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    kbd_blocked     TINYINT      DEFAULT 0,
    mse_blocked     TINYINT      DEFAULT 0,
    hooks_installed TINYINT      DEFAULT 0,
    reboot_pending  TINYINT      DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ── Global settings (dry-run / fail-open) ────────────────────

CREATE TABLE IF NOT EXISTS system_settings (
    setting_key   VARCHAR(100) PRIMARY KEY,
    setting_value VARCHAR(255) DEFAULT ''
);

INSERT INTO system_settings (setting_key, setting_value) VALUES
    ('dry_run_mode', '0'),
    ('fail_open_after_minutes', '5')
ON DUPLICATE KEY UPDATE setting_key = setting_key;

-- ── Seed super admin account ─────────────────────────────────
-- Default credentials: username=admin  password=admin123c
-- Password is SHA-256 hashed — log in once and change it.
-- This is the one top-level super admin: it can add branch admins
-- (Chennai / Tindivanam / Madurai / Kanchipuram) and employees.

INSERT INTO users (username, password, role, full_name, password_algo)
SELECT 'admin',
       '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9',
       'super_admin',
       'System Admin',
       'sha256'
WHERE NOT EXISTS (
    SELECT 1 FROM users WHERE username = 'admin'
);

-- ── Done ─────────────────────────────────────────────────────

SELECT 'Database setup complete (v7 — clean rebuild).' AS status;
SELECT id, username, role, full_name FROM users;
SHOW TABLES;

ALTER TABLE usb_ports ADD COLUMN port_admin_set INT DEFAULT 0;
UPDATE usb_ports SET port_admin_set = 1 WHERE port_enabled = 0;

-- Storage Access is now independent of Port Access — its own persistent
-- per-port policy, defaulting to blocked until an admin explicitly grants it.
ALTER TABLE usb_ports ADD COLUMN storage_admin_set INT DEFAULT 0;




SET SQL_SAFE_UPDATES = 0;

UPDATE usb_ports SET storage_enabled = 0 WHERE storage_admin_set = 0;

ALTER TABLE usb_ports
  ADD CONSTRAINT chk_storage_requires_admin_set
  CHECK (storage_enabled = 0 OR storage_admin_set = 1);

SET SQL_SAFE_UPDATES = 1;