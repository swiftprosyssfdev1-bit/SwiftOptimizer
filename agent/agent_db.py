import threading
import time
import traceback
import mysql.connector
from mysql.connector import errors as mysql_errors
import registry_utils
from agent_config import (
    log, DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, AGENT_VERSION
)

# ─────────────────────────────────────────────────────────────────────────
# Connection layer — ONE persistent connection per agent process, with
# auto-reconnect. No connection pool.
#
# Every function below this section is UNCHANGED from before: they still
# call `conn = _get_db_conn()` ... `finally: conn.close()`, exactly like
# when this was pooled. The difference is entirely hidden inside
# _get_db_conn(): it now always hands back the SAME underlying socket
# (reconnecting first if it died), wrapped in a thin proxy whose
# .close() is a no-op — so a function "closing" its connection just means
# "I'm done with it this call", not "tear down the persistent connection".
# The real socket only closes when shutdown() is called at agent exit.
#
# THREAD SAFETY: this agent runs the event watcher, scanner, heartbeat,
# and policy-sync work on separate threads (see agent_core.py /
# agent_events.py / swift_agent_service.py), and mysql.connector is NOT
# safe for concurrent use of a single connection object from multiple
# threads. _DB_LOCK below serializes access: _get_db_conn() acquires it
# and the proxy's close() releases it, so every existing
# `conn = _get_db_conn()` ... `finally: conn.close()` call site is
# automatically serialized without needing to be rewritten.
# ─────────────────────────────────────────────────────────────────────────

_DB_LOCK = threading.Lock()

