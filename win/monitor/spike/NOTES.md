# Phase 0 spike — findings

Answers the one question gating the whole native-print-queue plan
(`C:\Users\Quynn\.claude\plans\lazy-stirring-ladybug.md`): will `spoolsv.exe`
load an unsigned, locally-registered custom Port Monitor DLL under current
Windows security policy?

## Build

```powershell
gcc -Wall -Wextra -shared -o tmt70spike.dll tmt70spike.c -lgdi32 -luser32 \
    -Wl,--out-implib,libtmt70spike.a
```

Compiled clean, no warnings, with MinGW-w64 (WinLibs POSIX/UCRT distribution,
gcc 16.1.0). Exports exactly one symbol: `InitializePrintMonitor2` (verified
via `objdump -p`).

## Register / test

```powershell
# elevated PowerShell
.\register-spike.ps1
# print anything to the "TMT70 Spike Test" queue, then:
type $env:ProgramData\tmt70spike\writeport.log
# teardown:
.\register-spike.ps1 -Unregister
```

## Findings

Tested on a real machine: `10.0.26100.0` (`Get-ComputerInfo` reports
`WindowsProductName: Windows 10 Pro`, but the build number is a Windows 11
24H2-era build — recorded as observed), execution policy `RemoteSigned`
enforced by Group Policy at machine scope (`Set-ExecutionPolicy` itself is
blocked; running via `powershell -ExecutionPolicy Bypass -File ...` still
works — that override is per-process, not a persistent policy change, so it
wasn't blocked by the same GPO). Not confirmed domain-joined, but is under
active GPO management (execution policy, and PrintService/Admin showed AD
publish errors from unrelated prior activity).

- [x] `AddMonitor` succeeded (after fixing a real bug — see below) and the
      registry key
      `HKLM\SYSTEM\CurrentControlSet\Control\Print\Monitors\TMT70 Spike Monitor`
      appeared with `Driver = tmt70spike.dll`.
- [x] `spoolsv.exe` loaded the DLL — `DllMain` (`DLL_PROCESS_ATTACH`) and
      `InitializePrintMonitor2` both logged immediately after the spooler
      restart, with **no signing prompt, no policy block, no crash**.
- [x] `EnumPorts` was called (logged) both at spooler startup and again when
      the test queue was created via `Add-PrinterPort`/`Add-Printer`.
- [x] **A real print job (Notepad → File → Print → "TMT70 Spike Test")
      produced the full lifecycle**: `OpenPort` → `StartDocPort (job=2)` →
      `WritePort (len=121 bytes=...)` → `EndDocPort`. The 121 bytes decoded
      to plain ASCII text matching the Notepad document's actual content
      (confirmed against its `DocumentName` in `Get-PrintJob`), ending in a
      form-feed (`0x0C`) — i.e. genuine, correct document bytes reached our
      monitor's `WritePort`, not garbage or a truncated/wrong stream.
- [ ] No Event Viewer entries under `PrintService/Admin` or `/Operational`
      referenced this monitor at all (checked, found nothing tmt70-related)
      — loading and operating an unsigned monitor produced **no security
      log noise whatsoever** on this box.
- Two real bugs were found and fixed during this spike (see below) — the
  spike process itself is what surfaced them; that's exactly what it was for.

### Bugs found and fixed along the way

1. **`AddMonitor` failed with Win32 error 87 (`ERROR_INVALID_PARAMETER`).**
   Root cause: `MONITOR_INFO_2.pName` is the *monitor's own name* and must be
   set; it is NOT the same as `AddMonitor`'s own `pName` argument (the print
   *server* name, correctly `NULL` for local). The first version of
   `register-spike.ps1` left `MONITOR_INFO_2.pName` as `$null`. Fixed by
   setting it to the monitor name.
2. **First real print-job test produced zero new log lines** despite Windows
   reporting `JobStatus: Complete`. Root cause was never definitively pinned
   down (no corroborating Event Viewer evidence either way), but the fix that
   resolved it addressed the most likely explanation: the original logger
   resolved its log directory via `GetEnvironmentVariableW("ProgramData")`
   and silently swallowed `fopen` failures — a real risk if the per-job
   `WritePort` call runs in a different/more-restricted process context
   (e.g. Windows' driver-isolation host) than the management-time calls
   (`EnumPorts`/`OpenPort`), which ran fine via the elevated registration
   script. Switched to a hardcoded path (`C:\Windows\Temp\tmt70spike.log`)
   plus an unconditional Event Log fallback (`ReportEventW`, which needs no
   pre-registered event source and works from effectively any process
   context) so a future logging failure is visible instead of silent. After
   this change (and a clean `Stop-Service`/copy/`Start-Service` cycle — the
   DLL was locked by something other than the `Spooler` service itself
   during the first re-registration attempt, released after a full stop),
   the real job's bytes appeared correctly on the very next test.
3. **`Get-PrinterPort -Name "TMT70SPIKE:"` fails** with
   `HRESULT 0x8007000d` (invalid data) even though the port works correctly
   end-to-end. Left unresolved — likely the modern CIM/WMI print-management
   provider (`MSFT_PrinterPort`) doesn't fully support introspecting ports
   from third-party monitors, rather than a bug in this monitor's `EnumPorts`
   (raw `WritePort` traffic proves the port itself functions correctly
   regardless of what that one cmdlet reports). Not chased further since it
   didn't block anything; worth a note for Phase 1 in case any tooling
   depends on `Get-PrinterPort` succeeding for our port.

## Verdict

**Viable.** The core question is answered with a real, physically-confirmed
positive: an unsigned, locally-registered Port Monitor DLL loads into
`spoolsv.exe` and correctly handles a real print job's full lifecycle on
stock Windows 10/11 (build 26100), including under active GPO script-policy
enforcement, with zero security-log pushback. Phase 1 (a real
`tmt70print`-backed monitor, per the plan) can proceed.

Not yet tested: a domain-joined machine with `RestrictDriverInstallationToAdministrators`
or explicit Point-and-Print hardening GPOs (this box's GPO only touched
script execution policy, not driver/monitor installation specifically) — the
plan's suggestion to test on a second, more locked-down configuration before
fully committing still stands as good practice, just not yet done.

## Registration-scriptability note

`AddMonitor` is not one of Inno Setup's built-in Pascal-scriptable functions.
This spike calls it via PowerShell `Add-Type` + P/Invoke (see
`register-spike.ps1`), which works fine for a manual spike but is not
something `tmt70print.iss`'s `[Run]` section can do directly. Phase 1 will
need either: a small compiled registration helper `.exe` invoked from
`[Run]`, or an inline PowerShell invocation via `Exec('powershell.exe', ...)`
(the same P/Invoke technique this script uses) — record which approach was
verified to work under the installer's actual elevation context.

**Confirmed working here:** the PowerShell `Add-Type` + `DllImport("winspool.drv")`
P/Invoke technique itself is sound (once `MONITOR_INFO_2.pName` is set
correctly — see bug #1 above) — `AddMonitor`/`DeleteMonitor` both work from a
plain elevated PowerShell process with no special manifest or COM
registration needed. That derisks the "inline PowerShell invocation via
`Exec('powershell.exe', ...)`" option for Phase 1's installer integration;
the remaining open question is only whether Inno's own elevation context
(`PrivilegesRequired=admin`) is sufficient for that `Exec` call, which this
spike didn't test (it was run from a manually-elevated shell, not from
inside an actual Inno `[Run]` step).
