#!/usr/bin/env python3
"""tmt70print -- print to an EPSON TM-T70II on Windows.

SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tmt70win.cli import main   # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
