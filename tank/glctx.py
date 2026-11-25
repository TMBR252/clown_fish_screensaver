"""An OpenGL context on a plain Win32 window, and the window-less one used for tests.

The tank's window is a child of WorkerW, down in the desktop layer under DWM. Getting
a GL context there is the one part of the GPU path that could not be reasoned about in
advance, so it is isolated here and `spike_gl.py` exercises it on its own. On this
machine it gives GL 4.6 on the discrete GPU.

CS_OWNDC matters: without it the window's DC is recycled from a cache and the context
can end up bound to a DC that no longer belongs to us. WS_CLIPSIBLINGS and
WS_CLIPCHILDREN matter for the same reason siblings must not scribble into our pixels.
"""
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
opengl32 = ctypes.WinDLL("opengl32", use_last_error=True)

PFD_DRAW_TO_WINDOW, PFD_SUPPORT_OPENGL, PFD_DOUBLEBUFFER = 0x04, 0x20, 0x01
PFD_TYPE_RGBA, PFD_MAIN_PLANE = 0, 0
CS_VREDRAW, CS_HREDRAW, CS_OWNDC = 0x0001, 0x0002, 0x0020


class PIXELFORMATDESCRIPTOR(ctypes.Structure):
    _fields_ = [("nSize", wintypes.WORD), ("nVersion", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("iPixelType", wintypes.BYTE),
                ("cColorBits", wintypes.BYTE), ("cRedBits", wintypes.BYTE),
                ("cRedShift", wintypes.BYTE), ("cGreenBits", wintypes.BYTE),
                ("cGreenShift", wintypes.BYTE), ("cBlueBits", wintypes.BYTE),
                ("cBlueShift", wintypes.BYTE), ("cAlphaBits", wintypes.BYTE),
                ("cAlphaShift", wintypes.BYTE), ("cAccumBits", wintypes.BYTE),
                ("cAccumRedBits", wintypes.BYTE), ("cAccumGreenBits", wintypes.BYTE),
                ("cAccumBlueBits", wintypes.BYTE), ("cAccumAlphaBits", wintypes.BYTE),
                ("cDepthBits", wintypes.BYTE), ("cStencilBits", wintypes.BYTE),
                ("cAuxBuffers", wintypes.BYTE), ("iLayerType", wintypes.BYTE),
                ("bReserved", wintypes.BYTE), ("dwLayerMask", wintypes.DWORD),
                ("dwVisibleMask", wintypes.DWORD), ("dwDamageMask", wintypes.DWORD)]


for _fn, _res, _args in [
    (user32.GetDC, wintypes.HDC, [wintypes.HWND]),
    (user32.ReleaseDC, ctypes.c_int, [wintypes.HWND, wintypes.HDC]),
    (gdi32.ChoosePixelFormat, ctypes.c_int,
     [wintypes.HDC, ctypes.POINTER(PIXELFORMATDESCRIPTOR)]),
    (gdi32.SetPixelFormat, wintypes.BOOL,
     [wintypes.HDC, ctypes.c_int, ctypes.POINTER(PIXELFORMATDESCRIPTOR)]),
    (gdi32.SwapBuffers, wintypes.BOOL, [wintypes.HDC]),
    (opengl32.wglCreateContext, ctypes.c_void_p, [wintypes.HDC]),
    (opengl32.wglMakeCurrent, wintypes.BOOL, [wintypes.HDC, ctypes.c_void_p]),
    (opengl32.wglDeleteContext, wintypes.BOOL, [ctypes.c_void_p]),
    (opengl32.wglGetProcAddress, ctypes.c_void_p, [ctypes.c_char_p]),
]:
    _fn.restype, _fn.argtypes = _res, _args


def describe():
    pfd = PIXELFORMATDESCRIPTOR()
    pfd.nSize = ctypes.sizeof(PIXELFORMATDESCRIPTOR)
    pfd.nVersion = 1
    pfd.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER
    pfd.iPixelType = PFD_TYPE_RGBA
    pfd.cColorBits = 32
    pfd.cDepthBits = 24
    pfd.iLayerType = PFD_MAIN_PLANE
    return pfd


class WindowContext:
    """A GL context bound to an existing HWND, presented with SwapBuffers."""

    def __init__(self, hwnd):
        import moderngl
        self.hwnd = hwnd
        self.hdc = user32.GetDC(hwnd)
        if not self.hdc:
            raise RuntimeError("GetDC failed on %s" % hwnd)
        pfd = describe()
        fmt = gdi32.ChoosePixelFormat(self.hdc, ctypes.byref(pfd))
        if not fmt:
            raise RuntimeError("ChoosePixelFormat found nothing usable")
        if not gdi32.SetPixelFormat(self.hdc, fmt, ctypes.byref(pfd)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.hglrc = opengl32.wglCreateContext(self.hdc)
        if not self.hglrc:
            raise ctypes.WinError(ctypes.get_last_error())
        if not opengl32.wglMakeCurrent(self.hdc, self.hglrc):
            raise ctypes.WinError(ctypes.get_last_error())
        self.ctx = moderngl.create_context()        # attaches to the context just made
        self.set_vsync(False)

    def set_vsync(self, on):
        """Off by default: SwapBuffers blocking on the monitor would pin the frame rate
        and spend the wait as CPU time, which is the opposite of the point."""
        fn = opengl32.wglGetProcAddress(b"wglSwapIntervalEXT")
        if fn:
            ctypes.CFUNCTYPE(wintypes.BOOL, ctypes.c_int)(fn)(1 if on else 0)
            return True
        return False

    def make_current(self):
        return bool(opengl32.wglMakeCurrent(self.hdc, self.hglrc))

    def present(self):
        gdi32.SwapBuffers(self.hdc)

    def release(self):
        # Release moderngl's objects first. The shell rebuilds WorkerW on every
        # wallpaper change, so a tank that lives all day rebuilds its window many
        # times; dropping the Python reference alone leaks every texture, program and
        # buffer on the GPU, and after a dozen rebuilds the process dies.
        ctx = getattr(self, "ctx", None)
        if ctx is not None:
            try:
                ctx.release()
            except Exception:
                pass
            self.ctx = None
        opengl32.wglMakeCurrent(None, None)
        if getattr(self, "hglrc", None):
            opengl32.wglDeleteContext(self.hglrc)
            self.hglrc = None
        if getattr(self, "hdc", None):
            user32.ReleaseDC(self.hwnd, self.hdc)
            self.hdc = None


class OffscreenContext:
    """A standalone context with no window, for comparing output against the CPU path."""

    def __init__(self):
        import moderngl
        self.ctx = moderngl.create_context(standalone=True, require=330)

    def make_current(self):
        return True

    def present(self):
        pass

    def release(self):
        self.ctx.release()
