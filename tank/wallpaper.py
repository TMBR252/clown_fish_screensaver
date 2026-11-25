"""Putting the tank on the desktop, behind the icons, and keeping it fed with frames.

Windows draws the wallpaper in a window of class WorkerW that the shell owns. Sending
Progman the undocumented message 0x052C makes the shell spawn a second WorkerW that
sits *behind* the desktop icons; living inside it puts the fish under the icons, out
of Alt-Tab, and unable to take focus. This is the trick every animated-wallpaper tool
uses, and it has broken and been repaired across Windows releases, so every step is
checked and there is a named fallback if it fails:

    1. the WorkerW that follows the window owning SHELLDLL_DefView   (behind icons)
    2. a WorkerW child of Progman                                    (behind icons)
    3. Progman itself                                                (behind icons)
    4. an unparented, click-through, bottom-most window              (over icons)

Only the fourth is visibly wrong -- it covers the desktop icons -- so which one was
used is reported rather than left for you to notice.

One process runs per monitor. A single frame costs about 33ms, so two screens in one
thread would mean 15fps; separate processes get a core each and stay smooth. Each owns
one monitor's window, fish and reef, and the window is placed inside WorkerW, whose
client origin is the top-left of the *virtual* desktop -- not of the primary screen,
which matters as soon as a monitor sits left of or above the primary one.

Because the window sits behind everything it can never receive a mouse message. The
cursor is read with GetCursorPos instead, which works regardless of what is on top.
A fish only takes an interest in the cursor while it is on its own screen.
"""
import collections
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

from . import fish as fishmod
from . import glctx, gpu, reef, render, scene_gl, swimmer

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
try:
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
except OSError:
    shcore = None
try:
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
except OSError:
    dwmapi = None
try:
    winmm = ctypes.WinDLL("winmm", use_last_error=True)
except OSError:
    winmm = None

WS_POPUP, WS_VISIBLE, WS_CHILD = 0x80000000, 0x10000000, 0x40000000
WS_CLIPSIBLINGS, WS_CLIPCHILDREN = 0x04000000, 0x02000000
CS_VREDRAW, CS_HREDRAW, CS_OWNDC = 0x0001, 0x0002, 0x0020
WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = 0x08000000, 0x00000080
WS_EX_TRANSPARENT, WS_EX_LAYERED = 0x00000020, 0x00080000
SW_SHOWNOACTIVATE = 4
HWND_BOTTOM = 1
SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0010, 0x0040
LWA_COLORKEY, LWA_ALPHA = 0x01, 0x02
PM_REMOVE = 0x0001
WM_DESTROY, WM_CLOSE, WM_HOTKEY, WM_QUIT = 0x0002, 0x0010, 0x0312, 0x0012
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x4000
SMTO_NORMAL = 0x0000
BI_RGB, DIB_RGB_COLORS, SRCCOPY = 0, 0, 0x00CC0020
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
GW_HWNDNEXT = 2
GA_ROOT = 2
DWMWA_CLOAKED = 14
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW_BIT = 0x00000080
WS_EX_TRANSPARENT_BIT = 0x00000020
MONITORINFOF_PRIMARY = 1
CLASS_NAME = "AsciiClownfishTank"
ERROR_ALREADY_EXISTS = 183

_class_ready = False
# The window class keeps a raw pointer to the WNDPROC trampoline for the life of the
# process. Letting the ctypes callback be collected -- by rebuilding one per window --
# frees that trampoline under the class, and the next message dispatched jumps into
# freed memory and kills the process with no traceback. Hold them forever.
_keep_alive = []

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             ctypes.c_size_t, ctypes.c_ssize_t)
ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.c_ssize_t)


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                     ctypes.POINTER(RECT), ctypes.c_ssize_t)


