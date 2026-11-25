"""Pick a backend for this machine and start it.

The scene is the same everywhere (see scene.py). What differs is where the picture
goes, which is all a backend does:

    Windows   a child window of the desktop, under the icons    (verified)
    macOS     an NSWindow just below the icon window level      (UNVERIFIED)
"""
import sys


def usage():
    print(__doc__.strip())
    print("""
options
  --fps N          frame rate, default 24
  --bare           just the fish and its anemone, no sand, rocks or corals
  --seed N         the same fish every time
  --monitor N      one screen only, numbered as the self-check lists them
  --no-gpu         force the CPU renderer (Windows)
  --overlay        transparent, so your own wallpaper shows through (Windows)
  --solid          opaque, over the desktop icons (Windows)
""".rstrip())


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--help" in argv or "-h" in argv:
        usage()
        return 0

    if sys.platform == "win32":
        from .wallpaper import main as run_windows
        return run_windows(argv)

    if sys.platform == "darwin":
        fps, seed, index, bare = 24, None, None, False
        i = 0
        while i < len(argv):
            a = argv[i]
            if a == "--fps" and i + 1 < len(argv):
                fps = int(argv[i + 1]); i += 2
            elif a == "--seed" and i + 1 < len(argv):
                seed = int(argv[i + 1]); i += 2
            elif a == "--monitor" and i + 1 < len(argv):
                index = int(argv[i + 1]); i += 2
            elif a == "--bare":
                bare = True; i += 1
            else:
                i += 1
        try:
            from .macos_backend import run as run_macos
        except ImportError as exc:
            print("The macOS backend needs PyObjC: %s" % exc)
            print("Run ./setup.sh, or: pip install pyobjc-framework-Cocoa "
                  "pyobjc-framework-Quartz")
            return 1
        return run_macos(index=index, fps=fps, seed=seed, bare=bare)

    print("No backend for %s yet." % sys.platform)
    print("The renderer itself is portable; what is missing is a way to put a window")
    print("behind the desktop icons on this platform. On X11 that is usually an")
    print("override-redirect window at the bottom of the stack, or drawing to the root.")
    return 1
