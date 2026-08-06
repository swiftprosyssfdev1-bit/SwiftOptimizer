"""
registry_utils.py
-----------------
"""

import re
import sys
import logging
import ctypes
import ctypes.wintypes as _wintypes

logger = logging.getLogger("USBAgent")

IS_WINDOWS = sys.platform == "win32"

# ── SetupAPI constants ────────────────────────────────────────────────────
SPDRP_CONFIGFLAGS   = 0x0000002B
CONFIGFLAG_DISABLED = 0x00000001   # DisableFlags bit = disable device
DIGCF_ALLCLASSES    = 0x00000004
DIGCF_PRESENT       = 0x00000002

_HAS_WMI = False
if IS_WINDOWS:
    try:
        import wmi as _wmi_mod
        _HAS_WMI = True
    except ImportError:
        logger.warning("'wmi' package not found — port discovery unavailable. "
                       "Install with: pip install wmi pywin32")

# Internal fixed-hardware markers, mirrored from agent_scanner.py's
# _INTERNAL_VID_PIDS/_INTERNAL_NAME_PATTERNS. Kept as a separate local copy
# (rather than importing agent_scanner) so registry_utils.py has no
# dependency on the agent package — it's also used by the GUI side.
# Used only to keep internal fixed hardware (webcam, fingerprint reader,
# Bluetooth radio, etc.) OUT of the physical "USB port slot" list —
# these devices have their own internal sub-hub port numbering that has
# nothing to do with the laptop's real external USB-A/C connectors, and
# counting them as ports inflated "Total External Ports" and produced
# multiple colliding "USB Port N" labels (one per internal hub).
_TOPOLOGY_INTERNAL_MARKERS = [
    "SYNA", "SYNA2393", "ELAN", "ALPS", "CONVERTEDDEVICE", "INTC",
    "VID_413C&PID_8187",
    # Dell internal composite device (keyboard+touchpad controller) that
    # enumerates as generic "USB Composite Device" and occupies TWO
    # Port_#N.Hub_#N slots simultaneously — the generic FriendlyName means
    # _TOPOLOGY_INTERNAL_NAME_PATTERNS can't catch it by name.
    "VID_413C&PID_2113",
    # Internal fingerprint reader (Goodix), also reports as generic
    # "USB Composite Device".
    "VID_27C6&PID_5395",
    # Internal webcam (Sonix), also reports as generic
    # "USB Composite Device".
    "VID_0C45&PID_6723",
]
_TOPOLOGY_INTERNAL_NAME_PATTERNS = [
    "touch pad", "touchpad", "trackpad",
    "integrated webcam", "webcam",
    "fingerprint", "bluetooth", "wireless radio",
]


def _is_internal_topology_node(instance_id: str, friendly_name: str) -> bool:
    """True if this USB PnP node is known internal fixed hardware whose
    port/hub numbering should not be treated as an external port slot."""
    inst_upper = (instance_id or "").upper()
    name_lower = (friendly_name or "").lower()
    for marker in _TOPOLOGY_INTERNAL_MARKERS:
        if marker in inst_upper:
            return True
    for pattern in _TOPOLOGY_INTERNAL_NAME_PATTERNS:
        if pattern in name_lower:
            return True
    return False


# VID_FFFF is reserved/invalid in the USB-IF vendor ID space — no real
# hardware vendor is assigned it. UASP-attached drives (Seagate/WD/many
# SanDisk SSDs/flash sticks) frequently enumerate a SECOND PnP node for
# their bridge/enclosure controller under this placeholder VID, generic
# FriendlyName "USB Mass Storage Device", at a DIFFERENT LocationInfo
# than the drive's own real-VID node. Left unfiltered, PS topology
# discovery treats that bridge node as its own physical port slot, so a
# single storage device insert spawns a phantom extra "USB Port" in the
# DB/dashboard alongside the drive's real port.
_TOPOLOGY_BRIDGE_PLACEHOLDER_VIDS = ["VID_FFFF"]
_TOPOLOGY_BRIDGE_GENERIC_NAMES = [
    "usb mass storage device", "usb attached scsi", "mass storage device",
]


def _is_uasp_bridge_duplicate(instance_id: str, friendly_name: str) -> bool:
    """True if this node looks like a UASP bridge controller's own PnP
    entry (placeholder vendor ID + generic mass-storage name) rather than
    the drive's real device node — a phantom duplicate port, not a
    genuine second physical slot."""
    inst_upper = (instance_id or "").upper()
    name_lower = (friendly_name or "").lower()
    if not any(vid in inst_upper for vid in _TOPOLOGY_BRIDGE_PLACEHOLDER_VIDS):
        return False
    return any(pattern in name_lower for pattern in _TOPOLOGY_BRIDGE_GENERIC_NAMES)


def _wmi_conn():
    return _wmi_mod.WMI()


# ── Cached device snapshot (discovery only) ────────────────────────────────
_PNP_CACHE_TTL_SECONDS = 5.0
_pnp_cache = {"entities": None, "ts": 0.0}
import threading
_pnp_cache_lock = threading.Lock()


def _get_pnp_entities(force_refresh: bool = False):
    """Returns the full Win32_PnPEntity() list, cached briefly to avoid
    redundant WMI round-trips within one scan cycle. DISCOVERY ONLY — never
    used to decide live enable/disable state."""
    import time as _time
    now = _time.monotonic()
    with _pnp_cache_lock:
        if (not force_refresh
                and _pnp_cache["entities"] is not None
                and (now - _pnp_cache["ts"]) < _PNP_CACHE_TTL_SECONDS):
            return _pnp_cache["entities"]

        # WMI might hang if multiple threads bypass the cache simultaneously.
        entities = list(_wmi_conn().Win32_PnPEntity())
        _pnp_cache["entities"] = entities
        _pnp_cache["ts"] = now
        return entities


def invalidate_pnp_cache():
    """Call after a registry write so the very next scan re-reads fresh
    discovery data instead of a stale cached snapshot."""
    _pnp_cache["entities"] = None


# ── Physical port identity (discovery only) ────────────────────────────────

def _get_usb_parent_id(device_id: str) -> str:
    if device_id.upper().startswith("USB\\") and "&MI_" not in device_id.upper():
        return device_id
        
    import ctypes
    try:
        cfgmgr32 = ctypes.windll.cfgmgr32
        devinst = ctypes.c_ulong()
        res = cfgmgr32.CM_Locate_DevNodeW(ctypes.byref(devinst), ctypes.c_wchar_p(device_id), 0)
        if res != 0:
            res = cfgmgr32.CM_Locate_DevNodeW(ctypes.byref(devinst), ctypes.c_wchar_p(device_id), 1)
            
        if res == 0:
            current_inst = devinst.value
            for _ in range(4): 
                parent_devinst = ctypes.c_ulong()
                if cfgmgr32.CM_Get_Parent(ctypes.byref(parent_devinst), current_inst, 0) == 0:
                    buf = ctypes.create_unicode_buffer(200)
                    if cfgmgr32.CM_Get_Device_IDW(parent_devinst.value, buf, len(buf), 0) == 0:
                        parent_id = buf.value
                        if parent_id.upper().startswith("USB\\") and "&MI_" not in parent_id.upper():
                            return parent_id
                        current_inst = parent_devinst.value
                    else:
                        break
                else:
                    break
    except Exception:
        pass
    return device_id


def get_port_key(dev) -> str:
    """
    Returns a stable identifier for the physical USB port a Win32_PnPEntity
    is plugged into. Prefers LocationInformation (e.g. "Port_#0002.Hub_#0003"),
    which stays the same for that physical connector regardless of which
    device occupies it. Falls back to a synthetic key derived from the
    parent hub's DeviceID if location info isn't available.
    """
    loc = (getattr(dev, "LocationInformation", "") or "").strip().replace("\r", "").replace("\n", "")
    import re
    if loc and re.search(r'Port_#\d+\.Hub_#\d+', loc, re.IGNORECASE):
        return loc

    dev_id = (getattr(dev, "DeviceID", "") or "").strip().replace("\r", "").replace("\n", "")
    
    parent_id = _get_usb_parent_id(dev_id)
    
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SYSTEM\CurrentControlSet\Enum\{parent_id}") as key:
            reg_loc, _ = winreg.QueryValueEx(key, "LocationInformation")
            if reg_loc:
                reg_loc = reg_loc.strip().replace("\r", "").replace("\n", "")
                if re.search(r'Port_#\d+\.Hub_#\d+', reg_loc, re.IGNORECASE):
                    return reg_loc
    except Exception:
        pass
        
    parts = parent_id.split("\\")
    if len(parts) >= 3:
        return f"UNKNOWN_PORT::{parts[1]}::{parts[2]}"
    elif len(parts) == 2:
        return f"UNKNOWN_PORT::{parts[1]}"
    else:
        return f"UNKNOWN_PORT::{parent_id}"



def get_socket_id(hub_instance_id: str, port_number: int) -> str:
    """
    Returns a stable physical socket identity that survives Windows
    port_key (LocationInformation) renumbering.

    Built from the USB hub's own VID+PID+serial (or PCI location if no
    real serial is available) combined with the cable's physical position
    on that hub. Both facts are determined by hardware wiring, not by
    OS enumeration order, so this string does not change across reboots,
    PnP rescans, or storage-enable side-effect re-enumerations.

    hub_instance_id: the Windows InstanceId of the *hub* that owns this
        port, e.g. "USB\\VID_8087&PID_0032\\5&3a3d3c5f&0&1".
        Falls back gracefully if None / empty.
    port_number: the port's position on that hub (from Port_#NNN).

    Returns a string like:
        "hub:VID_8087&PID_0032:SN_4C531001::port:1"   (real serial)
        "hub:VID_8087&PID_0032:LOC_5&3A3D&0&1::port:2" (bus-relative)
        "hub:UNKNOWNHUB::port:1"                        (fallback)
    """
    if not hub_instance_id:
        return f"hub:UNKNOWNHUB::port:{port_number}"

    parts = hub_instance_id.upper().replace("/", "\\").split("\\")
    # parts[0] = bus prefix ("USB"), parts[1] = VID+PID, parts[2] = instance
    vid_pid = parts[1] if len(parts) > 1 else "UNKNOWNHUB"
    instance_seg = parts[2] if len(parts) > 2 else ""

    # A real serial number contains no '&' (bus-relative location strings do).
    if instance_seg and "&" not in instance_seg:
        hub_id = f"{vid_pid}:SN_{instance_seg}"
    elif instance_seg:
        # Bus-relative but still useful: normalise for a consistent key.
        hub_id = f"{vid_pid}:LOC_{instance_seg}"
    else:
        hub_id = vid_pid

    return f"hub:{hub_id}::port:{port_number}"


STORAGE_PNP_CLASSES = {"DISKDRIVE", "CDROM", "WPD"}


def _is_storage_device_id(dev_id: str) -> bool:
    """
    True if dev_id is (or belongs to) an external-storage devnode of ANY
    kind — bulk-only USB flash/pen drives (USBSTOR\\...), UASP-attached
    external hard disks / SSDs (SanDisk, WD, Seagate, etc., which enumerate
    as DiskDrive/SCSI under the USB bridge controller's own USB\\VID_xxxx
    id rather than a USBSTOR\\ id), card readers, and USB CD/DVD drives.

    This must recognise the SAME set of devices agent_scanner.py labels
    "Storage" on the dashboard and _find_storage_devices_at_port() enforces,
    or a UASP external drive would slip past the USBSTOR-only string check
    and get double-toggled by set_port_enabled() AND set_storage_enabled()
    the same way plain flash drives used to (see set_port_enabled below).
    """
    if not dev_id:
        return False
    if dev_id.upper().startswith("USBSTOR"):
        return True
    try:
        for dev in _get_pnp_entities():
            if (getattr(dev, "DeviceID", "") or "").upper() != dev_id.upper():
                continue
            pnp_class = (getattr(dev, "PNPClass", "") or "").upper()
            return pnp_class in STORAGE_PNP_CLASSES
    except Exception:
        pass
    return False


