"""From a scene to a wall of coloured letters to pixels.

Three things differ from the original's `grid()` + `paint()`:

  * The camera does not move. In clownfish.py it chased the fish on a lazy arc, which
    for a wallpaper would be nauseating, would slide the world out from under the
    cursor, and would mean reprojecting the sand, rocks and corals on every frame.
    Fixed, they rasterise exactly once into a cached layer that carries its own depth
    buffer, so the fish can still swim behind a rock.
  * No bloom. The original's paint() cost 275ms a frame on two Gaussian blurs and a
    90-pixel running-sum streak. At wallpaper size, behind icons, it bought nothing
    worth a thirtieth of a second.
  * The composite only touches cells that hold a glyph. Open water is most of the
    grid, so this is roughly a third of the work of filling the whole frame.

Everything about the view lives on a Camera rather than in module constants, because
one tank runs per monitor and monitors need not be the same size.
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import fish, reef
from .fish import BACK, CH, CW, FIN, FISH, glyph_id, line_glyph

CAM_D = 7.0
# The zoom is set by whichever dimension runs out first: the screen must show at
# least this much water each way. Taking it from the width alone made an ultrawide
# zoom in until the fish was 152 characters long; taking it from the height alone left
# a portrait screen only 1.3 units wide, narrower than the fish is long.
WORLD_TALL = 2.36            # world units the screen must be high
WORLD_WIDE = 3.20            # ...and wide
CHASE_Z = -0.10              # the depth the cursor is taken to live at
DECOR_Z = 8.0                # flat bottom decor: behind everything solid
SPECK_Z = 8.5                # drifting specks: behind the decor, in front of open water
EMPTY_Z = 99.0               # nothing here at all
NEAR_Z, FAR_Z = -0.45, 0.50  # how far towards and away from the lens the fish may go

FONT_CANDIDATES = (r"C:\Windows\Fonts\consola.ttf", "consola.ttf",
                   r"C:\Windows\Fonts\lucon.ttf", "DejaVuSansMono.ttf")

BEAMS = np.array([-2.6, -1.35, -0.2, 0.95, 2.1, 3.2])
PREY_INK = np.array([1.0, 0.84, 0.42])

_w = np.random.default_rng(5)
DOT, MOTE = _w.random((150, 3)), _w.random((46, 4))


class Camera:
    """A fixed view of the tank, sized to one monitor."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.cols, self.rows = w // CW, h // CH
        self.scale = min(h / WORLD_TALL, w / WORLD_WIDE)
        self.centre = np.array([0.0, -0.30])
        self.mid_y = h / 2

    def project(self, v):
        """World points to pixels, plus the scale they came out at."""
        shrink = self.scale * CAM_D / (CAM_D + v[:, 2])
        return (self.w / 2 + shrink * (v[:, 0] - self.centre[0]),
                self.mid_y - shrink * (v[:, 1] - self.centre[1]),
                shrink)

    def unproject(self, sx, sy, z=CHASE_Z):
        """A point on this screen to a world point on a chosen depth plane."""
        shrink = self.scale * CAM_D / (CAM_D + z)
        return np.array([self.centre[0] + (sx - self.w / 2) / shrink,
                         self.centre[1] - (sy - self.mid_y) / shrink,
                         z])

    def swim_box(self, keep_clear=0.0, fish_len=1.0):
        """The slab of water that stays on screen at every depth the fish may reach.

        `keep_clear` is a strip of pixels down the left of the screen to stay out of --
        the desktop icons live there, and on Windows builds where a window cannot be
        put behind them the next best thing is for the fish not to swim over them. The
        fish's own length is allowed for, so its nose stays clear too, not just its
        middle.
        """
        tight = self.scale * CAM_D / (CAM_D + NEAR_Z)      # nearest depth shows least world
        half_x = self.w / 2 / tight - 0.15
        half_y = self.h / 2 / tight - 0.12
        cy = self.centre[1]
        left = -half_x
        if keep_clear > 0:
            # where the icon strip ends, plus half a fish so the nose clears it too
            left = max(left, (keep_clear - self.w / 2) / tight + fish_len * 0.55)
            left = min(left, half_x - 0.4)        # never squeeze the tank to nothing
        lo = np.array([left, max(cy - half_y, reef.FLOOR + 0.30), NEAR_Z])
        hi = np.array([half_x, min(cy + half_y, 0.80), FAR_Z])
        return lo, hi


