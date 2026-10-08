; Inno Setup script for tmt70print -- the Windows front end of
; epson-tmt70ii-escpos.
;
; SPDX-License-Identifier: GPL-2.0-or-later
;
; Built by tools\build-win.ps1, which passes MyAppVersion and StageDir:
;     ISCC.exe /DMyAppVersion=1.0.0 /DStageDir=..\build\win win\tmt70print.iss
;
; Inno Setup is native Pascal: no .NET is required to build this, and none is
; required to run the result.

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#ifndef StageDir
  #define StageDir "..\build\win"
#endif

#define MyAppName "TM-T70II ESC/POS"
#define MyAppExe  "tmt70print.cmd"

[Setup]
AppId={{6B1D3F42-2C77-4E5A-9B6B-6E9A1D70A702}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=epson-tmt70ii-escpos
AppSupportURL=https://example.invalid/epson-tmt70ii-escpos
DefaultDirName={autopf}\tmt70print
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputBaseFilename=tmt70print-setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#StageDir}\docs\copyright
PrivilegesRequired=admin
UninstallDisplayName={#MyAppName} {#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "addtopath"; Description: "Add tmt70print to the system PATH"; \
    GroupDescription: "Integration:"
Name: "sampleini"; Description: "Install a default tmt70print.ini for this machine"; \
    GroupDescription: "Integration:"
Name: "nativequeue"; Description: "Register a native Windows print queue (experimental, text-only -- see docs\WINDOWS.md)"; \
    GroupDescription: "Integration:"; Flags: unchecked

[Files]
Source: "{#StageDir}\tmt70print.py";  DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\tmt70print.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\selftest.py";    DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\tmt70win\*";     DestDir: "{app}\tmt70win"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\src\*";          DestDir: "{app}\src"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\tools\*";        DestDir: "{app}\tools"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\docs\*";         DestDir: "{app}\docs"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
; optional bundled runtime and libusb (present only when built with -Fetch)
Source: "{#StageDir}\python\*";       DestDir: "{app}\python"; \
    Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "{#StageDir}\vendor\*";       DestDir: "{app}\vendor"; \
    Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
; native print queue (Task: nativequeue) -- see win\monitor\README.md
Source: "{#StageDir}\monitor\tmt70mon\tmt70mon.dll"; DestDir: "{app}\monitor\tmt70mon"; \
    Flags: ignoreversion; Tasks: nativequeue
Source: "{#StageDir}\monitor\register-monitor.ps1"; DestDir: "{app}\monitor"; \
    Flags: ignoreversion; Tasks: nativequeue
Source: "{#StageDir}\monitor\README.md"; DestDir: "{app}\monitor"; \
    Flags: ignoreversion skipifsourcedoesntexist; Tasks: nativequeue
; the sample config goes to a neutral name so an existing one is never clobbered
Source: "{#StageDir}\tmt70print.ini"; DestDir: "{app}"; \
    DestName: "tmt70print.ini.sample"; Flags: ignoreversion
Source: "{#StageDir}\tmt70print.ini"; DestDir: "{app}"; \
    Flags: onlyifdoesntexist uninsneveruninstall; Tasks: sampleini

[Icons]
Name: "{group}\{#MyAppName} help"; Filename: "{app}\{#MyAppExe}"; \
    Parameters: "--help"; WorkingDir: "{app}"
Name: "{group}\Documentation"; Filename: "{app}\docs\WINDOWS.md"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Registry]
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; \
    ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; \
    Check: NeedsPath(ExpandConstant('{app}')); Tasks: addtopath

[Run]
Filename: "{app}\{#MyAppExe}"; Parameters: "--list"; \
    Description: "Show reachable printers now"; \
    Flags: postinstall runascurrentuser shellexec skipifsilent
; registers the "TMT70 Monitor" port monitor + a queue bound to the inbox
; "Generic / Text Only" driver, targeting the default tmt70:// (WinUSB)
; device -- edit HKLM\...\Print\Monitors\TMT70 Monitor\Config afterward (or
; re-run register-monitor.ps1 -Device ...) to point it elsewhere.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\monitor\register-monitor.ps1"""; \
    WorkingDir: "{app}\monitor"; StatusMsg: "Registering the native TMT70 print queue..."; \
    Flags: runhidden; Tasks: nativequeue

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\monitor\register-monitor.ps1"" -Unregister"; \
    WorkingDir: "{app}\monitor"; Flags: runhidden; Tasks: nativequeue; RunOnceId: "TMT70UnregisterMonitor"

[Code]
function NeedsPath(Dir: string): Boolean;
var
  Existing: string;
begin
  if not RegQueryStringValue(HKLM,
      'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
      'Path', Existing) then
  begin
    Result := True;
    Exit;
  end;
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Existing) + ';') = 0;
end;