class MONITORINFOEX(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


# Handles are 64-bit. Without an explicit restype ctypes assumes c_int and silently
# truncates them, which produces handles that look plausible and do nothing.
for _fn, _res, _args in [
    (user32.DefWindowProcW, ctypes.c_ssize_t,
     [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]),
    (user32.FindWindowW, wintypes.HWND, [wintypes.LPCWSTR, wintypes.LPCWSTR]),
    (user32.FindWindowExW, wintypes.HWND,
     [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]),
    (user32.SetParent, wintypes.HWND, [wintypes.HWND, wintypes.HWND]),
    (user32.GetDC, wintypes.HDC, [wintypes.HWND]),
    (user32.ReleaseDC, ctypes.c_int, [wintypes.HWND, wintypes.HDC]),
    (user32.IsWindow, wintypes.BOOL, [wintypes.HWND]),
    (user32.DestroyWindow, wintypes.BOOL, [wintypes.HWND]),
    (user32.PostMessageW, wintypes.BOOL,
     [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]),
    (user32.GetClassNameW, ctypes.c_int, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]),
    (user32.SendMessageTimeoutW, ctypes.c_ssize_t,
     [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
      wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]),
    (user32.SetWindowPos, wintypes.BOOL,
     [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
      ctypes.c_int, ctypes.c_int, wintypes.UINT]),
    (user32.CreateWindowExW, wintypes.HWND,
     [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
      wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]),
    (user32.GetTopWindow, wintypes.HWND, [wintypes.HWND]),
    (user32.GetAncestor, wintypes.HWND, [wintypes.HWND, wintypes.UINT]),
    (user32.GetWindowLongW, ctypes.c_long, [wintypes.HWND, ctypes.c_int]),
    (user32.GetWindow, wintypes.HWND, [wintypes.HWND, wintypes.UINT]),
    (user32.IsWindowVisible, wintypes.BOOL, [wintypes.HWND]),
    (user32.IsIconic, wintypes.BOOL, [wintypes.HWND]),
    (user32.GetWindowRect, wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(RECT)]),
    (gdi32.CreateSolidBrush, wintypes.HBRUSH, [wintypes.DWORD]),
    (ctypes.windll.kernel32.GetModuleHandleW, wintypes.HINSTANCE, [wintypes.LPCWSTR]),
]:
    _fn.restype, _fn.argtypes = _res, _args

gdi32.StretchDIBits.restype = ctypes.c_int
gdi32.StretchDIBits.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.DWORD]


def claim_screen(index):
    """Take the only slot for this screen, or report that someone else has it.

    Without this, starting the launcher twice leaves two tanks per monitor fighting
    over the same window and burning twice the CPU for one visible fish.
    """
    # must be the use_last_error build: ctypes.windll.kernel32 does not capture
    # GetLastError, so the "already exists" answer would be lost
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    handle = k32.CreateMutexW(None, True, "AsciiClownfishTank.screen%d" % index)
    if not handle or ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return None
    return handle          # held for the life of the process; released when it exits


def dpi_aware():
    """Without this a scaled display stretches the window and the glyphs go soft."""
    try:                                   # per-monitor v2, the one that actually behaves
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return "per-monitor v2"
    except Exception:
        pass
    if shcore is not None:
        try:
            shcore.SetProcessDpiAwareness(2)
            return "per-monitor"
        except Exception:
            pass
    user32.SetProcessDPIAware()
    return "system"


# work is the screen minus the taskbar: what a maximised window actually covers
Monitor = collections.namedtuple(
    "Monitor", "name x y w h primary wx wy wr wb")


def monitors():
    """Every screen, primary first then left to right."""
    found = []

    def got(hmon, hdc, rect, lparam):
        info = MONITORINFOEX()
        info.cbSize = ctypes.sizeof(MONITORINFOEX)
        user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        r, k = info.rcMonitor, info.rcWork
        found.append(Monitor(info.szDevice, r.left, r.top, r.right - r.left,
                             r.bottom - r.top, bool(info.dwFlags & MONITORINFOF_PRIMARY),
                             k.left, k.top, k.right, k.bottom))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(got), 0)
    found.sort(key=lambda m: (not m.primary, m.x))
    return found


def _class_of(hwnd):
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def _look_for_host(progman):
    """Whatever wallpaper host already exists, without asking the shell for one."""
    found = []

    def walk(hwnd, _):
        if user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None):
            sibling = user32.FindWindowExW(None, hwnd, "WorkerW", None)
            if sibling:
                found.append(sibling)
        return True

    user32.EnumWindows(ENUMPROC(walk), 0)
    if found:
        return found[0], "WorkerW behind the desktop icons"
    if progman:
        child = user32.FindWindowExW(progman, None, "WorkerW", None)
        if child:
            return child, "WorkerW under Progman"
    return None, None


