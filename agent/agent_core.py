import sys
import time
import socket
import traceback
import ctypes

import registry_utils
from agent_config import (
    log, IS_WINDOWS, _CONFIG_FILE, DB_HOST, DB_NAME, AGENT_VERSION,
    POLL_INTERVAL, _CFG_USERNAME, _HAS_WINREG, _REG_KEY_PATH, _REG_VALUE
)

from agent_db import (
    report_unregistered,
    _get_user_id, _get_db_conn, _sync_devices_to_db,
    _upsert_available_ports, _scan_available_usb_slots, _expire_stale_transfer_grants,
    _fetch_port_settings, _send_heartbeat, load_port_name_cache,
    get_or_assign_slot_number
)

import agent_scanner
from agent_scanner import _build_port_universe, seed_port_names_from_db, mark_storage_authorized
import agent_events

def _resolve_username():
    """Return the identifier used to match this PC against a registered
    system: the machine's HOSTNAME. This is what admins now register
    systems under in the dashboard (the "Hostname" field).

    agent_config.txt or the registry override can still force a specific
    value (useful for testing), but the default is the PC hostname."""
    if _CFG_USERNAME:
        log.info(f"Username from agent_config.txt: '{_CFG_USERNAME}'")
        return _CFG_USERNAME

    if _HAS_WINREG:
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _REG_KEY_PATH, 0, winreg.KEY_READ)
            val, _ = winreg.QueryValueEx(key, _REG_VALUE)
            winreg.CloseKey(key)
            if val:
                log.info(f"Username from Registry: '{val}'")
                return val
        except Exception:
            pass

    hostname = socket.gethostname().strip()
    if hostname:
        log.info(f"Using PC hostname as identifier: '{hostname}'")
        return hostname

    log.critical(
        "Could not determine the hostname for this machine. Fix "
        "agent_config.txt or the registry override instead."
    )
    return None

