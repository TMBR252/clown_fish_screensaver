"""Report whether this machine can run the tank, and how well.

Run by setup, and worth running on its own if something looks wrong. It only reports;
it changes nothing. Exit code 0 means the tank should work.
"""
import ctypes
import os
import platform
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

OK, WARN, BAD = "  ok  ", " note ", " BAD  "


def line(mark, text):
    print("%s %s" % (mark, text))


def main():
    problems = 0
    notes = 0

    print("")
    print("  %s   %s" % (platform.system(), platform.release()))
    try:
        build = sys.getwindowsversion()
        print("  build %s.%s" % (build.major, build.build))
    except Exception:
        pass
    print("  python %s" % platform.python_version())
    print("")

    if sys.platform == "win32":
        backend = "Windows (verified)"
    elif sys.platform == "darwin":
        backend = "macOS (UNVERIFIED: never run on a Mac, please report)"
    else:
        line(BAD, "no backend for %s: the renderer is portable, but nothing here"
                  " knows how to get behind this desktop" % sys.platform)
        return 1
    line(OK, "backend: %s" % backend)

    # --- the libraries -------------------------------------------------------------
    try:
        import numpy
        line(OK, "numpy %s" % numpy.__version__)
    except Exception as exc:
        line(BAD, "numpy missing (%s)" % exc)
        problems += 1
    try:
        import PIL
        line(OK, "pillow %s" % PIL.__version__)
    except Exception as exc:
        line(BAD, "pillow missing (%s)" % exc)
        problems += 1

    gpu = False
    try:
        import moderngl                                     # noqa: F401
        line(OK, "moderngl %s" % moderngl.__version__)
        gpu = True
    except Exception as exc:
        line(WARN, "moderngl missing (%s)" % exc)
        line(WARN, "  the tank will fall back to the CPU renderer, which is far slower")
        notes += 1

    # --- can we actually get an OpenGL context? ------------------------------------
    if gpu:
        try:
            from tank import glctx
            ctx = glctx.OffscreenContext()
            info = ctx.ctx.info
            line(OK, "OpenGL %s on %s" % (ctx.ctx.version_code, info["GL_RENDERER"]))
            if ctx.ctx.version_code < 330:
                line(BAD, "  OpenGL 3.3 is the minimum; this machine is older")
                problems += 1
            ctx.release()
        except Exception as exc:
            line(WARN, "no OpenGL context (%s)" % exc)
            line(WARN, "  the tank will fall back to the CPU renderer")
            notes += 1

    # --- a monospace font ----------------------------------------------------------
    try:
        from tank import render
        render.atlas()
        line(OK, "monospace font found")
    except Exception as exc:
        line(BAD, "no usable monospace font (%s)" % exc)
        problems += 1

    # --- the desktop --------------------------------------------------------------
    try:
        if sys.platform == "darwin":
            from tank import macos_backend as wp
            mons = wp.screens()
            line(OK, "window level %d (icons are at %d)"
                 % (wp.desktop_level(),
                    wp.Quartz.CGWindowLevelForKey(wp.Quartz.kCGDesktopIconWindowLevelKey)))
        else:
            from tank import wallpaper as wp
            wp.dpi_aware()
            mons = wp.monitors()
        line(OK, "%d screen%s:" % (len(mons), "" if len(mons) == 1 else "s"))
        for i, m in enumerate(mons):
            print("         %d  %-14s %dx%d at (%d,%d)%s"
                  % (i, m.name, m.w, m.h, m.x, m.y, "  primary" if m.primary else ""))
        if len(mons) > 4:
            line(WARN, "more than 4 screens: one process each, so watch the CPU")
            notes += 1

        if sys.platform == "win32":
            pm = wp.user32.FindWindowW("Progman", None)
            if pm:
                line(OK, "desktop found (Progman)")
            else:
                line(BAD, "no Progman window: is Explorer running as the shell?")
                problems += 1
    except Exception as exc:
        line(BAD, "could not read the desktop (%s)" % exc)
        problems += 1

    print("")
    if problems:
        print("  %d problem(s). The tank may not work here." % problems)
        return 1
    if notes:
        print("  Usable, with the notes above.")
    else:
        print("  All good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
