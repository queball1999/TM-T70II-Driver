"""
cli -- tmt70print command line.

SPDX-License-Identifier: GPL-2.0-or-later

Options mirror the PPD keywords one-for-one, so a command line here and an
`lp -o ...` on Linux describe the same job.
"""

import argparse
import configparser
import os
import sys

from . import core, job, sinks, text

__all__ = ["main", "build_parser"]

VERSION = "1.0.0"

_CONFIG_KEYS = {
    "device": str, "media": str, "density": str, "speed": str, "cut": str,
    "feed": str, "margin": str, "reduce": str, "codepage": str,
    "copies": int, "rotate180": bool, "align": str, "cols": int,
    "encoding": str, "threshold": int, "dither": bool, "invert": bool,
    "scale": bool, "wrap": bool, "tabstop": int, "reflow": bool,
}


def config_paths(explicit=None):
    if explicit:
        return [explicit]
    out = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        out.append(os.path.join(appdata, "tmt70print", "tmt70print.ini"))
    for root in core.search_roots():
        out.append(str(root / "tmt70print.ini"))
    return out


def load_config(explicit=None):
    """Read [defaults] from the first config file that exists."""
    for path in config_paths(explicit):
        if not os.path.isfile(path):
            continue
        cp = configparser.ConfigParser()
        try:
            cp.read(path, encoding="utf-8")
        except (OSError, configparser.Error) as e:
            sys.stderr.write(f"WARNING: {path}: {e}\n")
            continue
        if not cp.has_section("defaults"):
            return {}, path
        out = {}
        for key, kind in _CONFIG_KEYS.items():
            if not cp.has_option("defaults", key):
                continue
            try:
                if kind is bool:
                    out[key] = cp.getboolean("defaults", key)
                elif kind is int:
                    out[key] = cp.getint("defaults", key)
                else:
                    out[key] = cp.get("defaults", key)
            except ValueError as e:
                sys.stderr.write(f"WARNING: {path}: {key}: {e}\n")
        return out, path
    return {}, None


def build_parser():
    p = argparse.ArgumentParser(
        prog="tmt70print",
        description="Print to an EPSON TM-T70II over WinUSB, TCP or the Windows "
                    "spooler. Options match the Linux PPD keywords.",
        epilog="Examples:\n"
               "  tmt70print receipt.txt --density dark --cut full\n"
               "  tmt70print logo.png --media X58mm --threshold 140\n"
               "  tmt70print receipt.txt --dry-run -o out.bin\n"
               "  tmt70print --list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("files", nargs="*", metavar="FILE",
                   help="input files; '-' reads standard input")

    g = p.add_argument_group("destination")
    g.add_argument("-d", "--device", metavar="URI",
                   help="tmt70://04b8:0202[/serial] (default), socket://host:9100, "
                        "printer://<queue>, file://path")
    g.add_argument("-o", "--out", metavar="PATH",
                   help="also write the raw ESC/POS stream here")
    g.add_argument("--dry-run", action="store_true",
                   help="build the stream but do not send it")

    g = p.add_argument_group("job options (same names as the PPD)")
    g.add_argument("--media", default=None, metavar="SIZE",
                   help="X80mm (default), X80mmLong, X58mm")
    g.add_argument("--density", choices=["light", "normal", "dark"])
    g.add_argument("--speed", metavar="N", help="auto or 1-4")
    g.add_argument("--cut", choices=["partial", "full", "none"])
    g.add_argument("--feed", metavar="MM", help="blank feed before the cut")
    g.add_argument("--margin", metavar="MM", help="left margin")
    g.add_argument("--reduce", choices=["off", "topbottom", "all"],
                   help="blank-space reduction")
    g.add_argument("--codepage", metavar="N", help="0=PC437 2=PC850 16=WPC1252 19=PC858")
    g.add_argument("--rotate180", action="store_true", default=None)
    g.add_argument("-n", "--copies", type=int, default=None)

    g = p.add_argument_group("text input")
    g.add_argument("--align", choices=["left", "center", "centre", "right"])
    g.add_argument("--cols", type=int, default=None,
                   help="characters per line (default: from --media)")
    g.add_argument("--encoding", default=None, help="input encoding (default utf-8)")
    g.add_argument("--tabstop", type=int, default=None)
    g.add_argument("--no-wrap", dest="wrap", action="store_false", default=None,
                   help="do not hard-wrap long lines")
    g.add_argument("--reflow", action="store_true", default=None,
                   help="rejoin consecutive non-blank lines into paragraphs "
                        "before wrapping (blank lines still break); use when "
                        "the source already hard-wrapped at a different width, "
                        "e.g. text printed via a GDI driver like Generic / Text Only")

    g = p.add_argument_group("image input")
    g.add_argument("--no-dither", dest="dither", action="store_false", default=None,
                   help="plain threshold instead of Floyd-Steinberg")
    g.add_argument("--threshold", type=int, default=None, help="0-255, default 128")
    g.add_argument("--invert", action="store_true", default=None)
    g.add_argument("--no-scale", dest="scale", action="store_false", default=None,
                   help="do not scale oversized images down to the paper width")

    g = p.add_argument_group("other")
    g.add_argument("--kind", choices=["auto", "text", "image", "escpos"],
                   default="auto", help="input type (default: sniffed)")
    g.add_argument("--config", metavar="PATH", help="INI file with a [defaults] section")
    g.add_argument("--list", action="store_true", help="list reachable destinations")
    g.add_argument("--show-config", action="store_true",
                   help="print the effective settings and exit")
    g.add_argument("-v", "--verbose", action="store_true")
    g.add_argument("--version", action="version", version=f"tmt70print {VERSION}")
    return p