def _is_admin():
    if not IS_WINDOWS:
        return True
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def _apply_all_device_settings(ports, dry_run=False):
    """Applies port/storage settings for every port in `ports`.

    Returns: {port_key: bool} — True only for a port whose live enforcement
    was CONFIRMED applied (both set_port_enabled and set_storage_enabled
    reported success, or there was legitimately nothing to enforce). The
    caller (the agent loop) uses this to decide which ports are safe to
    mark as "settled" — a port that comes back False must be retried on
    the next cycle instead of being silently treated as done. Previously
    nothing captured per-port success/failure here at all, so a port whose
    live pnputil/CM_* toggle failed (device gone from the tree, rc=3010
    reboot-required, transient WMI failure, etc.) was marked "applied"
    anyway — the DB/dashboard kept showing the admin's requested state
    forever while the real Windows device state silently stayed whatever
    it was before, with no retry ever issued again until some *other*
    change touched that port.
    """
    import threading

    if dry_run:
        for port in ports:
            port_key = port["port_key"]
            if not port_key:
                continue
            log.info(f"  [DRY-RUN] Would set port {port_key} -> "
                     f"{'ENABLED' if port['port_enabled'] else 'DISABLED'}, "
                     f"storage -> {'ALLOWED' if port['storage_enabled'] else 'DENIED'}")
        return {}

    results = {}

    def _apply_one(port):
        port_key = port["port_key"]
        if not port_key:
            return
        port_ok = False
        try:
            import pythoncom
            pythoncom.CoInitialize()
            _com_initialized = True
        except Exception:
            _com_initialized = False
        try:
            # Apply physical port toggle (controls keyboard/mouse/storage at the hardware level).
            # Skip entirely for a port with no non-storage device on it (empty,
            # or occupied only by storage) — set_storage_enabled() below is the
            # sole owner of storage enforcement. set_port_enabled() ALSO tries
            # to exclude storage devices internally, but it does that via a
            # live WMI re-discovery of the storage child devnode at call time,
            # which fails whenever that device happens to be currently
            # disabled (Windows removes a disabled parent's child devnodes
            # from the tree). That silent failure let set_port_enabled
            # wrongly re-disable a storage-only port's drive based on Port
            # Access, immediately undoing what set_storage_enabled was about
            # to do — leaving the drive disabled while the DB/dashboard still
            # showed "Allowed". Deciding this from the DB's stored
            # device_type (has_non_storage_device) instead is reliable
            # regardless of the device's live enabled/disabled state.
            if port.get("has_non_storage_device", True):
                port_ok = registry_utils.set_port_enabled(port_key, bool(port["port_enabled"]), port.get("device_ids", []))
            else:
                log.info(f"  Port {port_key}: no non-storage device — skipping set_port_enabled, "
                         f"set_storage_enabled owns this port")
                port_ok = True

            # Storage Access is its own independent admin decision now,
            # fully decoupled from Port Access (which only governs
            # keyboard/mouse/webcam). set_port_enabled() already excludes
            # storage devices at the hardware level — set_storage_enabled()
            # is the sole owner of storage enforcement — so this no longer
            # ANDs with port_enabled.
            storage_allowed = bool(port["storage_enabled"])
            storage_ok = registry_utils.set_storage_enabled(port_key, storage_allowed, port.get("device_ids", []))

            # Open the quarantine grace window the instant the live enable
            # succeeds - set_storage_enabled() itself triggers a bus rescan +
            # volume rescan right after enabling (see registry_utils.py) so
            # Windows re-enumerates this exact device within the next scan
            # cycle or two. Without this, agent_scanner's quarantine sees
            # that re-enumeration (a fresh instance_id on this port) and
            # treats it as a brand-new unauthorized device, disabling it
            # again seconds after it was approved.
            if storage_ok and storage_allowed:
                mark_storage_authorized(port_key)

            results[port_key] = bool(port_ok and storage_ok)
            if not results[port_key]:
                log.warning(f"  Port {port_key}: apply INCOMPLETE (port_ok={port_ok}, "
                            f"storage_ok={storage_ok}) — will retry next cycle, NOT "
                            f"marking as applied")
        except Exception as e:
            log.error(f"  Port {port_key}: exception during apply — will retry next "
                      f"cycle: {e}")
            results[port_key] = False
        finally:
            if _com_initialized:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    threads = [threading.Thread(target=_apply_one, args=(p,), daemon=True) for p in ports]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    # A thread that never finished within the 20s join timeout never wrote
    # to `results` at all — treat that as a failure too (retry next cycle)
    # rather than defaulting it to "applied" by omission.
    for p in ports:
        pk = p["port_key"]
        if pk and pk not in results:
            log.warning(f"  Port {pk}: apply thread did not finish within timeout — "
                        f"will retry next cycle")
            results[pk] = False

    # Single cache invalidation for the whole batch, now that every thread
    # has finished. Doing this once here (instead of once per port inside
    # set_port_enabled/set_storage_enabled) avoids sibling threads knocking
    # each other's WMI snapshot out mid-batch and forcing repeated full
    # system-wide device re-enumerations - the cause of the multi-second
    # stalls seen when several ports were applied in one cycle.
    registry_utils.invalidate_pnp_cache()
    agent_scanner._invalidate_hid_hint_cache()

    return results


def _force_apply_settings_core(user_id):
    ports = _fetch_port_settings(user_id)
    if not ports:
        log.warning("No port settings found in database for user %s", user_id)
        return

    log.info("=== FORCE APPLYING SETTINGS ===")
    for port in ports:
        log.info(f"  Queued: {port['port_key']} -> enabled={port['port_enabled']}, "
                 f"storage={port['storage_enabled']}")
    _apply_all_device_settings(ports, dry_run=False)
    log.info("=== FORCE APPLY COMPLETE ===")

_loop_last_applied_ports_state = {}


