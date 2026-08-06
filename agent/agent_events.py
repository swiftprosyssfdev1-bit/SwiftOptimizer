"""
agent_events.py
----------------
Windows device-change event watcher.

The old agent loop scanned the *entire* USB/port topology (WMI +
Get-PnpDevice + registry) on a fixed short timer (every POLL_INTERVAL
seconds), whether or not anything had actually changed. That's the
"continuous scanning" pattern: it burns CPU/DB round-trips constantly, and
worst case a plug/unplug still waits up to POLL_INTERVAL seconds to be
noticed.

This module replaces the timer as the *trigger* for a rescan with a real
Windows notification: it subscribes to WMI's Win32_DeviceChangeEvent
(EventType 2 = device arrival, EventType 3 = device removal — the same
underlying event Windows fires for WM_DEVICECHANGE) and wakes the main
agent loop the instant something is plugged in or removed. The fixed
timer becomes a rare safety net instead of the primary mechanism.

WMI/COM objects aren't thread-safe across apartments, so the watcher gets
its own background thread with its own CoInitialize call. It only ever
touches a threading.Event — all the actual scanning/DB/registry work
still happens on the single main agent thread, exactly as before.
"""
import threading
import time

from agent_config import log, IS_WINDOWS, _HAS_WMI

_device_change_event = threading.Event()
_stop_event = threading.Event()
_watcher_thread = None

# After the first device-change event fires, wait a short moment before
# acting on it. Plugging in a composite device (e.g. a USB hub, or a
# keyboard+mouse combo dongle) fires several arrival events in quick
# succession for its sub-interfaces; without this we'd kick off several
# redundant full rescans back-to-back instead of one settled scan.
_DEBOUNCE_SECONDS = 0.4


def wait_for_change(timeout: float) -> bool:
    """Block for up to `timeout` seconds, waking early if a device-change
    event has fired. Returns True if woken by an actual device event
    (after debouncing), False if it simply timed out."""
    fired = _device_change_event.wait(timeout)
    if not fired:
        return False
    time.sleep(_DEBOUNCE_SECONDS)
    _device_change_event.clear()
    return True


def _watch_loop():
    """Runs on its own dedicated thread for the life of the agent.
    Opens a WMI event subscription for USB device arrival/removal and
    sets `_device_change_event` every time one fires. Reconnects
    automatically if the subscription drops (WMI event queries do this
    occasionally, e.g. after the WMI service restarts)."""
    import pythoncom
    import wmi

    pythoncom.CoInitialize()
    try:
        while not _stop_event.is_set():
            try:
                c = wmi.WMI()
                watcher = c.watch_for(raw_wql="SELECT * FROM Win32_DeviceChangeEvent")
                log.info("Device-change event watcher connected (WMI Win32_DeviceChangeEvent) "
                         "— USB plug/unplug will trigger an immediate rescan instead of "
                         "waiting on the poll timer.")
                while not _stop_event.is_set():
                    try:
                        evt = watcher(timeout_ms=2000)
                    except wmi.x_wmi_timed_out:
                        continue
                    except Exception as e:
                        # A single flaky/ephemeral event object must not tear
                        # down the whole subscription. In practice this fires
                        # as a transient COM error ("SWbemPropertySet ...
                        # Not found") reading the NEXT event off the queue,
                        # not a real disconnect — letting it propagate to the
                        # outer except below meant the watcher reconnected
                        # (and hit the same transient error again almost
                        # immediately) in a tight loop, so it never stayed up
                        # long enough to deliver a single real event and every
                        # plug/unplug silently fell back to the slow
                        # fixed-interval safety-net scan instead. Since a
                        # spurious extra rescan is cheap (protected by the
                        # WMI caches) but a missed plug/unplug is a real
                        # detection gap on a security-relevant agent, treat
                        # an unreadable event as "something changed, unknown
                        # what" and trigger a rescan anyway instead of
                        # dropping it.
                        log.debug(f"Device-change event watcher: transient error "
                                  f"reading next event, triggering rescan anyway: {e}")
                        _device_change_event.set()
                        continue

                    try:
                        event_type = getattr(evt, "EventType", None)
                    except Exception as e:
                        log.debug(f"Device-change event watcher: could not read "
                                  f"EventType, triggering rescan anyway: {e}")
                        _device_change_event.set()
                        continue

                    # 2 = device arrival, 3 = device removal. (1 = "device
                    # configuration changed", which fires constantly for
                    # unrelated reasons — ignore it.)
                    if event_type in (2, 3):
                        log.debug(f"Device-change event received (EventType={event_type})")
                        _device_change_event.set()
            except Exception as e:
                if not _stop_event.is_set():
                    log.warning(f"Device-change event watcher error, reconnecting in 5s: {e}")
                    time.sleep(5)
    finally:
        pythoncom.CoUninitialize()


def start() -> bool:
    """Starts the background watcher thread. Safe to call once at agent
    startup; idempotent if already running. Returns False on non-Windows
    or if the `wmi` package isn't available — the caller should keep
    relying on its normal fixed-interval fallback in that case."""
    global _watcher_thread
    if not IS_WINDOWS or not _HAS_WMI:
        log.warning("Device-change event watching unavailable (not Windows, or 'wmi' package "
                    "not installed) — falling back to fixed-interval polling only.")
        return False
    if _watcher_thread and _watcher_thread.is_alive():
        return True
    _stop_event.clear()
    _watcher_thread = threading.Thread(
        target=_watch_loop, name="USBDeviceChangeWatcher", daemon=True
    )
    _watcher_thread.start()
    return True


def stop():
    """Signals the watcher thread to exit. Not currently called anywhere
    (the agent runs until the process is killed), but provided for
    completeness / tests."""
    _stop_event.set()
    _device_change_event.set()
