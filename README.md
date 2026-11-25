# Clownfish wallpaper

An ASCII clownfish that lives behind your desktop icons on Windows. It wanders on its
own, and when you move the mouse it notices, turns, and chases the pointer. One fish
per screen, up to as many screens as you have.

The anemone's tentacles bend away as the fish swims through them and light up where it
brushes past. Plankton in the water flashes when it rushes by and gets dragged along in
its wake. At speed the tail sheds sparks. Ripples of sunlight cross the sand.

Built from [ASCII Lab's clownfish](https://github.com/sapozhkov/ascii-lab-public) —
see [Credits and licence](#credits-and-licence), which matters here.

## Requirements

- **Windows 10 or 11.** It draws into the Windows desktop; there is no other platform.
- **Python 3.9 or newer.** [python.org](https://www.python.org/downloads/) — tick
  "Add Python to PATH" during install.
- A GPU with **OpenGL 3.3** (anything from the last decade). Without one it falls back
  to a CPU renderer that works but is much slower.

## Install

Clone or download, then double-click:

```
setup.cmd
```

That builds a private virtual environment in `.venv`, installs what it needs, and
checks your machine — Python, OpenGL, fonts, and the screens it found. It touches
nothing outside the project folder.

## Use

| | |
| --- | --- |
| `start.cmd` | start one fish per screen |
| `stop.cmd` | stop them all (or press **Ctrl+Alt+Shift+F** any time) |
| `install-startup.cmd` | run it automatically when you log in |
| `uninstall-startup.cmd` | stop doing that |
| `check.cmd` | report what your machine can do |

Nothing runs at login unless you ask it to. The startup entry is an ordinary shortcut
in your Startup folder, so Windows lists it under **Task Manager → Startup apps** and
you can disable it there.

### Options

Pass these to `start.cmd`, for example `start.cmd --fps 15`:

| | |
| --- | --- |
| `--fps N` | frame rate, default 24 |
| `--bare` | just the fish and its anemone, no sand, rocks or corals |
| `--overlay` | transparent: your own wallpaper shows through instead of black |
| `--solid` | opaque, sitting *over* the desktop icons |
| `--no-gpu` | force the CPU renderer |
| `--monitor N` | one screen only, numbered as `check.cmd` lists them |
| `--seed N` | the same fish every time |

## What it costs

Measured on an RTX 3090 at 1920×1080, 24 fps, per screen:

| | CPU |
| --- | --- |
| screen covered by a window | **0.01%** of the machine |
| screen visible | **~2%** of the machine (about half a core) |

It does not draw a screen that nothing can see, which is most of a working day. With
four screens visible at once expect roughly two cores' worth; it is a toy, so turn the
frame rate down if that bothers you.

GPU load is about 1.5 ms a frame — a few per cent of a mid-range card, at idle clocks.

## Several screens

`start.cmd` runs one process per screen, each on its own core, because a single thread
cannot keep up with two. Each fish watches only its own screen: move the pointer across
and one loses interest while the other wakes up.

Arrangements are read from Windows, so screens above, below, left, portrait, and mixed
resolutions all work. The zoom comes from whichever dimension is tighter, so an
ultrawide shows more water rather than a giant fish, and a portrait screen gets a tall
tank. *Tested on two 1920×1080 screens; other arrangements are correct by construction
and by test, but have not been run on real hardware.*

## How it works

```
tank/fish.py       the fish: parametric body, fins, eyes, the body wave
tank/reef.py       the anemone (a cloth simulation), rocks, sand, corals, plankton
tank/swimmer.py    what the fish decides to do, and the physics that carries it out
tank/render.py     the camera, the glyph atlas, the CPU renderer
tank/scene_gl.py   the GPU renderer, pass 1: the scene into a grid of characters
tank/gpu.py        the GPU renderer, pass 2: that grid into lit letters
tank/glctx.py      an OpenGL context on a plain Win32 window
tank/wallpaper.py  the desktop window, the frame loop, one process per screen
```

The scene is drawn into a grid one fragment per character — 240×90 at 1080p — with a
real depth test, then one fullscreen shader turns each cell into a glyph from an atlas.
Choosing the glyph is shader arithmetic: brightness indexes a ramp. The fish's spine,
pose and lighting happen in the vertex shader, so the CPU uploads its shape once and
sends a dozen numbers a frame.

### Getting behind the desktop icons

Windows draws the wallpaper in a window of class `WorkerW`. The usual trick is to send
`Progman` the undocumented message `0x052C` and live inside the `WorkerW` that appears.
On **Windows 11 24H2 and later this quietly stops working**, and two details cost a lot
of time here:

- **The child window must be `WS_EX_LAYERED`.** On 24H2+ the icons stay inside
  `Progman`, and a child window of the desktop is only composited if it is layered. An
  opaque child renders perfectly and is simply never shown — as a child of `WorkerW`,
  of `Progman`, or of the icon view, with OpenGL *or* GDI. Add `WS_EX_LAYERED` and
  `SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)` and it appears.
- **`0x052C` is reference counted.** `lParam=1` asks for the `WorkerW`; `lParam=0`
  hands it back and Explorer destroys it. The classic recipe sends both, which leaves
  you re-parenting into a dead window. Send only `lParam=1`.

Two more things worth knowing if you touch that code:

- **Only ask for the `WorkerW` if one is not already there.** Asking again makes the
  shell tear down the existing one, destroying every window inside it, including other
  copies of this.
- **The window class holds a raw pointer to the `WNDPROC` for the life of the
  process.** Letting the ctypes callback be collected frees that trampoline under the
  class, and the next message kills the process with no traceback.

A **wallpaper slideshow** rebuilds `WorkerW` every time it changes the picture. That is
handled — the tank notices within two seconds and moves back in — but it is why the
rebuild path has to be solid.

## Known limits

- Windows only, and the behind-the-icons path is verified on build 26300. Other builds
  take one of the documented fallbacks and the log says which.
- Tested at 100% display scaling. Per-monitor DPI awareness is set, but mixed-scaling
  setups are untested.
- The arms' rim and fill use the interpolated centreline rather than the original's
  nearest-of-54 search, so about 2% of cells choose a different character. Not visible.
- The camera does not move. The original's lazy orbit would be unbearable on a desktop.

If the fish does not appear, read `fishtank-0.log` beside this file: `pythonw` has no
console, so everything goes there, including which desktop window it found.

## Credits and licence

The fish itself — its geometry, its shading, the anemone's cloth simulation, the glyph
ramp, and the swimming model — is adapted from **[ASCII Lab](https://github.com/sapozhkov/ascii-lab-public)**
by [@ascii.lab](https://www.instagram.com/ascii.lab/), specifically `01-clownfish`.
That project is the creative work here; this one wraps it in a Windows desktop.

**Please read [NOTICE.md](NOTICE.md) before reusing this.** At the time of writing the
upstream repository carries no licence file, which in copyright terms means all rights
reserved. The MIT licence in this repository covers the Windows wallpaper harness
written here — the window handling, the GPU renderer, the behaviour layer — and cannot
grant rights over the parts derived from ASCII Lab.
