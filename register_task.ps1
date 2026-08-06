# SwiftProsys USB Control — Register Scheduled Task + per-user hook
# Called by Inno Setup during installation.

param(
    [string]$InstallDir,
    [string]$EmployeeUser
)

# ── Resolve the install directory ───────────────────────────────────────────
# Swift Agent must run from the SAME folder Swift Optimizer (the login/main
# app, SwiftOptimizer.exe) was actually installed to — Inno Setup passes this in
# as -InstallDir "{app}". Falling back to the script's own folder covers the
# case where this is run standalone for testing.
if (-not $InstallDir) {
    $InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}

$TaskName  = "SwiftAgent"
$AgentPath = Join-Path $InstallDir "SwiftAgent.exe"
$AgentDir  = $InstallDir

# ── Resolve the actual employee logged on to this PC ────────────────────────
# Never ask the admin to type a name during install. $env:USERNAME here would
# just be whoever is running this script (the IT admin doing the install,
# or SYSTEM if pushed via RMM) — not necessarily the employee using the
# machine. Instead, ask Windows who owns the current interactive/console
# session, which is correct regardless of which account launched setup.
function Get-InteractiveSessionUser {
    try {
        $csUser = (Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop).UserName
        if ($csUser) {
            # Comes back as "DOMAIN\user" or "COMPUTERNAME\user" — keep just the account name.
            return ($csUser -split '\\')[-1]
        }
    } catch { }

    try {
        # Fallback: parse `query user` for the Active console session.
        $line = quser 2>$null | Where-Object { $_ -match '\sActive\s' } | Select-Object -First 1
        if ($line) {
            return ($line.Trim() -replace '^>','' -split '\s+')[0]
        }
    } catch { }

    return $env:USERNAME
}

if (-not $EmployeeUser) {
    $EmployeeUser = Get-InteractiveSessionUser
}

# ── SYSTEM-mode scheduled task (registry/storage enforcement) ──────────────
# Remove old task if exists
schtasks /delete /tn $TaskName /f 2>$null | Out-Null

$action    = New-ScheduledTaskAction -Execute $AgentPath -WorkingDirectory $AgentDir
$trigger   = New-ScheduledTaskTrigger -AtStartup
$settings  = New-ScheduledTaskSettingsSet `
                -MultipleInstances IgnoreNew `
                -ExecutionTimeLimit ([TimeSpan]::Zero) `
                -RestartCount 999 `
                -RestartInterval (New-TimeSpan -Minutes 1) `
                -StartWhenAvailable

# Run as SYSTEM: no UAC prompt, no dependency on the logged-in user being
# an admin, works even before anyone logs in, and Task Scheduler keeps
# proper track of the process (no self-elevate-and-exit detachment).
$principal = New-ScheduledTaskPrincipal `
                -UserId "NT AUTHORITY\SYSTEM" `
                -LogonType ServiceAccount `
                -RunLevel Highest

Register-ScheduledTask `
    -TaskName  $TaskName `
    -Action    $action `
    -Trigger   $trigger `
    -Settings  $settings `
    -Principal $principal `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

# ── Per-user hook mode (HKCU Run, for the EMPLOYEE specifically) ───────────
# Same EXE, launched with --hook, for the CURRENTLY DESIGNATED user's own
# session — required because a SYSTEM process (Session 0) never sees real
# keyboard/mouse events. This must NOT be a scheduled task; it has to start
# from the user's own logon, same pattern as a normal per-user startup app.
#
# IMPORTANT: writing to plain "HKCU:\..." only ever targets whoever is
# running THIS PowerShell process. If that's not the employee (e.g. IT
# installed remotely under their own account), the Run key would land in
# the wrong profile and the employee would never get instant kbd/mouse
# blocking. So we resolve $EmployeeUser's SID and write into
# "Registry::HKEY_USERS\<SID>\..." instead of HKCU whenever possible.

$HookRunName = "USBControlHook"
$HookCommand = "`"$AgentPath`" --hook"

function Get-UserSid([string]$userName) {
    try {
        $acct = New-Object System.Security.Principal.NTAccount($userName)
        return $acct.Translate([System.Security.Principal.SecurityIdentifier]).Value
    } catch {
        return $null
    }
}

$sid = Get-UserSid $EmployeeUser

if ($sid) {
    $hivePath = "Registry::HKEY_USERS\$sid\Software\Microsoft\Windows\CurrentVersion\Run"
    $hiveLoaded = Test-Path "Registry::HKEY_USERS\$sid"

    if (-not $hiveLoaded) {
        # User has never logged on (so their hive isn't mounted under
        # HKEY_USERS yet) — load it from disk directly via reg.exe so the
        # Run key still gets written, and unload it again afterward.
        $profilePath = (Get-CimInstance Win32_UserProfile |
            Where-Object { $_.SID -eq $sid }).LocalPath
        if (-not $profilePath) {
            Write-Warning "Could not find a profile for '$EmployeeUser' (SID $sid). " +
                          "Hook Run key NOT written — they must log on once first, " +
                          "then re-run this script with -EmployeeUser '$EmployeeUser'."
        } else {
            $ntuserDat = Join-Path $profilePath "NTUSER.DAT"
            reg.exe load "HKU\TempHive_USBControl" "$ntuserDat" | Out-Null
            New-ItemProperty -Path "Registry::HKEY_USERS\TempHive_USBControl\Software\Microsoft\Windows\CurrentVersion\Run" `
                -Name $HookRunName -Value $HookCommand -PropertyType String -Force | Out-Null
            reg.exe unload "HKU\TempHive_USBControl" | Out-Null
            Write-Host "Hook Run key written to offline profile for '$EmployeeUser'."
        }
    } else {
        New-ItemProperty -Path $hivePath -Name $HookRunName -Value $HookCommand `
            -PropertyType String -Force | Out-Null
        Write-Host "Hook Run key written to logged-on hive for '$EmployeeUser' (SID $sid)."
    }
} else {
    Write-Warning "Could not resolve SID for '$EmployeeUser' — falling back to HKCU " +
                  "(only correct if '$EmployeeUser' is the account running this installer)."
    $HookRunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    New-ItemProperty -Path $HookRunKey -Name $HookRunName -Value $HookCommand `
        -PropertyType String -Force | Out-Null
}

# Start it immediately too, but ONLY if the target user is the one
# currently logged on — otherwise launching it now would run the hook in
# the WRONG session (e.g. the admin's), which is exactly the bug we're
# avoiding. It will still start correctly on the employee's own next logon
# via the Run key above.
if ($EmployeeUser -eq $env:USERNAME) {
    Start-Process -FilePath $AgentPath -ArgumentList "--hook" -WorkingDirectory $AgentDir
} else {
    Write-Host "Skipping immediate hook launch — '$EmployeeUser' is not the current session. " +
               "It will start automatically on their next logon."
}

# ── Hide agent files from Explorer ─────────────────────────────────────────
# agent_config.txt is no longer written to disk at all (it's read straight
# out of the bundled EXE), so there's nothing to hide there anymore. The
# log file is still created the moment the agent starts above; wait
# briefly for it to appear, then mark it Hidden+System.
$LogFile = Join-Path $env:PROGRAMDATA "USBAgent\usb_agent.log"

$filesToHide = @(
    $LogFile
)

# Wait up to 5 seconds for the log to be created by the agent
$waited = 0
while (-not (Test-Path $LogFile) -and $waited -lt 5) {
    Start-Sleep -Seconds 1
    $waited++
}

foreach ($f in $filesToHide) {
    if (Test-Path $f) {
        attrib +H +S $f
    }
}
