import re
import registry_utils
from agent_config import log, IS_WINDOWS, _HAS_WMI

if _HAS_WMI:
    try:
        import wmi
    except ImportError:
        pass

# Sticky mapping of VIRTUAL_KBD / VIRTUAL_MSE placeholder -> physical slot
# port_key, so a virtual device is folded onto the same empty physical slot
# every scan instead of "next available" (which was non-deterministic and
# could hand a virtual device the port_key most recently freed up by an
# unrelated real device).
_virtual_slot_cache = {}

# Sticky port_key -> display name mapping ("USB Port 1", "Unknown Port 1", ...).
# Assigned ONCE per port_key, the first time that key is ever seen, and never
# changed again for the life of the process. This is what makes "USB Port 1"
# always mean the same physical connector.
#
# The old logic instead re-sorted and renumbered ALL currently-known
# port_keys from scratch on every single scan (1, 2, 3... in sorted order of
# whichever keys happened to be occupied/empty *right now*). With only one
# port occupied at a time, whichever port_key was occupied always sorted
# into position 1 and got called "USB Port 1" no matter which physical
# connector (Port_#0001 vs Port_#0002 etc.) it actually was — so moving the
# same device to a different physical port still showed up as "USB Port 1"
# on the dashboard, even though the underlying port_key logged by the agent
# was genuinely different each time.
# Sticky port_key -> display name mapping ("USB Port 1", "Unknown Port 1", ...).
# Assigned ONCE per port_key and never changed again for the life of the process.
# Physical ports now get their number from port_physical_slots in the DB
# (via get_or_assign_slot_number), keyed on a stable socket_id derived from
# the hub's VID+PID+serial and port position — both hardware facts that survive
# Windows port_key (LocationInformation) renaming across reboots.
_port_name_cache = {}
_next_phys_idx = 1   # fallback-only counter (used when DB lookup fails)
_next_unk_idx = 1

# user_id and slot-lookup callback, set by seed_port_names_from_db() so that
# _assign_stable_port_names() can call get_or_assign_slot_number() on the DB.
_user_id: int = 0
_db_slot_fn = None  # callable(user_id, socket_id, port_key) -> int

import re as _re_portname


def _port_sort_key_for_naming(k):
    """Sort key used only to decide the ORDER new port_keys get their
    first-ever number in, so a machine with several hubs gets predictable
    numbering (hub 1's ports, then hub 2's, ...) instead of whatever order
    the scan happened to discover them in."""
    m = _re_portname.search(r'Port_#(\d+)\.Hub_#(\d+)', k, _re_portname.IGNORECASE)
    if m:
        return (0, int(m.group(2)), int(m.group(1)))
    return (1, 0, 0)


