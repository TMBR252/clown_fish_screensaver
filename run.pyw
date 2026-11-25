"""Start the tank with no console window (pythonw.exe runs .pyw files).

pythonw has no console, so sys.stdout and sys.stderr are None and any print() raises
AttributeError. Everything goes to a log beside this file instead, which is the first
place to look if the fish does not appear. One log per screen, since one process runs
per monitor.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_which = "main"
if "--monitor" in sys.argv:
    _which = sys.argv[sys.argv.index("--monitor") + 1]
_log = open(os.path.join(HERE, "fishtank-%s.log" % _which), "w",
            buffering=1, encoding="utf-8")
sys.stdout = sys.stderr = _log

try:
    from tank.wallpaper import main
    code = main()
except BaseException:
    import traceback
    traceback.print_exc(file=_log)
    raise
raise SystemExit(code)