def _agent_loop(username, hostname):
    global _loop_last_applied_ports_state

    log.info(f"Agent v{AGENT_VERSION} started for '{username}'")
    log.info(f"Config file: {_CONFIG_FILE} ({'found' if _CONFIG_FILE.exists() else 'not found — using defaults'})")
    log.info(f"DB host: {DB_HOST}, database: {DB_NAME}")

    # Start the Windows device-change event watcher (WM_DEVICECHANGE-style
    # notifications, delivered via WMI's Win32_DeviceChangeEvent). Plugging
    # or unplugging a device now triggers an immediate rescan below instead
    # of waiting on a fixed timer. SCAN_SAFETY_NET_INTERVAL is only a
    # fallback in case the watcher ever misses an event or can't start
    # (e.g. WMI unavailable) — it's deliberately long since it's no longer
    # the primary detection mechanism.
    agent_events.start()

    user_id = None
    last_device_snapshot = None
    last_scan_time = 0
    SCAN_SAFETY_NET_INTERVAL = 5
    device_event_pending = False

    while True:
        try:
            if user_id is None:
                user_id = _get_user_id(username)
                if user_id is None:
                    log.warning(f"User '{username}' not found in DB")
                    report_unregistered(username, hostname, AGENT_VERSION)
                    time.sleep(POLL_INTERVAL)
                    continue
                log.info(f"User ID: {user_id}")
                
                # Seed the scanner's port-name cache from the DB so that port
                # numbers (USB Port 1 / 2 / 3 ...) are stable across restarts.
                # Also pass user_id and get_or_assign_slot_number so the scanner
                # can anchor new port_keys to their stable socket_id in the DB.
                # Must run BEFORE the first _build_port_universe() call below.
                try:
                    _port_seed = load_port_name_cache(user_id)
                    seed_port_names_from_db(
                        _port_seed,
                        user_id=user_id,
                        slot_fn=get_or_assign_slot_number,
                    )
                    log.info(f"[PortNameCache] Loaded {len(_port_seed)} port name(s) from DB; "
                             f"socket_id-based slot assignment enabled for user {user_id}")
                except Exception as _seed_err:
                    log.warning(f"[PortNameCache] Could not seed port names: {_seed_err}")

                try:
                    conn = _get_db_conn()
                    c = conn.cursor()
                    c.execute("""
                        DELETE p1 FROM usb_ports p1 
                        JOIN usb_ports p2 ON p1.user_id = p2.user_id 
                                         AND p1.port_key = p2.port_key 
                                         AND p1.device_type = p2.device_type 
                        WHERE p1.connected = 0 AND p2.connected = 1
                    """)
                    if c.rowcount > 0:
                        log.info(f"Cleaned up {c.rowcount} duplicate disconnected DB rows")
                    conn.commit()
                    conn.close()
                except Exception as e:
                    log.warning(f"Failed to clean up duplicate DB rows: {e}")
                    
            now = time.time()
            if device_event_pending:
                # A device was just plugged in / removed — the cached WMI
                # PnP snapshot is now stale, so force it to be re-read instead of
                # serving a snapshot from before the change.
                registry_utils.invalidate_pnp_cache()
                agent_scanner._invalidate_hid_hint_cache()

            if device_event_pending or (now - last_scan_time >= SCAN_SAFETY_NET_INTERVAL):
                trigger = "device-change event" if device_event_pending else "safety-net interval"
                log.info(f"Scanning for external USB devices and physical ports (trigger: {trigger})...")
                devices, empty_ports = _build_port_universe()

                current_snapshot = (
                    tuple(sorted(d["device_id"] for d in devices)),
                    tuple(sorted(p["port_key"] for p in empty_ports)),
                )

                if current_snapshot != last_device_snapshot:
                    _sync_devices_to_db(user_id, devices, empty_ports)
                    log.info(f"Port state changed — synced {len(devices)} device(s) "
                             f"and {len(empty_ports)} empty port(s) to DB")
                    last_device_snapshot = current_snapshot

                available = _scan_available_usb_slots(empty_ports)
                success = _upsert_available_ports(user_id, available)
                if success:
                    log.info(f"Available slots updated ({len(available)} slot(s))")

                last_scan_time = now

            device_event_pending = False

            _expire_stale_transfer_grants(user_id)
            ports = _fetch_port_settings(user_id)
            if ports:
                # Re-apply a port when EITHER:
                #   (a) the (port_enabled, storage_enabled) policy actually
                #       changed since we last pushed it to hardware (a real
                #       admin toggle, or a brand-new port never applied yet), OR
                #   (b) the specific device(s) occupying the port changed,
                #       even if the port-level policy itself is unchanged.
                # (b) matters because unplugging one device and plugging in a
                # different one creates a brand-new Windows device node,
                # which the OS enables by default - regardless of whatever
                # enable/disable state the *previous* occupant of this port
                # was left in. Comparing policy alone would see "nothing
                # changed" and skip applying, leaving the new device
                # enabled/accessible even though the port shows
                # Blocked/Denied in the dashboard.
                def _port_state_key(p):
                    return (
                        p["port_enabled"], p["storage_enabled"],
                        tuple(sorted(p.get("device_ids", []))),
                        # Without this, a device that unplugs and later
                        # replugs into the same physical port can look
                        # completely identical to its last-applied state —
                        # the empty-port placeholder row deliberately keeps
                        # the last-known device_id (see agent_db.py), so
                        # device_ids alone doesn't change across an
                        # unplug/replug of the same unit — and the live
                        # enable/disable call never gets reissued to the
                        # fresh devnode Windows creates on reconnect, even
                        # though the dashboard/DB correctly show the port as
                        # allowed. Including connected (0 while empty, 1
                        # once occupied) makes that transition visible.
                        p.get("connected"),
                    )

                changed_ports = [
                    p for p in ports
                    if _loop_last_applied_ports_state.get(p["port_key"])
                       != _port_state_key(p)
                ]

                if changed_ports:
                    log.info(f"=== APPLYING SETTINGS FOR {len(changed_ports)} PORT(S) ===")
                    for port in changed_ports:
                        log.info(f"  Port {port['port_key']}: enabled={port['port_enabled']}, storage={port['storage_enabled']}")
                    apply_results = _apply_all_device_settings(changed_ports, dry_run=False)
                    log.info("=== SETTINGS APPLIED ===")

                    # Only remember a port as "settled" (skip it next cycle)
                    # if its live enforcement was CONFIRMED to succeed. A
                    # port whose apply failed or is missing from
                    # apply_results is deliberately left OUT of
                    # _loop_last_applied_ports_state, so _port_state_key(p)
                    # still won't match on the next poll and the port stays
                    # in `changed_ports` — i.e. it gets retried automatically
                    # every cycle until it actually applies. This is what
                    # closes the "dashboard says Allowed while the device is
                    # still disabled" gap: previously a failed apply was
                    # marked settled anyway and never retried.
                    failed = []
                    for port in changed_ports:
                        if apply_results.get(port["port_key"]):
                            _loop_last_applied_ports_state[port["port_key"]] = _port_state_key(port)
                        else:
                            failed.append(port["port_key"])
                    if failed:
                        log.warning(f"  {len(failed)} port(s) did not confirm apply and "
                                    f"will be retried next cycle: {failed}")

                # Drop bookkeeping for ports that no longer exist (unplugged /
                # deleted rows) so a future re-add is treated as new again.
                live_keys = {p["port_key"] for p in ports}
                for stale_key in list(_loop_last_applied_ports_state.keys()):
                    if stale_key not in live_keys:
                        del _loop_last_applied_ports_state[stale_key]
            else:
                log.warning("No port settings to apply - check database!")

            _send_heartbeat(user_id, hostname)

        except Exception as e:
            log.error(f"Error: {e}\n{traceback.format_exc()}")

        # Wait up to POLL_INTERVAL seconds for the DB-settings/heartbeat
        # cadence — but wake immediately (and skip straight to a rescan on
        # the next iteration) if a USB device was plugged in or removed in
        # the meantime.
        device_event_pending = agent_events.wait_for_change(timeout=POLL_INTERVAL)

