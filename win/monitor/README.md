# win/monitor — native Windows print queue

Gives `tmt70print` a real entry in Windows Settings → Printers that any app
can print to via File → Print — without needing a signed v4 print driver
(the thing `docs/WINDOWS.md` explains this project has always avoided).

## How it works

A print driver renders GDI output into bytes; a **Port Monitor** only
transports already-rendered bytes to a port. Pairing a **custom, locally
registered Port Monitor DLL** (`tmt70mon.dll`, this directory) with an
**already-installed, Microsoft-signed inbox driver** ("Generic / Text Only")
needs no new driver-signing path — only the monitor DLL needs registering
via `AddMonitor`, which (per the Phase 0 spike, see `spike/NOTES.md`) does
not require WHQL/attestation signing.

```
any app  --File→Print-->  "EPSON TM-T70II (tmt70print)" queue
                              (driver: Generic / Text Only, inbox/signed)
                                   |  GDI/EMF rendered to plain text + form feeds
                                   v
                           tmt70mon.dll  (this directory)
                              WritePort: strip 0x0C, accumulate to a temp file
                              EndDocPort: spawn tmt70print --kind text --device <configured>
                                   |
                                   v
                     win/tmt70win (job.py/text.py/sinks.py) -- UNMODIFIED,
                     same code path as the manual CLI
```

`tmt70mon.c` is a **thin transport shim, not a reimplementation** — it holds
no ESC/POS logic at all. It buffers bytes and shells out to the existing
`tmt70print` CLI as a subprocess, so every option (density/cut/margin/media),
every code page, every transport (WinUSB/TCP/spooler) already implemented in
`win/tmt70win/` works here unchanged.

## v1 scope — text only

This is the **committed** deliverable (Phase 1 of the plan this was built
from). It is paired with "Generic / Text Only", so:

- Works: plain text from any app whose print output degrades sensibly to
  text (Notepad, most text editors, many apps' "simple" print paths).
- Does **not** work: images, logos, mixed graphics/text layouts — that needs
  a raster-capable driver pairing, which is a separate, unproven research
  spike (Phase 2 — see the plan), not shipped here.
- Multi-page documents are printed as **one continuous job**: the "Generic /
  Text Only" print processor emits a page-eject byte (`0x0C`) between pages,
  which `tmt70mon.c`'s `WritePort` strips rather than acting on (see the
  comment there) — no per-page cut/feed is inserted at those boundaries.

## Files

| File | Role |
|---|---|
| `tmt70mon/tmt70mon.c` | The Monitor2 DLL. One fixed port (`TMT70:`). |
| `register-monitor.ps1` | Elevated install/uninstall: `AddMonitor`, registry config, queue creation. Not yet wired into `tmt70print.iss` — see below. |
| `spike/` | Phase 0 throwaway spike (not shipped) that proved an unsigned monitor DLL loads under real Windows security policy. Keep for reference; see `spike/NOTES.md` for the full findings and two real bugs it caught before they reached the production DLL. |

## Configuration

Everything the monitor needs is read fresh from the registry on every job —
no reinstall needed to change target device or options:

```
HKLM\SYSTEM\CurrentControlSet\Control\Print\Monitors\TMT70 Monitor\Config
    PythonExe   REG_SZ   full path to the interpreter to invoke
    ScriptPath  REG_SZ   full path to tmt70print.py
    Device      REG_SZ   tmt70print --device URI
    ExtraArgs   REG_SZ   extra tmt70print arguments, e.g. "--density dark --cut full"
```

`register-monitor.ps1` writes all four at install time from its `-Device`/
`-ExtraArgs` parameters, preferring a bundled embeddable Python
(`win\python\python.exe`, as staged by `tools\build-win.ps1 -Fetch`) over
whatever's on `PATH` — deliberately, since the monitor runs in a service
context where ambient `PATH` state isn't something to depend on (this bit
the Phase 0 spike once already; see `spike/NOTES.md` bug #2).

There is no port-properties UI in v1 — `ConfigurePort` just points you at
the registry key. Edit it and restart the Spooler service to apply changes.

## Diagnostics

- `C:\Windows\Temp\tmt70mon.log` — every monitor callback the spooler makes,
  timestamped. Also mirrored to the Windows Application event log
  (`ReportEventW`, source "Application") so a callback that fires inside a
  restricted/isolated process context is still visible even if the file
  write itself fails — this exact failure mode is what the Phase 0 spike hit
  and fixed (see `spike/NOTES.md` bug #2).
- `C:\Windows\Temp\tmt70mon-lastjob.log` — stdout/stderr of the most recent
  `tmt70print` subprocess invocation (its own `-v` output, or a traceback if
  it errored).

## Status

Working end-to-end on real hardware: registered via `register-monitor.ps1`,
printed from Notepad's normal File → Print dialog through the "EPSON
TM-T70II (tmt70print)" queue, through `tmt70mon.dll`'s `WritePort`/
`EndDocPort`, through the unmodified `tmt70print` CLI, out the physical
TM-T70II. Wired into the installer (`tmt70print.iss`'s opt-in `nativequeue`
task) and the build script (`tools\build-win.ps1` compiles `tmt70mon.dll`
via a C toolchain, failing loudly rather than silently skipping it — pass
`-SkipMonitor` to opt out of the requirement).

## Not yet done

- Phase 2 (image/graphics parity) — exploratory only, not started; see the
  plan this was built from.
- No port-properties UI (`ConfigurePort` just points at the registry key).
- Only tested on one machine/Windows build so far (10.0.26100, GPO-managed
  execution policy but not domain-joined) — see `spike/NOTES.md` for what a
  second, more locked-down test configuration should additionally check.