def _find_devices_at_port(port_key: str):
    """All Win32_PnPEntity (USB/HID) objects currently occupying this
    physical port location. DISCOVERY ONLY."""
    if not _HAS_WMI:
        return []
    try:
        result = []
        for dev in _get_pnp_entities():
            instance_id = getattr(dev, "DeviceID", "") or ""
            if not (instance_id.startswith("USB") or instance_id.startswith("HID")):
                continue
            if get_port_key(dev) == port_key:
                result.append(dev)
        return result
    except Exception as e:
        logger.error(f"WMI query failed while locating port {port_key}: {e}")
        return []


def list_ports():
    """
    Enumerate all currently-occupied physical USB ports.
    Returns a list of dicts: {port_key, devices: [{device_id, name, pnp_class}]}
    """
    if not IS_WINDOWS or not _HAS_WMI:
        return []
    try:
        by_port = {}
        for dev in _get_pnp_entities():
            instance_id = getattr(dev, "DeviceID", "") or ""
            if not (instance_id.startswith("USB") or instance_id.startswith("HID")):
                continue
            port_key = get_port_key(dev)
            by_port.setdefault(port_key, []).append({
                "device_id": instance_id,
                "name": dev.Name or dev.Caption or "Unknown",
                "pnp_class": getattr(dev, "PNPClass", "") or "",
            })
        return [{"port_key": k, "devices": v} for k, v in by_port.items()]
    except Exception as e:
        logger.error(f"WMI port enumeration failed: {e}")
        return []


def _get_hub_location(hub) -> str:
    loc = (getattr(hub, "LocationInformation", "") or "").strip()
    if loc:
        return loc
    return "HUBID::" + (getattr(hub, "DeviceID", "") or "unknown")


_ps_topology_cache = {"ports": None, "ts": 0.0}
_PS_TOPOLOGY_TTL_SECONDS = 60.0

# Backoff applied after a failed/timed-out PowerShell refresh, so a
# persistently-hanging subprocess doesn't get re-launched (and re-blocked-on)
# every single scan cycle. Without this, a machine where PowerShell hangs
# under the SYSTEM/service context pays the full subprocess timeout on
# EVERY cycle forever — which is what was actually happening: the agent
# loop was seen stalling ~30s per cycle repeatedly instead of just once.
_PS_TOPOLOGY_FAILURE_BACKOFF_SECONDS = 20.0

import threading as _threading
_ps_topology_lock = _threading.Lock()
_ps_topology_refreshing = False
_ps_topology_last_failure_ts = 0.0


def _cached_ps_topology():
    """Non-blocking cached wrapper around _discover_ports_via_powershell().

    The physical port slot list barely ever changes, so we only want to pay
    the slow PowerShell subprocess cost occasionally — and critically, we
    must NEVER let a slow/hanging PowerShell subprocess block the caller
    (the main agent scan/apply loop). Previously this function ran the
    subprocess synchronously and waited out its full 30s timeout right in
    the loop; when PowerShell was consistently slow to respond, every scan
    cycle (and therefore every enable/disable apply, which only runs AFTER
    the scan completes) stalled by ~30 seconds.

    Now: the actual PowerShell call is kicked off in a background thread.
    This function always returns immediately with whatever is currently
    cached (even if stale or empty) — WMI/registry (Layers 2/3 in the
    caller) exist precisely to cover for a stale/missing Layer-1 snapshot.
    A failed refresh backs off for _PS_TOPOLOGY_FAILURE_BACKOFF_SECONDS
    before trying again, instead of re-launching a subprocess every single
    cycle when it's known to be hanging.
    """
    import time as _time
    import copy as _copy
    global _ps_topology_refreshing, _ps_topology_last_failure_ts

    now = _time.monotonic()
    stale = (_ps_topology_cache["ports"] is None
              or (now - _ps_topology_cache["ts"]) >= _PS_TOPOLOGY_TTL_SECONDS)
    in_backoff = (now - _ps_topology_last_failure_ts) < _PS_TOPOLOGY_FAILURE_BACKOFF_SECONDS

    if stale and not in_backoff:
        with _ps_topology_lock:
            already_refreshing = _ps_topology_refreshing
            if not already_refreshing:
                _ps_topology_refreshing = True

        if not already_refreshing:
            def _refresh():
                global _ps_topology_refreshing, _ps_topology_last_failure_ts
                try:
                    fresh = _discover_ports_via_powershell()
                    if fresh:
                        _ps_topology_cache["ports"] = fresh
                        _ps_topology_cache["ts"] = _time.monotonic()
                    else:
                        _ps_topology_last_failure_ts = _time.monotonic()
                        if _ps_topology_cache["ports"] is None:
                            # Cold start with nothing cached yet — cache the
                            # empty result so callers at least get []
                            # instead of None, rather than hammering
                            # PowerShell every cycle before the backoff
                            # window is even set.
                            _ps_topology_cache["ports"] = fresh
                            _ps_topology_cache["ts"] = _time.monotonic()
                        else:
                            logger.warning(
                                "  [PS topology] refresh failed/timed out — "
                                f"keeping last known-good "
                                f"{len(_ps_topology_cache['ports'])} slot(s), "
                                f"retrying in {_PS_TOPOLOGY_FAILURE_BACKOFF_SECONDS:.0f}s"
                            )
                finally:
                    with _ps_topology_lock:
                        _ps_topology_refreshing = False

            _threading.Thread(target=_refresh, daemon=True).start()

    # Always return immediately — never block the caller on the subprocess.
    # Return fresh dict copies so callers can safely mutate "occupied"/
    # "devices" without corrupting the cached snapshot.
    return _copy.deepcopy(_ps_topology_cache["ports"]) if _ps_topology_cache["ports"] else []


def invalidate_ps_topology_cache():
    """Call if you know the physical topology changed (e.g. a new hub was
    plugged in) and want the next scan to re-run PowerShell in the
    background instead of waiting out the TTL."""
    _ps_topology_cache["ports"] = None
    global _ps_topology_last_failure_ts
    _ps_topology_last_failure_ts = 0.0


