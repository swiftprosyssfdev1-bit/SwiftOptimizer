"""
swift_agent_service.py — Windows Service wrapper for the Swift Agent.

Registers as a normal, visibly-named Windows Service:
    Service name : SwiftProSysUSBAgent
    Display name : SwiftProSys USB Control Agent

This shows up under its real name in services.msc, Task Manager (Services
tab), and `sc query`. Auto-restart-on-crash is configured through Windows'
own service recovery settings (see install instructions below) rather than
a custom respawn loop, so admins can see and change that behavior with
standard tools (`sc failure`, or the Recovery tab in services.msc).

Install / manage (run an elevated cmd/PowerShell from the folder containing
the frozen SwiftAgentService.exe, or `python swift_agent_service.py <verb>`
from source with pywin32 installed):

    swift_agent_service.py install      # register the service
    swift_agent_service.py start        # start it
    swift_agent_service.py stop         # stop it
    swift_agent_service.py remove       # unregister it
    swift_agent_service.py --startup auto install   # ensure Automatic start

After installing, set crash-recovery behavior once (Windows has no install-
time flag for this, so it's a follow-up `sc` call — safe to run every time,
it just overwrites the same settings):

    sc failure SwiftProSysUSBAgent reset= 86400 actions= restart/60000/restart/60000/restart/60000
    sc description SwiftProSysUSBAgent "Monitors and enforces USB device policy for this workstation. Installed and managed by SwiftProSys IT."

`register_task.ps1` / the scheduled-task approach is no longer needed once
this is installed as a service — a service already starts at boot, before
login, and keeps running after logoff.
"""

import sys
import threading

import servicemanager
import win32event
import win32service
import win32serviceutil

# Make sure sibling modules (agent_core, agent_config, etc.) are importable
# whether we're run from source or frozen with PyInstaller.
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_config import log, AGENT_VERSION
from agent_core import _agent_loop, _resolve_username
import socket


class SwiftAgentService(win32serviceutil.ServiceFramework):
    _svc_name_ = "SwiftAgent"
    _svc_display_name_ = "Swift Agent"
    _svc_description_ = (
        "Swift Agent — monitors and enforces USB device policy for this "
        "workstation. Installed and managed by SwiftProSys IT as part of "
        "Swift Optimizer."
    )

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._worker_thread = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        log.info("Service stop requested — shutting down agent loop.")
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        log.info(f"=== Swift Agent v{AGENT_VERSION} (service) starting ===")

        hostname = socket.gethostname().strip()

        # _agent_loop runs forever, so drive it on a background thread and
        # use the service's own stop_event as the wait/exit signal — this
        # keeps SvcDoRun responsive to Windows' stop requests instead of
        # blocking inside the agent loop itself.
        #
        # NOTE: username resolution is intentionally done inside
        # _run_agent_loop (after pythoncom.CoInitialize()), not here.
        # _resolve_username() -> _get_windows_profile_name() uses WMI to
        # read the account's real display name (e.g. "Swift ProSys CHN
        # 029"). WMI requires the calling thread to be COM-initialized;
        # SvcDoRun's own thread never calls CoInitialize, so doing the
        # lookup here makes WMI throw silently and the code falls back to
        # the bare login name (e.g. 'spslw') instead of the display name.
        self._worker_thread = threading.Thread(
            target=self._run_agent_loop, args=(hostname,), daemon=True
        )
        self._worker_thread.start()

        # Block here until SvcStop() signals us; SCM expects SvcDoRun not
        # to return until the service is actually stopping.
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)

        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STOPPED,
            (self._svc_name_, ""),
        )

    def _run_agent_loop(self, hostname):
        # WMI/COM requires CoInitialize on every thread that uses it. The
        # main service thread gets this implicitly in the old code path,
        # but this loop runs on its own worker thread (so SvcDoRun stays
        # responsive to stop requests) — without this call, every WMI scan
        # fails with "you're probably running inside a thread without
        # first calling pythoncom.CoInitialize[Ex]" and device detection
        # silently falls back to stale registry/PS topology data only.
        import pythoncom
        pythoncom.CoInitialize()
        # Username resolution also depends on WMI (to read the Windows
        # account's real display name), so it must happen AFTER
        # CoInitialize() and on this same thread — not in SvcDoRun.
        username = _resolve_username()
        try:
            _agent_loop(username, hostname)
        except Exception as e:
            log.error(f"Agent loop crashed: {e}")
            # Let Windows' own service-recovery policy (configured via
            # `sc failure`) decide whether/how to restart us, instead of
            # looping internally.
            self.SvcStop()
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


if __name__ == "__main__":
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(SwiftAgentService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(SwiftAgentService)
