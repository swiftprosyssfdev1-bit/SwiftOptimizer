#define AppName "Swift Optimizer"
#define AppVersion "1.6"
#define TaskName "SwiftAgent"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=SwiftProSys Private Ltd
AppId={{B3F2A1C4-9D7E-4F6B-A8C2-123456789ABC}
DefaultDirName={autopf}\SwiftProsys\Swift Optimizer
DisableDirPage=yes
OutputDir=Output
OutputBaseFilename=SwiftProsys_Swift_Optimizer_Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ShowLanguageDialog=no
WizardStyle=modern
DisableWelcomePage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\SwiftOptimizer.exe";        DestDir: "{app}"; Flags: ignoreversion
Source: "dist\SwiftAgent.exe";     DestDir: "{app}"; Flags: ignoreversion
; Neither agent_config.txt nor .env is installed as a separate visible
; file — both are embedded straight into their respective .exe by
; PyInstaller (see --add-data in build_exe.bat) and read from the
; extracted temp folder at runtime, so no plaintext credentials file
; ever lands in {app} for a user to open.

[Icons]
Name: "{commondesktop}\Swift Optimizer"; Filename: "{app}\SwiftOptimizer.exe"; Comment: "Swift Optimizer"

[Run]
; Register SwiftProSysUSBAgent as a real Windows Service, start it, and
; configure crash-recovery via `sc failure` — all visible under the
; service's real name in services.msc / Task Manager. No hidden files,
; no disguised task name.
Filename: "{app}\SwiftAgent.exe"; Parameters: "--startup auto install"; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "failure SwiftAgent reset= 86400 actions= restart/60000/restart/60000/restart/60000"; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "description SwiftAgent ""Swift Agent — monitors and enforces USB device policy for this workstation. Installed and managed by SwiftProSys IT as part of Swift Optimizer."""; Flags: runhidden waituntilterminated
Filename: "{app}\SwiftAgent.exe"; Parameters: "start"; Flags: runhidden waituntilterminated

[UninstallRun]
Filename: "{app}\SwiftAgent.exe"; Parameters: "stop"; Flags: runhidden waituntilterminated skipifdoesntexist
Filename: "{app}\SwiftAgent.exe"; Parameters: "remove"; Flags: runhidden waituntilterminated skipifdoesntexist

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
; usb_agent.log lives at C:\SwiftAgent, outside {app} (which is under
; Program Files), so it needs its own cleanup entry - the {app} deletion
; above won't reach it.
Type: filesandordirs; Name: "C:\SwiftAgent"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
  if not IsAdminLoggedOn then
  begin
    MsgBox('Please right-click and Run as Administrator.', mbError, MB_OK);
    Result := False;
  end;
end;