def _discover_ports_via_powershell() -> list:
    """
    Layer 1 — PowerShell PnP topology query.

    Win32_USBHub.NumberOfPorts is broken on Windows 10/11 (always returns 0
    for most controllers), so we bypass WMI for hub-slot discovery entirely
    and instead ask PowerShell for every USB port PnP device.

    Strategy:
      - Query Get-PnpDevice for Class=USB (hubs, controllers, and port nodes)
        and Class=HIDClass to find all USB topology items.
      - Pull LocationInformation from Get-PnpDeviceProperty for every USB
        node that has a VID_/PID_ in its InstanceId (i.e. actual external
        ports, not root hub controllers themselves).
      - For nodes whose LocationInformation is a well-formed Port_#NNN.Hub_#NNN
        string, we trust it as a physical port key.

    Returns a list of dicts:
        {port_key, hub_name, port_number, occupied: bool, devices: [...]}
    Only port_key and hub_name are populated here; occupied/devices are
    filled in by the caller using the already-computed occupied_by_port map.
    """
    if not IS_WINDOWS:
        return []

    import subprocess, json, re

    # PowerShell script: enumerate all USB device instance IDs, then batch-pull
    # LocationInformation via Get-PnpDeviceProperty. Output as JSON so we parse
    # it without caring about PS column widths / encoding quirks.
    ps_script = r"""
$ports = @()
$devs = Get-PnpDevice -Class USB -ErrorAction SilentlyContinue
$devs += Get-PnpDevice -Class USBDevice -ErrorAction SilentlyContinue
foreach ($d in $devs) {
    $loc = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_LocationInfo' -ErrorAction SilentlyContinue).Data
    if ($loc -match 'Port_#\d+\.Hub_#\d+') {
        # Walk one level up the parent chain to get the hub's own InstanceId.
        # DEVPKEY_Device_Parent gives us the hub that physically owns this port,
        # whose VID+PID+serial is the stable hardware identity we anchor on.
        $parentId = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue).Data
        $ports += [PSCustomObject]@{
            InstanceId   = $d.InstanceId
            FriendlyName = $d.FriendlyName
            LocationInfo = $loc
            HubInstanceId = if ($parentId) { $parentId } else { '' }
        }
    }
}
$ports | ConvertTo-Json -Compress
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        )
        raw = (result.stdout or "").strip()
        if not raw:
            return []
        # PS returns a single object (not array) when there's only one item
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        ports_out = []
        seen_keys = set()
        skipped_internal = 0
        skipped_bridge = 0
        for item in data:
            loc = (item.get("LocationInfo") or "").strip()
            if not loc or loc in seen_keys:
                continue
            # Only accept well-formed Port_#NNN.Hub_#NNN keys
            m = re.search(r'Port_#(\d+)\.Hub_#(\d+)', loc, re.IGNORECASE)
            if not m:
                continue
            inst_id = item.get("InstanceId") or ""
            friendly = item.get("FriendlyName") or ""
            if _is_internal_topology_node(inst_id, friendly):
                skipped_internal += 1
                continue
            if _is_uasp_bridge_duplicate(inst_id, friendly):
                skipped_bridge += 1
                logger.info(f"    [PS topology] skipped {loc} <- InstanceId={inst_id!r} "
                            f"FriendlyName={friendly!r} — UASP bridge controller duplicate, "
                            f"not a genuine physical port")
                continue
            port_num = int(m.group(1))
            hub_num  = int(m.group(2))
            hub_inst_id = (item.get("HubInstanceId") or "").strip()
            seen_keys.add(loc)
            ports_out.append({
                "port_key":       loc,
                "hub_name":       f"USB Hub #{hub_num}",
                "port_number":    port_num,
                "hub_instance_id": hub_inst_id,   # stable hub identity for socket_id
                "occupied":       False,   # caller fills this in
                "devices":        [],
            })
            logger.info(f"    [PS topology] accepted {loc} <- InstanceId={inst_id!r} "
                        f"HubInstanceId={hub_inst_id!r} FriendlyName={friendly!r}")
        if skipped_internal:
            logger.info(f"  [PS topology] skipped {skipped_internal} internal "
                        f"fixed-hardware node(s) (webcam/fingerprint/Bluetooth/"
                        f"touchpad/etc.) — not counted as external port slots")
        if skipped_bridge:
            logger.info(f"  [PS topology] skipped {skipped_bridge} UASP bridge "
                        f"controller duplicate node(s) — not counted as external port slots")
        logger.info(f"  [PS topology] discovered {len(ports_out)} physical USB port slot(s)")
        return ports_out
    except Exception as e:
        logger.warning(f"PowerShell port discovery failed: {e}")
        return []


def _discover_ports_via_registry(known_keys: set) -> list:
    """
    Layer 2/3 — Registry fallback.

    Walks HKLM\\SYSTEM\\CurrentControlSet\\Enum\\USB and collects every
    LocationInformation value for removable devices (Capabilities & 4).
    Only adds port_keys not already in known_keys.

    This catches ports that have been used at least once (their last
    device left a registry entry behind) but aren't currently occupied.
    True brand-new ports that have NEVER had a device become visible here
    the first time something is plugged in — which is the best Windows
    allows without a specialised USB topology driver.

    Option B: Only port keys that match the canonical Port_#NNN.Hub_#NNN
    format (the same format Layer 1 / PowerShell produces) are admitted.
    Stale registry entries with oddly-formatted or legacy location strings
    are silently skipped — they represent ghost ports from old hardware
    connections, not real physical slots currently present on the machine.
    """
    if not IS_WINDOWS:
        return []
    try:
        import winreg, re as _re
    except ImportError:
        return []

    _PORT_HUB_RE = _re.compile(r'Port_#\d+\.Hub_#\d+', _re.IGNORECASE)

    ports_out = []
    try:
        usb_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                  r"SYSTEM\CurrentControlSet\Enum\USB")
        for i in range(winreg.QueryInfoKey(usb_key)[0]):
            vid_pid = winreg.EnumKey(usb_key, i)
            vid_key = winreg.OpenKey(usb_key, vid_pid)
            for j in range(winreg.QueryInfoKey(vid_key)[0]):
                inst = winreg.EnumKey(vid_key, j)
                inst_key = winreg.OpenKey(vid_key, inst)
                try:
                    loc, _ = winreg.QueryValueEx(inst_key, "LocationInformation")
                    if not loc or loc in known_keys:
                        continue
                    # Option B: require canonical Port_#NNN.Hub_#NNN format —
                    # the same key shape that Layer 1 (PowerShell) produces.
                    # Legacy / oddly-formatted location strings are ghost ports
                    # from historical connections and inflate the count.
                    if not _PORT_HUB_RE.search(loc):
                        logger.debug(
                            f"  [Registry supplement] skipping non-canonical "
                            f"location key {loc!r} (not Port_#N.Hub_#N format)"
                        )
                        continue
                    # Only include removable devices (Capabilities bit 2)
                    try:
                        caps, _ = winreg.QueryValueEx(inst_key, "Capabilities")
                        if not (caps & 4):
                            continue
                    except OSError:
                        pass  # no Capabilities — include anyway
                    known_keys.add(loc)
                    ports_out.append({
                        "port_key":    loc,
                        "hub_name":    "USB Port",
                        "port_number": 0,
                        "occupied":    False,
                        "devices":     [],
                    })
                except OSError:
                    pass
                finally:
                    winreg.CloseKey(inst_key)
            winreg.CloseKey(vid_key)
        winreg.CloseKey(usb_key)
    except Exception as e:
        logger.warning(f"Registry port discovery failed: {e}")
    return ports_out


def list_all_physical_ports(known_external_devices=None):
    """
    Enumerate EVERY physical USB port slot on the machine — occupied or
    empty — using a 3-layer discovery strategy. DISCOVERY ONLY.

    Layer 1 — PowerShell Get-PnpDevice + Get-PnpDeviceProperty:
        Most reliable on Windows 10/11. Queries USB topology via the PnP
        store, which reports port slots even when empty (unlike WMI's broken
        Win32_USBHub.NumberOfPorts). Falls through to Layer 2 on failure.

    Layer 2 — Win32_USBHub (WMI):
        Works on older Windows or when a USB hub driver reports NumberOfPorts
        correctly. Skipped silently when all hubs report 0 ports.

    Layer 3 — Registry HKLM\\...\\Enum\\USB (LocationInformation):
        Always runs as a supplement to Layers 1+2. Catches any port that
        has ever had a device plugged in (the registry retains the entry).
        Brand-new ports that have NEVER been used appear here only after
        their first device is plugged in — a fundamental Windows limitation
        with no workaround short of a custom USB PnP filter driver.

    known_external_devices: optional list of the ALREADY-FILTERED external
    device dicts produced by agent_scanner._scan_real_usb_devices() (each
    with "device_id"/"device_name"/"port_key"). When provided, this is used
    as the sole source of "what is occupying this port" instead of the raw,
    unfiltered list_ports() WMI enumeration below.

    Why this matters (phantom-port fix): list_ports() enumerates every
    USB\\/HID\\ Win32_PnPEntity with no internal/external filtering at all —
    unlike agent_scanner's _is_external_device()/_is_internal_topology_node()
    checks, which deliberately exclude internal-but-canonically-located
    hardware (webcams, Bluetooth radios, fingerprint readers, internal hubs,
    etc). An internal device that happens to report a well-formed
    "Port_#NNN.Hub_#NNN" LocationInformation is correctly skipped by PS
    Layer 1 (_is_internal_topology_node) — but previously reappeared anyway
    via the "safety net" loop below, which blindly trusted list_ports()'s
    raw occupied_by_port map. That safety-net addition is a canonical,
    OCCUPIED port_key, so Option C's empty-slot trimming never removes it
    either (only empty slots are capped) — it just sits there forever as an
    extra "USB Port" the dashboard shows despite no real external socket
    being there. Passing the pre-filtered device list closes that hole at
    the source: an internal device can never again manufacture a phantom
    occupied port, on this path or any future one.
    """
    if not IS_WINDOWS or not _HAS_WMI:
        return []

    # Map port_key -> devices for ports that are currently occupied.
    occupied_by_port = {}
    if known_external_devices is not None:
        for d in known_external_devices:
            occupied_by_port.setdefault(d["port_key"], []).append({
                "device_id": d.get("device_id", ""),
                "name": d.get("device_name", "Unknown"),
                "pnp_class": "",
            })
    else:
        # Legacy/fallback path (no pre-filtered device list supplied) — kept
        # for any other caller, but this is the path that let internal
        # devices leak into the physical port count. Prefer passing
        # known_external_devices wherever possible.
        for entry in list_ports():
            occupied_by_port[entry["port_key"]] = entry["devices"]

    all_ports   = []
    seen_keys   = set()

    # ── Layer 1: PowerShell PnP topology ────────────────────────────────────
    # This spawns a PowerShell subprocess + a Get-PnpDeviceProperty call per
    # USB device — it can easily take 5-15 seconds. Physical port slots don't
    # change between scans (only what's plugged into them does), so we cache
    # the *slot list* (port_key/hub_name/port_number) for a while and only
    # refresh the fast "which slots are occupied" part every cycle.
    ps_ports = _cached_ps_topology()
    # Option C: record how many slots Layer 1 (PowerShell) found — this is
    # the authoritative physical-slot count we use to cap later layers.
    ps_slot_count = len(ps_ports)
    for p in ps_ports:
        pk = p["port_key"]
        if pk in seen_keys:
            continue
        seen_keys.add(pk)
        p["occupied"] = pk in occupied_by_port
        p["devices"]  = occupied_by_port.get(pk, [])
        all_ports.append(p)

    # ── Layer 2: Win32_USBHub (kept for completeness, rarely adds anything
    #            on modern Windows but harmless to run) ─────────────────────
    # This is an uncached WMI query, run every scan cycle. When Layer 1
    # (the cached PowerShell topology) already produced a non-empty slot
    # list, Layer 2 essentially never contributes anything new on modern
    # Windows (Win32_USBHub.NumberOfPorts is broken/0 there — see the
    # warning below) — so paying for this WMI round-trip on every single
    # scan was pure latency for no benefit. Only run it when Layer 1 came
    # back empty (i.e. it's actually needed as a fallback), which is also
    # when it's cheap to skip entirely if it too finds nothing.
    hub_objects_seen = 0
    hub_ports_added  = 0
    if ps_slot_count == 0:
        try:
            c = _wmi_conn()
            for hub in c.Win32_USBHub():
                hub_objects_seen += 1
                try:
                    num_ports = int(getattr(hub, "NumberOfPorts", 0) or 0)
                    hub_name  = getattr(hub, "Name", "") or "USB Hub"
                    hub_dev_id = getattr(hub, "DeviceID", "") or ""
                    logger.info(f"  Win32_USBHub seen: name={hub_name!r} "
                                f"NumberOfPorts={num_ports} DeviceID={hub_dev_id!r}")
                    if num_ports <= 0:
                        continue
                    hub_loc = _get_hub_location(hub)
                    for port_num in range(1, num_ports + 1):
                        if hub_loc.startswith("Hub_#") or ".Hub_#" in hub_loc:
                            port_key = f"Port_#{port_num:04d}.{hub_loc}"
                        else:
                            port_key = f"{hub_loc}::port_{port_num}"
                        if port_key in seen_keys:
                            continue
                        seen_keys.add(port_key)
                        devices = occupied_by_port.get(port_key, [])
                        all_ports.append({
                            "port_key":    port_key,
                            "hub_name":    hub_name[:80],
                            "port_number": port_num,
                            "occupied":    bool(devices),
                            "devices":     devices,
                        })
                        hub_ports_added += 1
                except Exception as e:
                    logger.warning(f"Error walking hub ports: {e}")
        except Exception as e:
            logger.error(f"WMI hub topology enumeration failed: {e}")

    if hub_objects_seen > 0 and hub_ports_added == 0:
        logger.warning(f"Win32_USBHub returned {hub_objects_seen} hub "
                       "object(s), but none reported NumberOfPorts > 0 — "
                       "common on Windows 10/11; PowerShell + registry layers cover this.")

    # ── Layer 3: Registry supplement (historically used ports) ──────────────
    # IMPORTANT: when Layer 1 (PowerShell) succeeded, it is the authoritative
    # list of REAL physical slots (ps_slot_count > 0 means we already added
    # every genuine Port_#NNN.Hub_#NNN slot that exists on this machine to
    # all_ports/seen_keys above). Any *additional* canonical-shaped key the
    # registry turns up on top of that is therefore NOT a real slot PS missed
    # — it's a ghost left behind by some device that used to be plugged in
    # under a hub/port numbering that no longer applies (e.g. a stale
    # "Port_#0018.Hub_#0001" from long-removed hardware). Previously these
    # ghost canonical keys were added and competed on equal footing with the
    # real empty slot during the "Option C" trim below, and could win the
    # slice — displacing the genuine empty port (e.g. "USB Port 1") with a
    # ghost ("USB Port 18") in the DB/dashboard.
    #
    # Fix: once PS has spoken, only admit NON-canonical registry entries
    # (which bypass the cap entirely as informational/legacy rows). Canonical
    # ghost keys are dropped. Registry is only trusted for canonical slots
    # when PS found nothing at all (ps_slot_count == 0), i.e. its usual role
    # as a fallback layer.
    reg_ports = _discover_ports_via_registry(seen_keys)
    if ps_slot_count > 0:
        import re as _re_l3
        _CANON_L3 = _re_l3.compile(r'Port_#\d+\.Hub_#\d+', _re_l3.IGNORECASE)
        dropped = [p for p in reg_ports if _CANON_L3.search(p["port_key"])]
        if dropped:
            logger.info(f"  [Registry supplement] dropped {len(dropped)} ghost "
                        f"canonical port key(s) not reported by PS topology "
                        f"(PS is authoritative once it succeeds): "
                        f"{[p['port_key'] for p in dropped]}")
        reg_ports = [p for p in reg_ports if not _CANON_L3.search(p["port_key"])]
    if reg_ports:
        logger.info(f"  [Registry supplement] added {len(reg_ports)} historically-known port(s)")
    for p in reg_ports:
        pk = p["port_key"]
        p["occupied"] = pk in occupied_by_port
        p["devices"]  = occupied_by_port.get(pk, [])
        all_ports.append(p)

    # ── Safety net: ensure every currently-occupied port has a row ──────────
    for port_key, devices in occupied_by_port.items():
        if port_key not in seen_keys:
            all_ports.append({
                "port_key":    port_key,
                "hub_name":    "Unknown Hub",
                "port_number": 0,
                "occupied":    True,
                "devices":     devices,
            })

    # ── Option C: cap total port count to the PowerShell-authoritative total ─
    # If PowerShell (Layer 1) returned a non-zero slot count, it is the most
    # reliable source of truth for how many physical external slots exist.
    #
    # IMPORTANT: The WMI safety-net loop above adds non-canonical occupied
    # entries for internal USB devices (root hubs, fingerprint readers, etc.)
    # whose port keys do NOT match the Port_#NNN.Hub_#NNN format that Layer 1
    # uses. These must NOT be counted against the PS budget — on this machine
    # they inflate occupied_count to 14 while ps_slot_count is only 5, which
    # previously drove empty_budget to 0 and wiped all empty port rows from
    # the DB (causing "No port settings to apply").
    #
    # Fix: split all_ports into canonical (Port_#NNN.Hub_#NNN format) and
    # non-canonical groups. Only canonical ports are counted and trimmed.
    # Non-canonical ports are always preserved and bypass the cap entirely.
    if ps_slot_count > 0:
        import re as _re_optc
        _CANONICAL_RE = _re_optc.compile(r'Port_#\d+\.Hub_#\d+', _re_optc.IGNORECASE)

        canonical_occupied  = [p for p in all_ports if     p["occupied"] and _CANONICAL_RE.search(p["port_key"])]
        canonical_empty     = [p for p in all_ports if not p["occupied"] and _CANONICAL_RE.search(p["port_key"])]
        non_canonical       = [p for p in all_ports if not _CANONICAL_RE.search(p["port_key"])]

        # How many empty canonical slots are allowed: PS total minus the
        # number of canonical slots already occupied by real external devices.
        empty_budget = max(0, ps_slot_count - len(canonical_occupied))

        if len(canonical_empty) > empty_budget:
            # Sort by the actual Windows port number before trimming. The
            # previous behavior sliced in whatever order ports happened to
            # be discovered this cycle, which is NOT stable — a port that
            # was JUST freed (e.g. a mouse unplugged from Port_#0001) could
            # lose out to a leftover/ghost high-numbered key (Port_#0018)
            # purely because of discovery order that cycle, making the
            # just-emptied port vanish from the dashboard while an
            # unrelated stale slot took its place. Sorting numerically
            # means lower-numbered ports are always preferred when the
            # budget forces a cut, which is also far more likely to match
            # the ports a person actually plugs external devices into.
            def _port_sort_key(p):
                m = re.search(r'Port_#(\d+)', p["port_key"], re.IGNORECASE)
                return int(m.group(1)) if m else float('inf')
            canonical_empty = sorted(canonical_empty, key=_port_sort_key)

            logger.info(
                f"  [Option C] Trimming canonical empty port list from "
                f"{len(canonical_empty)} to {empty_budget} "
                f"(PS authoritative slot count: {ps_slot_count}, "
                f"canonical occupied: {len(canonical_occupied)}, "
                f"non-canonical preserved: {len(non_canonical)})"
            )
            canonical_empty = canonical_empty[:empty_budget]

        all_ports = canonical_occupied + canonical_empty + non_canonical

    logger.info(f"list_all_physical_ports: {len(all_ports)} total port slot(s) found "
                f"({sum(1 for p in all_ports if p['occupied'])} occupied, "
                f"{sum(1 for p in all_ports if not p['occupied'])} empty)")
    return all_ports


# ── THE single enforcement mechanism: SetupAPI ConfigFlags ─────────────────

def _dedupe_root_ids(device_ids: list) -> list:
    """Strip &MI_xx child suffixes and de-duplicate, preserving order.
    Shared by set_port_enabled's fast-path and its fresh-discovery fallback."""
    root_ids = []
    seen = set()
    for did in device_ids:
        if did:
            root = _strip_mi_suffix(did)
            if root not in seen:
                seen.add(root)
                root_ids.append(root)
    return root_ids