def _assign_stable_port_names(all_keys, seed_from_db=None, socket_id_map=None):
    """
    Given the full set of port_keys visible in this scan (occupied + empty),
    return {port_key: display_name}, reusing any name already assigned to a
    key in a previous scan and only minting a new name for keys seen for the
    first time.

    For physical ports (Port_#NNN.Hub_#NNN keys), the slot number is looked up
    (or permanently assigned) via get_or_assign_slot_number() in the DB, keyed
    on socket_id — a stable hardware identity built from the hub's VID+PID+serial
    and the port's wiring position on that hub.  This makes "USB Port 1" always
    mean the same physical connector regardless of how Windows renumbers the
    LocationInformation string across reboots or PnP rescans.

    seed_from_db:   optional {port_key: name} from DB at startup — applied once
                    to pre-populate the cache so numbers survive restarts.
    socket_id_map:  {port_key: (socket_id, hub_instance_id, port_number)} built
                    by _build_port_universe() from the physical topology data.
    """
    global _next_phys_idx, _next_unk_idx

    # Apply DB seed exactly once: merge it into the cache so we don't re-mint
    # numbers for keys we already named in a previous process run.
    if seed_from_db:
        for k, name in seed_from_db.items():
            if k not in _port_name_cache:
                _port_name_cache[k] = name
                # Advance the fallback counters past any numbers already in use.
                import re as _re
                m = _re.search(r'USB Port (\d+)$', name)
                if m:
                    _next_phys_idx = max(_next_phys_idx, int(m.group(1)) + 1)
                m2 = _re.search(r'Unknown Port (\d+)$', name)
                if m2:
                    _next_unk_idx = max(_next_unk_idx, int(m2.group(1)) + 1)
        # Clear seed so it's only applied once for the lifetime of the process.
        seed_from_db.clear()

    socket_id_map = socket_id_map or {}

    for k in sorted(all_keys, key=_port_sort_key_for_naming):
        if k in _port_name_cache:
            # Already named — if we have hub data for this key, ensure the
            # DB row's port_key is up-to-date (handles the rename case where
            # Windows changed the LocationInfo string but the cache still has
            # the old name from the seed).
            if k in socket_id_map and _db_slot_fn and _user_id:
                sid, _, _ = socket_id_map[k]
                if sid and not sid.startswith("hub:UNKNOWNHUB"):
                    # Touch DB to update port_key if it changed, slot stays same.
                    _db_slot_fn(_user_id, sid, k)
            continue

        # Virtual / unknown — not real physical sockets; use in-memory counter.
        if k in ('VIRTUAL_KBD', 'VIRTUAL_MSE') or k.startswith('UNKNOWN_PORT'):
            _port_name_cache[k] = f"Unknown Port {_next_unk_idx}"
            _next_unk_idx += 1
            continue

        # Physical port: look up (or permanently assign) slot number from DB.
        slot_num = 0
        if _db_slot_fn and _user_id and k in socket_id_map:
            sid, _, _ = socket_id_map[k]
            if sid and not sid.startswith("hub:UNKNOWNHUB"):
                try:
                    slot_num = _db_slot_fn(_user_id, sid, k)
                except Exception as _e:
                    log.warning(f"[PhysSlots] DB lookup failed for {k}: {_e}")

        if slot_num > 0:
            _port_name_cache[k] = f"USB Port {slot_num}"
            # Keep fallback counter ahead of any DB-assigned number.
            _next_phys_idx = max(_next_phys_idx, slot_num + 1)
        else:
            # Fallback: DB unavailable or no socket_id — use in-memory counter.
            # This preserves the old behaviour so the system degrades gracefully.
            _port_name_cache[k] = f"USB Port {_next_phys_idx}"
            log.warning(f"[PhysSlots] No DB slot for {k!r} — assigned fallback "
                        f"'USB Port {_next_phys_idx}' (not persisted to port_physical_slots)")
            _next_phys_idx += 1

    return {k: _port_name_cache[k] for k in all_keys}

# Storage device IDs we've already fired an immediate quarantine-disable for.
# This is intentionally a "seen once, never again" set for the life of the
# process: quarantine exists purely to close the exposure window on FIRST
# sight of a new storage device (see _quarantine_new_storage_device below).
# Once a device has been quarantined, the normal DB-driven apply cycle in
# agent_core.py takes over for its actual enabled/disabled state (including
# re-enabling it live if an admin approves access) - we must not keep
# re-disabling it on every later scan, or an admin-approved device would
# never stay enabled.
_quarantined_storage_ids = set()

