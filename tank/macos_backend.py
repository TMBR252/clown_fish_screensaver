"""The macOS backend: a window per screen, sitting behind the desktop icons.

    UNVERIFIED. This was written from Apple's documentation and from several working
    open-source live-wallpaper projects, but it has never been run: it was developed on
    Windows, with no Mac to test on. Treat every line as a starting point. The places
    most likely to be wrong are marked "CHECK:" and the self-check reports each one.

How it is meant to work
-----------------------
macOS has no WorkerW. Instead every window has a *level*, and the desktop picture and
the Finder's icons sit at known levels. A borderless window placed between them appears
behind the icons and in front of the wallpaper, which is what we want.

    kCGDesktopWindowLevel        the desktop picture itself
    kCGDesktopIconWindowLevel    the Finder's icons
    ... and we sit at kCGDesktopIconWindowLevel - 1

CHECK: projects disagree about whether kCGDesktopWindowLevel alone is enough. Several
report that a window there is at the same level as the picture and may not be drawn, so
this uses the icon level minus one, which is what the more recently maintained ones do.
If nothing appears, try other offsets: DESKTOP_LEVEL_OFFSET below.

OpenGL on macOS
---------------
Deprecated but present, and capped at 4.1. Only forward-compatible *core* profiles are
offered for 3.x and up, and a 3.2 core context only gives GLSL 1.50 -- too old for our
`#version 330` shaders. So we must ask for 4.1 core, which does provide GLSL 330.
"""
import sys
import time

if sys.platform != "darwin":                      # pragma: no cover - guard
    raise ImportError("the macOS backend only loads on macOS")

import objc                                        # noqa: E402
from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,  # noqa: E402
                    NSBackingStoreBuffered, NSBorderlessWindowMask, NSColor,
                    NSEvent, NSOpenGLContext, NSOpenGLPixelFormat, NSScreen, NSView,
                    NSWindow, NSWindowCollectionBehaviorCanJoinAllSpaces,
                    NSWindowCollectionBehaviorIgnoresCycle,
                    NSWindowCollectionBehaviorStationary)
from Foundation import NSAutoreleasePool, NSDate, NSMakeRect  # noqa: E402
import Quartz                                      # noqa: E402

from .scene import Tank, cursor_for, moved_enough   # noqa: E402

# how far below the icon level to sit; see the note above
DESKTOP_LEVEL_OFFSET = -1

# NSOpenGLPixelFormatAttribute values. PyObjC does not export all of them reliably,
# so they are spelled out; they are stable parts of the public headers.
NSOpenGLPFAAccelerated = 73
NSOpenGLPFADoubleBuffer = 5
NSOpenGLPFAColorSize = 8
NSOpenGLPFAAlphaSize = 11
NSOpenGLPFADepthSize = 12
NSOpenGLPFAOpenGLProfile = 99
NSOpenGLProfileVersion4_1Core = 0x4100           # CHECK: 3.2 core is too old for GLSL 330


class Screen(object):
    """One display, in top-left-origin pixels like every other platform here."""

    def __init__(self, index, nsscreen, primary_top):
        self.index = index
        self.ns = nsscreen
        f = nsscreen.frame()
        self.w = int(f.size.width)
        self.h = int(f.size.height)
        self.x = int(f.origin.x)
        # Cocoa measures y upwards from the bottom of the primary screen; everything
        # else in this project measures downwards from the top. Convert once, here.
        self.y = int(primary_top - (f.origin.y + f.size.height))
        self.frame = f
        self.primary = index == 0
        self.name = str(nsscreen.localizedName()) if hasattr(nsscreen, "localizedName") \
            else "screen %d" % index

    @property
    def rect(self):
        return (self.x, self.y, self.w, self.h)


def screens():
    """Every display, primary first."""
    all_screens = list(NSScreen.screens())
    if not all_screens:
        return []
    primary_top = all_screens[0].frame().origin.y + all_screens[0].frame().size.height
    return [Screen(i, s, primary_top) for i, s in enumerate(all_screens)]


def pointer(primary_top):
    """The mouse, in top-left-origin virtual pixels, or None."""
    p = NSEvent.mouseLocation()
    return (float(p.x), float(primary_top - p.y))


def desktop_level():
    """The window level that puts us under the icons and over the picture."""
    icons = Quartz.CGWindowLevelForKey(Quartz.kCGDesktopIconWindowLevelKey)
    return int(icons) + DESKTOP_LEVEL_OFFSET


