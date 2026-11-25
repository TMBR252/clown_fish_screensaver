#!/usr/bin/env python3
"""Start the tank, printing to the terminal. Used on macOS; works anywhere.

On Windows prefer run.pyw, which hides the console and writes to a log instead.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tank.app import main   # noqa: E402

raise SystemExit(main())