_singleton_mutex_handle = None


def _ensure_single_instance(mutex_name: str = "USBControlAgent_Mutex"):
    global _singleton_mutex_handle
    if not IS_WINDOWS:
        return True
    
    try:
        import win32event
        import win32api
        import winerror
        
        mutex = win32event.CreateMutex(None, False, mutex_name)
        
        if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            log.warning("Another instance of the agent is already running. Exiting.")
            return False

        _singleton_mutex_handle = mutex
        return True
        
    except ImportError:
        try:
            import subprocess
            result = subprocess.run(
                ["wmic", "process", "where", "name='SwiftAgent.exe'", "get", "CommandLine"],
                capture_output=True, text=True
            )
            lines = [l.strip() for l in result.stdout.strip().split('\n') if l.strip()]
            same_mode_count = sum(1 for l in lines if "CommandLine" not in l)

            if same_mode_count > 1:
                log.warning(f"Multiple instances of the agent detected ({same_mode_count} running). Exiting.")
                return False
            return True
            
        except Exception as e:
            log.warning(f"Fallback single-instance check failed: {e}")
            return True

def main():
    log.info(f"=== Swift Agent v{AGENT_VERSION} ===")

    is_hook_mode = "--hook" in sys.argv
    mutex_name = "USBControlAgent_Hook_Mutex" if is_hook_mode else "USBControlAgent_Mutex"

    if not _ensure_single_instance(mutex_name):
        return 0

    if IS_WINDOWS and not is_hook_mode and not _is_admin():
        log.warning("Not running with an elevated token (expected/harmless when running as SYSTEM).")

    username = _resolve_username()
    if not username:
        log.critical("No username found")
        return 1
    hostname = socket.gethostname().strip()

    if is_hook_mode:
        log.info("Hook mode is deprecated. Exiting.")
        return 0
        
    _agent_loop(username, hostname)
    return 0