class Surface(object):
    """A borderless window on one screen, with an OpenGL context on it."""

    def __init__(self, screen):
        self.screen = screen
        rect = NSMakeRect(screen.frame.origin.x, screen.frame.origin.y,
                          screen.w, screen.h)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_screen_(
            rect, NSBorderlessWindowMask, NSBackingStoreBuffered, False, screen.ns)
        self.window.setLevel_(desktop_level())
        self.window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle)
        self.window.setIgnoresMouseEvents_(True)       # clicks reach the desktop
        self.window.setOpaque_(True)
        self.window.setBackgroundColor_(NSColor.blackColor())
        self.window.setHasShadow_(False)
        self.window.setReleasedWhenClosed_(False)

        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, screen.w, screen.h))
        self.window.setContentView_(view)
        self.view = view

        attrs = [NSOpenGLPFAAccelerated,
                 NSOpenGLPFADoubleBuffer,
                 NSOpenGLPFAColorSize, 24,
                 NSOpenGLPFAAlphaSize, 8,
                 NSOpenGLPFADepthSize, 24,
                 NSOpenGLPFAOpenGLProfile, NSOpenGLProfileVersion4_1Core,
                 0]
        fmt = NSOpenGLPixelFormat.alloc().initWithAttributes_(attrs)
        if fmt is None:
            raise RuntimeError("no OpenGL 4.1 core pixel format on this Mac")
        self.gl = NSOpenGLContext.alloc().initWithFormat_shareContext_(fmt, None)
        if self.gl is None:
            raise RuntimeError("could not make an OpenGL context")
        self.gl.setView_(view)
        self.gl.makeCurrentContext()
        # swap interval 0: we pace ourselves, as on Windows
        try:
            self.gl.setValues_forParameter_([0], 222)   # NSOpenGLCPSwapInterval
        except Exception:
            pass

        import moderngl
        self.ctx = moderngl.create_context(require=330)
        self.window.orderBack_(None)
        self.window.setIsVisible_(True)

    def make_current(self):
        self.gl.makeCurrentContext()

    def present(self):
        self.gl.flushBuffer()

    def release(self):
        try:
            if self.ctx is not None:
                self.ctx.release()
        except Exception:
            pass
        self.ctx = None
        try:
            NSOpenGLContext.clearCurrentContext()
            self.window.orderOut_(None)
        except Exception:
            pass


def run(index=None, fps=24, seed=None, bare=False):
    """Run tanks on every screen (or just one) until interrupted.

    Unlike Windows, this draws every screen from a single process: macOS wants its UI
    on the main thread, and one process per screen would mean one NSApplication each.
    CHECK: with several large screens that may not keep up, in which case the frame
    rate is the dial to turn.
    """
    pool = NSAutoreleasePool.alloc().init()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)   # no Dock icon

    found = screens()
    if not found:
        print("no screens found")
        return 1
    if index is not None:
        if not 0 <= index < len(found):
            print("no screen %d: this Mac has %d" % (index, len(found)))
            return 1
        found = [found[index]]

    primary = NSScreen.screens()[0].frame()
    primary_top = primary.origin.y + primary.size.height

    print("macOS backend (UNVERIFIED - please report what happens)")
    print("  window level : %d  (icons %d, offset %d)"
          % (desktop_level(),
             Quartz.CGWindowLevelForKey(Quartz.kCGDesktopIconWindowLevelKey),
             DESKTOP_LEVEL_OFFSET))
    tanks = []
    for s in found:
        tank = Tank(s.w, s.h, seed=seed, bare=bare)
        surface = Surface(s)
        tank.use_gl(surface.ctx)
        print("  screen %d     : %s %dx%d at (%d,%d)  GL %s"
              % (s.index, s.name, s.w, s.h, s.x, s.y, surface.ctx.version_code))
        tanks.append((s, tank, surface))

    period = 1.0 / fps
    last = time.perf_counter()
    was = None
    running = True
    forever = NSDate.distantPast()
    try:
        while running:
            loop_pool = NSAutoreleasePool.alloc().init()
            while True:                       # drain pending events, never block
                event = app.nextEventMatchingMask_untilDate_inMode_dequeue_(
                    0xFFFFFFFF, forever, "kCFRunLoopDefaultMode", True)
                if event is None:
                    break
                app.sendEvent_(event)

            now = time.perf_counter()
            dt = min(now - last, 0.2)
            last = now
            here = pointer(primary_top)
            moved = moved_enough(was, here)
            was = here

            for s, tank, surface in tanks:
                surface.make_current()
                tank.step(dt, cursor_for(s.rect, here), moved)
                tank.present_gl(surface.ctx)
                surface.present()

            del loop_pool
            spare = period - (time.perf_counter() - now)
            if spare > 0:
                time.sleep(spare)
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        for _, _, surface in tanks:
            surface.release()
        del pool
    return 0