# ---- glyphs ------------------------------------------------------------------------
_atlas = None
_atlas_u16 = None


def _font(size):
    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    raise OSError("No monospace font found; tried " + ", ".join(FONT_CANDIDATES))


def atlas():
    global _atlas
    if _atlas is None:
        out = np.zeros((96, CH, CW), np.float32)
        size = max(8, round(20 * CH / 18))           # the original drew 20pt into 12x18
        f = _font(size)
        base = max(1, round(3 * CH / 18))
        for i in range(95):
            im = Image.new("L", (CW, CH), 0)
            lift = base + 2 if chr(32 + i) == "_" else base   # room below the baseline
            ImageDraw.Draw(im).text((0, -lift), chr(32 + i), font=f, fill=255)
            out[i] = np.asarray(im, np.float32) / 255
        _atlas = out
    return _atlas


def atlas_u16():
    """The same glyphs as 0..255 integers, for the composite."""
    global _atlas_u16
    if _atlas_u16 is None:
        _atlas_u16 = (atlas() * 255).astype(np.uint16)
    return _atlas_u16


# ---- rasterising primitives --------------------------------------------------------
def stroke(cam, chars, ink, owner, pts, col, glyph=None):
    """A polyline given in pixels, drawn with the slash that fits each piece."""
    pts = np.asarray(pts, float)
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        n = int(max(abs(x1 - x0) / CW, abs(y1 - y0) / CH) * 1.6) + 2
        f = np.linspace(0, 1, n)
        c = ((x0 + (x1 - x0) * f) // CW).astype(int)
        r = ((y0 + (y1 - y0) * f) // CH).astype(int)
        ok = (c >= 0) & (c < cam.cols) & (r >= 0) & (r < cam.rows)
        chars[r[ok], c[ok]] = line_glyph(x1 - x0, y1 - y0) if glyph is None else glyph_id(glyph)
        ink[r[ok], c[ok]] = col
        owner[r[ok], c[ok]] = BACK


def draw_coral(cam, chars, ink, owner, x, y, size, col, seed):
    twigs, buds = reef.coral_shape(seed)
    for x0, y0, x1, y1, level in twigs:
        stroke(cam, chars, ink, owner,
               [(x + size * x0, y - size * y0), (x + size * x1, y - size * y1)],
               col * (0.55 + 0.1 * (5 - level)))
    for bx, by, round_ in buds:
        c, r = int((x + size * bx) // CW), int((y - size * by) // CH)
        if 0 <= c < cam.cols and 0 <= r < cam.rows:
            chars[r, c] = glyph_id("o" if round_ else "*")
            ink[r, c] = np.minimum(col * 1.5, 1)
            owner[r, c] = BACK


def _depth_pass(cam, chars, ink, owner, zbuf, parts):
    """Project a point cloud, pick a glyph per point and keep the nearest per cell."""
    p, q, col, lum, kind, ray = parts
    sx, sy, _ = cam.project(p)
    qx, qy, _ = cam.project(q)
    g = np.where(ray, line_glyph(qx - sx, qy - sy),
                 fish.RAMP_G[np.clip(((1 - lum) ** 1.3 * (len(fish.RAMP) - 1)).astype(int),
                                     0, len(fish.RAMP) - 1)])
    # stone is quiet: dots, lighter on top
    g = np.where(kind == BACK,
                 reef.ROCK_G[np.clip((lum * len(reef.ROCK_G)).astype(int),
                                     0, len(reef.ROCK_G) - 1)], g)
    c, r = np.floor(sx / CW).astype(int), np.floor(sy / CH).astype(int)
    ok = ((c >= 0) & (c < cam.cols) & (r >= 0) & (r < cam.rows) & (p[:, 2] > -CAM_D + 0.5))
    c, r, g, col, kind, depth = c[ok], r[ok], g[ok], col[ok], kind[ok], p[ok, 2]
    keep = depth < zbuf[r, c]                        # drop whatever is already hidden
    c, r, g, col, kind, depth = (x[keep] for x in (c, r, g, col, kind, depth))
    order = np.argsort(-depth, kind="stable")        # far first, so the nearest wins
    c, r, g, col, kind, depth = (x[order] for x in (c, r, g, col, kind, depth))
    chars[r, c], ink[r, c], owner[r, c], zbuf[r, c] = g, col, kind, depth


def water(cam, chars, ink, t):
    """Specks and motes hanging in the open water."""
    rows, cols = cam.rows, cam.cols
    r = ((DOT[:, 1] * rows + 0.18 * t * (0.5 + DOT[:, 2])) % rows).astype(int)
    c = ((DOT[:, 0] * cols + 0.25 * np.sin(0.4 * t + DOT[:, 2] * 9)) % cols).astype(int)
    chars[r, c] = glyph_id(".")
    ink[r, c] = fish.rgb(64, 84, 112) * (0.5 + 0.5 * DOT[:, 2:3])
    r = ((MOTE[:, 1] * rows - 0.3 * t * (0.4 + MOTE[:, 2])) % rows).astype(int)
    c = ((MOTE[:, 0] * cols + 0.8 * np.sin(0.5 * t + MOTE[:, 3] * 20)) % cols).astype(int)
    tw = np.sin(t * (1.0 + 2.0 * MOTE[:, 2]) + MOTE[:, 3] * 40)
    chars[r, c] = np.where(tw > 0.7, glyph_id("*"), glyph_id("+"))
    ink[r, c] = fish.rgb(70, 118, 160) * (0.5 + 0.5 * np.clip(tw + 0.4, 0, 1))[:, None]


_sand = None


def sand_live(cam, t):
    """The sea floor, with ripples of sunlight running over it.

    The original modulated the sand's brightness by a pair of crossing waves; baking
    the sand into a static layer lost that. It is a couple of thousand points, so it
    costs almost nothing to emit every frame instead.
    """
    global _sand
    if _sand is None:
        pts, ripple = reef.sand_points()
        fade = np.clip(1.0 - (pts[:, 2] + 1.5) / 6.5, 0.15, 1.0)
        glyph = np.where(ripple, glyph_id("~"), glyph_id("."))
        base = fish.rgb(168, 140, 98) * np.where(ripple, 0.56, 0.36)[:, None]
        _sand = (pts, base, glyph.astype(int), fade)
    pts, base, glyph, fade = _sand
    x, z = pts[:, 0], pts[:, 2]
    net = (np.sin(x * 5.0 + 1.3 * np.sin(z * 3.0 + fish.W3 * t))
           * np.sin(z * 4.0 - fish.W2 * t + x))
    lit = fade * (1 + 1.1 * np.clip(net, 0, 1) ** 2)
    return pts, np.clip(base * lit[:, None], 0, 1), glyph


_coral_cells = None


def coral_live(cam, t):
    """Corals leaning in the surge, as cells that shift with the water."""
    global _coral_cells
    if _coral_cells is None:
        out = []
        for x, y, z, size, tone, seed in reef.CORALS:
            bx, by, shrink = cam.project(np.array([[x, y, z]]))
            if not (-300 < bx[0] < cam.w + 300):
                continue
            px = size * shrink[0]
            col = tone * np.clip(0.52 - 0.10 * z, 0.22, 0.50)
            twigs, buds = reef.coral_shape(seed)
            cells, cols, glyphs, highs = [], [], [], []
            for x0, y0, x1, y1, level in twigs:
                n = int(max(abs(x1 - x0) * px / CW, abs(y1 - y0) * px / CH) * 1.6) + 2
                f = np.linspace(0, 1, n)
                hx, hy = x0 + (x1 - x0) * f, y0 + (y1 - y0) * f
                cells.append(np.stack([(bx[0] + px * hx) / CW, (by[0] - px * hy) / CH,
                                       np.full(n, z)], -1))
                cols.append(np.repeat((col * (0.55 + 0.1 * (5 - level)))[None], n, 0))
                glyphs.append(np.full(n, line_glyph(x1 - x0, -(y1 - y0))))
                highs.append(hy)
            for bxx, byy, round_ in buds:
                cells.append(np.array([[(bx[0] + px * bxx) / CW, (by[0] - px * byy) / CH, z]]))
                cols.append(np.minimum(col * 1.5, 1)[None])
                glyphs.append(np.array([glyph_id("o" if round_ else "*")]))
                highs.append(np.array([byy]))
            out.append((np.concatenate(cells), np.concatenate(cols),
                        np.concatenate(glyphs).astype(int), np.concatenate(highs),
                        px, float(seed)))
        _coral_cells = out

    pos, col, gly = [], [], []
    for cells, cols, glyphs, highs, px, seed in _coral_cells:
        lean = 0.06 * np.sin(fish.W1 * t + 0.7 * seed) * np.clip(highs, 0, None) ** 1.5
        shifted = cells.copy()
        shifted[:, 0] += lean * px / CW
        pos.append(shifted)
        col.append(cols)
        gly.append(glyphs)
    if not pos:
        return np.zeros((0, 3)), np.zeros((0, 3)), np.zeros(0, int)
    return (np.concatenate(pos), np.concatenate(col), np.concatenate(gly))


def overlay_cells(cam, t, sw, specks=True):
    """Specks, the morsel and crumbs as cell-space points, for the GPU path.

    These are placed on the grid rather than in the world -- the drifting specks have
    no 3D position at all -- so they go to the GPU as cell coordinates plus a depth.
    Returns (positions (n,3) = col,row,z), colours (n,3), glyphs (n,).
    """
    rows, cols = cam.rows, cam.cols
    FAR = SPECK_Z
    pos, col, gly = [], [], []

    if specks:
        # drifting motes: backdrop, so they are left out when the tank is drawn as an
        # overlay on top of a real wallpaper
        r = (DOT[:, 1] * rows + 0.18 * t * (0.5 + DOT[:, 2])) % rows
        c = (DOT[:, 0] * cols + 0.25 * np.sin(0.4 * t + DOT[:, 2] * 9)) % cols
        pos.append(np.stack([c.astype(int), r.astype(int), np.full(len(r), FAR)], -1))
        col.append(fish.rgb(64, 84, 112) * (0.5 + 0.5 * DOT[:, 2:3]))
        gly.append(np.full(len(r), glyph_id(".")))

        r = (MOTE[:, 1] * rows - 0.3 * t * (0.4 + MOTE[:, 2])) % rows
        c = (MOTE[:, 0] * cols + 0.8 * np.sin(0.5 * t + MOTE[:, 3] * 20)) % cols
        tw = np.sin(t * (1.0 + 2.0 * MOTE[:, 2]) + MOTE[:, 3] * 40)
        pos.append(np.stack([c.astype(int), r.astype(int), np.full(len(r), FAR)], -1))
        col.append(fish.rgb(70, 118, 160) * (0.5 + 0.5 * np.clip(tw + 0.4, 0, 1))[:, None])
        gly.append(np.where(tw > 0.7, glyph_id("*"), glyph_id("+")))

    def at(where, glyph, tone):
        sx, sy, _ = cam.project(np.asarray(where, float)[None])
        cc, rr = int(sx[0] // CW), int(sy[0] // CH)
        if 0 <= cc < cols and 0 <= rr < rows:
            pos.append(np.array([[cc, rr, where[2]]]))
            col.append(np.clip(np.asarray(tone, float), 0, 1)[None])
            gly.append(np.array([glyph_id(glyph)]))

    if sw.cursor is not None and sw.interest > 0.08 and sw.cooldown <= 0:
        heat = min(1.0, sw.interest * 1.3)
        glint = 1.0 if sw.stare > 0 else 0.0
        tone = heat * (0.85 + 0.15 * np.sin(9.0 * t))
        flick = int(t * 5) % 2
        core = [(0, 0, "@", 1.3), (-1, 0, "8", 1.0), (1, 0, "8", 1.0),
                (-2, 0, "~-"[flick], 0.7), (2, 0, "-~"[flick], 0.7),
                (0, -1, "o", 0.8), (0, 1, "o", 0.6)]
        rays = [(-1, -1, "\\", 1.0), (1, -1, "/", 1.0), (-1, 1, "/", 1.0), (1, 1, "\\", 1.0),
                (0, -2, "|", 0.9), (0, 2, "|", 0.9), (-3, 0, "-", 0.9), (3, 0, "-", 0.9)]
        base = np.asarray(sw.cursor, float)
        for dc, dr, glyph, dim in core:
            at(base + np.array([dc * CW / cam.scale, -dr * CH / cam.scale, 0.0]), glyph,
               PREY_INK * dim * tone * (1 + glint) + 0.5 * glint)
        if glint > 0:
            for dc, dr, glyph, dim in rays:
                at(base + np.array([dc * CW / cam.scale, -dr * CH / cam.scale, 0.0]), glyph,
                   PREY_INK * dim * glint + 0.5 * glint)
    for where, age in sw.crumbs:
        at(where, ".", PREY_INK * 0.9 * (1 - age / 0.7))

    if not pos:
        empty = np.zeros((0, 3))
        return empty, empty, np.zeros(0, int)
    return (np.concatenate(pos).astype(float), np.concatenate(col),
            np.concatenate(gly).astype(int))


def shafts(cam, chars, ink, t):
    """Slanted beams of sunlight standing in the water; whatever is inside one is brighter."""
    cx = (np.arange(cam.cols) + 0.5) * CW
    cy = (np.arange(cam.rows) + 0.5) * CH
    lit = np.zeros((cam.rows, cam.cols))
    for k, x0 in enumerate(BEAMS):
        foot = cam.w / 2 + 0.85 * cam.scale * (x0 + 0.22 * np.sin(fish.W1 * t + 1.7 * k)
                                               - cam.centre[0])
        lean = foot - 0.30 * (cy - cam.mid_y)
        wide = 60 + 22 * np.sin(fish.W2 * t + 2.3 * k)
        lit += (0.65 + 0.35 * np.sin(fish.W3 * t + 1.1 * k)) * \
            np.exp(-((cx[None, :] - lean[:, None]) / wide) ** 2)
    lit *= np.clip(1.15 - 0.55 * cy / cam.h, 0.3, 1)[:, None]      # light dies with depth
    ink *= (1 + 0.42 * lit)[..., None]
    np.clip(ink, 0, 1, out=ink)
    rr, cc = np.indices(lit.shape)
    dust = (chars == 0) & (lit > 0.30) & ((rr * 2 + cc) % 5 == 0)
    chars[dust] = glyph_id("/")
    ink[dust] = fish.rgb(46, 84, 108) * lit[dust, None]


def draw_arms(cam, chars, ink, zbuf, arms):
    """Each arm is a soft tube: a broken bright rim, a quiet fill, a pale tip."""
    line, thick, depth, glow = arms
    fine = np.linspace(0, line.shape[1] - 1, 54)
    lo = fine.astype(int).clip(0, line.shape[1] - 2)
    w = (fine - lo)[None, :, None]
    line = line[:, lo] * (1 - w) + line[:, lo + 1] * w
    glow = glow[:, lo] * (1 - w[..., 0]) + glow[:, lo + 1] * w[..., 0]
    thick = np.interp(fine, np.arange(len(thick)), thick)
    u = fine / fine[-1]
    sx, sy, shrink = cam.project(line.reshape(-1, 3))
    sx, sy, shrink = (a.reshape(line.shape[:2]) for a in (sx, sy, shrink))
    rad = thick[None] * shrink
    cx = (np.arange(cam.cols) + 0.5) * CW
    cy = (np.arange(cam.rows) + 0.5) * CH
    for i in np.argsort(-line[:, :, 2].mean(1)):
        c0, c1 = int((sx[i] - rad[i]).min() // CW), int((sx[i] + rad[i]).max() // CW) + 1
        r0, r1 = int((sy[i] - rad[i]).min() // CH), int((sy[i] + rad[i]).max() // CH) + 1
        c0, c1 = max(c0, 0), min(c1, cam.cols)
        r0, r1 = max(r0, 0), min(r1, cam.rows)
        if c0 >= c1 or r0 >= r1:
            continue
        ox = cx[None, c0:c1, None] - sx[i][None, None, :]
        oy = cy[r0:r1, None, None] - sy[i][None, None, :]
        d = np.sqrt(ox * ox + oy * oy) / rad[i][None, None, :]
        j = d.argmin(-1)
        d = np.take_along_axis(d, j[..., None], -1)[..., 0]
        ox = np.take_along_axis(ox, j[..., None], -1)[..., 0]
        oy = np.take_along_axis(oy, j[..., None], -1)[..., 0]
        z = line[i, j, 2]
        hit = (d < 1.0) & (z < zbuf[r0:r1, c0:c1])
        if not hit.any():
            continue
        tipness = fish.ease((u[j] - 0.30) / 0.62)
        tone = (reef.STEM * (1 - tipness[..., None]) + reef.TIP * tipness[..., None]) * depth[i]
        lit = np.clip(0.5 - 0.5 * (ox * 0.8 + oy * 0.6) / np.maximum(rad[i][j], 1), 0, 1)
        rim = d > 0.70
        broken = np.sin(j * 0.9 + i * 2.1) > -0.86
        shine = glow[i][j] * (0.55 + 0.45 * depth[i]) * (0.25 + 0.75 * tipness)
        value = 0.14 + 0.24 * lit + 0.42 * tipness ** 2 + 0.25 * shine
        g = np.where(rim, line_glyph(-oy, ox),
                     reef.FILL_G[np.clip((value * 1.25 * len(reef.FILL_G)).astype(int),
                                         0, len(reef.FILL_G) - 1)])
        col = np.where(rim[..., None],
                       tone * (0.50 + 0.38 * lit[..., None] + 0.22 * tipness[..., None]) *
                       np.where(broken, 1.0, 0.45)[..., None],
                       tone * (0.16 + 0.26 * lit[..., None] + 0.5 * tipness[..., None] ** 2))
        col = np.clip(col + reef.GLOW_INK * shine[..., None] *
                      np.where(rim, 0.62, 0.34)[..., None], 0, 1)
        view = (slice(r0, r1), slice(c0, c1))
        chars[view][hit], ink[view][hit], zbuf[view][hit] = g[hit], col[hit], z[hit]


def draw_morsel(cam, chars, ink, zbuf, where, heat, glint, t):
    """The cursor, seen as food: one warm letter with a whisker that flicks."""
    sx, sy, _ = cam.project(np.asarray(where, float)[None])
    c, r = int(sx[0] // CW), int(sy[0] // CH)
    z = where[2]
    tone = heat * (0.85 + 0.15 * np.sin(9.0 * t))
    flick = int(t * 5) % 2
    core = [(0, 0, "@", 1.3), (-1, 0, "8", 1.0), (1, 0, "8", 1.0),
            (-2, 0, "~-"[flick], 0.7), (2, 0, "-~"[flick], 0.7),
            (0, -1, "o", 0.8), (0, 1, "o", 0.6)]
    rays = [(-1, -1, "\\", 1.0), (1, -1, "/", 1.0), (-1, 1, "/", 1.0), (1, 1, "\\", 1.0),
            (0, -2, "|", 0.9), (0, 2, "|", 0.9), (-3, 0, "-", 0.9), (3, 0, "-", 0.9)]
    for dc, dr, glyph, dim in core:
        cc, rr = c + dc, r + dr
        if 0 <= cc < cam.cols and 0 <= rr < cam.rows and zbuf[rr, cc] > z - 0.05:
            chars[rr, cc] = glyph_id(glyph)
            ink[rr, cc] = np.clip(PREY_INK * dim * tone * (1 + glint) + 0.5 * glint, 0, 1)
    if glint > 0:
        for dc, dr, glyph, dim in rays:
            cc, rr = c + dc, r + dr
            if 0 <= cc < cam.cols and 0 <= rr < cam.rows and zbuf[rr, cc] > z - 0.05:
                chars[rr, cc] = glyph_id(glyph)
                ink[rr, cc] = np.clip(PREY_INK * dim * glint + 0.5 * glint, 0, 1)


def draw_crumbs(cam, chars, ink, zbuf, crumbs):
    for where, age in crumbs:
        fade = 1 - age / 0.7
        sx, sy, _ = cam.project(np.asarray(where, float)[None])
        c, r = int(sx[0] // CW), int(sy[0] // CH)
        if 0 <= c < cam.cols and 0 <= r < cam.rows and zbuf[r, c] > where[2] - 0.05:
            chars[r, c] = glyph_id(".")
            ink[r, c] = np.clip(PREY_INK * 0.9 * fade, 0, 1)


# ---- the static layer --------------------------------------------------------------
def build_background(cam):
    """Sand, corals, rocks and the anemone's body, rasterised once with their depths."""
    chars = np.zeros((cam.rows, cam.cols), int)
    ink = np.zeros((cam.rows, cam.cols, 3))
    owner = np.zeros((cam.rows, cam.cols), int)
    zbuf = np.full((cam.rows, cam.cols), 99.0)

    # The sand and the corals are no longer baked here: both move with time (caustics
    # and the surge) even though the camera does not, so they are emitted per frame.
    stone = [np.concatenate([a, b]) for a, b in zip(reef.rocks(), reef.foot())]
    _depth_pass(cam, chars, ink, owner, zbuf, stone)
    return dict(chars=chars, ink=ink, owner=owner, zbuf=zbuf)


# ---- a frame -----------------------------------------------------------------------
class Renderer:
    def __init__(self, w, h):
        atlas()
        self.cam = Camera(w, h)
        self.bg = build_background(self.cam)
        self.bg_mask = self.bg["chars"] != 0
        self.pixels = np.zeros((self.cam.rows, CH, self.cam.cols, CW, 3), np.uint8)
        self.image = self.pixels.reshape(self.cam.rows * CH, self.cam.cols * CW, 3)

    def grid(self, t, sw, arms):
        cam = self.cam
        chars = np.zeros((cam.rows, cam.cols), int)
        ink = np.zeros((cam.rows, cam.cols, 3))
        water(cam, chars, ink, t)
        m = self.bg_mask
        chars[m] = self.bg["chars"][m]
        ink[m] = self.bg["ink"][m]
        owner = self.bg["owner"].copy()
        zbuf = self.bg["zbuf"].copy()

        # the sand and corals are emitted per frame (they ripple and sway), so the CPU
        # renderer has to draw them too -- they are no longer in the baked layer
        sand_pts, sand_col, sand_gly = sand_live(cam, t)
        sx, sy, _ = cam.project(sand_pts)
        c, r = np.floor(sx / CW).astype(int), np.floor(sy / CH).astype(int)
        ok = (c >= 0) & (c < cam.cols) & (r >= 0) & (r < cam.rows)
        ok &= np.where(ok, zbuf[np.clip(r, 0, cam.rows - 1),
                                np.clip(c, 0, cam.cols - 1)] > DECOR_Z, False)
        chars[r[ok], c[ok]] = sand_gly[ok]
        ink[r[ok], c[ok]] = sand_col[ok]

        cor_pos, cor_col, cor_gly = coral_live(cam, t)
        if len(cor_pos):
            c = np.floor(cor_pos[:, 0]).astype(int)
            r = np.floor(cor_pos[:, 1]).astype(int)
            ok = (c >= 0) & (c < cam.cols) & (r >= 0) & (r < cam.rows)
            chars[r[ok], c[ok]] = cor_gly[ok]
            ink[r[ok], c[ok]] = cor_col[ok]
            owner[r[ok], c[ok]] = BACK

        place, yaw, pitch = sw.pose()
        fish.set_pose(**sw.shape())
        _depth_pass(cam, chars, ink, owner, zbuf, fish.build(t, place, yaw, pitch))

        # outline: where the fish's flank meets water, draw the edge in its own tone
        solid = np.pad(owner == FISH, 1)
        up, down = ~solid[:-2, 1:-1], ~solid[2:, 1:-1]
        left, right = ~solid[1:-1, :-2], ~solid[1:-1, 2:]
        here = owner == FISH
        edge = np.full((cam.rows, cam.cols), -1)
        edge[here & up & (owner != FIN)] = glyph_id("-")
        edge[here & down] = glyph_id("_")
        edge[here & left] = glyph_id("(")
        edge[here & left & up] = glyph_id("/")
        edge[here & left & down] = glyph_id("\\")
        edge[here & right & up] = glyph_id("\\")
        edge[here & right & down] = glyph_id("/")
        near_fin = np.pad(owner == FIN, 1)
        touching = (near_fin[:-2, 1:-1] | near_fin[2:, 1:-1] |
                    near_fin[1:-1, :-2] | near_fin[1:-1, 2:])
        soft = np.random.default_rng(int(t * 3)).random((cam.rows, cam.cols)) > 0.12
        sel = (edge >= 0) & ~touching & soft            # a soft, broken line
        chars[sel] = edge[sel]
        ink[sel] = ink[sel] * 0.78 + 0.1

        draw_arms(cam, chars, ink, zbuf, arms)
        shafts(cam, chars, ink, t)
        if sw.cursor is not None and sw.interest > 0.08 and sw.cooldown <= 0:
            draw_morsel(cam, chars, ink, zbuf, sw.cursor, min(1.0, sw.interest * 1.3),
                        1.0 if sw.stare > 0 else 0.0, t)
        draw_crumbs(cam, chars, ink, zbuf, sw.crumbs)
        return chars, ink

    def paint(self, chars, ink):
        """Composite the occupied cells into the BGR frame buffer.

        Integer maths throughout. Multiplying the float32 glyph mask by the float64 ink
        built a (cells, 18, 12, 3) float64 block -- about 21MB a frame -- only to cast it
        straight back to uint8. In uint16 the same product is a quarter of the bytes.
        """
        self.pixels[...] = 0
        r, c = np.nonzero(chars)
        if len(r) == 0:
            return self.image
        glyph = atlas_u16()[chars[r, c]]                   # (n, CH, CW) 0..255
        bgr = (ink[r, c][:, ::-1] * 255).astype(np.uint16)  # the DIB wants blue first
        block = (glyph[..., None] * bgr[:, None, None, :]) >> 8
        self.pixels[r, :, c, :, :] = block.astype(np.uint8)
        return self.image

    def frame(self, t, sw, arms):
        return self.paint(*self.grid(t, sw, arms))