class _PersistentAgentConnection:
    def __init__(self):
        self._conn = None

    def _is_alive(self):
        if self._conn is None:
            return False
        try:
            self._conn.ping(reconnect=False, attempts=1, delay=0)
            return True
        except Exception:
            return False

    def get(self):
        if self._is_alive():
            return self._conn

        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

        log.warning("Agent DB connection lost — reconnecting...")
        delay = 1
        while True:
            try:
                self._conn = mysql.connector.connect(
                    host=DB_HOST, user=DB_USER, password=DB_PASSWORD,
                    database=DB_NAME, connect_timeout=8, use_pure=True,
                )
                log.info("Agent DB connection re-established.")
                return self._conn
            except mysql_errors.Error as e:
                log.error(f"Agent DB reconnect failed ({e}); retrying in {delay}s")
                time.sleep(delay)
                delay = min(delay * 2, 30)   # backoff, capped at 30s

    def shutdown(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


_PERSISTENT = _PersistentAgentConnection()


class _ConnProxy:
    """Forwards cursor()/commit()/rollback() to the real persistent
    connection. .close() releases the thread-safety lock acquired in
    _get_db_conn() -- it does NOT close the underlying persistent
    socket (see shutdown() below for that)."""

    def __init__(self, real_conn):
        self._real = real_conn
        self._lock_released = False

    def cursor(self, *args, **kwargs):
        return self._real.cursor(*args, **kwargs)

    def commit(self):
        self._real.commit()

    def rollback(self):
        self._real.rollback()

    def close(self):
        # Idempotent: safe even if a call site calls close() more than
        # once (e.g. once explicitly, once in a finally block).
        if not self._lock_released:
            self._lock_released = True
            _DB_LOCK.release()


def _get_db_conn():
    """Every function in this file calls this instead of
    mysql.connector.connect(). Returns the agent's one persistent
    connection (reconnecting automatically if it dropped).

    Acquires _DB_LOCK so only one thread at a time can use the shared
    connection; the lock is released when the returned proxy's
    .close() is called (every call site already does this in a
    finally block, so no other code needed to change)."""
    _DB_LOCK.acquire()
    try:
        return _ConnProxy(_PERSISTENT.get())
    except Exception:
        _DB_LOCK.release()
        raise


def shutdown():
    """Call once on clean agent shutdown (e.g. from usb_agent.py's exit
    path) to actually close the persistent socket."""
    _PERSISTENT.shutdown()


# ─────────────────────────────────────────────────────────────────────────
# Everything below is unchanged from the original agent_db.py.
# ─────────────────────────────────────────────────────────────────────────

_available_slots_table_ready = False

# NOTE: this module used to maintain a local port_device_grants.json cache
# (keyed by port_key::device_serial) as a second, independent record of
# storage/File Transfer authorization, separate from the usb_ports DB table.
# It was removed: the DB row (via old_rows / the port-reconciliation logic
# above) is the single source of truth for storage access, and the JSON
# cache could resurrect stale access on a socket the admin never authorized
# once a port_key string got reused for a different physical port. If a
# leftover agent/port_device_grants.json file exists on a deployed machine
# from an older build, it is simply no longer read and can be deleted.

def _ensure_available_slots_table():
    """Ensure the available_usb_slots table exists.

    This used to run a full CREATE TABLE IF NOT EXISTS (with its own fresh
    DB connection) on every single scan cycle. Even with the new
    event-driven scan trigger, there's no reason to pay that connection
    cost more than once per process run.
    """
    global _available_slots_table_ready
    if _available_slots_table_ready:
        return True

    conn = None
    try:
        conn = _get_db_conn()
        c = conn.cursor()
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
        log.info("Available USB slots table verified/created")
        _available_slots_table_ready = True
        return True
    except Exception as e:
        log.error(f"Failed to create available_usb_slots table: {e}")
        return False
    finally:
        if conn:
            conn.close()

def _upsert_available_ports(user_id: int, available_slots: list):
    """
    Synchronizes the available_usb_slots rows for this user by performing
    a diff between the existing rows and the new slots. This prevents UI
    glitching caused by deleting and re-inserting all slots constantly.
    """
    conn = None
    try:
        if not _ensure_available_slots_table():
            return False
        
        conn = _get_db_conn()
        c = conn.cursor()
        
        c.execute("SELECT id, slot_id FROM available_usb_slots WHERE user_id=%s", (user_id,))
        existing = {row[1]: row[0] for row in c.fetchall()}
        
        desired = {slot.get("slot_id", ""): slot for slot in available_slots}
        
        to_delete = []
        for slot_id, db_id in existing.items():
            if slot_id not in desired:
                to_delete.append(db_id)
                
        to_insert = []
        for slot_id, slot in desired.items():
            if slot_id not in existing:
                to_insert.append(slot)
                
        if not to_delete and not to_insert:
            # No changes needed
            return True
            
        if to_delete:
            format_strings = ','.join(['%s'] * len(to_delete))
            c.execute(f"DELETE FROM available_usb_slots WHERE id IN ({format_strings})", tuple(to_delete))
            log.info(f"Deleted {len(to_delete)} stale available slots for user {user_id}")
            
        inserted = 0
        for slot in to_insert:
            try:
                c.execute("""
                    INSERT INTO available_usb_slots (user_id, slot_name, slot_id, controller, location)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    user_id,
                    slot.get("slot_name", "Unknown Port"),
                    slot.get("slot_id", ""),
                    slot.get("controller", ""),
                    slot.get("location", ""),
                ))
                inserted += 1
            except Exception as e:
                log.error(f"Failed to insert slot {slot.get('slot_name')}: {e}")
                
        conn.commit()
        if to_delete or inserted:
            log.info(f"SUCCESS: Synced available slots (deleted {len(to_delete)}, inserted {inserted}) for user {user_id}")
        
        return True
        
    except Exception as e:
        log.error(f"Failed to upsert available ports: {e}")
        log.error(traceback.format_exc())
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

def _scan_available_usb_slots(empty_ports: list) -> list:
    """
    Builds the legacy/cosmetic "available_usb_slots" table rows directly
    from the empty_ports already discovered by _build_port_universe() /
    registry_utils.list_all_physical_ports() — the same data that creates
    the real "- Empty -" placeholder rows in usb_ports.
    """
    return [
        {
            "slot_name": p["port_name"],
            "slot_id": p["port_key"],
            "controller": p.get("hub_name", "USB Hub"),
            "location": p["port_name"],
        }
        for p in empty_ports
    ]

_GENERIC_DEVICE_NAMES = {"usb input device", "hid-compliant device"}

def _extract_device_serial(device_id):
    """Pull a stable serial number out of a raw USB instance ID, e.g.
    'USB\\VID_0781&PID_5581\\4C531001440414123545' -> '4C531001440414123545'.
    Returns '' if the trailing segment looks like a bus-relative address
    ('5&32184FCD&2&12' — contains '&') rather than a real serial: those
    shift across replug/renumbering same as port_key does, so matching on
    them would just reintroduce the instability we're trying to route
    around. Only the first id in a comma-joined _all_ids list is used —
    that's enough to identify the physical device.
    """
    if not device_id:
        return ""
    first_id = device_id.split(",")[0].strip()
    tail = first_id.split("\\")[-1].strip()
    if not tail or "&" in tail:
        return ""
    return tail.upper()

def _dedupe_port_devices(port_devices):
    """
    Collapses to a single row per (port_key, device_type), preferring the
    most descriptive device name when more than one candidate is found.
    """
    best = {}
    for dev in port_devices:
        dtype = dev["device_type"]
        name_lower = (dev["device_name"] or "").strip().lower()
        if dtype not in best:
            best[dtype] = dev
            best[dtype]["_all_ids"] = [dev["device_id"]]
            continue

        best[dtype]["_all_ids"].append(dev["device_id"])

        current_name = (best[dtype]["device_name"] or "").strip().lower()
        if current_name in _GENERIC_DEVICE_NAMES and name_lower not in _GENERIC_DEVICE_NAMES:
            all_ids = best[dtype]["_all_ids"]
            best[dtype] = dev
            best[dtype]["_all_ids"] = all_ids
    return list(best.values())

def _sync_devices_to_db(user_id, devices, empty_ports=None):
    empty_ports = empty_ports or []
    conn = None
    try:
        conn = _get_db_conn()
        c = conn.cursor()

        c.execute("""
            SELECT id, port_key, port_enabled, port_name, device_name, device_type, device_id, connected,
                   keyboard_enabled, mouse_enabled, storage_enabled, status, port_admin_set, storage_admin_set,
                   last_updated
            FROM usb_ports WHERE user_id = %s AND port_key IS NOT NULL AND port_key != ''
        """, (user_id,))
        
        existing_by_port = {}
        for row in c.fetchall():
            existing_by_port.setdefault(row[1], []).append({
                "id": row[0],
                "port_enabled": row[2],
                "port_name": row[3],
                "device_name": row[4],
                "device_type": row[5],
                "device_id": row[6],
                "connected": row[7],
                "keyboard_enabled": row[8],
                "mouse_enabled": row[9],
                "storage_enabled": row[10],
                "status": row[11],
                "port_admin_set": row[12],
                "storage_admin_set": row[13],
                "last_updated": row[14],
            })

        desired_rows = []
        
        by_port = {}
        for dev in devices:
            by_port.setdefault(dev["port_key"], []).append(dev)

        # ── Empty-slot -> occupied-slot reconciliation ───────────────────────
        # Windows can report a different LocationInformation port_key for a
        # device than it reported for that same physical connector while it
        # was empty (e.g. a stale/"ghost" registry location from an earlier
        # session vs. the PowerShell-topology number currently in use). When
        # that happens, an admin's approval set on the empty port_key would
        # otherwise never be found for the newly-occupied port_key, and a
        # brand-new storage device silently falls back to the blocked
        # default even though it was pre-approved.
        #
        # Detect it here: a port_key that previously existed as an empty,
        # admin-decided row (port_admin_set or storage_admin_set) but does
        # NOT appear anywhere in this scan (neither occupied nor empty) has
        # "vanished". If exactly one brand-new occupied port_key (no prior
        # row of its own) shows up in the same scan, treat it as the same
        # physical slot and carry the vanished row's admin decisions over to
        # it, then drop the stale row so it doesn't linger as an orphan.
        current_keys = set(by_port.keys()) | {p["port_key"] for p in (empty_ports or [])}
        # Each entry: (old_pkey, old_row, is_admin_decided, allow_positional)
        # allow_positional gates Pass 2 (below): it's only ever True for a
        # port_key that's genuinely gone from the topology this scan. A
        # port_key that's still being reported — just empty — must never be
        # positionally guessed onto a different slot; see "ghost" below.
        vanished_rows = []
        _INPUT_DEVICE_TYPES = {"Mouse", "Keyboard", "Webcam"}
        for pkey, rows in existing_by_port.items():
            if pkey in by_port:
                # Something is already occupying this exact port_key again
                # this scan — it isn't vanished, ghost, or anything else;
                # skip it entirely.
                continue

            # "Truly vanished": this port_key isn't reported at all this
            # scan cycle (neither occupied nor empty). Almost certainly the
            # same physical socket, just renumbered under a new
            # LocationInformation string this cycle.
            #
            # "Ghost/stale-empty": Windows is still reporting this EXACT
            # port_key, just empty. On hardware with unstable
            # LocationInformation reporting, the socket a device just
            # vacated can get relabeled to a brand-new port_key on its way
            # back to idle while the OLD port_key keeps reappearing as its
            # own (now genuinely-empty-looking) placeholder — a leftover,
            # "ghost" registry location rather than a real second socket.
            # Because that old port_key never disappears from the scan, it
            # previously never reached this reconciliation pass at all, so
            # its serial was never checked against newly-occupied ports —
            # the row just sat there as a permanent stale placeholder while
            # a brand-new row got minted for the device's real new port_key
            # every single renumber.
            truly_vanished = pkey not in current_keys

            for r in rows:
                if r.get("connected"):
                    continue
                is_admin_decided = bool(r.get("port_admin_set") or r.get("storage_admin_set"))
                has_serial = bool(_extract_device_serial(r.get("device_id", "")))
                is_input_device = r.get("device_type") in _INPUT_DEVICE_TYPES

                if not truly_vanished:
                    # Ghost/stale-empty case: we KNOW this exact port_key
                    # still exists in the topology (it's reported empty),
                    # so trust nothing but a verified device-serial match —
                    # never a positional guess, which would risk re-keying
                    # a slot that's genuinely, still just sitting empty.
                    if has_serial:
                        vanished_rows.append((pkey, r, is_admin_decided, False))
                    continue

                # Truly-vanished case.
                if is_admin_decided or has_serial:
                    # Admin-decided rows always qualify (existing behavior).
                    # Rows an admin never explicitly touched now also
                    # qualify IF we can identify them by serial — e.g. a
                    # storage device auto-disabled by the on-detection
                    # quarantine (agent_scanner._quarantine_new_storage_device)
                    # never sets storage_admin_set, so without this it kept
                    # spawning a new "USB Port N" row every time its own
                    # LocationInfo shifted, even though no grant was at risk.
                    vanished_rows.append((pkey, r, is_admin_decided, True))
                elif is_input_device:
                    # A keyboard/mouse/webcam sitting at its own intelligent
                    # auto-ON default (never explicitly toggled by an
                    # admin) has no serial to verify against — but Port
                    # Access carries no real authorization risk for these
                    # types the way File Transfer does for storage, so a
                    # positional guess is safe to make for them too.
                    # Without this, a renumbered mouse/keyboard left its
                    # old row behind as a permanent orphan "- Empty -"
                    # placeholder and minted a brand-new port entry for the
                    # same physical device on every single renumber, so
                    # port numbers climbed forever (1,2 -> 3,4 -> 7,8 -> ...).
                    vanished_rows.append((pkey, r, is_admin_decided, True))

        if vanished_rows:
            # Reconciliation targets: any port_key with no existing row of
            # its own — whether it showed up occupied (a device landed on
            # it) or empty (the vacated slot got renumbered on the way
            # back to idle). Restricting this to occupied-only used to
            # mean a storage grant survived a replug but was silently lost
            # on a plain unplug, if Windows happened to report the
            # now-empty slot under a different port_key than the one it
            # occupied while connected.
            brand_new_occupied = [
                pk for pk in by_port
                if pk not in existing_by_port or not existing_by_port[pk]
            ]
            brand_new_empty = [
                p["port_key"] for p in (empty_ports or [])
                if p["port_key"] not in existing_by_port or not existing_by_port[p["port_key"]]
            ]

            # A physical port renumbering (same socket, Windows just reports a
            # different LocationInformation string this cycle) and a genuine
            # device relocation (unplugged from one socket, plugged into a
            # DIFFERENT one) look identical from a single serial match alone
            # — both show the old port_key vanishing and the same serial
            # showing up under a new one. On hardware/hubs with unstable
            # LocationInformation reporting (confirmed here: the exact same
            # port_key string has been reused for two totally unrelated
            # devices at different times), treating every renumber as a
            # relocation caused a File Transfer grant to be wiped every time
            # the SAME physical drive in the SAME socket got relabeled —
            # without the user ever touching the cable.
            #
            # Distinguish the two cases with a short grace window: if the old
            # port_key's row was still live (last_updated) within the last
            # _RENUM_GRACE_SECONDS when it vanished, treat this as the same
            # physical socket being relabeled and carry File Transfer
            # forward. A deliberate move to a genuinely different port won't
            # typically race the very next scan cycle like a same-socket
            # relabel does, and — more importantly — if the OLD port really
            # is a distinct second physical socket, it keeps reporting itself
            # as a real (now-empty) slot instead of vanishing from the
            # topology entirely, so it wouldn't reach this vanished-row path
            # in the first place.
            import datetime as _dt
            _RENUM_GRACE_SECONDS = 20

            def _within_renumber_grace(old_row) -> bool:
                lu = old_row.get("last_updated")
                if not lu:
                    return False
                try:
                    age = (_dt.datetime.now() - lu).total_seconds()
                except Exception:
                    return False
                return 0 <= age <= _RENUM_GRACE_SECONDS

            def _reconcile(old_pkey, old_row, pk, method):
                same_socket_relabel = _within_renumber_grace(old_row)
                if old_row.get("storage_enabled") or old_row.get("storage_admin_set"):
                    if same_socket_relabel:
                        log.info(f"  [Port reconciliation] Carrying File Transfer authorization "
                                 f"from '{old_pkey}' to '{pk}' — reappeared within "
                                 f"{_RENUM_GRACE_SECONDS}s, treating as the same physical "
                                 f"socket being relabeled, not a move to a different port.")
                    else:
                        log.info(f"  [Port reconciliation] NOT carrying File Transfer "
                                 f"authorization from '{old_pkey}' to '{pk}' — storage access "
                                 f"is bound to the physical port; too long since '{old_pkey}' "
                                 f"was last seen ({_RENUM_GRACE_SECONDS}s grace window expired) "
                                 f"to treat this as the same socket, so '{pk}' now requires its "
                                 f"own admin approval.")

                old_row = dict(old_row)
                if not same_socket_relabel:
                    old_row["storage_enabled"] = 0
                    old_row["storage_admin_set"] = 0

                log.info(f"  [Port reconciliation] carrying row from vanished "
                         f"port '{old_pkey}' onto port '{pk}' (same physical slot, "
                         f"different reported location key — matched by {method}); "
                         f"Port Access carried, File Transfer "
                         f"{'carried (within grace window)' if same_socket_relabel else 'reset to blocked/undecided'}")
                # Re-key the existing row onto the new port_key in place
                # (an UPDATE further down, not a DELETE+INSERT here) so it
                # keeps its database id. Deleting and re-inserting handed
                # the row a brand-new id, which broke any dashboard toggle
                # already in flight against the old row (it would target
                # an id that no longer existed and fail with "Toggle
                # Failed — port may have been resynced").
                existing_by_port[old_pkey] = [
                    r for r in existing_by_port.get(old_pkey, []) if r["id"] != old_row["id"]
                ]
                if not existing_by_port[old_pkey]:
                    existing_by_port.pop(old_pkey, None)
                existing_by_port.setdefault(pk, []).append(old_row)

            # Pass 1 — exact serial match against newly-occupied ports.
            # Applies to every vanished row with a usable serial, admin-
            # decided or not: it's safe even when several devices reshuffle
            # port_key in the same scan, since it only pairs a vanished row
            # with a port that's actually reporting the same physical
            # device.
            remaining_vanished = list(vanished_rows)
            remaining_occupied = list(brand_new_occupied)
            for old_pkey, old_row, is_admin, allow_positional in list(remaining_vanished):
                old_serial = _extract_device_serial(old_row.get("device_id", ""))
                if not old_serial:
                    continue
                for pk in remaining_occupied:
                    new_serials = {
                        _extract_device_serial(d.get("device_id", ""))
                        for d in by_port.get(pk, [])
                    }
                    if old_serial in new_serials:
                        _reconcile(old_pkey, old_row, pk, "device serial")
                        remaining_vanished.remove((old_pkey, old_row, is_admin, allow_positional))
                        remaining_occupied.remove(pk)
                        break

            # Pass 2 — whatever's left with no usable serial (e.g. a
            # keyboard/mouse, or a vacated slot that's now empty rather
            # than occupied so there's nothing to read a serial off of)
            # falls back to positional pairing — but ONLY for rows flagged
            # allow_positional: a genuinely admin-decided row, or an
            # input-device (keyboard/mouse/webcam) sitting at its own safe
            # auto-ON default. A "ghost" row (its old port_key is still
            # being reported this scan, just empty) is never eligible here
            # — see where allow_positional is set, above. Without a serial
            # to verify against, positional pairing is a guess; that guess
            # is worth making to protect an admin's grant or a low-risk
            # input-device default, but not worth making for an ordinary
            # undecided, non-input row where a wrong guess would misreport
            # device history for no real benefit.
            remaining_vanished = [t for t in remaining_vanished if t[3]]
            for pk in remaining_occupied + brand_new_empty:
                if not remaining_vanished:
                    break
                old_pkey, old_row, _, _ = remaining_vanished.pop(0)
                _reconcile(old_pkey, old_row, pk, "position — no serial available")

        used_port_names = set()

        for port_key, raw_port_devices in by_port.items():
            port_devices = _dedupe_port_devices(raw_port_devices)

            old_rows = existing_by_port.get(port_key, [])

            # Prefer the DB-stored port name over the freshly-minted scanner name.
            # After a serial-match reconciliation the scanner may have assigned a
            # new number to the same physical port; anchoring to the DB row's name
            # keeps the label stable across agent restarts and port renumbering.
            if old_rows and old_rows[0].get("port_name"):
                port_nm = old_rows[0]["port_name"]
            else:
                port_nm = port_devices[0]["port_name"]

            # Deduplicate across the whole sync: if the DB-restored name is
            # already claimed by another port in this cycle, fall back to the
            # scanner's fresh name (which should be unique by construction).
            if port_nm in used_port_names:
                port_nm = port_devices[0]["port_name"]
            counter = 2
            base_nm = port_nm
            while port_nm in used_port_names:
                port_nm = f"{base_nm} ({counter})"
                counter += 1
            used_port_names.add(port_nm)
            old_ids = {r.get("device_id", "") for r in old_rows if r.get("device_id", "")}
            
            new_ids = set()
            for dev in port_devices:
                for did in dev.get("_all_ids", [dev.get("device_id", "")]):
                    if did: new_ids.add(did)

            if old_rows:
                old_type = old_rows[0].get("device_type", "None")
                new_type = port_devices[0].get("device_type", "None")
                
                # If the device is blocked/disabled, Windows reports a generic
                # Error-22 node instead of the real device type. Resolve it:
                #   • If we have a prior DB row with a real type, use that.
                #   • If the old DB row is empty/None (device inserted directly
                #     into a quarantine before the DB ever recorded it), assume
                #     Storage — it's the ONLY device type the agent actively
                #     quarantines on first sight, so DisabledPlaceholder always
                #     means Storage here.
                #   • Use the scanner's own device_name as fallback so the name
                #     "USB Mass Storage Device" is shown even on first insertion.
                if new_type == "DisabledPlaceholder":
                    resolved_type = old_type if old_type not in ("None", None, "DisabledPlaceholder") else "Storage"
                    new_type = resolved_type
                    for dev in port_devices:
                        dev["device_type"] = resolved_type
                        # Prefer DB name if we have one; otherwise keep the
                        # scanner's live name (e.g. "USB Mass Storage Device")
                        # so a brand-new quarantined device still has a label.
                        if old_rows[0].get("device_name") and old_rows[0]["device_name"] not in ("- Empty -", "Empty Slot", ""):
                            dev["device_name"] = old_rows[0]["device_name"]
                        # else: keep dev["device_name"] from the scanner

                # A port we already have a row for should NEVER have its
                # enabled/disabled state silently reset just because Windows
                # reports a different (or no) device_type for it now — this
                # covers both a real device type changing (e.g. a disabled
                # camera node briefly re-reporting differently) and a device
                # being plugged into a port that was previously empty
                # (old_type == "None"). In the empty case the stored
                # port_enabled already reflects the admin's real decision —
                # either an explicit ON they set on the empty slot, or the
                # OFF that was correctly recorded when the last device was
                # unplugged from a blocked port — so it must never be
                # overridden by a type-based default (that was the bug:
                # a re-inserted mouse was forced ON, and a storage device
                # inserted into an admin-enabled empty port was forced OFF,
                # both ignoring what the admin actually set). The admin's
                # last explicit choice always wins here, regardless of type —
                # UNLESS the admin never actually made a choice on this port
                # (it's still sitting on the empty-port OFF default) and a
                # keyboard/mouse/webcam is landing on it for the first time.
                # In that case, give it the same intelligent auto-ON default
                # a brand-new occupied port gets below — otherwise a port
                # the agent happened to see empty first stays OFF forever,
                # forcing admin to manually enable it every single time.
                _port_admin_set = old_rows[0].get("port_admin_set", 0)
                if not _port_admin_set and old_type == "None" and new_type in ("Keyboard", "Mouse", "Webcam"):
                    port_en = 1
                else:
                    port_en = old_rows[0]["port_enabled"]
            else:
                # Brand new port — never seen before.
                new_type = port_devices[0].get("device_type", "None")
                # DisabledPlaceholder on a brand-new port means the quarantine
                # fired before the DB ever created a row for this device. Resolve
                # to Storage (same logic as above) so it appears correctly on the
                # dashboard and the admin can act on it.
                if new_type == "DisabledPlaceholder":
                    new_type = "Storage"
                    for dev in port_devices:
                        dev["device_type"] = "Storage"
                        # dev["device_name"] is already set by the scanner —
                        # e.g. "USB Mass Storage Device" — keep it as-is.
                port_en = 0 if new_type == "Storage" else 1


            port_status = "ON" if port_en else "OFF"
            
            # Whether an admin has ever explicitly decided THIS port (as
            # opposed to it merely sitting at the agent's own type-based
            # auto-default, e.g. ON because a mouse happened to land here
            # first). Only update_port_toggle (db.py) ever sets this to 1 —
            # the agent only ever carries it forward unchanged.
            port_admin_set = old_rows[0].get("port_admin_set", 0) if old_rows else 0

            # Storage Access: first check for a device-specific grant (an
            # admin decision recorded against this exact port + device
            # serial, e.g. via an approved Access Request) in our local
            # persistent cache. If none exists, fall back to the port's own
            # already-stored policy (old_rows) — this is the blanket "File
            # Transfer" authorization an admin can set on a port while it's
            # still empty (see db.py's update_port_toggle, which explicitly
            # writes storage_enabled/storage_admin_set to the empty-port
            # placeholder row "so the grant is already in effect before a
            # device is even plugged in"). Without this fallback, a storage
            # device landing on an admin-authorized empty port always found
            # no device-specific grant (it's the FIRST time we've seen that
            # serial) and silently fell back to blocked — ignoring the
            # admin's actual port-level decision. Only when neither a
            # device-specific grant nor a prior port-level decision exists
            # does storage correctly default to blocked.
            storage_admin_set = old_rows[0].get("storage_admin_set", 0) if old_rows else 0
            storage_en = old_rows[0].get("storage_enabled", 0) if old_rows else 0

            for dev in port_devices:
                all_ids = ",".join(dev.get("_all_ids", [dev.get("device_id", "")]))
                # NOTE: storage_en/storage_admin_set are already resolved above
                # from old_rows (the port's own DB-stored policy), which the
                # reconciliation pass (_reconcile, above) correctly resets to
                # blocked whenever a device genuinely relocates to a different
                # physical port. A port_device_grants.json lookup used to sit
                # here as a second, independent override keyed on
                # port_key::serial — but port_key strings get reused for
                # different physical sockets over time (confirmed on real
                # hardware), so a stale grants.json entry could resurrect
                # File Transfer access on a socket the admin never authorized,
                # even after the DB row had already been correctly reset. The
                # DB (old_rows/reconciliation) is the single source of truth
                # for storage access now; the JSON override was fully
                # redundant with it in the normal case and actively wrong in
                # the port-key-reuse case, so it's been removed.

                desired_rows.append({
                    "port_key": port_key,
                    "port_name": port_nm,
                    "device_name": dev["device_name"],
                    "device_type": dev["device_type"],
                    "port_enabled": port_en,
                    "port_admin_set": port_admin_set,
                    "keyboard_enabled": port_en if dev["device_type"] == "Keyboard" else 0,
                    "mouse_enabled": port_en if dev["device_type"] == "Mouse" else 0,
                    "storage_enabled": storage_en,
                    "storage_admin_set": storage_admin_set,
                    "status": port_status,
                    "device_id": all_ids,
                    "connected": 1
                })

        for port in empty_ports:
            port_key = port["port_key"]
            if port_key in by_port:
                continue

            old_rows = existing_by_port.get(port_key, [])
            old_type = old_rows[0].get("device_type", "None") if old_rows else "None"

            # Same name-anchoring as the occupied branch: prefer DB-stored name.
            if old_rows and old_rows[0].get("port_name"):
                port_nm = old_rows[0]["port_name"]
            else:
                port_nm = port["port_name"]
            if port_nm in used_port_names:
                port_nm = port["port_name"]
            counter = 2
            base_nm = port_nm
            while port_nm in used_port_names:
                port_nm = f"{base_nm} ({counter})"
                counter += 1
            used_port_names.add(port_nm)

            # Port-level enable/disable is the admin's persistent hardware
            # toggle for this physical port — it is not tied to whichever
            # device happens to be plugged in, so unplugging a device must
            # never reset it. Whatever was already stored carries forward
            # unchanged: an admin's explicit ON on an empty slot, or their
            # ON/OFF decision made while a device occupied this port. (This
            # was the second half of the reinsert bug: resetting to 0 here
            # on every unplug meant the admin's ON was already destroyed in
            # the DB before the device was even plugged back in.)
            port_en = old_rows[0].get("port_enabled", 0) if old_rows else 0
            port_status = "ON" if port_en else "OFF"

            # Storage Access on an empty port is the admin's persistent,
            # port-level policy — same principle as port_en just above — and
            # must carry forward unchanged, NOT reset to Blocked (0). An
            # admin can explicitly turn File Transfer ON for a still-empty
            # port (db.py's update_port_toggle writes storage_enabled=1,
            # storage_admin_set=1 straight to this placeholder row precisely
            # so the authorization is already in effect before any device
            # shows up), and that decision must survive every scan cycle
            # between the toggle and the device actually being plugged in —
            # otherwise the very next unrelated device-change event (a scan
            # runs on ANY USB change, not just this port's) would silently
            # wipe the admin's authorization back to blocked before the
            # storage device ever arrives. A never-touched port still
            # correctly defaults to Blocked here, since old_rows carries
            # forward whatever 0/1 was already stored — new ports simply
            # start at 0 (see the "no old_rows" branch above).
            storage_admin_set = old_rows[0].get("storage_admin_set", 0) if old_rows else 0
            storage_en = old_rows[0].get("storage_enabled", 0) if old_rows else 0

            dev_name = "- Empty -"
            dev_type = "None"
            # Keep the last-known device_id instead of wiping it to "" —
            # it's what carries the device serial (see
            # _extract_device_serial) that vanished-row reconciliation
            # above needs to recognize this same physical device when it
            # reappears occupying a DIFFERENT LocationInfo/port_key than
            # the one it's vacating here. Blanking it on every unplug was
            # destroying that identity a scan cycle before reconciliation
            # ever got a chance to use it, which is why a storage device
            # kept spawning a new "USB Port N" row on replug even after
            # the serial-matching fix went in — the empty placeholder it
            # left behind had nothing left to match on. Doesn't affect
            # anything that reads emptiness: that's decided by
            # device_name/device_type/connected, never device_id.
            dev_id = old_rows[0].get("device_id", "") if old_rows else ""

            desired_rows.append({
                "port_key": port_key,
                "port_name": port_nm,
                "device_name": dev_name,
                "device_type": dev_type,
                "port_enabled": port_en,
                "port_admin_set": old_rows[0].get("port_admin_set", 0) if old_rows else 0,
                "keyboard_enabled": (old_rows[0].get("keyboard_enabled", 0) if old_rows else 0) if old_type == "None" else 0,
                "mouse_enabled": (old_rows[0].get("mouse_enabled", 0) if old_rows else 0) if old_type == "None" else 0,
                "storage_enabled": storage_en,
                "storage_admin_set": storage_admin_set,
                "status": port_status,
                "device_id": dev_id,
                "connected": 1 if dev_name not in ("- Empty -", "Empty Slot") else 0
            })

        desired_port_keys = {d["port_key"] for d in desired_rows}

        for d in desired_rows:
            reused_id = None
            port_key = d["port_key"]
            if port_key in existing_by_port and existing_by_port[port_key]:
                idx = 0
                for i, ex in enumerate(existing_by_port[port_key]):
                    if ex["device_type"] == d["device_type"]:
                        idx = i
                        break
                reused_id = existing_by_port[port_key].pop(idx)["id"]
                
            if reused_id is not None:
                c.execute("""
                    UPDATE usb_ports SET
                        port_key=%s, port_name=%s, device_name=%s, device_type=%s, port_enabled=%s, port_admin_set=%s,
                        keyboard_enabled=%s, mouse_enabled=%s, storage_enabled=%s, storage_admin_set=%s,
                        status=%s, device_id=%s, connected=%s
                    WHERE id=%s
                """, (
                    d["port_key"], d["port_name"], d["device_name"], d["device_type"], d["port_enabled"], d["port_admin_set"],
                    d["keyboard_enabled"], d["mouse_enabled"], d["storage_enabled"], d["storage_admin_set"],
                    d["status"], d["device_id"], d["connected"], reused_id
                ))
            else:
                c.execute("""
                    INSERT INTO usb_ports
                        (user_id, port_name, device_name, device_type, port_key,
                         port_enabled, port_admin_set, keyboard_enabled, mouse_enabled, storage_enabled, storage_admin_set,
                         status, device_id, connected)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    user_id, d["port_name"], d["device_name"], d["device_type"], d["port_key"],
                    d["port_enabled"], d["port_admin_set"], d["keyboard_enabled"], d["mouse_enabled"], d["storage_enabled"], d["storage_admin_set"],
                    d["status"], d["device_id"], d["connected"]
                ))

        # ── Phantom-blocked-storage pass ──────────────────────────────────────
        # A storage device with an UNKNOWN_PORT:: key (e.g. an NVMe-over-SCSI
        # drive) is force-removed from the Windows device tree when blocked.
        # list_all_physical_ports() never reports it as an empty physical slot,
        # so its DB row falls into the "leftover" bucket below and gets deleted
        # — making it disappear from the dashboard until the next unplug/replug.
        #
        # Fix: before the leftover cleanup, scan every port_key's remaining
        # (not-yet-consumed) existing rows. If any is a blocked storage device
        # (storage_enabled=0, device_type="Storage"), inject a synthetic
        # desired_row to keep it alive. Tag it with a last_seen timestamp so
        # we can expire it later if the drive is physically removed (the admin
        # must toggle File Transfer ON to clear a genuinely-unplugged device).
        # Limit phantom lifetime to ~5 minutes (300s) to auto-clean if the
        # drive is removed without admin interaction.
        _PHANTOM_TTL_SECONDS = 300
        import time as _time
        _now_ts = _time.time()

        desired_port_keys_set = {d["port_key"] for d in desired_rows}
        for pkey, leftover_rows in list(existing_by_port.items()):
            for ex in list(leftover_rows):
                if ex.get("device_type") != "Storage":
                    continue
                if ex.get("storage_enabled", 1):
                    continue  # storage is enabled — not blocked, don't phantom it
                if pkey in desired_port_keys_set:
                    continue  # already covered (e.g. physical slot now has a new device)
                if ex.get("device_name", "") in ("", "- Empty -", "Empty Slot"):
                    continue  # no name to restore
                if not pkey.startswith("UNKNOWN_PORT"):
                    # A canonical Port_#NNNN.Hub_#MMMM key IS a physical slot
                    # list_all_physical_ports() can (and does) correctly
                    # report as empty once the device is genuinely gone — so
                    # a normal unplug already produces a correct "- Empty -"
                    # desired_row for it elsewhere in this function. Phantom
                    # injection exists only for the case this function's own
                    # docstring describes: an UNKNOWN_PORT:: device that got
                    # force-removed from the devnode tree and therefore
                    # NEVER reappears as an empty slot at all. Applying it to
                    # every canonical port too meant a device you'd already
                    # physically unplugged kept showing as "Blocked" on the
                    # dashboard for up to 5 minutes after removal, even
                    # though the port was correctly, immediately empty.
                    continue

                # Check TTL using device_id as a proxy timestamp key stored in
                # a module-level dict so we don't need a schema change.
                phantom_key = f"{ex.get('id', '')}:{pkey}"
                first_seen = _phantom_blocked_first_seen.setdefault(phantom_key, _now_ts)
                age_seconds = _now_ts - first_seen
                if age_seconds > _PHANTOM_TTL_SECONDS:
                    log.info(f"  [Phantom] Blocked storage '{ex.get('device_name')}' on '{pkey}' "
                             f"exceeded {_PHANTOM_TTL_SECONDS}s TTL — treating as physically removed")
                    _phantom_blocked_first_seen.pop(phantom_key, None)
                    continue  # let the leftover cleanup delete it

                # Keep the row alive by injecting a desired_row for this port_key.
                storage_en = ex.get("storage_enabled", 0)
                port_en    = ex.get("port_enabled", 0)
                phantom_nm = ex.get("port_name") or pkey
                log.info(f"  [Phantom] Keeping blocked storage '{ex.get('device_name')}' "
                         f"visible on '{phantom_nm}' (age {int(age_seconds)}s / {_PHANTOM_TTL_SECONDS}s TTL)")
                desired_rows.append({
                    "port_key":        pkey,
                    "port_name":       phantom_nm,
                    "device_name":     ex.get("device_name", "Unknown Storage"),
                    "device_type":     "Storage",
                    "port_enabled":    port_en,
                    "port_admin_set":  ex.get("port_admin_set", 0),
                    "keyboard_enabled": 0,
                    "mouse_enabled":   0,
                    "storage_enabled": storage_en,
                    "storage_admin_set": ex.get("storage_admin_set", 0),
                    "status":          "ON" if port_en else "OFF",
                    "device_id":       ex.get("device_id", ""),
                    "connected":       0,
                })
                desired_port_keys_set.add(pkey)
                # Remove from leftover_rows so the cleanup loop below won't delete it.
                leftover_rows.remove(ex)
                break

        for leftovers in existing_by_port.values():
            for ex in leftovers:
                c.execute("DELETE FROM usb_ports WHERE id = %s", (ex["id"],))
                
        c.execute("DELETE FROM devices WHERE user_id = %s", (user_id,))
        for d in desired_rows:
            if d["device_name"] not in ("- Empty -", "Empty Slot"):
                # Status must be type-aware: Port Access (port_enabled) only
                # governs keyboard/mouse/webcam — Storage has its own
                # independent File Transfer policy (storage_enabled). Using
                # port_enabled for every device type meant a Storage device
                # always showed "Blocked" here whenever Port Access was off
                # for that port, even with File Transfer explicitly turned
                # on — the Device Details tab and the Port Details tab
                # (which already reads storage_enabled for storage rows)
                # disagreed about the very same device as a result.
                is_allowed = d["storage_enabled"] if d["device_type"] == "Storage" else d["port_enabled"]
                c.execute("""
                    INSERT INTO devices (user_id, device_name, device_type, port_name, port_key, status, connected)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (user_id, d["device_name"], d["device_type"], d["port_name"], d["port_key"], "Allowed" if is_allowed else "Blocked", d["connected"]))

        conn.commit()
    except Exception as e:
        log.error(f"DB sync failed: {e}")
        if conn: conn.rollback()
    finally:
        if conn: conn.close()

# Module-level dict tracking first-seen timestamps for phantom blocked-storage
# rows so we can expire them if the device is physically removed.
_phantom_blocked_first_seen: dict = {}


def load_port_name_cache(user_id: int) -> dict:
    """Load the persisted port_key -> port_name mapping from the DB so the
    scanner's in-memory _port_name_cache can be seeded before the first scan.
    This makes port numbering (USB Port 1 / USB Port 2 / ...) survive agent
    restarts without renumbering from scratch.

    Reads from port_physical_slots first (the stable socket_id anchor table
    introduced in the revised stable-numbering implementation).  Falls back
    to usb_ports.port_name for any port_key not yet in the new table (i.e.
    pre-migration rows or rows that predate the first agent scan after upgrade).

    Returns {port_key: port_name} or {} on error.
    """
    conn = None
    try:
        conn = _get_db_conn()
        c = conn.cursor()

        # Primary source: port_physical_slots (stable socket_id -> slot_number).
        # port_key here is the CURRENT Windows LocationInformation string that
        # maps to this socket, kept up-to-date by get_or_assign_slot_number().
        c.execute("""
            SELECT port_key, CONCAT('USB Port ', slot_number)
            FROM port_physical_slots
            WHERE user_id = %s
              AND port_key IS NOT NULL AND port_key != ''
        """, (user_id,))
        result = {row[0]: row[1] for row in c.fetchall()}

        # Fallback: fill in any port_key not yet in port_physical_slots from
        # the legacy usb_ports.port_name column.  This covers port rows that
        # existed before the upgrade (the backfill in init_db covers most of
        # them, but the agent-side cache load runs before init_db on a cold
        # start where the dashboard server hasn't yet connected).
        if not result:
            c.execute("""
                SELECT DISTINCT port_key, port_name
                FROM usb_ports
                WHERE user_id = %s
                  AND port_key IS NOT NULL AND port_key != ''
                  AND port_name IS NOT NULL AND port_name != ''
            """, (user_id,))
            for row in c.fetchall():
                if row[0] not in result:
                    result[row[0]] = row[1]

        log.info(f"[PortNameCache] Seeded {len(result)} port name(s) from DB for user {user_id} "
                 f"(port_physical_slots is the primary source)")
        return result
    except Exception as e:
        log.error(f"[PortNameCache] Failed to load port names from DB: {e}")
        return {}
    finally:
        if conn:
            conn.close()


def get_or_assign_slot_number(user_id: int, socket_id: str,
                               current_port_key: str = "") -> int:
    """Returns the permanent display slot number (the N in 'USB Port N') for
    a given physical socket, writing to port_physical_slots on first sight.

    - If socket_id already has a row, returns its slot_number and silently
      updates port_key if Windows renamed the LocationInformation string for
      this same physical socket (the port_key-rename reconciliation case).
    - If not seen before, assigns MAX(slot_number)+1 for this user and inserts.

    socket_id        : stable physical socket identity from registry_utils.get_socket_id()
    current_port_key : current Windows LocationInformation string for this socket

    Returns the slot number (>= 1), or 0 on a DB error (caller should fall back
    to the in-memory counter in that case).
    """
    conn = None
    try:
        conn = _get_db_conn()
        c = conn.cursor()

        c.execute(
            "SELECT slot_number, port_key FROM port_physical_slots "
            "WHERE user_id=%s AND socket_id=%s FOR UPDATE",
            (user_id, socket_id)
        )
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
                log.info(f"[PhysSlots] port_key renamed for socket '{socket_id}': "
                         f"'{stored_pk}' -> '{current_port_key}' (slot {slot_num} unchanged)")
            return slot_num

        # New socket seen for the first time — assign the next slot number.
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
        log.info(f"[PhysSlots] New socket assigned: socket_id='{socket_id}' "
                 f"port_key='{current_port_key}' -> slot_number={new_slot}")
        return new_slot
    except Exception as e:
        log.error(f"[PhysSlots] get_or_assign_slot_number error (falling back to 0): {e}")
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        return 0
    finally:
        if conn:
            conn.close()


def _get_user_id(username):
    conn = None
    try:
        conn = _get_db_conn()
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(%s)", (username,))
        row = c.fetchone()
        return row[0] if row else None
    except Exception as e:
        log.error(f"Failed to get user: {e}")
        return None
    finally:
        if conn:
            conn.close()

def report_unregistered(username, hostname, agent_version=None):
    """Called when _get_user_id(username) comes back empty — records that an
    agent is running on a PC/account the dashboard doesn't recognize yet, so
    it can show up as an alert instead of only sitting in this machine's
    local log file. Safe to call repeatedly (upserts on username+hostname)."""
    conn = None
    try:
        conn = _get_db_conn()
        c = conn.cursor()
        c.execute("""
            INSERT INTO unregistered_agents (resolved_username, hostname, agent_version)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                agent_version = VALUES(agent_version),
                last_seen = NOW()
        """, (username, hostname or "", agent_version or ""))
        conn.commit()
    except Exception as e:
        log.error(f"Failed to report unregistered agent: {e}")
    finally:
        if conn:
            conn.close()


def _send_heartbeat(user_id, hostname):
    conn = None
    try:
        conn = _get_db_conn()
        c = conn.cursor()
        c.execute("""
            INSERT INTO agent_status
                (user_id, hostname, agent_version, last_checkin,
                 kbd_blocked, mse_blocked, hooks_installed)
            VALUES (%s, %s, %s, NOW(), 0, 0, 0)
            ON DUPLICATE KEY UPDATE
                hostname=%s, agent_version=%s, last_checkin=NOW()
        """, (user_id, hostname, AGENT_VERSION,
              hostname, AGENT_VERSION))
        conn.commit()
    except Exception as e:
        log.error(f"Heartbeat failed: {e}")
    finally:
        if conn:
            conn.close()

def _fetch_port_settings(user_id):
    conn = None
    try:
        conn = _get_db_conn()
        c = conn.cursor(dictionary=True)
        # Fetch detailed rows so we can track specific device authorizations
        c.execute("""
            SELECT port_key, device_id, device_type, port_enabled, storage_enabled, storage_admin_set, connected
            FROM usb_ports
            WHERE user_id = %s AND port_key IS NOT NULL AND port_key != ''
        """, (user_id,))
        rows = c.fetchall()
        # "None" rows (empty-slot placeholders) shouldn't count toward
        # has_non_storage_device below — an empty port row's leftover
        # device_type from whatever last occupied it is not "currently a
        # non-storage device at this port".

        # (Previously this mirrored storage grants out to a local
        # port_device_grants.json cache. That cache was only ever read back
        # as an override in _sync_devices_to_db, and has been removed there —
        # see the note at that call site. Writing to it here served no
        # remaining purpose, so it's been removed too.)

        # Return the port-level rolled up settings that agent_core.py expects
        results_by_port = {}
        for r in rows:
            pk = (r["port_key"] or "").strip().replace("\r", "\n").replace("\n", "")
            if pk not in results_by_port:
                results_by_port[pk] = {
                    "port_key": pk,
                    "port_enabled": bool(r.get("port_enabled", 1)),
                    "storage_enabled": bool(r.get("storage_enabled", 0)),
                    # A port's empty-placeholder row deliberately keeps the
                    # last-known device_id around (see the empty_ports
                    # comment in _sync_devices_to_db) so a device replugged
                    # into the same physical port can be recognized. That
                    # means device_ids alone can look IDENTICAL right before
                    # an unplug and right after the same device is replugged
                    # — so agent_core.py's change-detection (which compares
                    # port_enabled/storage_enabled/device_ids against the
                    # last-applied state) saw "nothing changed" across an
                    # actual unplug -> replug and never reissued the live
                    # enable call to the fresh devnode Windows created on
                    # reconnect. The dashboard/DB correctly showed
                    # "Allowed" (that part was never wrong) while the
                    # physical device stayed unenforced. Surfacing
                    # `connected` here (0 while empty, 1 once occupied —
                    # already tracked correctly in usb_ports.connected) lets
                    # the caller fold it into that comparison so a
                    # disconnect/reconnect is never mistaken for "no
                    # change", regardless of whether the device_id matches.
                    "connected": bool(r.get("connected", 0)),
                    # True if any row for this port is a real, non-storage
                    # device type (Keyboard/Mouse/Webcam/etc — not "None",
                    # not "Storage"). agent_core.py uses this to decide
                    # whether set_port_enabled() needs to run at all for
                    # this port. Previously that decision was made INSIDE
                    # set_port_enabled() via a live WMI re-discovery of the
                    # storage child devnode at call time — which silently
                    # fails whenever the storage device happens to be
                    # currently disabled, since Windows removes a disabled
                    # parent's child devnodes from the device tree (see the
                    # long comment at that call site). A port sitting at
                    # (Port Access OFF, File Transfer ON) for a pure storage
                    # device would then get its ONLY device wrongly
                    # re-disabled by set_port_enabled just before
                    # set_storage_enabled tried to re-enable it — set_storage
                    # _enabled's own live lookup then also found nothing
                    # (same vanished-child-node reason) and silently did
                    # nothing, leaving the drive disabled while the
                    # dashboard/DB still showed "Allowed". The DB's stored
                    # device_type doesn't depend on live enumeration, so
                    # deciding this here instead is reliable regardless of
                    # the device's current enabled/disabled state.
                    "has_non_storage_device": False,
                    "device_ids": []
                }
            else:
                results_by_port[pk]["port_enabled"] = results_by_port[pk]["port_enabled"] or bool(r.get("port_enabled", 1))
                results_by_port[pk]["storage_enabled"] = results_by_port[pk]["storage_enabled"] or bool(r.get("storage_enabled", 0))
                results_by_port[pk]["connected"] = results_by_port[pk]["connected"] or bool(r.get("connected", 0))

            if r.get("device_type") not in (None, "", "None", "Storage"):
                results_by_port[pk]["has_non_storage_device"] = True

            if r.get("device_id"):
                results_by_port[pk]["device_ids"].extend(r["device_id"].split(","))
                
        return list(results_by_port.values())
    except Exception as e:
        log.error(f"Fetch settings failed: {e}")
        return []
    finally:
        if conn:
            conn.close()

def _expire_stale_transfer_grants(user_id):
    conn = None
    try:
        conn = _get_db_conn()
        c = conn.cursor()
        c.execute("""
            SELECT id, port_name FROM usb_ports
            WHERE user_id = %s AND storage_enabled = 1
              AND transfer_expires_at IS NOT NULL
              AND transfer_expires_at < NOW()
        """, (user_id,))
        stale = c.fetchall()
        if not stale:
            return
        for port_id, port_name in stale:
            c.execute(
                "UPDATE usb_ports SET storage_enabled=0, transfer_expires_at=NULL WHERE id=%s",
                (port_id,)
            )
            c.execute("""
                UPDATE devices SET status='Blocked'
                WHERE user_id=%s AND port_name=%s AND device_type NOT IN ('Keyboard', 'Mouse')
            """, (user_id, port_name))
            c.execute("""
                INSERT INTO audit_logs
                    (user_id, action, old_value, new_value, port_id, port_name_snapshot)
                VALUES (%s, 'storage_grant_expired', '1', '0', %s, %s)
            """, (user_id, port_id, port_name))
            log.info(f"File-transfer grant expired for port '{port_name}' — storage access revoked")
        conn.commit()
    except Exception as e:
        log.error(f"Expire transfer grants failed: {e}")
    finally:
        if conn:
            conn.close()
