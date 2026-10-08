"""
core -- load the Linux filter and backend as importable modules.

SPDX-License-Identifier: GPL-2.0-or-later

The two programs in src/ have no .py extension (they are CUPS executables), so
they are loaded by path with importlib.  Both guard their entry point with
`if __name__ == "__main__"`, so importing them is side-effect free.

Nothing in src/ is modified.  The ESC/POS encoder, the bitmap transforms, the
option parser and the USB/socket/file sinks are therefore shared byte-for-byte
between the Linux .deb and this Windows package.

The one Windows accommodation is libusb: src/backend/tmt70 asks ctypes for
"usb-1.0" and falls back to "libusb-1.0.so.0".  On Windows the DLL is named
libusb-1.0.dll, so find_library() is shimmed for that one name before the
backend is imported.
"""

import ctypes.util
import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

__all__ = [
    "filter_mod", "backend_mod", "mkraster_mod", "libusb_path", "search_roots",
    "FilterMissing",
]


class FilterMissing(RuntimeError):
    """src/filter/rastertoescpos or src/backend/tmt70 could not be located."""


# --------------------------------------------------------------------------- #
# where the shared sources may live
# --------------------------------------------------------------------------- #

def search_roots():
    """Directories that may hold the shared sources, most specific first.

    Covers three layouts:
      * repo checkout      <repo>/win/tmt70win/core.py  -> <repo>/src/...
      * installed package  C:\\Program Files\\...\\src\\...
      * frozen exe         <exedir>/src/...
    """
    roots = []

    def add(p):
        p = Path(p).resolve()
        if p not in roots:
            roots.append(p)

    here = Path(__file__).resolve().parent          # ...\win\tmt70win
    add(here.parent)                                # ...\win
    add(here.parent.parent)                         # repo root / install root
    if getattr(sys, "frozen", False):
        add(Path(sys.executable).parent)
    env = os.environ.get("TMT70_HOME")
    if env:
        add(env)
    add(Path.cwd())
    return roots


def _find(relpath):
    """Locate a shared source file under any search root."""
    rel = Path(relpath)
    for root in search_roots():
        cand = root / rel
        if cand.is_file():
            return cand
        # installed layout keeps the basename only, flattened next to the package
        flat = root / rel.name
        if flat.is_file():
            return flat
    raise FilterMissing(
        f"cannot find {relpath}.  Looked under: "
        + ", ".join(str(r) for r in search_roots())
        + ".  Set TMT70_HOME to the directory holding src\\."
    )


def _load(modname, path):
    """Import a extension-less Python program as a module."""
    if modname in sys.modules:
        return sys.modules[modname]
    loader = importlib.machinery.SourceFileLoader(modname, str(path))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    try:
        loader.exec_module(mod)
    except Exception:
        del sys.modules[modname]
        raise
    return mod


# --------------------------------------------------------------------------- #
# libusb-1.0.dll
# --------------------------------------------------------------------------- #

def libusb_path():
    """Absolute path to a bundled libusb-1.0.dll, or None to use the system one.

    Looked for in <root>\\vendor\\libusb-1.0.dll and <root>\\libusb-1.0.dll.
    """
    names = ("libusb-1.0.dll", "usb-1.0.dll")
    for root in search_roots():
        for sub in ("vendor", "."):
            for name in names:
                cand = (root / sub / name).resolve()
                if cand.is_file():
                    return cand
    return None


_shim_installed = False


def _install_libusb_shim():
    """Make find_library("usb-1.0") resolve to the bundled libusb-1.0.dll."""
    global _shim_installed
    if _shim_installed or os.name != "nt":
        return
    dll = libusb_path()
    if dll is None:
        return                                  # fall through to the system loader
    original = ctypes.util.find_library

    def find_library(name):
        if name == "usb-1.0":
            return str(dll)
        return original(name)

    ctypes.util.find_library = find_library
    # let the DLL find its own neighbours (Python 3.8+ ignores PATH for DLLs)
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(str(dll.parent))
        except OSError:
            pass
    _shim_installed = True


# --------------------------------------------------------------------------- #
# the two shared modules
# --------------------------------------------------------------------------- #

_filter = None
_backend = None
_mkraster = None


def filter_mod():
    """src/filter/rastertoescpos -- EscposOut, CupsRaster, bitmap ops, options."""
    global _filter
    if _filter is None:
        _filter = _load("tmt70_filter", _find("src/filter/rastertoescpos"))
    return _filter


def backend_mod():
    """src/backend/tmt70 -- LibUsb, sink_usb, sink_socket, sink_file, discover."""
    global _backend
    if _backend is None:
        _install_libusb_shim()
        _backend = _load("tmt70_backend", _find("src/backend/tmt70"))
    return _backend


def mkraster_mod():
    """tools/mkraster.py -- the CUPS raster v3 header offsets.

    Imported rather than copied so the header layout cannot drift away from the
    one the Linux self-test exercises.
    """
    global _mkraster
    if _mkraster is None:
        _mkraster = _load("tmt70_mkraster", _find("tools/mkraster.py"))
    return _mkraster