class _StaleDeviceId(Exception):
    """Internal signal: a fast-path device_id from the DB no longer resolves
    on the live system, so the caller should fall back to fresh discovery."""
    pass


def _strip_mi_suffix(device_id: str) -> str:
    """
    Finds the root USB parent device ID (USB\\VID_xxx) for any given device ID.
    This is necessary because HID children (HID\\) or Storage children (USBSTOR\\)
    cannot be reliably disabled or forcefully removed; we must target their parent.
    """
    if device_id.upper().startswith("USB\\") and "&MI_" not in device_id.upper():
        return device_id

    # We DO NOT want to find the USB parent for Storage drives!
    # /remove-device on the parent fails if a disk is mounted.
    # But /remove-device directly on the USBSTOR\\Disk child works instantly!
    if device_id.upper().startswith("USBSTOR\\"):
        return device_id

    import ctypes
    try:
        cfgmgr32 = ctypes.windll.cfgmgr32
        devinst = ctypes.c_ulong()
        # 0 = NORMAL, 1 = PHANTOM (for disconnected devices)
        res = cfgmgr32.CM_Locate_DevNodeW(ctypes.byref(devinst), ctypes.c_wchar_p(device_id), 0)
        if res != 0:
            res = cfgmgr32.CM_Locate_DevNodeW(ctypes.byref(devinst), ctypes.c_wchar_p(device_id), 1)

        if res == 0:
            current_inst = devinst.value
            for _ in range(3): # Trace up to 3 levels max to prevent infinite loop
                parent_devinst = ctypes.c_ulong()
                if cfgmgr32.CM_Get_Parent(ctypes.byref(parent_devinst), current_inst, 0) == 0:
                    buf = ctypes.create_unicode_buffer(200)
                    if cfgmgr32.CM_Get_Device_IDW(parent_devinst.value, buf, len(buf), 0) == 0:
                        parent_id = buf.value
                        if parent_id.upper().startswith("USB\\") and "&MI_" not in parent_id.upper():
                            logger.debug(f"  USB parent found via cfgmgr32: {device_id} -> {parent_id}")
                            return parent_id
                        current_inst = parent_devinst.value
                    else:
                        break
                else:
                    break
    except Exception as e:
        logger.debug(f"  cfgmgr32 parent lookup failed: {e}")

    import re
    rewritten = re.sub(r'&MI_[0-9A-Fa-f]+', '', device_id, flags=re.IGNORECASE)
    logger.debug(f"  MI_xx regex fallback: {device_id} -> {rewritten}")
    return rewritten


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize",    _wintypes.DWORD),
        ("ClassGuid", ctypes.c_byte * 16),
        ("DevInst",   _wintypes.DWORD),
        ("Reserved",  ctypes.c_void_p),
    ]

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

_setupapi_initialized = False

def _init_setupapi_bindings():
    global _setupapi_initialized
    if _setupapi_initialized or not IS_WINDOWS:
        return
    setupapi = ctypes.windll.setupapi
    setupapi.SetupDiGetClassDevsW.restype  = ctypes.c_void_p
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong]
    setupapi.SetupDiEnumDeviceInfo.restype  = ctypes.c_bool
    setupapi.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
    setupapi.SetupDiGetDeviceInstanceIdW.restype  = ctypes.c_bool
    setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.POINTER(_wintypes.DWORD)]
    setupapi.SetupDiGetDeviceRegistryPropertyW.restype  = ctypes.c_bool
    setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_wintypes.DWORD)]
    setupapi.SetupDiSetDeviceRegistryPropertyW.restype  = ctypes.c_bool
    setupapi.SetupDiSetDeviceRegistryPropertyW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong]
    setupapi.SetupDiDestroyDeviceInfoList.restype  = ctypes.c_bool
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    _setupapi_initialized = True

def _setupapi_get_class_guid(effective_id: str):
    setupapi = ctypes.windll.setupapi
    INVALID_HANDLE = ctypes.c_void_p(-1).value
    hdevinfo_all = setupapi.SetupDiGetClassDevsW(None, None, None, DIGCF_ALLCLASSES | DIGCF_PRESENT)
    if not hdevinfo_all or hdevinfo_all == INVALID_HANDLE:
        return None
        
    devinfo = SP_DEVINFO_DATA()
    devinfo.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
    target_lower = effective_id.lower()
    found_guid = None
    i = 0
    
    try:
        while setupapi.SetupDiEnumDeviceInfo(hdevinfo_all, i, ctypes.byref(devinfo)):
            i += 1
            buf = ctypes.create_unicode_buffer(512)
            req_size = _wintypes.DWORD(0)
            if not setupapi.SetupDiGetDeviceInstanceIdW(hdevinfo_all, ctypes.byref(devinfo), buf, ctypes.sizeof(buf) // 2, ctypes.byref(req_size)):
                continue
            if buf.value.lower() == target_lower:
                found_guid = bytes(devinfo.ClassGuid)
                break
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdevinfo_all)
        
    return found_guid

def _setupapi_apply_config_flags(effective_id: str, found_guid: bytes, disabled: bool) -> bool:
    setupapi = ctypes.windll.setupapi
    INVALID_HANDLE = ctypes.c_void_p(-1).value
    guid_struct = GUID.from_buffer_copy(found_guid)
    
    hdevinfo_class = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid_struct), None, None, DIGCF_PRESENT)
    if not hdevinfo_class or hdevinfo_class == INVALID_HANDLE:
        return _winreg_set_config_flags(effective_id, disabled)
        
    devinfo = SP_DEVINFO_DATA()
    devinfo.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
    target_lower = effective_id.lower()
    found = False
    j = 0
    
    try:
        while setupapi.SetupDiEnumDeviceInfo(hdevinfo_class, j, ctypes.byref(devinfo)):
            j += 1
            buf = ctypes.create_unicode_buffer(512)
            req_size = _wintypes.DWORD(0)
            if not setupapi.SetupDiGetDeviceInstanceIdW(hdevinfo_class, ctypes.byref(devinfo), buf, ctypes.sizeof(buf) // 2, ctypes.byref(req_size)):
                continue
            if buf.value.lower() != target_lower:
                continue

            cur_flags = _wintypes.DWORD(0)
            req_size2 = _wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceRegistryPropertyW(
                hdevinfo_class, ctypes.byref(devinfo), SPDRP_CONFIGFLAGS,
                None, ctypes.byref(cur_flags), ctypes.sizeof(cur_flags), ctypes.byref(req_size2)
            )
            
            new_val = (cur_flags.value | CONFIGFLAG_DISABLED) if disabled else (cur_flags.value & ~CONFIGFLAG_DISABLED)
            
            if new_val == cur_flags.value:
                # Already at the desired state. This is NOT a failure — it
                # means the ConfigFlags bit already reflects what we wanted,
                # most commonly because the live pnputil enable/disable call
                # that just ran (CM_Enable_DevNode/CM_Disable_DevNode) already
                # wrote this exact same registry bit as part of its own
                # persistence mechanism (that's how a Device-Manager-style
                # disable survives reboot without any extra write). Treating
                # "already correct" as a failure here made this function
                # report FAILED on essentially every successful toggle,
                # logging a misleading "ConfigFlags persistence not written"
                # warning even though persistence was fine the whole time.
                # Report success — there's genuinely nothing left to do.
                return True
                
            new_flags = _wintypes.DWORD(new_val)
            ok = setupapi.SetupDiSetDeviceRegistryPropertyW(
                hdevinfo_class, ctypes.byref(devinfo), SPDRP_CONFIGFLAGS, ctypes.byref(new_flags), ctypes.sizeof(new_flags)
            )
            
            if ok:
                logger.info(f"  [ConfigFlags] {effective_id[:60]} -> {'disabled' if disabled else 'enabled'}")
                found = True
            else:
                found = _winreg_set_config_flags(effective_id, disabled)
            break
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdevinfo_class)
        
    return found