# ── Storage-authorization grace window (race-condition fix) ────────────────
# When an admin approves storage access for a port, registry_utils.
# set_storage_enabled(True) doesn't just flip a flag - it live-enables the
# devnode and then deliberately forces a bus rescan + volume rescan
# (_pnp_rescan / _force_volume_rescan) so the drive letter actually remounts.
# Those forced rescans make Windows re-enumerate the physical device, which
# hands it a BRAND NEW instance_id (and/or fires fresh Win32_DeviceChangeEvent
# arrivals that wake agent_events and trigger an immediate rescan here).
#
# Without this grace window, that re-enumeration looked to
# _quarantine_new_storage_device() exactly like a brand-new, never-before-seen
# storage device (new instance_id => not in _quarantined_storage_ids) showing
# up on a port that the admin had just approved - so it got immediately
# quarantined (disabled) again, a few hundred ms after being enabled. That's
# the authorization race: an approved enable, immediately undone by the
# agent's own quarantine reacting to the side effect of that same enable.
#
# The fix: agent_core marks a port_key as "storage recently authorized" the
# moment its live storage-enable succeeds. For _STORAGE_AUTH_GRACE_SECONDS
# afterward, any storage device discovered on that exact port_key - no matter
# its instance_id - is treated as part of that approved enable workflow, not
# a new unauthorized insertion, and is skipped by quarantine.
_STORAGE_AUTH_GRACE_SECONDS = 30
_storage_authorized_until: dict = {}


def mark_storage_authorized(port_key: str):
    """Call the instant a live 'enable storage' apply succeeds for
    port_key. Opens a grace window during which any storage device
    discovered on this same physical port is treated as the expected
    Windows re-enumeration of that same approved device, not a new
    unauthorized insertion, so quarantine won't re-disable it."""
    import time as _time
    if not port_key:
        return
    _storage_authorized_until[port_key] = _time.monotonic() + _STORAGE_AUTH_GRACE_SECONDS


def _is_storage_recently_authorized(port_key: str) -> bool:
    import time as _time
    if not port_key:
        return False
    deadline = _storage_authorized_until.get(port_key)
    return deadline is not None and _time.monotonic() < deadline


def _quarantine_new_storage_device(dev, instance_id: str, name: str, port_key: str = None):
    """
    Immediately (synchronously, right here in the scan) attempts a live
    disable of a storage device the FIRST time it's ever seen by this agent
    process - before the normal scan -> DB sync -> fetch settings -> diff ->
    apply pipeline has a chance to run.

    Why this exists: that normal pipeline is correct (new storage devices
    default to enabled=False/storage=False in the DB - see agent_db.py) but
    it involves several DB round-trips to a remote host plus multiple
    subprocess/API calls, which can take anywhere from a few seconds to over
    ten seconds end-to-end (see agent_core.py's threaded apply with its
    20s join timeout, and the WMI device-change watcher's frequent
    reconnect-with-5s-sleep cycle that forces a fallback to the slower fixed
    poll). During that whole window the drive was fully mounted and
    readable/writable in Windows - which is indistinguishable from "not
    blocked" to a user with a stopwatch. This function exists purely to
    shrink that window to near-zero by firing the disable the moment the
    scan itself discovers the device, using the WMI object we already have
    in hand instead of waiting for a fresh port-lookup query.

    This is a best-effort speed optimization, not a replacement for the
    normal DB-driven pipeline - that pipeline still runs afterwards and is
    the source of truth for whether the device should end up enabled
    (e.g. if an admin has pre-approved this exact device) or stay disabled.
    """
    if instance_id in _quarantined_storage_ids:
        return
    _quarantined_storage_ids.add(instance_id)

    if _is_storage_recently_authorized(port_key):
        log.info(f"  [QUARANTINE] Skipped for {name} ({instance_id}) on port {port_key}: "
                 f"storage was approved/enabled on this port within the last "
                 f"{_STORAGE_AUTH_GRACE_SECONDS}s — treating this as the expected Windows "
                 f"re-enumeration from that approved enable, not a new unauthorized insertion.")
        return

    # For UASP-attached drives, instance_id is the SCSI\ disk node — but the
    # normal DB-driven pipeline always resolves to the USB bridge controller
    # above it (via _dedupe_root_ids -> _strip_mi_suffix in
    # registry_utils.set_port_enabled), since that's the node Windows
    # actually lets us enable/disable reliably. If quarantine targets the
    # disk node while every later admin toggle targets the bridge
    # controller, the two paths are silently acting on two different
    # devnodes for the same physical drive — which is exactly the kind of
    # mismatch that makes a later 'enable' toggle look stuck even though it
    # succeeded on a node the dashboard/DB weren't tracking. Resolve to the
    # same target here so quarantine and every later toggle agree.
    # NOTE: _strip_mi_suffix (not _get_usb_parent_id) deliberately leaves
    # USBSTOR\ (older bulk-only) drive IDs untouched — only SCSI\ (UASP)
    # IDs get walked up to their parent — so this must match exactly, or
    # ordinary USBSTOR drives would regress into the same mismatch instead.
    target_id = registry_utils._strip_mi_suffix(instance_id)

    try:
        live_ok = registry_utils._try_live_disable_devnode(target_id, disabled=True)
        # Best-effort persistence bookkeeping, same as the normal path -
        # failure here is logged inside the helper and doesn't affect the
        # live block that already happened (or didn't).
        registry_utils._setupapi_set_config_flags(target_id, disabled=True)
        if live_ok:
            log.info(f"  [QUARANTINE] New storage device blocked immediately on detection: {name} ({target_id})")
        else:
            log.warning(f"  [QUARANTINE] Immediate block FAILED for new storage device: {name} ({target_id}) "
                        f"— normal apply pipeline will retry shortly")
    except Exception as e:
        log.warning(f"  [QUARANTINE] Error attempting immediate block for {name} ({target_id}): {e}")