def _resolve_host():
    """One attempt at finding somewhere to live.

    0x052C is only sent if no host exists yet. Sending it when one already does makes
    the shell tear the existing WorkerW down and build a new one -- which destroys any
    window living inside it, including ours and any sibling tank. Asking twice is what
    breaks this, not the message itself.
    """
    progman = user32.FindWindowW("Progman", None)
    host, how = _look_for_host(progman)
    if host:
        return host, how
    if progman:
        res = ctypes.c_size_t()
        # ask for the split, and only that: lParam 0 hands the WorkerW back and
        # explorer destroys it a moment later, leaving a dead parent
        user32.SendMessageTimeoutW(progman, 0x052C, 0xD, 0x1,
                                   SMTO_NORMAL, 2000, ctypes.byref(res))
        host, how = _look_for_host(progman)
        if host:
            return host, how
        return progman, "Progman itself"
    return None, "no desktop host found"


def defview_host():
    """Inside the icon view itself, beneath the list of icons.

    SHELLDLL_DefView is explorer's own child of Progman and it does composite, unlike
    a WorkerW child from another process. Its SysListView32 is transparent except where
    an icon is, so a sibling placed underneath shows through the gaps -- which is what
    "behind the desktop icons" actually needs.
    """
    pm = user32.FindWindowW("Progman", None)
    if not pm:
        return None, "no Progman"
    dv = user32.FindWindowExW(pm, None, "SHELLDLL_DefView", None)
    return (dv, "inside SHELLDLL_DefView") if dv else (None, "no icon view")


def progman_host():
    """Progman itself. Its own children (the icon view) do composite on builds where
    WorkerW's children do not, so this is worth trying before giving up and covering
    the icons with a top-level window."""
    pm = user32.FindWindowW("Progman", None)
    return (pm, "Progman directly") if pm else (None, "no Progman")


def find_wallpaper_host(wait=0.0):
    """The window to live inside, and a word for which trick worked.

    Started at login we can easily be ahead of the shell: Progman exists but its
    WorkerW does not yet. Taking Progman then would put the tank *over* the desktop
    icons for the whole session, so when there is time to spare we wait for a real
    WorkerW instead. A desktop that is already up answers on the first try.
    """
    deadline = time.monotonic() + wait
    while True:
        host, how = _resolve_host()
        if host is not None and "WorkerW" in how:
            return host, how
        if time.monotonic() >= deadline:
            return host, how
        time.sleep(1.5)


def _cloaked(hwnd):
    """True for a window DWM is hiding, such as a suspended UWP app.

    These are visible by every normal test and cover the whole screen, so without this
    the tank would decide it is hidden and freeze while you are looking at it.
    """
    if dwmapi is None:
        return False
    val = ctypes.c_int(0)
    if dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_CLOAKED,
                                    ctypes.byref(val), ctypes.sizeof(val)) == 0:
        return val.value != 0
    return False


def monitor_covered(mon):
    """Is this screen hidden behind some window?

    Tested against the *work area*, not the full screen. A maximised window stops at the
    taskbar, so nothing ever covers a monitor rect outright and testing that would mean
    never sleeping at all. The strip behind the taskbar is not worth drawing.

    Deliberately conservative otherwise: one visible, un-minimised, un-cloaked window has
    to cover it outright. Guessing wrong this way wastes a little power; guessing wrong
    the other way freezes a fish in plain sight.
    """
    mx, my, right, bottom = mon.wx, mon.wy, mon.wr, mon.wb
    hwnd = user32.GetTopWindow(None)
    while hwnd:
        cl = _class_of(hwnd)
        if cl in ("Progman", "WorkerW"):
            break                       # reached the desktop itself; nothing below hides us
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        # Tool windows and click-through windows are overlays, not applications. The
        # NVIDIA GeForce overlay is a full-screen, visible, un-cloaked tool window that
        # sits over the desktop permanently -- believing it meant this screen was always
        # "covered" and the fish never drew at all.
        overlay = bool(ex & (WS_EX_TOOLWINDOW_BIT | WS_EX_TRANSPARENT_BIT))
        if (user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd)
                and not _cloaked(hwnd) and not overlay):
            r = RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(r)):
                if r.left <= mx and r.top <= my and r.right >= right and r.bottom >= bottom:
                    return True
        hwnd = user32.GetWindow(hwnd, GW_HWNDNEXT)
    return False