def _setupapi_set_config_flags(device_id: str, disabled: bool) -> bool:
    if not IS_WINDOWS:
        return False
        
    effective_id = _strip_mi_suffix(device_id)
    try:
        _init_setupapi_bindings()
        
        found_guid = _setupapi_get_class_guid(effective_id)
        if found_guid is None:
            # Device not present or removed via pnputil, fallback to registry
            return _winreg_set_config_flags(effective_id, disabled)
            
        if found_guid == b'\x00' * 16:
            return _winreg_set_config_flags(effective_id, disabled)
            
        return _setupapi_apply_config_flags(effective_id, found_guid, disabled)
    except Exception as e:
        # An exception here (as opposed to a missing/zero class GUID, both
        # handled above) still leaves us with a device that needs its
        # ConfigFlags persisted. Fall back to the direct winreg write —
        # same effect, and it's what the missing/zero-GUID branches above
        # already do — instead of giving up and leaving the live toggle
        # as the only record of the disable/enable decision.
        logger.warning(f"  _setupapi_set_config_flags error for {effective_id[:70]}: {e} "
                        f"— falling back to winreg ConfigFlags write")
        return _winreg_set_config_flags(effective_id, disabled)


def _winreg_set_config_flags(device_id: str, disabled: bool) -> bool:
    """
    Direct winreg fallback for ConfigFlags write when SetupAPI refuses
    (e.g. zero ClassGuid, class-less devices, or ghost nodes).

    Writes HKLM\\SYSTEM\\CurrentControlSet\\Enum\\<device_id>\\ConfigFlags
    directly. Same effect as SetupAPI — takes effect on reboot/replug.

    Works for:
      - HID\\VID_xxx\\... interface nodes
      - USB\\VID_xxx\\... nodes with no driver class yet installed
      - Any device_id whose registry key exists under Enum\\
    """
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        key_path = r"SYSTEM\CurrentControlSet\Enum" + "\\" + device_id
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, key_path,
                0, winreg.KEY_READ | winreg.KEY_WRITE
            )
        except FileNotFoundError:
            logger.warning(f"  winreg fallback: key not found: {key_path[:80]}")
            return False
        except PermissionError:
            logger.warning(f"  winreg fallback: permission denied: {key_path[:80]}")
            return False

        try:
            cur_val, _ = winreg.QueryValueEx(key, "ConfigFlags")
        except FileNotFoundError:
            cur_val = 0

        new_val = (cur_val | CONFIGFLAG_DISABLED) if disabled else (cur_val & ~CONFIGFLAG_DISABLED)
        
        if new_val == cur_val:
            # Already at the desired state — success, nothing left to write.
            # Same reasoning as the SetupAPI path above: don't report this
            # as a failure just because there was no delta to persist.
            winreg.CloseKey(key)
            return True
            
        winreg.SetValueEx(key, "ConfigFlags", 0, winreg.REG_DWORD, new_val)
        winreg.CloseKey(key)

        action = "disabled" if disabled else "enabled"
        logger.info(f"  [winreg fallback] {device_id[:60]} -> {action} (0x{cur_val:X}->0x{new_val:X})")
        return True
    except Exception as e:
        logger.error(f"  _winreg_set_config_flags error for {device_id[:60]}: {e}")
        return False


def _expand_fallback_ids(fallback_device_ids: list, port_key: str) -> list:
    r"""
    If the only IDs we have in the fallback list are HID\... IDs, we need to find 
    the parent USB node to successfully write ConfigFlags, because writing ConfigFlags
    to an HID interface won't physically disable the USB port.
    """
    dev_ids = set(fallback_device_ids)
    
    # Check if we already have a USB parent
    for dev_id in dev_ids:
        if dev_id.startswith("USB\\"):
            return list(dev_ids)
            
    # If not, extract VID/PID from the port_key
    import winreg
    vid_pid = ""
    if "VID_" in port_key and "PID_" in port_key:
        try:
            parts = port_key.split("::")
            if len(parts) > 1:
                vid_pid = parts[-1]
        except Exception:
            pass
            
    if vid_pid:
        try:
            # Search HKLM\SYSTEM\CurrentControlSet\Enum\USB for this VID_PID
            usb_key_path = r"SYSTEM\CurrentControlSet\Enum\USB\\" + vid_pid
            usb_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, usb_key_path, 0, winreg.KEY_READ)
            idx = 0
            while True:
                try:
                    instance_id = winreg.EnumKey(usb_key, idx)
                    parent_id = f"USB\\{vid_pid}\\{instance_id}"
                    dev_ids.add(parent_id)
                    idx += 1
                except OSError:
                    break
            winreg.CloseKey(usb_key)
        except FileNotFoundError:
            pass
            
    return list(dev_ids)


# ── Port enable/disable — single registry method for ALL device types ─────

def _is_device_removable(device_id: str) -> bool:
    """
    Returns True if the device is physically removable (external USB).
    Returns False if it is non-removable (built-in/internal).

    Uses the Windows registry Capabilities value:
      Bit 0x10 (CM_DEVCAP_REMOVABLE) or 0x04 — set means removable.
    If the key is unreadable, defaults to True (fail-open = allow).
    """
    if not IS_WINDOWS:
        return True
        
    # The Capabilities bits for removability (0x10, 0x04) are often only set on
    # the parent USB node. HID child interfaces (HID\...) and USB composite
    # child interfaces (USB\...&MI_...) often have Capabilities like 0xA0 or 0x80
    # which do not include the removable bits, even for external dongles.
    # Therefore, only enforce the capabilities check on root USB\ nodes.
    device_upper = device_id.upper()
    if not device_upper.startswith("USB\\") or "&MI_" in device_upper:
        return True
        
    try:
        import winreg
        key_path = r"SYSTEM\CurrentControlSet\Enum" + "\\" + device_id
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            caps, _ = winreg.QueryValueEx(key, "Capabilities")
            is_removable = bool(caps & 0x14)   # 0x10 | 0x04 = removable
            if not is_removable:
                # Desktop PC fallback: Rear USB ports often lack the removable bit
                # even though they are external facing. If it is a generic USB device 
                # (and not an internal system hub like USBHUB3), treat it as removable.
                service, _ = winreg.QueryValueEx(key, "Service")
                if service and service.lower() in ("usbhub", "usbhub3", "ucx01000"):
                    return False
                return True
            return True
    except Exception:
        return True   # can't read → assume external, fail-open


def _force_volume_rescan() -> bool:
    """
    Forces Windows' Mount Manager/Volume Manager to re-examine every disk
    and reassign drive letters, via the PowerShell Storage module cmdlet
    Update-HostStorageCache (Windows 8/Server 2012+).

    _pnp_rescan() (`pnputil /scan-devices`) only rescans PnP *buses* for
    devnodes — it re-arms the disk hardware but does not touch the volume
    layer above it. When a storage devnode is disabled then re-enabled via
    CM_Disable_DevNode/CM_Enable_DevNode (no real unplug/replug), the disk
    comes back and Device Manager shows it enabled, but the partition on it
    is not guaranteed to get remounted / reassigned a drive letter — a real
    physical replug always triggers that step, a bare devnode enable does
    not always. This is the "Device Manager says enabled, Explorer shows no
    drive letter / doesn't appear in This PC" symptom. Run this AFTER
    _pnp_rescan(), not instead of it — the bus rescan re-arms the hardware
    first, then this forces the volume layer to catch up.
    """
    if not IS_WINDOWS:
        return False
    import subprocess
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Update-HostStorageCache"],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=15
        )
        ok = res.returncode == 0
        if ok:
            logger.info("  [RESCAN] Update-HostStorageCache completed — "
                        "drive letters should now reflect the enabled device")
        else:
            err = (res.stderr or b"").decode(errors="replace").strip()
            logger.warning(f"  [RESCAN] Update-HostStorageCache rc={res.returncode} — {err!r}")
        return ok
    except Exception as e:
        logger.warning(f"  [RESCAN] Update-HostStorageCache raised: {e}")
        return False


def _pnp_rescan() -> bool:
    """
    Triggers a full PnP bus rescan (`pnputil /scan-devices`), which makes
    Windows re-enumerate any devnode that was forcibly removed (see
    _force_remove_devnode) without needing a physical unplug/replug.

    This exists specifically for the ENABLE side of the admin toggle: if a
    storage device was previously blocked via forced removal (because the
    live disable toggle got stuck, e.g. pnputil rc=3010), the devnode is
    gone from Windows' device tree entirely. A later 'enable' toggle has
    nothing to enable — pnputil/SetupAPI both see no matching device — so
    without this rescan step, enabling the same drive after a forced
    block would silently stay stuck until someone unplugs and replugs it.
    """
    if not IS_WINDOWS:
        return False
    import subprocess
    try:
        res = subprocess.run(
            ["pnputil", "/scan-devices"],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=10
        )
        ok = res.returncode == 0
        if ok:
            logger.info("  [RESCAN] pnputil /scan-devices completed — "
                        "previously removed devices should re-enumerate")
        else:
            logger.warning(f"  [RESCAN] pnputil /scan-devices rc={res.returncode}")
        return ok
    except Exception as e:
        logger.warning(f"  [RESCAN] pnputil /scan-devices raised: {e}")
        return False


