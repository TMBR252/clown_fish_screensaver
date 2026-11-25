"""Spike: can we get an OpenGL context on a child window of WorkerW?

This is the one step in the GPU plan that cannot be reasoned to certainty -- a GL
context on a desktop-layer child window, under DWM compositing. Nothing else is built
until this draws. Run it with the venv's python:

    .venv\\Scripts\\python.exe spike_gl.py
"""
import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
opengl32 = ctypes.WinDLL("opengl32", use_last_error=True)

WS_CHILD, WS_VISIBLE = 0x40000000, 0x10000000
WS_CLIPSIBLINGS, WS_CLIPCHILDREN = 0x04000000, 0x02000000
WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = 0x08000000, 0x00000080
PFD_DRAW_TO_WINDOW, PFD_SUPPORT_OPENGL, PFD_DOUBLEBUFFER = 0x04, 0x20, 0x01
PFD_TYPE_RGBA, PFD_MAIN_PLANE = 0, 0

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             ctypes.c_size_t, ctypes.c_ssize_t)
ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.c_ssize_t)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


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


for fn, res, args in [
    (user32.FindWindowW, wintypes.HWND, [wintypes.LPCWSTR, wintypes.LPCWSTR]),
    (user32.FindWindowExW, wintypes.HWND,
     [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]),
    (user32.GetDC, wintypes.HDC, [wintypes.HWND]),
    (user32.DefWindowProcW, ctypes.c_ssize_t,
     [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]),
    (user32.CreateWindowExW, wintypes.HWND,
     [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
      wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]),
    (user32.DestroyWindow, wintypes.BOOL, [wintypes.HWND]),
    (gdi32.ChoosePixelFormat, ctypes.c_int,
     [wintypes.HDC, ctypes.POINTER(PIXELFORMATDESCRIPTOR)]),
    (gdi32.SetPixelFormat, wintypes.BOOL,
     [wintypes.HDC, ctypes.c_int, ctypes.POINTER(PIXELFORMATDESCRIPTOR)]),
    (gdi32.SwapBuffers, wintypes.BOOL, [wintypes.HDC]),
    (gdi32.CreateSolidBrush, wintypes.HBRUSH, [wintypes.DWORD]),
    (opengl32.wglCreateContext, ctypes.c_void_p, [wintypes.HDC]),
    (opengl32.wglMakeCurrent, wintypes.BOOL, [wintypes.HDC, ctypes.c_void_p]),
    (opengl32.wglDeleteContext, wintypes.BOOL, [ctypes.c_void_p]),
    (opengl32.wglGetProcAddress, ctypes.c_void_p, [ctypes.c_char_p]),
    (ctypes.windll.kernel32.GetModuleHandleW, wintypes.HINSTANCE, [wintypes.LPCWSTR]),
]:
    fn.restype, fn.argtypes = res, args

_keep = []


def find_host():
    progman = user32.FindWindowW("Progman", None)
    found = []

    def walk(hwnd, _):
        if user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None):
            sib = user32.FindWindowExW(None, hwnd, "WorkerW", None)
            if sib:
                found.append(sib)
        return True

    user32.EnumWindows(ENUMPROC(walk), 0)
    if found:
        return found[0], "WorkerW behind icons"
    if progman:
        res = ctypes.c_size_t()
        child = user32.FindWindowExW(progman, None, "WorkerW", None)
        if child:
            return child, "WorkerW under Progman"
        for wp, lp in ((0, 0), (0xD, 0x1)):
            user32.SendMessageTimeoutW(progman, 0x052C, wp, lp, 0, 1000, ctypes.byref(res))
        child = user32.FindWindowExW(progman, None, "WorkerW", None)
        if child:
            return child, "WorkerW under Progman (spawned)"
        return progman, "Progman"
    return None, "none"