_INTERNAL_VID_PIDS = [
    "SYNA", "SYNA2393",  # Synaptics touchpad / internal HID
    "ELAN",              # Elan touchpad
    "ALPS",              # Alps touchpad
    "CONVERTEDDEVICE",   # Laptop keyboard in tablet mode
    "INTC",              # Intel integrated components
    "VID_413C&PID_8187",  # Dell internal Bluetooth module
]
# NOTE: VID_413C&PID_301A used to be listed here as "Dell internal
# keyboard". That assumption doesn't hold on this hardware: Windows itself
# reports this exact VID/PID as HID_DEVICE_SYSTEM_MOUSE (msmouse.inf,
# HID_Mouse_Inst.NT) - a Dell MOUSE, not a keyboard. Dell reuses VID_413C
# across many unrelated internal *and* external peripherals, so hardcoding
# one specific VID/PID as "always internal" is fragile - it silently drops
# a real external mouse from every scan wherever that PID happens to be a
# mouse instead of a keyboard. Internal-vs-external is decided by
# _is_internal_by_capabilities() (which reads the Capabilities bit from
# the registry) a few lines below instead - that check is hardware-honest
# and doesn't need a per-VID/PID guess list at all.

_INTERNAL_NAME_PATTERNS = [
    "touch pad", "touchpad", "trackpad",
    "integrated webcam", "webcam",
    "fingerprint", "bluetooth",
    "wireless radio",
    "system controller",
]

_GENERIC_CONTAINER_NAMES = {
    # We no longer aggressively skip "usb input device" here, because when a
    # device is disabled, its specific child node ("HID Keyboard Device")
    # disappears, leaving only the generic parent. We must detect the parent
    # so it doesn't disappear from the dashboard. agent_db.py will handle
    # deduping this if both parent and child are present.
    "hid-compliant consumer control device",
    "hid-compliant consumer",
    "consumer control",
}

# Win32_Keyboard()/Win32_PointingDevice() are separate, uncached WMI
# round-trips. _get_hid_type_hints() used to run both on EVERY single scan
# cycle regardless of whether anything changed, which was a big chunk of the
# 5-8s scan latency (each of these two class queries commonly costs
# 1-3s+ on top of the Win32_PnPEntity() query that agent_scanner already
# does). Neither list changes unless a device is actually plugged/unplugged,
# so cache them on the SAME TTL/invalidation as registry_utils._pnp_cache -
# a scan triggered by a real device-change event (or the safety-net timer
# after one) will still see fresh data, but back-to-back scans within the
# TTL reuse the last result instead of paying for two more WMI queries.
_HID_HINT_CACHE_TTL_SECONDS = 5.0
_hid_hint_cache = {"hints": None, "ts": 0.0}