def set_port_enabled(port_key: str, enabled: bool, fallback_device_ids: list = None) -> bool:
    """
    Enable or disable EVERY device currently occupying a physical USB port
    via a live pnputil enable/disable-device toggle, applied instantly.
    A ConfigFlags registry write is also made for bookkeeping, but there is
    no reboot/replug fallback anymore — if the live toggle doesn't take,
    the call is reported as failed rather than deferred to a later reboot.

    SAFETY GUARANTEE: never touches non-removable (internal/built-in) devices.
    If a port resolves to a non-removable device (e.g. internal laptop keyboard),
    the call is silently skipped — no registry write is made.
    """
    if not IS_WINDOWS:
        logger.info(f"[SIM] Port {port_key} {'enabled' if enabled else 'disabled'}")
        return True

    action = "enabled" if enabled else "disabled"

    dev_ids = []
    if fallback_device_ids:
        # FAST PATH: the DB already knows which device_id(s) occupy this
        # port from the last scan sync — use them directly instead of
        # doing a fresh WMI Win32_PnPEntity discovery here. That discovery
        # is the single biggest contributor to per-toggle latency (roughly
        # 1-2s whenever the 5s PnP cache has just been invalidated by a
        # previous toggle), and it was being done unconditionally even
        # when we already had everything we needed from the DB.
        dev_ids = _expand_fallback_ids([d for d in fallback_device_ids if d], port_key)

    if not dev_ids:
        devices = _find_devices_at_port(port_key)
        if not devices and enabled:
            # Nothing found live — if we're trying to ENABLE, this may be a
            # device that got force-removed by a previous (stuck) disable
            # attempt. Try a rescan and check once more before accepting
            # "nothing here" as the final answer.
            if _pnp_rescan():
                devices = _find_devices_at_port(port_key)
        if devices:
            dev_ids = [getattr(d, "DeviceID", "") or "" for d in devices if getattr(d, "DeviceID", "")]
        else:
            # Nothing plugged in right now — log and return success.
            # The DB stores the intent; when a device is later plugged in,
            # the agent's sync will read port_enabled from DB and apply it.
            logger.info(f"set_port_enabled: no devices at port {port_key} — "
                        f"intent saved in DB (applies when device is plugged in)")
            return True

    # Always target the root parent device (strip &MI_xx). Windows ignores ConfigFlags
    # on child interfaces, and pnputil cannot restart them reliably.
    dev_ids = _dedupe_root_ids(dev_ids)

    # ── Storage devices are handled EXCLUSIVELY by set_storage_enabled() ──────
    # _apply_all_device_settings() calls set_port_enabled() and then
    # set_storage_enabled() back-to-back for every port. For a pure storage
    # port (e.g. a flash drive), both functions resolve to the SAME USBSTOR
    # device id, and each fires its own independent pnputil enable/disable
    # call against it. Windows' CM_Disable_DevNode/CM_Enable_DevNode does not
    # tolerate being told to flip the identical devnode twice in quick
    # succession — the second call frequently comes back refused (rc=3010
    # "reboot is needed") or silently no-ops. That is exactly the
    # intermittent re-enable failure being seen: set_port_enabled's toggle
    # succeeds, then set_storage_enabled's toggle on the same id fails a
    # split-second later. Excluding USBSTOR ids here makes set_storage_enabled
    # the single source of truth for storage devnodes, so each physical
    # device gets exactly one live toggle per apply cycle.
    non_storage_ids = [d for d in dev_ids if not _is_storage_device_id(d)]
    # UASP-attached drives (Seagate/WD/many SanDisk SSDs) only ever appear in
    # a port's device_ids as their USB bridge controller id (the SCSI\Disk
    # child isn't USB\/HID\ prefixed, so it never lands in device_ids to
    # begin with) — and that bridge controller's OWN PNPClass is "USB", not
    # a storage class, so _is_storage_device_id() alone can't recognize it.
    # Resolve it via the actual storage device(s) discovered at this port
    # instead: if a storage child here resolves (via _strip_mi_suffix) up to
    # one of our dev_ids, that id is the bridge controller for a UASP drive
    # and must be excluded here too, so set_storage_enabled remains the sole
    # owner of the toggle and the volume-remount rescan on enable.
    storage_parent_ids = {
        _strip_mi_suffix(getattr(d, "DeviceID", "") or "").upper()
        for d in _find_storage_devices_at_port(port_key)
    }
    non_storage_ids = [d for d in non_storage_ids if d.upper() not in storage_parent_ids]
    if len(non_storage_ids) != len(dev_ids):
        logger.debug(f"set_port_enabled: excluding storage device id(s) at port {port_key} "
                     f"— handled exclusively by set_storage_enabled()")
    dev_ids = non_storage_ids
    if not dev_ids:
        logger.info(f"set_port_enabled: port {port_key} contains only storage device(s) "
                    f"— nothing to do here (set_storage_enabled owns this port)")
        return True

    # ── SAFETY: never touch non-removable (internal/built-in) devices ────────
    # Check every device at this port. If ANY is non-removable, skip the
    # entire port — the built-in laptop keyboard/touchpad must never be
    # touched by ConfigFlags writes, even if a stale DB entry triggered this.
    non_removable = [did for did in dev_ids if did and not _is_device_removable(did)]
    if non_removable:
        logger.warning(
            f"set_port_enabled: SKIPPING port {port_key} — contains non-removable "
            f"(internal) device(s): {non_removable}. Internal devices are never managed."
        )
        return False

    import subprocess

    def _toggle_dev_ids(ids):
        ok = False
        for dev_id in ids:
            # PRIMARY: live toggle via CM_Disable_DevNode/CM_Enable_DevNode
            # (pnputil disable-device / enable-device). This is the same
            # mechanism proven reliable for storage devices elsewhere in this
            # codebase (_try_live_disable_devnode). It takes effect instantly
            # and, unlike a bare ConfigFlags registry write, is respected by
            # Windows through a device re-enumeration/restart — a raw registry
            # write can get silently reset back to its driver-default during
            # that process, which is what was causing re-enable to fail while
            # disable "worked" by coincidence.
            live_ok = False
            try:
                action_flag = "/disable-device" if not enabled else "/enable-device"
                logger.info(f"  [{action.upper()}] Triggering live {action_flag} for {dev_id[:70]}")
                res = subprocess.run(
                    ["pnputil", action_flag, dev_id],
                    capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=5
                )
                out = (res.stdout or b"").decode(errors="replace").strip()
                err = (res.stderr or b"").decode(errors="replace").strip()
                out_lower = out.lower()

                # pnputil can print "Failed to disable/enable device..." while
                # STILL returning exit code 0 — the exit code alone is not
                # trustworthy. Check the actual message text first. This was
                # silently reported as a successful toggle before, even
                # though the device stayed exactly as it was — most visibly
                # with "Cannot disable critical system device", which Windows
                # raises for a device it treats as the machine's only
                # keyboard/HID input path (no built-in keyboard to fall back
                # on, unlike a laptop).
                pnputil_says_failed = (
                    "failed to disable" in out_lower or "failed to enable" in out_lower
                    or "cannot disable" in out_lower or "cannot enable" in out_lower
                )
                # rc=3010 (ERROR_SUCCESS_REBOOT_REQUIRED) means pnputil queued
                # the change but Windows refused to apply it live this time —
                # commonly seen when the same devnode has already been
                # toggled a couple of times this session. Treat it exactly
                # like pnputil_says_failed: try the SetupAPI devnode retry
                # below instead of reporting it as an unhandled success/failure.
                pnputil_reboot_required = (
                    res.returncode == 3010 or "reboot is needed" in out_lower
                    or "reboot required" in out_lower
                )

                if "already enabled" in out_lower or "already disabled" in out_lower:
                    # The device is already in the desired state — this is NOT
                    # a failure. Treating it as one (as before) caused the code
                    # to fall through to the destructive remove-device+scan
                    # fallback below on every single poll cycle, forcing a
                    # needless re-enumeration of the device each time — which
                    # is what was making it visibly blink/reset every ~15-20s
                    # even though nothing needed to change.
                    live_ok = True
                    logger.info(f"  [{action.upper()}] pnputil {action_flag} — already {action}, nothing to do "
                                f"for {dev_id[:70]} — {out!r}")
                elif "no such device" in out_lower or "not found" in out_lower or "no matching devices" in out_lower:
                    # Stale ID — the fast-path device_id from the DB no longer
                    # resolves. Let the caller know so it can fall back to a
                    # fresh discovery instead of silently reporting failure.
                    logger.warning(f"  [{action.upper()}] pnputil {action_flag} — {dev_id[:70]} not found "
                                    f"(stale device_id) — {out!r}")
                    raise _StaleDeviceId()
                elif pnputil_says_failed or pnputil_reboot_required:
                    reason = "reported rc=3010 (reboot required)" if pnputil_reboot_required \
                        else "reported rc=0 but the message says it failed"
                    logger.warning(f"  [{action.upper()}] pnputil {action_flag} {reason} "
                                    f"for {dev_id[:70]} — {out!r} — trying SetupAPI devnode "
                                    f"toggle as a second live attempt")
                    live_ok = _try_live_disable_devnode(dev_id, disabled=not enabled)
                    if not live_ok and not enabled:
                        # Still refusing to disable live (this is the "blocked
                        # in DB but still mounted in Explorer" scenario) — as a
                        # last resort, force the devnode out of the tree via
                        # DIF_REMOVE. Windows re-enumerates it fresh next time
                        # it's plugged in or rescanned, so the drive stops
                        # being accessible right now instead of only after a
                        # reboot the admin never asked the employee to do.
                        live_ok = _force_remove_devnode(dev_id)
                        if live_ok:
                            logger.info(f"  [{action.upper()}] Forced device removal succeeded for "
                                        f"{dev_id[:70]} — drive is no longer accessible")
                    if not live_ok:
                        logger.warning(f"  [{action.upper()}] SetupAPI devnode toggle and forced removal "
                                        f"both failed for {dev_id[:70]} — Windows is refusing to {action} "
                                        f"this device live (likely because it's the machine's only "
                                        f"keyboard/HID input; Windows blocks that to avoid a total input "
                                        f"lockout — this guard does not apply to storage devices)")
                elif res.returncode == 0:
                    live_ok = True
                    logger.info(f"  [{action.upper()}] pnputil {action_flag} OK for {dev_id[:70]} — {out!r}")
                else:
                    logger.warning(f"  [{action.upper()}] pnputil {action_flag} rc={res.returncode} "
                                    f"for {dev_id[:70]} — stdout={out!r} stderr={err!r}")
            except _StaleDeviceId:
                raise
            except Exception as e:
                logger.warning(f"pnputil {action_flag} raised for {dev_id[:70]}: {e}")

            # SECONDARY: also write ConfigFlags so the intended state survives
            # a reboot/replug even if the live toggle above already applied it.
            # Best-effort — its failure doesn't override a successful live toggle.
            cfg_ok = _setupapi_set_config_flags(dev_id, disabled=not enabled)
            if not cfg_ok:
                logger.warning(f"  _setupapi_set_config_flags FAILED for {dev_id[:70]} "
                                f"(disabled={not enabled}) — ConfigFlags persistence not written "
                                f"(live toggle {'succeeded' if live_ok else 'also failed'})")

            ok = ok or live_ok or cfg_ok

            if not live_ok:
                # Live toggle didn't take — ConfigFlags was still written above
                # (best-effort), but we no longer force a restart-device or a
                # destructive remove+scan re-enumeration to paper over it. If
                # the device doesn't honor the live toggle, that's reported as
                # a failure below rather than silently deferred to a reboot.
                logger.warning(f"  [{action.upper()}] Live toggle did not take effect for {dev_id[:70]} "
                                f"(ConfigFlags write {'succeeded' if cfg_ok else 'also failed'})")
        return ok

    try:
        any_ok = _toggle_dev_ids(dev_ids)
    except _StaleDeviceId:
        # The fast-path DB device_ids didn't resolve on the live system —
        # port_key likely shifted between scans. Fall back to a fresh WMI
        # discovery exactly like the old code path always did, just now
        # only when actually needed instead of on every single call.
        logger.info(f"set_port_enabled: fast-path device_id(s) stale for port {port_key} "
                    f"— falling back to fresh discovery")
        devices = _find_devices_at_port(port_key)
        if not devices and enabled and _pnp_rescan():
            devices = _find_devices_at_port(port_key)
        fresh_ids = [getattr(d, "DeviceID", "") or "" for d in devices if getattr(d, "DeviceID", "")]
        fresh_ids = _dedupe_root_ids(fresh_ids)
        any_ok = _toggle_dev_ids(fresh_ids) if fresh_ids else False

    logger.info(f"Port {port_key} {action} via registry and PnP manager ({'OK' if any_ok else 'FAILED'}) "
                f"— applied instantly")
    # NOTE: invalidate_pnp_cache() used to be called here on every single
    # port toggle. When _apply_all_device_settings() runs several ports on
    # concurrent threads (as it does), one thread finishing would invalidate
    # the shared 5s WMI cache while sibling threads were still mid-toggle,
    # forcing each of them into a full fresh Win32_PnPEntity() system-wide
    # re-enumeration instead of reusing one shared snapshot for the whole
    # batch. That was the main source of the multi-second stalls seen when
    # applying settings for several ports at once. The caller now invalidates
    # once after the entire batch finishes instead - see agent_core.py's
    # _apply_all_device_settings().
    return any_ok



