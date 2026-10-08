"""
sinks -- where the ESC/POS bytes go on Windows.

SPDX-License-Identifier: GPL-2.0-or-later

  tmt70://04b8:0202[/serial]   WinUSB, via the unmodified Linux backend + libusb-1.0.dll
  socket://host[:9100]         raw TCP (UB-E04, serial-to-Ethernet)
  file://C:\\path\\out.bin       dump the stream
  printer://<queue name>       RAW write through the Windows spooler

The USB and socket sinks are src/backend/tmt70's own functions, so retry
behaviour, timeouts and logging match the Linux backend exactly.  Only the
spooler sink is Windows-specific.
"""

import os
import re
import sys

from . import core

__all__ = ["send", "parse_uri", "discover", "list_windows_printers", "SinkError"]

OK = 0


class SinkError(RuntimeError):
    pass


_USB_RE = re.compile(r"^(?:tmt70|usb)://([0-9a-fA-F]{4}):([0-9a-fA-F]{4})(?:/(.+))?$")


def parse_uri(uri):
    """('usb'|'socket'|'file'|'printer', payload) for a device URI."""
    u = (uri or "").strip()
    if not u:
        return ("usb", None)

    m = _USB_RE.match(u)
    if m:
        return ("usb", m.group(3))                       # serial or None
    if u in ("usb://", "usb:", "tmt70://", "tmt70:"):
        return ("usb", None)

    for scheme in ("socket://", "tcp://"):
        if u.startswith(scheme):
            rest = u[len(scheme):]
            host, _, port = rest.partition(":")
            return ("socket", (host, int(port or 9100)))

    if u.startswith("file://"):
        return ("file", u[len("file://"):])
    if u.startswith("printer://") or u.startswith("winspool://"):
        return ("printer", u.split("://", 1)[1])

    # a bare path or queue name
    if os.path.splitdrive(u)[0] or u.startswith("\\\\") or os.sep in u:
        return ("file", u)
    return ("printer", u)


# --------------------------------------------------------------------------- #
# Windows spooler (RAW)
# --------------------------------------------------------------------------- #

def _winspool():
    if os.name != "nt":
        raise SinkError("the spooler sink is only available on Windows")
    import ctypes
    from ctypes import wintypes

    ws = ctypes.WinDLL("winspool.drv", use_last_error=True)

    class DOC_INFO_1(ctypes.Structure):
        _fields_ = [("pDocName", wintypes.LPWSTR),
                    ("pOutputFile", wintypes.LPWSTR),
                    ("pDatatype", wintypes.LPWSTR)]

    ws.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE),
                                ctypes.c_void_p]
    ws.OpenPrinterW.restype = wintypes.BOOL
    ws.StartDocPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                    ctypes.POINTER(DOC_INFO_1)]
    ws.StartDocPrinterW.restype = wintypes.DWORD
    ws.StartPagePrinter.argtypes = [wintypes.HANDLE]
    ws.StartPagePrinter.restype = wintypes.BOOL
    ws.WritePrinter.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD)]
    ws.WritePrinter.restype = wintypes.BOOL
    ws.EndPagePrinter.argtypes = [wintypes.HANDLE]
    ws.EndPagePrinter.restype = wintypes.BOOL
    ws.EndDocPrinter.argtypes = [wintypes.HANDLE]
    ws.EndDocPrinter.restype = wintypes.BOOL
    ws.ClosePrinter.argtypes = [wintypes.HANDLE]
    ws.ClosePrinter.restype = wintypes.BOOL
    return ctypes, wintypes, ws, DOC_INFO_1


def sink_printer(queue, data, doc_name="TM-T70II ESC/POS"):
    """Write raw bytes to a Windows print queue (needs a pass-through driver)."""
    ctypes, wintypes, ws, DOC_INFO_1 = _winspool()

    handle = wintypes.HANDLE()
    if not ws.OpenPrinterW(queue, ctypes.byref(handle), None):
        raise SinkError(f"OpenPrinter({queue!r}) failed: "
                        f"error {ctypes.get_last_error()}")
    try:
        info = DOC_INFO_1(doc_name, None, "RAW")
        job = ws.StartDocPrinterW(handle, 1, ctypes.byref(info))
        if not job:
            raise SinkError(f"StartDocPrinter failed: error {ctypes.get_last_error()}")
        try:
            if not ws.StartPagePrinter(handle):
                raise SinkError(f"StartPagePrinter failed: "
                                f"error {ctypes.get_last_error()}")
            written = wintypes.DWORD(0)
            buf = ctypes.create_string_buffer(bytes(data), len(data))
            if not ws.WritePrinter(handle, buf, len(data), ctypes.byref(written)):
                raise SinkError(f"WritePrinter failed: error {ctypes.get_last_error()}")
            if written.value != len(data):
                raise SinkError(f"short write: {written.value} of {len(data)} bytes")
            ws.EndPagePrinter(handle)
        finally:
            ws.EndDocPrinter(handle)
    finally:
        ws.ClosePrinter(handle)
    return OK


def list_windows_printers():
    """Names of installed Windows print queues (local and connected)."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    ws = ctypes.WinDLL("winspool.drv", use_last_error=True)

    class PRINTER_INFO_4W(ctypes.Structure):
        _fields_ = [("pPrinterName", wintypes.LPWSTR),
                    ("pServerName", wintypes.LPWSTR),
                    ("Attributes", wintypes.DWORD)]

    PRINTER_ENUM_LOCAL, PRINTER_ENUM_CONNECTIONS = 0x02, 0x04
    flags = PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS

    needed = wintypes.DWORD(0)
    returned = wintypes.DWORD(0)
    ws.EnumPrintersW(flags, None, 4, None, 0,
                     ctypes.byref(needed), ctypes.byref(returned))
    if not needed.value:
        return []
    buf = ctypes.create_string_buffer(needed.value)
    if not ws.EnumPrintersW(flags, None, 4, buf, needed,
                            ctypes.byref(needed), ctypes.byref(returned)):
        return []
    arr = ctypes.cast(buf, ctypes.POINTER(PRINTER_INFO_4W))
    return [arr[i].pPrinterName for i in range(returned.value)]


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #

def send(uri, data, doc_name="TM-T70II ESC/POS"):
    """Deliver `data` to `uri`. Returns a CUPS-style exit code (0 = OK)."""
    kind, payload = parse_uri(uri)

    if kind == "file":
        b = core.backend_mod()
        return b.sink_file(payload, data)

    if kind == "socket":
        b = core.backend_mod()
        host, port = payload
        return b.sink_socket(host, port, data)

    if kind == "printer":
        return sink_printer(payload, data, doc_name)

    b = core.backend_mod()
    return b.sink_usb(payload, data)


def discover():
    """Report reachable devices: USB units plus Windows queues."""
    found = []
    try:
        b = core.backend_mod()
        lu = b.LibUsb()
        try:
            for vid, pid, serial in lu.find():
                uri = f"tmt70://{vid:04x}:{pid:04x}"
                if serial:
                    uri += f"/{serial}"
                found.append(("usb", uri, f"EPSON TM printer {vid:04x}:{pid:04x}"
                                          + (f" s/n {serial}" if serial else "")))
        finally:
            lu.close()
    except Exception as e:                                  # noqa: BLE001
        found.append(("usb", None, f"USB scan unavailable: {e}"))

    for name in list_windows_printers():
        found.append(("printer", f"printer://{name}", "Windows queue"))
    return found