def _get_hid_type_hints():
    import time as _time
    now = _time.monotonic()
    if (_hid_hint_cache["hints"] is not None
            and (now - _hid_hint_cache["ts"]) < _HID_HINT_CACHE_TTL_SECONDS):
        return _hid_hint_cache["hints"]

    hints = {}
    if not _HAS_WMI:
        return hints
    try:
        c = wmi.WMI()
        for kb in c.Win32_Keyboard():
            did = (getattr(kb, "DeviceID", "") or "").upper()
            if did:
                hints[did] = "Keyboard"
    except Exception as e:
        log.warning(f"Win32_Keyboard query failed: {e}")
    try:
        c = wmi.WMI()
        for ms in c.Win32_PointingDevice():
            did = (getattr(ms, "DeviceID", "") or "").upper()
            if did:
                hints[did] = "Mouse"
    except Exception as e:
        log.warning(f"Win32_PointingDevice query failed: {e}")

    _hid_hint_cache["hints"] = hints
    _hid_hint_cache["ts"] = now
    return hints


def _invalidate_hid_hint_cache():
    _hid_hint_cache["hints"] = None


_hid_prefix_cache = None

def _populate_hid_prefix_cache():
    global _hid_prefix_cache
    _hid_prefix_cache = {}
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\HID") as root_key:
            num_subkeys = winreg.QueryInfoKey(root_key)[0]
            for i in range(num_subkeys):
                sub_name = winreg.EnumKey(root_key, i)
                with winreg.OpenKey(root_key, sub_name) as sub_key:
                    num_instances = winreg.QueryInfoKey(sub_key)[0]
                    for j in range(num_instances):
                        instance_name = winreg.EnumKey(sub_key, j)
                        inst_path = rf"SYSTEM\CurrentControlSet\Enum\HID\{sub_name}\{instance_name}"
                        
                        device_type = None
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, inst_path) as inst_key:
                            service = None
                            try:
                                service, _ = winreg.QueryValueEx(inst_key, "Service")
                            except Exception:
                                pass
                            
                            desc = None
                            try:
                                desc, _ = winreg.QueryValueEx(inst_key, "DeviceDesc")
                            except Exception:
                                pass
                                
                        if service:
                            service_lower = service.lower()
                            if service_lower in ("mouhid", "mouclass"):
                                device_type = "Mouse"
                            elif service_lower in ("kbdhid", "kbdclass"):
                                device_type = "Keyboard"
                        
                        if not device_type and desc:
                            desc_lower = desc.lower()
                            if "mouse" in desc_lower:
                                device_type = "Mouse"
                            elif "keyboard" in desc_lower:
                                device_type = "Keyboard"
                                
                        if device_type:
                            _hid_prefix_cache[instance_name] = device_type
    except Exception as e:
        log.warning(f"Failed to build HID prefix cache: {e}")

def _find_type_from_registry_children(parent_id: str) -> str:
    import winreg
    prefix = None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SYSTEM\CurrentControlSet\Enum\{parent_id}") as key:
            prefix, _ = winreg.QueryValueEx(key, "ParentIdPrefix")
    except Exception:
        pass
        
    if not prefix:
        return None
        
    if _hid_prefix_cache is None:
        _populate_hid_prefix_cache()
        
    for instance_name, device_type in _hid_prefix_cache.items():
        if instance_name.startswith(prefix):
            return device_type
            
    return None