def get_port_status(port_key: str) -> bool:
    """
    Returns the hardware-level enabled state for a physical port by reading
    the ConfigFlags registry value for every device currently occupying that
    port. A port is considered ENABLED (True) only if NONE of its devices
    have the CONFIGFLAG_DISABLED bit set.

    The authoritative source for the admin-intended state is still the
    database (port_enabled column in usb_ports). Use this function only
    when you need to verify that the hardware state actually matches.

    Returns True (enabled) if no devices are found at the port (nothing to
    check) or if the registry key is unreadable (fail-open).
    """
    if not IS_WINDOWS:
        return True
    devices = _find_devices_at_port(port_key)
    if not devices:
        return True
    try:
        import winreg
        for dev in devices:
            dev_id = (getattr(dev, "DeviceID", "") or "").strip()
            if not dev_id:
                continue
            key_path = r"SYSTEM\CurrentControlSet\Enum" + "\\" + dev_id
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path,
                                     0, winreg.KEY_READ)
                try:
                    flags, _ = winreg.QueryValueEx(key, "ConfigFlags")
                    if flags & CONFIGFLAG_DISABLED:
                        return False
                except FileNotFoundError:
                    pass  # no ConfigFlags value means not disabled
                finally:
                    winreg.CloseKey(key)
            except (FileNotFoundError, PermissionError):
                pass  # key absent or unreadable — treat as enabled (fail-open)
    except Exception as e:
        logger.debug(f"get_port_status: registry read failed for {port_key}: {e}")
    return True


# ── File-transfer (storage-only) enable/disable ────────────────────────────
#
# Narrower sibling of set_port_enabled(): only targets the storage-class
# child PnP entity at a port, leaving keyboard/mouse sharing that port_key
# untouched. Uses the SAME single ConfigFlags mechanism.

def _find_storage_devices_at_port(port_key: str):
    """Storage-class device(s) at a physical port. DISCOVERY ONLY.

    Must match every PNPClass that agent_scanner._determine_device_type()
    labels as "Storage" on the dashboard (DiskDrive, CDROM, WPD, USBSTOR).
    Previously this only matched USBSTOR/DiskDrive, so CD/DVD drives and
    WPD-class devices (many SD/MTP-mode card readers, some external
    enclosures) were shown as "Blocked" in the dashboard but never actually
    found here - set_storage_enabled() then reported success anyway because
    "no matching devices" and "nothing to enforce" look identical from its
    point of view. Matching the same class list closes that gap.

    IMPORTANT: this scans _get_pnp_entities() directly instead of routing
    through _find_devices_at_port(), which restricts to USB\\/HID\\ prefixed
    device IDs. UASP-attached external hard disks/SSDs (Seagate, WD, many
    SanDisk SSDs — anything using the modern USB-Attached SCSI protocol
    instead of legacy bulk-only storage) enumerate as SCSI\\Disk&... device
    instances, not USB\\... ones. get_port_key() can still correctly resolve
    their physical port (it walks up the parent chain via CM_Get_Parent
    regardless of prefix), but the old USB\\/HID\\ filter dropped these
    devices before get_port_key() was ever consulted — so set_storage_enabled
    silently found "nothing to enforce" for every UASP drive, every cycle,
    and never toggled or rescanned the actual disk devnode. Only the USB
    bridge controller (toggled separately by set_port_enabled) was ever
    flipped, which blocks/restores the link but never re-triggers the
    volume-remount rescan tied to a successful storage enable.
    """
    storage = []
    fallback_disabled = []
    try:
        for dev in _get_pnp_entities():
            raw_id = getattr(dev, "DeviceID", "") or ""
            instance_id = raw_id.upper()
            pnp_class = (getattr(dev, "PNPClass", "") or "").upper()
            if not (instance_id.startswith("USBSTOR") or pnp_class in STORAGE_PNP_CLASSES):
                # Not classed as storage right now — but if it's a DISABLED
                # device sitting at this physical port, it MIGHT be the
                # parent/bridge node of a storage device whose own
                # USBSTOR/DiskDrive child devnode Windows already tore down
                # (Windows removes a disabled device's child devnodes from
                # the tree — see the matching note in agent_core.py). The
                # generic parent bridge/composite node is ALSO what a
                # disabled mouse/keyboard composite controller looks like,
                # though — its PNPClass is just as generically "USB", and
                # it has no live child to distinguish it either. Blindly
                # claiming every disabled device here as a storage fallback
                # (an earlier version of this fix did exactly that) meant a
                # disabled mouse got misclassified as "storage", excluded
                # from set_port_enabled's target list, and instead driven
                # by set_storage_enabled — which enforces the unrelated
                # Storage-Access toggle instead of Port-Access, so the
                # mouse's actual Port Access setting was never applied and
                # it silently stayed disabled.
                #
                # _was_storage_device() checks the registry for a genuine
                # historical USBSTOR child under this exact parent (Windows
                # keeps a torn-down child's Enum key even once its live
                # WMI devnode disappears), so only a device that ACTUALLY
                # hosted mass storage gets claimed here — a disabled mouse
                # correctly falls through and stays untouched by this fn.
                if (getattr(dev, "ConfigManagerErrorCode", 0) != 0
                        and get_port_key(dev) == port_key
                        and _was_storage_device(raw_id)):
                    fallback_disabled.append(dev)
                continue
            if get_port_key(dev) == port_key:
                storage.append(dev)
    except Exception as e:
        logger.error(f"WMI query failed while locating storage device(s) at port {port_key}: {e}")

    if not storage and fallback_disabled:
        logger.info(
            f"  [StorageDiscovery] no live USBSTOR/DiskDrive node at port "
            f"{port_key} — found {len(fallback_disabled)} disabled "
            f"bridge/parent node(s) with a known storage history instead; "
            f"using them as the re-enable target so the real storage "
            f"child devnode can re-enumerate"
        )
        return fallback_disabled
    return storage


def _was_storage_device(instance_id: str) -> bool:
    """True if `instance_id` (typically a currently-disabled parent/bridge
    devnode) has a USBSTOR child device registered underneath it in the
    registry — i.e. it genuinely used to host mass storage, as opposed to
    e.g. a disabled mouse/keyboard composite controller which looks
    identical from WMI alone once its live child devnode is torn down.

    Windows keeps a device's own Enum\\<InstanceId> registry key (and its
    ParentIdPrefix value) even after that device is disabled and its live
    child devnode is gone from the WMI tree — this mirrors
    _find_type_from_registry_children()'s HID lookup, but walks
    Enum\\USBSTOR instead to look for a storage child.
    """
    if not IS_WINDOWS or not instance_id:
        return False
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                rf"SYSTEM\CurrentControlSet\Enum\{instance_id}") as key:
            prefix, _ = winreg.QueryValueEx(key, "ParentIdPrefix")
    except Exception:
        prefix = None
    if not prefix:
        return False
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Enum\USBSTOR") as stor_key:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(stor_key, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(stor_key, sub) as inst_key:
                        j = 0
                        while True:
                            try:
                                inst_name = winreg.EnumKey(inst_key, j)
                            except OSError:
                                break
                            j += 1
                            if inst_name.startswith(prefix):
                                return True
                except Exception:
                    continue
    except Exception:
        pass
    return False


def _try_live_disable_devnode(dev_id: str, disabled: bool) -> bool:
    """
    Attempt a LIVE (instant, no reboot/replug) enable/disable via
    SetupDiCallClassInstaller(DIF_PROPERTYCHANGE), which maps to
    CM_Disable_DevNode/CM_Enable_DevNode under the hood.

    Storage devices generally do NOT carry the CR_NOT_DISABLEABLE
    capability flag that blocks this for keyboards/mice (Windows doesn't
    treat removable storage as boot-critical input), so this frequently
    succeeds instantly for storage. Returns False on any failure so the
    caller can fall back to the ConfigFlags/winreg method, which always
    requires reboot/replug but is far more universally accepted.
    """
    if not IS_WINDOWS:
        return False
    try:
        setupapi = ctypes.windll.setupapi

        setupapi.SetupDiGetClassDevsW.restype  = ctypes.c_void_p
        setupapi.SetupDiGetClassDevsW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong
        ]
        setupapi.SetupDiEnumDeviceInfo.restype  = ctypes.c_bool
        setupapi.SetupDiGetDeviceInstanceIdW.restype = ctypes.c_bool
        setupapi.SetupDiCallClassInstaller.restype = ctypes.c_bool
        setupapi.SetupDiSetClassInstallParamsW.restype = ctypes.c_bool
        setupapi.SetupDiDestroyDeviceInfoList.restype = ctypes.c_bool

        DIGCF_ALLCLASSES_PRESENT = DIGCF_ALLCLASSES | DIGCF_PRESENT
        DIF_PROPERTYCHANGE = 0x12
        DICS_ENABLE  = 1
        DICS_DISABLE = 2
        DICS_FLAG_GLOBAL = 1
        DICS_FLAG_CONFIGSPECIFIC = 2

        class SP_DEVINFO_DATA(ctypes.Structure):
            _fields_ = [
                ("cbSize",    ctypes.c_ulong),
                ("ClassGuid", ctypes.c_byte * 16),
                ("DevInst",   _wintypes.DWORD),
                ("Reserved",  ctypes.c_void_p),
            ]

        class SP_PROPCHANGE_PARAMS(ctypes.Structure):
            _fields_ = [
                ("ClassInstallHeader", ctypes.c_byte * 8),
                ("StateChange", ctypes.c_ulong),
                ("Scope",       ctypes.c_ulong),
                ("HwProfile",   ctypes.c_ulong),
            ]

        hdevinfo = setupapi.SetupDiGetClassDevsW(None, None, None, DIGCF_ALLCLASSES_PRESENT)
        if not hdevinfo or hdevinfo == ctypes.c_void_p(-1).value:
            return False

        # Attempt pnputil first on modern Windows 10/11
        import subprocess
        try:
            cmd = ["pnputil", "/disable-device" if disabled else "/enable-device", dev_id]
            # Every other subprocess call in this file has an explicit
            # timeout — this one didn't. An occasionally-slow/hung pnputil
            # call would block the calling apply-thread indefinitely, with
            # nothing to stop it short of the outer 20s thread-join timeout
            # in agent_core.py's _apply_all_device_settings — which doesn't
            # kill the thread, just stops waiting on it, leaving pnputil
            # running in the background and the whole "APPLYING SETTINGS"
            # cycle stalled for the full 20s. Matches the log's storage
            # single-port applies routinely taking ~15-20+ seconds when
            # this path was hit. Same 10s budget as the other pnputil-class
            # calls in this file (e.g. keyboard restart, port toggle).
            res = subprocess.run(cmd, capture_output=True,
                                  creationflags=subprocess.CREATE_NO_WINDOW,
                                  timeout=10)
            out = (res.stdout or b"").decode(errors="replace").strip().lower()
            # Same rule as set_port_enabled: pnputil can print "Failed to
            # disable/enable..." while still returning exit code 0, so the
            # exit code alone can't be trusted — check the message too.
            pnputil_refused = "failed to disable" in out or "failed to enable" in out \
                or "cannot disable" in out or "cannot enable" in out
            if res.returncode == 0 and not pnputil_refused:
                logger.info(f"  [LIVE] {dev_id[:60]} -> {'disabled' if disabled else 'enabled'} instantly via pnputil")
                return True
            if pnputil_refused:
                logger.info(f"  [LIVE] pnputil retry also refused for {dev_id[:60]} — "
                            f"trying the SetupAPI devnode call directly instead")
        except Exception:
            pass

        try:
            devinfo = SP_DEVINFO_DATA()
            devinfo.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            target_lower = dev_id.lower()
            i = 0
            found = False
            while setupapi.SetupDiEnumDeviceInfo(hdevinfo, i, ctypes.byref(devinfo)):
                i += 1
                buf = ctypes.create_unicode_buffer(512)
                req = _wintypes.DWORD(0)
                if not setupapi.SetupDiGetDeviceInstanceIdW(
                    hdevinfo, ctypes.byref(devinfo), buf, ctypes.sizeof(buf) // 2, ctypes.byref(req)
                ):
                    continue
                if buf.value.lower() != target_lower:
                    continue
                found = True
                break
            if not found:
                return False

            params = SP_PROPCHANGE_PARAMS()
            # ClassInstallHeader.cbSize (first 4 bytes) + InstallFunction (next 4)
            ctypes.memmove(ctypes.byref(params.ClassInstallHeader), ctypes.byref(ctypes.c_ulong(8)), 4)
            ctypes.memmove(ctypes.byref(params.ClassInstallHeader, 4), ctypes.byref(ctypes.c_ulong(DIF_PROPERTYCHANGE)), 4)
            params.StateChange = DICS_DISABLE if disabled else DICS_ENABLE
            params.Scope = DICS_FLAG_CONFIGSPECIFIC
            params.HwProfile = 0

            if not setupapi.SetupDiSetClassInstallParamsW(
                hdevinfo, ctypes.byref(devinfo), ctypes.byref(params), ctypes.sizeof(params)
            ):
                return False

            ok = setupapi.SetupDiCallClassInstaller(DIF_PROPERTYCHANGE, hdevinfo, ctypes.byref(devinfo))
            if ok:
                action = "disabled" if disabled else "enabled"
                logger.info(f"  [LIVE] {dev_id[:60]} -> {action} instantly, no reboot/replug needed")
            return bool(ok)
        finally:
            setupapi.SetupDiDestroyDeviceInfoList(hdevinfo)
    except Exception as e:
        logger.debug(f"  Live devnode toggle failed for {dev_id[:60]}: {e}")
        return False