def main():
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    host, how = find_host()
    print("host: %s (%s)" % (host, how))
    if not host:
        return 1

    proc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
    _keep.append(proc)
    cls = WNDCLASS()
    cls.lpfnWndProc = proc
    cls.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
    cls.lpszClassName = "ClownfishGLSpike"
    cls.hbrBackground = gdi32.CreateSolidBrush(0)
    cls.style = 0x0020 | 0x0002 | 0x0001          # CS_OWNDC | CS_HREDRAW | CS_VREDRAW
    _keep.append(cls)
    if not user32.RegisterClassW(ctypes.byref(cls)):
        print("RegisterClassW failed:", ctypes.get_last_error())
        return 1

    # GL on a child window needs these two or siblings scribble over the context
    style = WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | WS_CLIPCHILDREN
    hwnd = user32.CreateWindowExW(WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
                                  cls.lpszClassName, "glspike", style,
                                  0, 0, 640, 360, host, None, cls.hInstance, None)
    if not hwnd:
        print("CreateWindowExW failed:", ctypes.get_last_error())
        return 1
    print("window: %s (child of the desktop layer)" % hwnd)

    hdc = user32.GetDC(hwnd)
    pfd = PIXELFORMATDESCRIPTOR()
    pfd.nSize = ctypes.sizeof(PIXELFORMATDESCRIPTOR)
    pfd.nVersion = 1
    pfd.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER
    pfd.iPixelType = PFD_TYPE_RGBA
    pfd.cColorBits = 32
    pfd.cDepthBits = 24
    pfd.iLayerType = PFD_MAIN_PLANE
    fmt = gdi32.ChoosePixelFormat(hdc, ctypes.byref(pfd))
    print("ChoosePixelFormat -> %s" % fmt)
    if not fmt or not gdi32.SetPixelFormat(hdc, fmt, ctypes.byref(pfd)):
        print("SetPixelFormat failed:", ctypes.get_last_error())
        return 1

    hglrc = opengl32.wglCreateContext(hdc)
    print("wglCreateContext -> %s" % hglrc)
    if not hglrc:
        print("failed:", ctypes.get_last_error())
        return 1
    if not opengl32.wglMakeCurrent(hdc, hglrc):
        print("wglMakeCurrent failed:", ctypes.get_last_error())
        return 1
    print("context is current")

    import moderngl
    ctx = moderngl.create_context()           # attaches to the current wgl context
    print("moderngl attached: GL %s" % (ctx.version_code,))
    print("  vendor  : %s" % ctx.info["GL_VENDOR"])
    print("  renderer: %s" % ctx.info["GL_RENDERER"])
    print("  version : %s" % ctx.info["GL_VERSION"])
    print("  GLSL    : %s" % ctx.info.get("GL_SHADING_LANGUAGE_VERSION", "?"))
    print("  compute : %s" % ("yes" if ctx.version_code >= 430 else "no"))

    # turn vsync off: SwapBuffers blocking on 60Hz would both pin the rate and burn wait
    swap = opengl32.wglGetProcAddress(b"wglSwapIntervalEXT")
    if swap:
        ctypes.CFUNCTYPE(wintypes.BOOL, ctypes.c_int)(swap)(0)
        print("  vsync   : off")
    else:
        print("  vsync   : wglSwapIntervalEXT unavailable")

    # draw something and prove the pixels came out
    ctx.viewport = (0, 0, 640, 360)
    ctx.clear(0.10, 0.45, 0.85, 1.0)
    ctx.finish()
    got = ctx.screen.read(components=3, dtype="f1")
    px = tuple(got[:3])
    print("read back first pixel: %s (expected about (26,115,217))" % (px,))
    gdi32.SwapBuffers(hdc)

    # how fast can we clear+swap? this is the floor for the real renderer
    n, t0 = 0, time.perf_counter()
    while time.perf_counter() - t0 < 2.0:
        ctx.clear(0.10, 0.45, 0.85, 1.0)
        gdi32.SwapBuffers(hdc)
        n += 1
    print("clear+swap: %d in 2.0s (%.0f fps ceiling)" % (n, n / 2.0))

    opengl32.wglMakeCurrent(None, None)
    opengl32.wglDeleteContext(hglrc)
    user32.DestroyWindow(hwnd)
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