def _determine_device_type(dev, hid_hint: str = "") -> str:
    instance_id = dev.DeviceID or ""
    name = dev.Name or dev.Caption or "Unknown"
    pnp_class = getattr(dev, "PNPClass", "") or ""
    instance_upper = instance_id.upper()
    name_lower = name.lower()
    
    if hid_hint == "Keyboard" or pnp_class == "Keyboard" or "keyboard" in name_lower:
        return "Keyboard"
    if hid_hint == "Mouse" or pnp_class == "Mouse" or "mouse" in name_lower:
        return "Mouse"
    if pnp_class in ("Image", "Camera") or "webcam" in name_lower or "camera" in name_lower:
        return "Webcam"
        
    comp_ids = getattr(dev, "CompatibleID", None)
    if comp_ids:
        for cid in comp_ids:
            cid_upper = cid.upper()
            if "CLASS_03&SUBCLASS_01&PROT_01" in cid_upper or cid_upper.endswith("PROT_01"):
                return "Keyboard"
            if "CLASS_03&SUBCLASS_01&PROT_02" in cid_upper or cid_upper.endswith("PROT_02"):
                return "Mouse"
                
    if instance_id.startswith("USB\\"):
        reg_type = _find_type_from_registry_children(instance_id)
        if reg_type:
            return reg_type
            
    if pnp_class == "HIDClass":
        # If it's a disconnected/disabled HID device and we lost the hint,
        # default to Keyboard so it stays in the list as an input device.
        return "Keyboard"

            
    if pnp_class in ("DiskDrive", "CDROM", "WPD", "USBSTOR") or (pnp_class == "" and instance_upper.startswith("USBSTOR")):
        return "Storage"
        
    if getattr(dev, "ConfigManagerErrorCode", 0) != 0:
        return "DisabledPlaceholder"
        
    return "Storage"


def _is_internal_by_capabilities(instance_id: str) -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        reg_path = rf"SYSTEM\CurrentControlSet\Enum\{instance_id}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as key:
            caps, _ = winreg.QueryValueEx(key, "Capabilities")
            is_removable = bool(caps & 0x14)
            return not is_removable
    except Exception:
        return False


def _is_external_device(dev, hid_hint: str = "") -> bool:
    instance_id = dev.DeviceID or ""
    name = dev.Name or dev.Caption or "Unknown"
    pnp_class = getattr(dev, "PNPClass", "") or ""
    
    instance_upper = instance_id.upper()
    name_lower = name.lower()
    
    if not (instance_upper.startswith("USB\\") or instance_upper.startswith("USBSTOR\\")
            or instance_upper.startswith("HID\\") or instance_upper.startswith("SCSI\\")):
        return False

    if instance_upper.startswith("SCSI\\"):
        # UASP (USB Attached SCSI) external drives — most modern USB 3.0
        # docks/enclosures use this mode — enumerate as a plain SCSI disk,
        # NOT under USBSTOR\ like older bulk-only mass-storage drives. If we
        # don't special-case this, these drives are invisible to the agent
        # entirely (never scanned, never shown on the dashboard, can never
        # be blocked) even though Explorer/Disk Management show them fine.
        #
        # Internal disks (NVMe, SATA/AHCI) also enumerate under SCSI\, so we
        # can't just allow the prefix — confirm this exact device actually
        # sits behind a real USB controller by walking its CM parent chain.
        # _get_usb_parent_id returns the device's own ID unchanged if no
        # USB ancestor is found anywhere in the chain (e.g. an internal NVMe
        # drive whose ancestry is PCI, not USB) — that's the signal to skip.
        usb_parent = registry_utils._get_usb_parent_id(instance_id)
        if usb_parent == instance_id:
            log.debug(f"  [SKIP] SCSI disk with no USB ancestor (internal): {name}")
            return False
    
    for pattern in _INTERNAL_VID_PIDS:
        if pattern.upper() in instance_upper:
            log.debug(f"  [SKIP] Matches internal VID/PID pattern '{pattern}': {name}")
            return False
    
    for pattern in _INTERNAL_NAME_PATTERNS:
        if pattern in name_lower:
            log.debug(f"  [SKIP] Matches internal name pattern '{pattern}': {name}")
            return False

    for generic in _GENERIC_CONTAINER_NAMES:
        if generic in name_lower:
            log.debug(f"  [SKIP] Generic container/aggregation device: {name}")
            return False
    
    if ("VID_" not in instance_upper and not instance_upper.startswith("USBSTOR\\")
            and not instance_upper.startswith("SCSI\\")):
        return False

    if instance_upper.startswith("USB\\") and _is_internal_by_capabilities(instance_id):
        log.debug(f"  [SKIP] Non-removable (internal) device: {name} ({instance_id})")
        return False

    if pnp_class == "USB" and instance_upper.startswith("USB\\") and "unknown usb device" not in name_lower:
        # If the device is disabled (ConfigManagerErrorCode != 0), we want to KEEP it.
        # This allows us to track blocked devices and know when they are physically unplugged!
        if getattr(dev, "ConfigManagerErrorCode", 0) == 0:
            log.debug(f"  [SKIP] USB composite parent container: {name}")
            return False
        
    dtype = _determine_device_type(dev, hid_hint)

    if dtype in ("Keyboard", "Mouse", "Webcam", "DisabledPlaceholder"):
        log.info(f"  [OK] EXTERNAL {dtype}: {name}")
        return True

    if dtype == "Storage":
        if pnp_class in ("DiskDrive", "CDROM", "WPD", "USBSTOR") or (pnp_class == "" and instance_upper.startswith("USBSTOR")):
            log.info(f"  [OK] EXTERNAL {dtype}: {name}")
            return True
        if pnp_class == "USB" and "unknown usb device" in name_lower:
            log.info(f"  [OK] EXTERNAL {dtype} (Failed Enumeration): {name}")
            return True
        return False
        
    return False