def _force_remove_devnode(dev_id: str) -> bool:
    """
    Last-resort BLOCK-only fallback for when Windows refuses both the
    pnputil toggle and the SetupAPI DIF_PROPERTYCHANGE toggle live (most
    often signaled by pnputil rc=3010 "reboot is needed" — a real Windows
    quirk that shows up after a devnode has already been
    enabled/disabled a couple of times in the same session).

    Uses SetupDiCallClassInstaller(DIF_REMOVE) to pull the devnode out of
    the tree entirely, right now, with no reboot. Windows re-enumerates
    the device fresh the next time it's unplugged/replugged or a rescan
    runs — at which point the normal DB-driven pipeline (default OFF for
    new storage devices, see agent_db.py) takes over again.

    Only ever called on the DISABLE path — never on enable, since
    re-attaching a removed devnode isn't a live operation and forcing
    that would just leave the device in a confusing half-state. Enabling
    an already-removed device is handled naturally by the normal
    scan/apply cycle once it's replugged.
    """
    if not IS_WINDOWS:
        return False
    try:
        setupapi = ctypes.windll.setupapi

        setupapi.SetupDiGetClassDevsW.restype  = ctypes.c_void_p
        setupapi.SetupDiGetClassDevsW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong
        ]
        setupapi.SetupDiEnumDeviceInfo.restype  = ctypes.c_bool
        setupapi.SetupDiGetDeviceInstanceIdW.restype = ctypes.c_bool
        setupapi.SetupDiCallClassInstaller.restype = ctypes.c_bool
        setupapi.SetupDiDestroyDeviceInfoList.restype = ctypes.c_bool

        DIGCF_ALLCLASSES_PRESENT = DIGCF_ALLCLASSES | DIGCF_PRESENT
        DIF_REMOVE = 0x05

        class SP_DEVINFO_DATA(ctypes.Structure):
            _fields_ = [
                ("cbSize",    ctypes.c_ulong),
                ("ClassGuid", ctypes.c_byte * 16),
                ("DevInst",   _wintypes.DWORD),
                ("Reserved",  ctypes.c_void_p),
            ]

        hdevinfo = setupapi.SetupDiGetClassDevsW(None, None, None, DIGCF_ALLCLASSES_PRESENT)
        if not hdevinfo or hdevinfo == ctypes.c_void_p(-1).value:
            return False

        try:
            devinfo = SP_DEVINFO_DATA()
            devinfo.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            target_lower = dev_id.lower()
            i = 0
            found = False
            while setupapi.SetupDiEnumDeviceInfo(hdevinfo, i, ctypes.byref(devinfo)):
                i += 1
                buf = ctypes.create_unicode_buffer(512)
                req = _wintypes.DWORD(0)
                if not setupapi.SetupDiGetDeviceInstanceIdW(
                    hdevinfo, ctypes.byref(devinfo), buf, ctypes.sizeof(buf) // 2, ctypes.byref(req)
                ):
                    continue
                if buf.value.lower() != target_lower:
                    continue
                found = True
                break
            if not found:
                return False

            ok = setupapi.SetupDiCallClassInstaller(DIF_REMOVE, hdevinfo, ctypes.byref(devinfo))
            if ok:
                logger.info(f"  [FORCE-REMOVE] {dev_id[:60]} removed from devnode tree — "
                            f"no longer accessible until replugged/rescanned")
            return bool(ok)
        finally:
            setupapi.SetupDiDestroyDeviceInfoList(hdevinfo)
    except Exception as e:
        logger.debug(f"  Forced devnode removal failed for {dev_id[:60]}: {e}")
        return False


def set_storage_enabled(port_key: str, enabled: bool, device_ids: list = None) -> bool:
    """
    Enable or disable just the storage-class device(s) at a physical port.

    Tries a LIVE disable/enable — storage devices usually aren't
    CR_NOT_DISABLEABLE the way keyboards/mice are, so this often just
    works. There is no reboot/replug fallback: if the live toggle fails,
    the call is reported as failed (a ConfigFlags write is still attempted
    for bookkeeping, but it no longer counts as success on its own).

    device_ids: optional list of specific instance IDs to restrict the
    toggle to (mirrors set_port_enabled's signature). If omitted/empty,
    falls back to discovering storage devices at the port automatically.

    Returns True only if the live toggle actually applied, or if there's
    currently no storage device at this port (nothing to enforce — not a
    failure).
    """
    if not IS_WINDOWS:
        logger.info(f"[SIM] Storage at port {port_key} {'enabled' if enabled else 'disabled'}")
        return True

    if device_ids:
        devices = [d for d in _find_storage_devices_at_port(port_key)
                   if (getattr(d, "DeviceID", "") or "") in set(device_ids)]
        if not devices:
            # Explicit device_ids given but none matched at this port — fall
            # back to the port-based lookup rather than silently no-op'ing.
            devices = _find_storage_devices_at_port(port_key)
    else:
        devices = _find_storage_devices_at_port(port_key)
    if not devices and enabled:
        # Same recovery as set_port_enabled: a device force-removed by a
        # previous stuck disable won't show up in a live discovery at all.
        # Rescan and check once more before accepting "nothing here".
        if _pnp_rescan():
            devices = _find_storage_devices_at_port(port_key)
    if not devices:
        logger.debug(f"set_storage_enabled: no storage device at port {port_key} — nothing to enforce.")
        return True

    any_live_ok = False
    any_fallback_ok = False
    for dev in devices:
        raw_id = getattr(dev, "DeviceID", "") or ""
        if not raw_id:
            continue
        # UASP-attached drives (Seagate/WD/many SanDisk SSDs) enumerate as
        # SCSI\Disk&... — Windows will not reliably toggle that logical disk
        # devnode directly via pnputil. _strip_mi_suffix() walks it up to the
        # USB bridge controller (the node that's actually toggleable), while
        # leaving USBSTOR\ bulk-drive ids untouched since those DO toggle
        # directly and reliably as-is.
        dev_id = _strip_mi_suffix(raw_id)
        # PRIMARY: try the live pnputil toggle unconditionally, exactly like
        # set_port_enabled does. Previously this was gated behind
        # _setupapi_set_config_flags succeeding first — but for some storage
        # bridge chipsets (e.g. this VendorCo device), ConfigFlags always
        # fails (empty/zero ClassGuid while the USBSTOR node is in certain
        # states), which meant the live pnputil call — the one that actually
        # works, as proven by set_port_enabled calling it directly on the
        # same device — was never even attempted.
        if _try_live_disable_devnode(dev_id, disabled=not enabled):
            any_live_ok = True
        elif not enabled:
            # Same last-resort as set_port_enabled: if we're trying to BLOCK
            # this storage device and both pnputil and the SetupAPI devnode
            # toggle refused (e.g. rc=3010, reboot required), force it out of
            # the devnode tree so it stops being readable/writable right now.
            if _force_remove_devnode(dev_id):
                any_live_ok = True
                logger.info(f"  [DISABLED] Forced device removal succeeded for {dev_id[:70]} "
                            f"— drive is no longer accessible")

        # SECONDARY: best-effort ConfigFlags write, purely for persistence
        # bookkeeping. It is no longer treated as a substitute path when the
        # live toggle fails — there is no more reboot/replug fallback.
        ok = _setupapi_set_config_flags(dev_id, disabled=not enabled)
        any_fallback_ok = any_fallback_ok or ok

    action = "enabled" if enabled else "disabled"
    if any_live_ok:
        logger.info(f"Storage at port {port_key} {action} LIVE — took effect instantly")
        if enabled:
            # CM_Enable_DevNode (via pnputil /enable-device) only re-arms the
            # disk hardware node. It does NOT guarantee Windows' Mount
            # Manager/Volume Manager actually finishes remounting the
            # partition and reassigning a drive letter right after — a real
            # unplug/replug always triggers that automatically, but a bare
            # devnode enable sometimes leaves the volume in limbo, especially
            # if this same device was previously force-evicted (DIF_REMOVE)
            # during a stuck disable earlier in the session. Device Manager
            # then shows the device as present/enabled while Explorer never
            # gets a drive letter back for it — exactly the "enabled but
            # unusable" symptom. A rescan forces Windows to finish that
            # remount step instead of leaving it half-done.
            if _pnp_rescan():
                logger.info(f"  [RESCAN] Triggered post-enable bus rescan for port {port_key}")
            # The bus rescan above only re-arms the disk hardware node — it
            # does not reassign a drive letter. Force the volume layer to
            # catch up too, or the device can sit "Allowed"/enabled in
            # Device Manager while never reappearing in This PC/Explorer.
            if _force_volume_rescan():
                logger.info(f"  [RESCAN] Triggered post-enable volume rescan for port {port_key} "
                            f"so the drive letter finishes remounting")
    else:
        logger.warning(f"Storage at port {port_key} {action} — live toggle FAILED "
                        f"(ConfigFlags write {'succeeded' if any_fallback_ok else 'also failed'})")
    # See the matching note in set_port_enabled() above - cache invalidation
    # moved out of the per-call path for the same reason (concurrent-thread
    # cache thrashing during a multi-port apply batch).
    return any_live_ok