def _resolve(args, cfg):
    """Command line beats config file beats built-in default."""
    def pick(name, fallback=None):
        val = getattr(args, name, None)
        if val is not None:
            return val
        if name in cfg:
            return cfg[name]
        return fallback

    return {
        "device": pick("device", "tmt70://04b8:0202"),
        "media": pick("media", "X80mm"),
        "density": pick("density"),
        "speed": pick("speed"),
        "cut": pick("cut"),
        "feed": pick("feed"),
        "margin": pick("margin"),
        "reduce": pick("reduce"),
        "codepage": pick("codepage"),
        "rotate180": pick("rotate180", False),
        "copies": pick("copies", 1),
        "align": pick("align", "left"),
        "cols": pick("cols"),
        "encoding": pick("encoding", "utf-8"),
        "tabstop": pick("tabstop", 4),
        "wrap": pick("wrap", True),
        "reflow": pick("reflow", False),
        "dither": pick("dither", True),
        "threshold": pick("threshold", 128),
        "invert": pick("invert", False),
        "scale": pick("scale", True),
    }


def _read(path):
    if path == "-":
        return sys.stdin.buffer.read()
    with open(path, "rb") as fh:
        return fh.read()


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg, cfg_path = load_config(args.config)
    s = _resolve(args, cfg)

    if args.verbose and cfg_path:
        sys.stderr.write(f"INFO: settings from {cfg_path}\n")

    if args.list:
        rows = sinks.discover()
        width = max([len(u or "-") for _, u, _ in rows] + [24])
        for kind, uri, desc in rows:
            print(f"{kind:8} {uri or '-':{width}}  {desc}")
        return 0

    if args.show_config:
        if cfg_path:
            print(f"# config: {cfg_path}")
        for k, v in sorted(s.items()):
            print(f"{k} = {v}")
        return 0

    if not args.files:
        build_parser().print_usage(sys.stderr)
        sys.stderr.write("tmt70print: no input files (use '-' for stdin)\n")
        return 2

    opts = job.Options(
        page_size=s["media"],
        Density=s["density"],
        PrintSpeed=s["speed"],
        EndOfJobCut=s["cut"],
        FeedBeforeCut=s["feed"],
        LeftMargin=s["margin"],
        BlankReduction=s["reduce"],
        CodePage=s["codepage"],
        Rotate180="true" if s["rotate180"] else None,
    )
    opts["_copies"] = str(max(1, int(s["copies"])))

    rc = 0
    for path in args.files:
        try:
            data = _read(path)
        except OSError as e:
            sys.stderr.write(f"ERROR: {path}: {e}\n")
            rc = 1
            continue

        try:
            stream = job.build_job(
                opts, data, kind=args.kind, encoding=s["encoding"],
                align=s["align"], cols=s["cols"], tabstop=s["tabstop"],
                wrap=s["wrap"], reflow=s["reflow"], dither=s["dither"],
                threshold=s["threshold"], invert=s["invert"], scale=s["scale"],
            )
        except Exception as e:                              # noqa: BLE001
            sys.stderr.write(f"ERROR: {path}: {e}\n")
            rc = 1
            continue

        if args.verbose:
            sys.stderr.write(f"INFO: {path}: {len(stream)} bytes of ESC/POS\n")

        if args.out:
            try:
                with open(args.out, "wb") as fh:
                    fh.write(stream)
                if args.verbose:
                    sys.stderr.write(f"INFO: wrote {args.out}\n")
            except OSError as e:
                sys.stderr.write(f"ERROR: {args.out}: {e}\n")
                rc = 1

        if args.dry_run:
            if not args.out:
                sys.stdout.buffer.write(stream)
            continue

        try:
            code = sinks.send(s["device"], stream, doc_name=os.path.basename(path))
        except sinks.SinkError as e:
            sys.stderr.write(f"ERROR: {e}\n")
            rc = 1
            continue
        if code != 0:
            sys.stderr.write(f"ERROR: {path}: delivery failed (backend code {code})\n")
            rc = 1

    return rc