def _scan_real_usb_devices():
    if not _HAS_WMI:
        return []
    try:
        entities = registry_utils._get_pnp_entities()
    except Exception as e:
        log.error(f"WMI failed: {e}")
        return []

    hid_hints = _get_hid_type_hints()
    devices = []

    _vidpid_hint: dict = {}
    for did_upper, hint in hid_hints.items():
        m = re.search(r"(VID_[0-9A-F]+&PID_[0-9A-F]+)", did_upper)
        if m:
            vp = m.group(1)
            if vp not in _vidpid_hint:
                _vidpid_hint[vp] = hint

    for dev in entities:
        try:
            instance_id = dev.DeviceID or ""
            if not instance_id:
                continue
            if not (instance_id.startswith("USB") or instance_id.startswith("HID")
                    or instance_id.startswith("SCSI")):
                continue
            
            name = dev.Name or dev.Caption or "Unknown"
            instance_upper = instance_id.upper()
            hid_hint = hid_hints.get(instance_upper, "")
            
            if not hid_hint and instance_upper.startswith("USB\\"):
                hw_seg = instance_upper.split("\\")[1] if "\\" in instance_upper else ""
                if "&MI_" not in hw_seg and "&COL" not in hw_seg:
                    m = re.search(r"(VID_[0-9A-F]+&PID_[0-9A-F]+)", instance_upper)
                    if m:
                        hid_hint = _vidpid_hint.get(m.group(1), "")
            
            if _is_external_device(dev, hid_hint):
                port_key = registry_utils.get_port_key(dev)
                dtype = _determine_device_type(dev, hid_hint)

                if dtype == "Storage":
                    _quarantine_new_storage_device(dev, instance_id, name, port_key)

                devices.append({
                    "device_id": instance_id,
                    "device_name": name,
                    "device_type": dtype,
                    "port_key": port_key,
                    "port_name": None,
                })
        except Exception as e:
            log.warning(f"Error processing device: {e}")
    
    log.info(f"Found {len(devices)} external USB device(s) across "
             f"{len({d['port_key'] for d in devices})} occupied physical port(s)")
    return devices