DESKTOP_CLASSES = ("Progman", "WorkerW", CLASS_NAME)


def cursor_on_desktop(pt):
    """Is the cursor over bare desktop, rather than over somebody's window?

    The fish should not try to eat a pointer that is busy in another application. Our
    own window is WS_EX_TRANSPARENT, so hit testing falls straight through it to
    whatever is underneath.
    """
    hwnd = user32.WindowFromPoint(pt)
    if not hwnd:
        return False
    root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    return _class_of(root) in DESKTOP_CLASSES


def close_all_tanks():
    """Ask every tank on this desktop to stop, so one hotkey clears all the screens."""
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        return

    def walk(hwnd, _):
        if _class_of(hwnd) == CLASS_NAME:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        return True

    user32.EnumChildWindows(progman, ENUMPROC(walk), 0)


class Wallpaper:
    """One tank, filling one monitor."""

    def __init__(self, monitor=0, fps=24, seed=None, no_gl=False, boot_wait=45.0,
                 always_draw=False, over_icons=False, host_pref='auto',
                 keyed=False, bare=False, icon_margin=170.0):
        self.icon_margin = icon_margin
        self.no_gl = no_gl
        self.keyed = keyed
        self.bare = bare
        self.host_pref = host_pref
        self.over_icons = over_icons
        self.always_draw = always_draw
        self.boot_wait = boot_wait
        self.dpi = dpi_aware()
        mons = monitors()
        if not mons:
            raise RuntimeError("no monitors found")
        if not 0 <= monitor < len(mons):
            raise ValueError("no screen %d: this machine has %d (0..%d)"
                             % (monitor, len(mons), len(mons) - 1))
        self.index = monitor
        self.mon = mons[self.index]
        m = self.mon
        self.name, self.mx, self.my = m.name, m.x, m.y
        self.w, self.h, self.primary = m.w, m.h, m.primary

        # WorkerW spans the virtual desktop, so a child's position is measured from the
        # virtual origin, which is not the primary screen's corner when a monitor sits
        # to the left of or above it.
        vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        self.child_x, self.child_y = self.mx - vx, self.my - vy
        self.host_pref = host_pref

        self.fps = fps
        self.running = True
        self.lost = False
        self.hidden = False
        self.fails = 0

        self.renderer = render.Renderer(self.w, self.h)
        self.weed = reef.Anemone()
        self.weed.settle(240)          # let the arms find the surge before anyone looks
        lo, hi = self.renderer.cam.swim_box(keep_clear=self.icon_margin)
        self.fish = swimmer.Swimmer(lo, hi, seed)

        if self.host_pref == "defview":
            self.host, self.how = defview_host()
        elif self.host_pref == "progman":
            self.host, self.how = progman_host()
        else:
            self.host, self.how = find_wallpaper_host(self.boot_wait)
        self.behind_icons = self.host is not None
        self._make_window()

        self.bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), self.w, -self.h,
                                    1, 24, BI_RGB, 0, 0, 0, 0, 0)
        self.hdc = user32.GetDC(self.hwnd)
        self.last_pos = None
        self.gl = None
        self.painter = None
        self.scene = None
        self._start_gl()

    def _make_window(self):
        if getattr(self, "_proc", None) is None:
            self._proc = WNDPROC(self._wndproc)
            _keep_alive.append(self._proc)       # see _keep_alive above: never collect this
        cls = WNDCLASS()
        cls.lpfnWndProc = self._proc
        cls.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        cls.lpszClassName = CLASS_NAME
        cls.hbrBackground = gdi32.CreateSolidBrush(0)
        cls.style = CS_OWNDC | CS_HREDRAW | CS_VREDRAW
        global _class_ready
        if not _class_ready:
            if not user32.RegisterClassW(ctypes.byref(cls)):
                raise ctypes.WinError(ctypes.get_last_error())
            _class_ready = True
        _keep_alive.append(cls)                  # likewise, for its strings

        ex = WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        if self.behind_icons:
            # On 24H2 and later the icons stay inside Progman and a child window is
            # only composited when it is layered. Opaque children of Progman, WorkerW
            # or the icon view render correctly and are never shown.
            ex |= WS_EX_LAYERED
        self.hwnd = None
        if self.over_icons:
            self.behind_icons = False
            self.how = "forced over the icons (--over-icons)"
        if self.behind_icons:
            # A child window must be given its parent at creation: creating it top-level
            # with WS_CHILD and reparenting afterwards fails with ERROR_INVALID_PARAMETER
            # ("cannot create a top-level child window").
            self.hwnd = user32.CreateWindowExW(ex, CLASS_NAME, "clownfish",
                                               WS_CHILD | WS_VISIBLE |
                                               WS_CLIPSIBLINGS | WS_CLIPCHILDREN,
                                               self.child_x, self.child_y, self.w, self.h,
                                               self.host, None, cls.hInstance, None)
            if not self.hwnd:
                self.behind_icons = False
                self.how = "could not parent into %s; sitting over the icons" % self.how
        if not self.hwnd:
            # Top-level, because on some Windows builds a child of Progman or WorkerW
            # owned by another process never composites at all -- neither GL nor GDI.
            # Keyed transparent on black so the desktop wallpaper and icons show
            # through and the fish swims in front of them.
            ex |= WS_EX_TRANSPARENT          # clicks still reach the desktop
            if self.keyed:
                ex |= WS_EX_LAYERED
            self.hwnd = user32.CreateWindowExW(ex, CLASS_NAME, "clownfish",
                                               WS_POPUP | WS_VISIBLE |
                                               WS_CLIPSIBLINGS | WS_CLIPCHILDREN,
                                               self.mx, self.my, self.w, self.h,
                                               None, None, cls.hInstance, None)
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            if self.keyed:
                user32.SetLayeredWindowAttributes(self.hwnd, 0x000000, 255, LWA_COLORKEY)
            user32.SetWindowPos(self.hwnd, HWND_BOTTOM, self.mx, self.my, self.w, self.h,
                                SWP_NOACTIVATE | SWP_SHOWWINDOW)
        if self.behind_icons:
            # alpha 255: fully opaque, but layered, which is what makes it visible
            user32.SetLayeredWindowAttributes(self.hwnd, 0, 255, LWA_ALPHA)
        if self.behind_icons and self.host_pref == "defview":
            user32.SetWindowPos(self.hwnd, HWND_BOTTOM, self.child_x, self.child_y,
                                self.w, self.h, SWP_NOACTIVATE | SWP_SHOWWINDOW)
        elif self.behind_icons and self.host_pref == "progman":
            # Slot in directly beneath the icon view: the icons stay on top of the fish,
            # and the fish stays on top of WorkerW, which is what paints the wallpaper.
            # HWND_BOTTOM would put us under WorkerW, where nothing is ever seen.
            icons = user32.FindWindowExW(self.host, None, "SHELLDLL_DefView", None)
            after = icons if icons else HWND_BOTTOM
            user32.SetWindowPos(self.hwnd, after, self.child_x, self.child_y,
                                self.w, self.h, SWP_NOACTIVATE | SWP_SHOWWINDOW)
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        # Only one process can hold a given hotkey; whichever wins it stops them all.
        self.owns_hotkey = bool(user32.RegisterHotKey(
            None, 1, MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT, 0x46))

    def _start_gl(self):
        """Put the composite on the GPU if we can; keep the numpy path if we cannot.

        Turning a 160x60 grid of glyphs into 1920x1080 pixels costs 13.7ms in numpy and
        0.05ms here, because it is a glyph atlas and a multiply -- what a GPU is for.
        """
        if self.no_gl:
            self.how_gl = "disabled (--no-gpu)"
            return
        self.gl_tries = getattr(self, "gl_tries", 0) + 1
        try:
            self.gl = glctx.WindowContext(self.hwnd)
            cam = self.renderer.cam
            self.painter = gpu.CellPainter(self.gl.ctx, cam, render.atlas())
            self.scene = scene_gl.SceneRenderer(self.gl.ctx, cam, self.renderer.bg,
                                                bare=self.bare)
            self.gl.ctx.viewport = (0, 0, self.w, self.h)
            self.how_gl = "%s, GL %s" % (self.gl.ctx.info["GL_RENDERER"],
                                         self.gl.ctx.version_code)
        except Exception as exc:
            self.gl = self.painter = self.scene = None
            self.how_gl = "unavailable (%s); drawing on the CPU" % (exc,)

    def _draw_gpu(self):
        """The whole scene on the GPU: geometry in, lit letters out."""
        ctx = self.gl.ctx
        t = self.fish.clock
        place, yaw, pitch = self.fish.pose()
        fishmod.set_pose(**self.fish.shape())
        const = fishmod.local_const()
        moving = fishmod.local_moving(t)
        along, reach, body_end = fishmod.spine(t)
        spin = fishmod.pose_matrix(yaw, pitch)
        pose = dict(spin=spin, place=place, reach=reach, body_end=body_end,
                    facing=fishmod.facing_of(spin))
        cam = self.renderer.cam
        overlay = render.overlay_cells(cam, t, self.fish, specks=not self.bare)
        floor = None if self.bare else (render.sand_live(cam, t),
                                        render.coral_live(cam, t))
        ink_tex, meta_tex = self.scene.draw(t, const, moving, self.weed.curve(),
                                            overlay, pose, self.weed.motes(),
                                            floor)
        ctx.screen.use()
        ctx.viewport = (0, 0, self.w, self.h)
        self.painter.draw_textures(ink_tex, meta_tex)
        _p = time.perf_counter()
        self.gl.present()
        self.presented += time.perf_counter() - _p

    def _show(self, chars, ink):
        if self.painter is not None:
            self.painter.upload(chars, ink)
            self.painter.draw()
            self.gl.present()
        else:
            image = self.renderer.paint(chars, ink)
            gdi32.StretchDIBits(self.hdc, 0, 0, self.w, self.h, 0, 0, self.w, self.h,
                                image.ctypes.data, ctypes.byref(self.bmi),
                                DIB_RGB_COLORS, SRCCOPY)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_DESTROY:
            # Not a reason to quit. The shell rebuilds WorkerW whenever the wallpaper
            # changes or explorer restarts, and takes our window down with it; the tank
            # should reappear rather than silently vanish.
            self.lost = True
            return 0
        if msg == WM_CLOSE:
            self.running = False
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _rebuild(self):
        """Our window went down with its host. Find a new one and move back in."""
        if getattr(self, "gl", None) is not None:
            try:
                self.gl.release()
            except Exception:
                pass
            self.gl = self.painter = self.scene = None
        if getattr(self, "hdc", None):
            user32.ReleaseDC(self.hwnd, self.hdc)
            self.hdc = None
        self.host, self.how = find_wallpaper_host(10.0)
        self.behind_icons = self.host is not None
        self.lost = False
        try:
            self._make_window()
        except Exception as exc:
            print("  could not make a window: %r" % (exc,), flush=True)
            return False
        self.hdc = user32.GetDC(self.hwnd)
        self._start_gl()
        print("  gpu: %s" % self.how_gl, flush=True)
        return True

    def _read_cursor(self):
        """Where the cursor is in this tank's water, and whether it just moved.

        GetCursorPos is in virtual-desktop coordinates covering every screen, so a
        cursor on the other monitor reads as absent here and this fish loses interest.
        """
        pt = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            return None, False
        moved = (self.last_pos is not None and
                 abs(pt.x - self.last_pos[0]) + abs(pt.y - self.last_pos[1]) > 2)
        self.last_pos = (pt.x, pt.y)
        lx, ly = pt.x - self.mx, pt.y - self.my
        if not (0 <= lx < self.w and 0 <= ly < self.h):
            return None, False
        if not cursor_on_desktop(pt):
            return None, False          # busy in another window: not food
        return self.renderer.cam.unproject(lx, ly), moved

    def _blit(self, image):
        gdi32.StretchDIBits(self.hdc, 0, 0, self.w, self.h, 0, 0, self.w, self.h,
                            image.ctypes.data, ctypes.byref(self.bmi),
                            DIB_RGB_COLORS, SRCCOPY)

    def run(self):
        # Windows' default timer granularity is about 15.6ms. Sleeping for the rest of
        # a 41.7ms frame then overshoots by a random slice of that, which reads as
        # stutter. Asking for 1ms makes the wait land where it should.
        if winmm is not None:
            winmm.timeBeginPeriod(1)
        msg = wintypes.MSG()
        period = 1.0 / self.fps
        last = check = seen = self.counted = time.perf_counter()
        self.drawn = 0
        self.spent = self.presented = 0.0
        self.hidden = False
        while self.running:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == WM_QUIT:
                    self.running = False
                elif msg.message == WM_HOTKEY:
                    close_all_tanks()
                    self.running = False
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            now = time.perf_counter()
            dt = min(now - last, 0.2)
            last = now

            # the shell rebuilds WorkerW on a wallpaper change or an explorer restart,
            # taking our window with it; move into the new one and carry on
            if now - check > 2.0:
                check = now
                # starting at login can beat the display driver; a context that failed
                # then would otherwise leave us on the CPU renderer for the whole session
                if (self.scene is None and not self.no_gl and self.gl_tries < 6
                        and not self.lost and user32.IsWindow(self.hwnd)):
                    self._start_gl()
                    if self.scene is not None:
                        print("  gpu came up on retry %d: %s"
                              % (self.gl_tries, self.how_gl), flush=True)
                if self.lost or not user32.IsWindow(self.hwnd):
                    print("window lost (the shell rebuilt WorkerW); moving back in",
                          flush=True)
                    if not self._rebuild():
                        self.fails += 1
                        print("  rebuild failed (%d in a row)" % self.fails, flush=True)
                        if self.fails >= 10:
                            print("giving up after 10 failed rebuilds", flush=True)
                            self.running = False
                            break
                        time.sleep(1.0)
                        continue
                    self.fails = 0
                    print("  back in: %s" % self.how, flush=True)

            # nobody can see a covered screen, so do not pay to draw one
            if now - seen > 0.5:
                seen = now
                was, self.hidden = self.hidden, (False if self.always_draw
                                                 else monitor_covered(self.mon))
                if was != self.hidden:
                    print("screen %d %s" % (self.index,
                                            "covered: sleeping" if self.hidden
                                            else "visible again: drawing"), flush=True)
            if self.hidden:
                time.sleep(0.3)
                last = time.perf_counter()   # resume without a jump in the clock
                continue

            where, moved = self._read_cursor()
            self.fish.attend(where, moved)
            self.fish.advance(dt)
            place, yaw, pitch = self.fish.pose()
            self.weed.advance(dt, place, self.fish.velocity, yaw, pitch)
            if self.scene is not None:
                _t0 = time.perf_counter()
                self._draw_gpu()
                self.spent += time.perf_counter() - _t0
            else:
                self._show(*self.renderer.grid(self.fish.clock, self.fish,
                                               self.weed.curve()))

            self.drawn += 1
            if now - self.counted > 10.0:
                print("  %.1f fps over %.0fs (target %d); %.1f ms per frame inside "
                      "_draw_gpu, %.1f ms in present"
                      % (self.drawn / (now - self.counted), now - self.counted, self.fps,
                         1000 * self.spent / max(self.drawn, 1),
                         1000 * self.presented / max(self.drawn, 1)), flush=True)
                self.counted, self.drawn = now, 0
                self.spent = self.presented = 0.0

            spare = period - (time.perf_counter() - now)
            if spare > 0:
                time.sleep(spare)
        self.close()

    def close(self):
        if winmm is not None:
            winmm.timeEndPeriod(1)
        if getattr(self, "gl", None) is not None:
            try:
                self.gl.release()
            except Exception:
                pass
        if getattr(self, "owns_hotkey", False):
            user32.UnregisterHotKey(None, 1)
        if getattr(self, "hdc", None):
            user32.ReleaseDC(self.hwnd, self.hdc)
        if getattr(self, "hwnd", None):
            user32.DestroyWindow(self.hwnd)


