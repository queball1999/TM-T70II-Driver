# Phase 2 — image/graphics parity research spike: findings

Exploratory only, per the plan (`C:\Users\Quynn\.claude\plans\lazy-stirring-ladybug.md`)
— not committed scope. Goal: can a Windows-inbox driver be paired with our
existing custom Port Monitor (`win/monitor/tmt70mon/`) to get real
GDI-rendered raster/image bytes at `WritePort`, matching what CUPS + the PPD
already gives on Linux, the way "Generic / Text Only" gives us text?

## What was tried

1. **Driver survey.** `C:\Windows\inf\ntprint4.inf` (the modern v4 print
   driver core) references render filters including `PWGRRenderFilter.dll`
   (PWG Raster — structurally close to the CUPS Raster v3 format
   `win/tmt70win/job.py::raster_stream()` already builds/parses) and
   `PCLmRenderFilter.dll`. This machine already has "Microsoft IPP Class
   Driver", "Universal Print Class Driver", and "Microsoft Print To PDF"
   installed — all plausible candidates for actually rasterizing GDI output
   rather than just passing through plain text.

2. **Attach one of those drivers to our existing port** (`TMT70:`, already
   working for "Generic / Text Only"), first via `Add-Printer` (fails,
   `HRESULT 0x80070709`), then — suspecting a CIM/`MSFT_Printer` provider
   quirk, since `Get-PrinterPort` on our port fails the same way (see
   `spike/NOTES.md`) — via the raw Win32 `AddPrinter` API directly
   (`win/monitor/phase2/raw-addprinter-test.ps1`), bypassing CIM entirely.
   Same failure, same underlying code: **Win32 error 1801
   (`ERROR_INVALID_PRINTER_NAME`)**.

3. **Controlled the experiment**: ran the identical raw `AddPrinter` call
   (same `PRINTER_INFO_2`, same `PrintProcessor="winprint"`,
   `Datatype="RAW"`) against "Generic / Text Only" instead — **succeeded**,
   ruling out the test harness itself as the cause.

Tested against: "Microsoft IPP Class Driver", "Universal Print Class
Driver", "Microsoft Print To PDF" — **all three failed identically**;
"Generic / Text Only" succeeded via the exact same code path.

## Conclusion

Every actually-useful (raster- or vector-capable) inbox driver available on
this machine refuses to bind to an arbitrary local port via the classic
`AddPrinter` API — including "Microsoft Print To PDF", a driver known to work
correctly elsewhere on this exact system, which rules out a driver-specific
quirk in favor of a broader pattern. The likely explanation: these are all
modern "class driver" (v4) packages designed to be provisioned through
IPP/Universal-Print discovery flows or a fixed intrinsic port (PDF's
`PORTPROMPT:`), not through manual local queue creation the way legacy
monolithic (v3) drivers like "Generic / Text Only" support. That provisioning
surface is a substantially different, harder API area (`InstallPrinterDriverFromPackage`-style
flows) than a Port Monitor plugs into at all — a Port Monitor only ever sees
bytes after a queue already exists and a driver has already rendered a job to
it.

**This is not proof no path exists** (only three candidates were tried, on
one Windows build), but it's a real, controlled negative result against the
most promising available options, not a configuration mistake.

## Ideas not yet tried (if someone picks this back up)

- Investigate `InstallPrinterDriverFromPackage` / the modern printer
  provisioning APIs directly, to see if a v4 class driver can be attached to
  a custom port through that surface instead of `AddPrinter`.
- Try `rundll32 printui.dll,PrintUIEntry /if` (the legacy UI-driven install
  path) in case it exercises a different internal code path than either
  `AddPrinter` or the CIM cmdlets.
- Look for any *v3-style* (non-class-driver) raster-capable driver still
  inboxed on Windows — this Windows 11 24H2 build's `ntprint.inf` no longer
  lists the legacy Epson/IBM ESC/P dot-matrix "Generic" drivers that older
  Windows versions shipped (grepped for "IBM", "ProPrinter", "Epson",
  "9-pin"/"24-pin", "dot matrix", "Generic" — zero hits), so that specific
  historical lead is dead on this build; worth checking whether an older
  Windows version still ships one, purely for architectural reference (not
  something this project would ship, but useful to know if any inbox v3
  raster driver still exists anywhere).
- Consider whether a **custom Print Processor** (a separate spooler
  extension from a Port Monitor, sitting between rendering and the port)
  paired with an existing driver could get further than a Port Monitor
  alone — this was flagged as a possible second unknown in the original
  plan's Phase 2 section and wasn't investigated here at all.

## Files

- `raw-addprinter-test.ps1` — the raw-API probe script used above (throwaway,
  not shipped). `-DriverName <name>` to try a different candidate,
  `-Remove` to tear down the test queue.