# Module-level holder for the one-time DB seed dict passed in at startup.
# agent_core.py calls seed_port_names_from_db() once after resolving user_id;
# _build_port_universe() drains it into _assign_stable_port_names on the
# very first scan so names survive agent restarts.
_db_port_name_seed: dict = {}


def seed_port_names_from_db(names: dict, user_id: int = 0, slot_fn=None):
    """Called once at agent startup with the {port_key: port_name} dict
    loaded from the database.  Also accepts the user_id and the
    get_or_assign_slot_number() callable so _assign_stable_port_names()
    can look up/persist slot numbers from port_physical_slots."""
    global _db_port_name_seed, _user_id, _db_slot_fn
    _db_port_name_seed = names
    if user_id:
        _user_id = user_id
    if slot_fn is not None:
        _db_slot_fn = slot_fn


def _build_port_universe():
    devices = _scan_real_usb_devices()

    # Devices whose port_key is a *stable per-device* fallback
    # ("UNKNOWN_PORT::vid_pid::instance", built in registry_utils.get_port_key
    # from the device's own parent hardware ID) must NOT be remapped onto a
    # generic physical slot below - that key is already unique and repeatable
    # for this exact device across scans.
    stable_devices = [d for d in devices if d["port_key"].startswith("UNKNOWN_PORT")]
    virtual_devices = [d for d in devices if d["port_key"].startswith("VIRTUAL")]
    known_devices = [d for d in devices if d not in stable_devices and d not in virtual_devices]

    occupied_keys = {d["port_key"] for d in known_devices} | {d["port_key"] for d in stable_devices}

    all_physical = registry_utils.list_all_physical_ports(known_external_devices=devices)
    empty_ports = [p for p in all_physical if p["port_key"] not in occupied_keys and not p["occupied"]]

    # Sticky slot assignment for virtual placeholder devices only.
    global _virtual_slot_cache
    for d in virtual_devices:
        virtual_id = d["port_key"]  # e.g. "VIRTUAL_KBD" / "VIRTUAL_MSE"
        cached_slot = _virtual_slot_cache.get(virtual_id)
        if cached_slot and cached_slot not in occupied_keys and any(
            p["port_key"] == cached_slot for p in all_physical
        ):
            d["port_key"] = cached_slot
        elif empty_ports:
            assigned_port = empty_ports.pop(0)
            d["port_key"] = assigned_port["port_key"]
            _virtual_slot_cache[virtual_id] = d["port_key"]
        occupied_keys.add(d["port_key"])

    # Build socket_id_map: {port_key -> (socket_id, hub_instance_id, port_number)}
    # from the physical topology data returned by list_all_physical_ports().
    # This is the critical bridge between the unstable Windows port_key string
    # and the stable hardware identity (hub VID+PID+serial x port position).
    socket_id_map = {}
    for p in all_physical:
        pk = p.get("port_key", "")
        hub_inst = p.get("hub_instance_id", "")
        port_num = p.get("port_number", 0)
        if pk and not pk.startswith("VIRTUAL") and not pk.startswith("UNKNOWN_PORT"):
            sid = registry_utils.get_socket_id(hub_inst, port_num)
            socket_id_map[pk] = (sid, hub_inst, port_num)

    all_keys = sorted(occupied_keys | {p["port_key"] for p in empty_ports})
    # Pass the DB seed and socket_id_map on the first call.
    name_map = _assign_stable_port_names(
        all_keys,
        seed_from_db=_db_port_name_seed or None,
        socket_id_map=socket_id_map,
    )

    for d in devices:
        d["port_name"] = name_map[d["port_key"]]

    empty_records = [
        {
            "port_key": p["port_key"],
            "port_name": name_map[p["port_key"]],
            "hub_name": p["hub_name"],
        }
        for p in empty_ports
    ]

    if not all_physical:
        log.warning("Hub topology enumeration returned nothing — empty ports "
                    "won't be visible until something is plugged into them. "
                    "(WMI Win32_USBHub may be unavailable on this system.)")

    return devices, empty_records