def _spawn_per_monitor(argv, count):
    """Start one detached process per screen and let this one go."""
    DETACHED_PROCESS, CREATE_NO_WINDOW = 0x00000008, 0x08000000
    script = os.path.abspath(sys.argv[0])
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.basename(script).lower() == "wallpaper.py":
        base = [sys.executable, "-m", "tank.wallpaper"]   # started with -m, keep it that way
    else:
        base = [sys.executable, script]
    started = []
    for i in range(count):
        subprocess.Popen(base + ["--monitor", str(i)] + argv, cwd=root, close_fds=True,
                         creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW)
        started.append(i)
    return started


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    fps, seed, monitor, no_gl = 24, None, None, False
    boot_wait = 45.0
    always_draw = False
    host_pref = 'progman'    # 24H2+: the icons live in Progman
    keyed = False            # a real wallpaper, behind the desktop icons
    bare = False             # the whole reef, as the original had it
    icon_margin = 0.0        # not needed: the icons are drawn on top of us
    over_icons = False
    passthrough = []
    i = 0
    while i < len(argv):
        if argv[i] == "--fps" and i + 1 < len(argv):
            fps = int(argv[i + 1]); passthrough += argv[i:i + 2]; i += 2
        elif argv[i] == "--seed" and i + 1 < len(argv):
            seed = int(argv[i + 1]); passthrough += argv[i:i + 2]; i += 2
        elif argv[i] == "--boot-wait" and i + 1 < len(argv):
            boot_wait = float(argv[i + 1]); passthrough += argv[i:i + 2]; i += 2
        elif argv[i] == "--no-gpu":
            no_gl = True; passthrough.append(argv[i]); i += 1
        elif argv[i] == "--solid":
            keyed = False; passthrough.append(argv[i]); i += 1
        elif argv[i] == "--icon-margin" and i + 1 < len(argv):
            icon_margin = float(argv[i + 1]); passthrough += argv[i:i + 2]; i += 2
        elif argv[i] == "--reef":
            bare = False; passthrough.append(argv[i]); i += 1
        elif argv[i] == "--behind-icons":
            keyed = False; over_icons = False; passthrough.append(argv[i]); i += 1
        elif argv[i] == "--host" and i + 1 < len(argv):
            host_pref = argv[i + 1]; passthrough += argv[i:i + 2]; i += 2
        elif argv[i] == "--over-icons":
            over_icons = True; passthrough.append(argv[i]); i += 1
        elif argv[i] == "--always-draw":
            always_draw = True; passthrough.append(argv[i]); i += 1
        elif argv[i] == "--monitor" and i + 1 < len(argv):
            monitor = int(argv[i + 1]); i += 2
        else:
            i += 1

    dpi_aware()
    mons = monitors()
    if monitor is None and len(mons) > 1:
        # one tank per screen: a frame costs ~33ms, so sharing a thread would halve the
        # rate. Separate processes get a core each.
        started = _spawn_per_monitor(passthrough, len(mons))
        print("screens: %d" % len(mons))
        for n, m in enumerate(mons):
            print("  %d %-14s %dx%d at (%d,%d)%s" %
                  (n, m.name, m.w, m.h, m.x, m.y, "  primary" if m.primary else ""))
        print("started one tank per screen: %s" % started)
        return 0

    index = 0 if monitor is None else monitor
    if index >= len(mons):
        print("no screen %d: this machine has %d (numbered 0..%d)"
              % (index, len(mons), len(mons) - 1))
        return 1
    claim = claim_screen(index)
    if claim is None:
        print("screen %d already has a tank; nothing to do" % index)
        return 0
    tank = Wallpaper(monitor=index, fps=fps, seed=seed, no_gl=no_gl,
                     boot_wait=boot_wait, always_draw=always_draw,
                     over_icons=over_icons or keyed, host_pref=host_pref,
                     keyed=keyed, bare=bare, icon_margin=icon_margin)
    print("screen       : %d %s %dx%d at (%d,%d)%s"
          % (tank.index, tank.name, tank.w, tank.h, tank.mx, tank.my,
             "  primary" if tank.primary else ""))
    print("DPI awareness: %s" % tank.dpi)
    print("desktop host : %s" % tank.how)
    if tank.icon_margin > 0:
        print("icon strip   : staying %.0fpx clear down the left" % tank.icon_margin)
    print("mode         : %s%s" % (
        "transparent over your wallpaper, icons stay" if tank.keyed else
        "behind the icons" if tank.behind_icons else
        "solid: covers wallpaper and icons",
        "" if not tank.bare else "  (fish only)"))
    print("gpu          : %s" % tank.how_gl)
    print("hotkey owner : %s" % ("yes" if tank.owns_hotkey else "no (another tank has it)"))
    print("quit with Ctrl+Alt+Shift+F")
    try:
        tank.run()
    except BaseException:
        import traceback
        traceback.print_exc()
        raise
    print("stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
