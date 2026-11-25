"""The place the fish lives in: the anemone it shelters in, its rock, the sand and corals.

The anemone is the one part of the original that had to be genuinely rewritten rather
than thinned. In clownfish.py it was driven by the baked track: `Anemone.at(t)` looked
up `fish_pose(t)` from module state and, if asked for an earlier t than it had reached,
threw itself away and resimulated from scratch. Live there is no t to rewind to, so it
becomes a plain `step(place, velocity, yaw, pitch)` advanced by the frame clock.

Everything else here is static geometry. The camera does not move, so it is rasterised
once into a cached background layer (see render.py) instead of every frame.
"""
import numpy as np

from . import fish
from .fish import BACK

TAU = 2 * np.pi
STEP = 1 / 60           # the simulation's own fixed tick; the frame clock feeds it in slices

# ---- the anemone -------------------------------------------------------------------
# Every arm is a chain of NODES points rooted on a mound. Each tick:
#   1. water drags a node towards the local current (the surge plus the fish's wake),
#      and the arm bends itself a little, a slow wave from root to tip;
#   2. the arm's springiness pulls each node back to its rest place relative to its parent,
#      strongly near the root and weakly near the tip, so the tip arrives last;
#   3. no node may be inside the fish: it is pushed out to the fish's skin;
#   4. the links keep their length.
ARMS, NODES = 50, 14            # the original density: drawing them is free on the GPU
RELAX = 2                       # two relaxation passes, as the original had
SAMPLES = 40                    # points along a drawn arm

_a = np.random.default_rng(11)
ARM_LON = _a.random(ARMS) * TAU
ARM_LAT = 0.06 + 1.12 * _a.random(ARMS) ** 1.15      # 0 = top of the mound
ARM_LEN = 0.62 + 0.40 * _a.random(ARMS)
ARM_PH = _a.random(ARMS) * TAU                       # unused; kept so the dice below match
ARM_CURL = _a.normal(0, 0.42, (ARMS, 2))
_b = np.random.default_rng(31)
ARM_BEAT = _b.integers(4, 9, (ARMS, 2))              # whole waves per surge
ARM_PHASE = _b.random((ARMS, 2)) * TAU
ARM_BEND = 2.2                  # how far an arm curls away from a straight line at rest
ARM_STIFF = (0.20, 2.2, 0.012)  # how hard a link holds its rest angle: root, fall-off, tip
ARM_LIFE = 0.34                 # how hard an arm bends itself, as a water speed
MOUND = np.array([1.30, 0.44, 0.88])
TIP, STEM = fish.rgb(186, 228, 236), fish.rgb(112, 94, 160)
FILL_G = np.array([fish.glyph_id(c) for c in ".::;;ioo08"])
ARM_R = 0.044                   # half the thickness of an arm
FISH_C = np.array([0.08, 0.0, 0.0])
FISH_R = np.array([0.60, 0.30, 0.17])                # the fish as an egg, for touching
GLOW_FADE, GLOW_RUN = 0.7, 13.0
GLOW_INK = np.array([0.80, 1.0, 0.97])

WEED_AT = np.array([0.30, -1.42, 0.24])              # where the anemone sits

# [plankton] motes hanging in the water: everywhere the fish goes, thicker along the
# pass in front of the anemone
_m = np.random.default_rng(23)
MOTES = np.concatenate([_m.uniform([-3.0, -1.25, -1.7], [3.1, 1.0, 1.2], (300, 3)),
                        _m.uniform([-1.9, -0.85, -1.55], [1.9, -0.10, -0.70], (170, 3))])
MOTE_FADE, MOTE_INK = 1.6, np.array([0.50, 0.92, 1.0])
# [wake] sparks shed from the tail at speed; they stay where they were born
WAKE, WAKE_RATE, WAKE_LIFE = 460, 130.0, 2.6


def current(x, t):
    # the surge: water sloshes sideways as a slow wave that crosses the reef
    push = 0.62 * np.sin(fish.W1 * t - 1.3 * x[..., 0]) + 0.16 * np.sin(fish.W3 * t + 2.0 * x[..., 2])
    return np.stack([push,
                     0.10 * np.cos(fish.W1 * t - 1.3 * x[..., 0]),
                     0.08 * np.cos(fish.W2 * t + 3.0 * x[..., 0])], -1)


class Anemone:
    def __init__(self, centre=WEED_AT):
        self.centre = np.asarray(centre, float)
        u = np.linspace(0, 1, NODES)[None, :, None]
        normal = np.stack([np.sin(ARM_LAT) * np.cos(ARM_LON), np.cos(ARM_LAT),
                           np.sin(ARM_LAT) * np.sin(ARM_LON)], -1)
        root = self.centre + normal * MOUND
        aim = normal * np.array([1.05, 0.7, 1.0]) + np.array([0.0, 0.42, 0.0])
        aim[:, 1] = np.maximum(aim[:, 1], 0.30)
        aim = fish.unit(aim)
        side = fish.unit(np.cross(aim, np.array([0.0, 0.0, 1.0])))
        fore = np.cross(aim, side)
        length = ARM_LEN[:, None, None]
        bend = side[:, None] * ARM_CURL[:, :1, None] + fore[:, None] * ARM_CURL[:, 1:, None]
        rest = root[:, None] + length * (aim[:, None] * u + ARM_BEND * bend * (u ** 2 - 0.55 * u ** 3))
        rest[..., 1] += 0.20 * length[..., 0] * u[..., 0] ** 2      # arms curl up towards the light
        self.rest = rest
        self.side, self.fore, self.u = side[:, None], fore[:, None], u[..., 0]
        self.offset = np.diff(rest, axis=1)          # node i+1 relative to node i, at rest
        self.link = np.linalg.norm(self.offset, axis=-1)
        self.stiff = (ARM_STIFF[0] * (1 - u[0, 1:, 0]) ** ARM_STIFF[1] + ARM_STIFF[2])[None, :, None]
        self.drag = (1.2 + 4.2 * u[0, :, 0] ** 1.2)[None, :, None]   # the tip feels the water most
        self.back = (root[:, 2] - self.centre[2] + MOUND[2]) / (2 * MOUND[2])
        self.x, self.v = rest.copy(), np.zeros_like(rest)
        self.glow = np.zeros(rest.shape[:2])         # how brightly each node shines
        self.clock = 0.0
        self.owed = 0.0                              # unspent frame time, in seconds
        # plankton: how brightly each mote burns, and how far the wake has dragged it
        self.lit = np.zeros(len(MOTES))
        self.off = np.zeros((len(MOTES), 3))
        # wake: a ring of sparks, each with the moment it was born
        self.wake = np.zeros((WAKE, 3))
        self.born = np.full(WAKE, -99.0)
        self.sparks_owed = 0.0
        self.dice = np.random.default_rng(7)

    def settle(self, ticks=200):
        """Let the arms find the surge before the first frame is shown."""
        still = np.zeros(3)
        for _ in range(ticks):
            self.tick(np.array([9.0, 9.0, 9.0]), still, 0.0, 0.0)

    def advance(self, dt, place, velocity, yaw, pitch):
        """Run the fixed-tick simulation over a frame of real time."""
        self.owed = min(self.owed + dt, 0.25)        # after a long stall, skip rather than catch up
        while self.owed >= STEP:
            self.owed -= STEP
            self.tick(place, velocity, yaw, pitch)

    def tick(self, place, velocity, yaw, pitch):
        self.clock += STEP
        t = self.clock
        x, v = self.x, self.v
        touched = np.zeros(x.shape[:2], bool)
        flow = current(x, t)
        near = np.exp(-((x - place) ** 2).sum(-1) / 0.10)
        flow = flow + 0.9 * near[..., None] * velocity         # the fish drags water along
        # an arm is a muscle, not a reed: a slow bend runs root to tip, each arm in its own time
        beat = ARM_BEAT[:, None, :] * TAU / fish.SURGE * t - 5.0 * self.u[..., None] + ARM_PHASE[:, None, :]
        own = np.sin(beat[..., :1]) * self.side + 0.7 * np.sin(beat[..., 1:]) * self.fore
        flow = flow + ARM_LIFE * (self.u ** 0.7)[..., None] * own
        v = v + self.drag * (flow - v) * STEP
        v[:, 0] = 0
        x = x + v * STEP
        for _ in range(RELAX):
            for i in range(1, NODES):
                want = x[:, i - 1] + self.offset[:, i - 1]
                x[:, i] += self.stiff[0, i - 1] * (want - x[:, i])
            # the fish: go to its own frame, where it is an egg; anything inside moves to the shell
            local = fish.rot_z(fish.rot_y(x - place, -yaw), -pitch)
            e = (local - FISH_C) / (FISH_R + ARM_R)
            d = np.linalg.norm(e, axis=-1, keepdims=True)
            inside = d < 1
            touched |= inside[..., 0]
            out = np.where(inside, e / np.maximum(d, 1e-6), e) * (FISH_R + ARM_R) + FISH_C
            x = np.where(inside, x + 0.5 * (fish.rot_y(fish.rot_z(out, pitch), yaw) + place - x), x)
            x[:, 0] = self.rest[:, 0]
            for i in range(1, NODES):
                x[:, i] = x[:, i - 1] + fish.unit(x[:, i] - x[:, i - 1]) * self.link[:, i - 1, None]
        self.v = (x - self.x) / STEP * 0.985
        fast = np.linalg.norm(self.v, axis=-1, keepdims=True)
        self.v *= np.minimum(1.0, 1.1 / np.maximum(fast, 1e-6))   # water does not let an arm whip
        self.v[:, 0] = 0
        self.x = x
        # an arm the fish has touched lights up where it was touched; the light runs out to the
        # tip and fades. A fish that rushes past wakes the nearest arms too.
        speed = np.linalg.norm(velocity)
        rush = np.clip((speed - 0.55) * 3.0, 0, 1) * np.exp(-((x - place) ** 2).sum(-1) / 0.16)
        glow = self.glow * np.exp(-STEP / GLOW_FADE)
        glow[:, 1:] += GLOW_RUN * STEP * (glow[:, :-1] - glow[:, 1:])
        self.glow = np.maximum(glow, np.maximum(touched * 1.0, 0.8 * rush))

        # [plankton] motes flash when the fish rushes by, the faster the brighter; its
        # wake drags them along for a moment, then they drift back and go dark
        step_move = velocity * STEP
        gap = ((MOTES + self.off - place) ** 2).sum(-1)
        flash = (np.clip((speed - 0.42) * 2.4, 0, 1) * np.exp(-gap / 0.22)
                 + 0.35 * np.exp(-gap / 0.03))
        self.lit = np.maximum(self.lit * np.exp(-STEP / MOTE_FADE), np.clip(flash, 0, 1))
        self.off = (self.off * np.exp(-STEP / 2.2)
                    + 0.55 * np.exp(-gap / 0.10)[:, None] * step_move)

        # [wake] the faster it goes, the more sparks it sheds behind its tail
        self.sparks_owed += WAKE_RATE * np.clip((speed - 0.50) / 0.30, 0, 1) * STEP
        if speed > 1e-6:
            heading = velocity / speed
            while self.sparks_owed >= 1:
                self.sparks_owed -= 1
                k = int(self.dice.integers(WAKE))
                self.wake[k] = (place - heading * self.dice.uniform(0.30, 0.85)
                                + self.dice.normal(0, [0.07, 0.20, 0.16]))
                self.born[k] = t
        else:
            self.sparks_owed = min(self.sparks_owed, 1.0)
        self.wake[:, 1] += 0.04 * STEP          # they rise, very slowly

    def motes(self):
        """Plankton and wake sparks as (points, colours, glyphs) ready to draw."""
        pts = MOTES + self.off + 0.03 * np.sin(fish.W2 * self.clock + MOTES[:, ::-1] * 9.0)
        age = (self.clock - self.born) / WAKE_LIFE
        alive = (age >= 0) & (age < 1)
        n_alive = int(alive.sum())
        if n_alive:
            spark_age = age[alive]
            pts = np.concatenate([pts, self.wake[alive]])
            glow = np.concatenate([self.lit,
                                   (1 - spark_age) ** 1.4
                                   * (0.75 + 0.25 * np.sin(40 * spark_age
                                                           + np.arange(n_alive)))])
        else:
            glow = self.lit
        keep = glow > 0.02                      # a dark mote is not worth a cell
        pts, glow = pts[keep], np.clip(glow[keep], 0, 1)
        col = np.clip(MOTE_INK * (0.16 + 0.84 * glow)[:, None]
                      + 0.5 * np.clip(glow - 0.5, 0, 1)[:, None], 0, 1)
        gly = np.where(glow > 0.55, fish.glyph_id("*"),
                       np.where(glow > 0.18, fish.glyph_id("+"), fish.glyph_id(".")))
        return pts, col, gly.astype(int)

    def curve(self):
        """The arms as smooth curves ready to draw: (line, thickness, depth tone, glow)."""
        uu = np.linspace(0, 1, SAMPLES)
        cap = np.sqrt(np.clip(1 - ((uu - 0.90) / 0.10) ** 2, 0, 1))     # the rounded end
        thick = np.where(uu > 0.90, (ARM_R + 0.006) * cap + 0.004,
                         ARM_R + 0.008 - 0.014 * uu + 0.012 * np.exp(-((uu - 0.86) / 0.08) ** 2))
        fine = np.linspace(0, NODES - 1, SAMPLES)
        i0 = np.clip(fine.astype(int), 0, NODES - 2)
        f = (fine - i0)[None, :, None]
        p0, p1 = self.x[:, i0], self.x[:, i0 + 1]
        m0 = (self.x[:, np.clip(i0 + 1, 0, NODES - 1)] - self.x[:, np.clip(i0 - 1, 0, NODES - 1)]) / 2
        m1 = (self.x[:, np.clip(i0 + 2, 0, NODES - 1)] - self.x[:, i0]) / 2
        line = ((2 * f ** 3 - 3 * f ** 2 + 1) * p0 + (f ** 3 - 2 * f ** 2 + f) * m0 +
                (-2 * f ** 3 + 3 * f ** 2) * p1 + (f ** 3 - f ** 2) * m1)
        glow = self.glow[:, i0] * (1 - f[..., 0]) + self.glow[:, i0 + 1] * f[..., 0]
        return line, thick, np.clip(1.1 - 0.7 * self.back, 0.30, 1.0), glow


# ---- the bottom: sand, rocks, corals -----------------------------------------------
FLOOR = -1.42
ROCKS = [(0.30, 0.26, 1.70, 0.40, 1.05, 0.5),        # the anemone's own rock
         (2.25, 0.80, 0.95, 0.80, 0.85, 1.0), (-3.3, 1.5, 0.8, 0.5, 0.7, 2.0),
         (1.2, 2.9, 1.4, 0.9, 0.8, 3.0), (-1.2, 3.3, 1.1, 0.65, 0.7, 4.0),
         (4.6, 1.9, 0.9, 0.6, 0.7, 5.0), (-4.9, 0.4, 0.7, 0.45, 0.6, 6.0)]
CORALS = [(-2.25, FLOOR, 0.85, 0.34, fish.rgb(236, 96, 132), 3),
          (-2.85, FLOOR, 0.45, 0.24, fish.rgb(240, 150, 90), 8),
          (2.60, FLOOR + 0.72, 1.25, 0.27, fish.rgb(80, 200, 200), 5),
          (1.0, FLOOR, 2.3, 0.30, fish.rgb(180, 110, 220), 12),
          (-0.9, FLOOR + 0.5, 3.4, 0.30, fish.rgb(236, 96, 132), 21),
          (4.3, FLOOR, 0.9, 0.25, fish.rgb(240, 150, 90), 30)]
ROCK_G = np.array([fish.glyph_id(c) for c in " . .:.:;:=+"])

N_ROCK = (40, 96)       # denser again: the GPU projects these, not numpy
N_FOOT = (36, 104)


def rocks():
    parts = []
    nth, nph = N_ROCK
    for cx, cz, rx, ry, rz, seed in ROCKS:
        th, ph = [v.ravel() for v in np.meshgrid(np.linspace(0.02, 1.57, nth),
                                                 np.linspace(0, TAU, nph, endpoint=False),
                                                 indexing="ij")]
        lump = 1 + 0.10 * np.sin(5 * ph + seed) * np.sin(3 * th + seed) + 0.06 * np.sin(11 * ph + 2 * seed)
        n = np.stack([np.sin(th) * np.cos(ph), np.cos(th), np.sin(th) * np.sin(ph)], -1)
        p = np.array([cx, FLOOR, cz]) + n * lump[:, None] * np.array([rx, ry, rz])
        lit = np.clip(fish.unit(n / np.array([rx, ry, rz])).dot(fish.LIGHT), 0, 1)
        far = np.clip(1.0 - (cz + 1.0) / 6.0, 0.35, 1.0)
        parts.append((p, fish.rgb(104, 116, 150) * (0.20 + 0.50 * lit[:, None]) * far,
                      0.05 + 0.75 * lit ** 1.5))
    p, col, lum = [np.concatenate([part[i] for part in parts]) for i in range(3)]
    return [p, p, col, lum, np.full(len(p), BACK), np.zeros(len(p), bool)]


def foot():
    """The fleshy body of the anemone that the arms grow from, sitting on its rock."""
    nth, nph = N_FOOT
    th, ph = [v.ravel() for v in np.meshgrid(np.linspace(0.02, 1.57, nth),
                                             np.linspace(0, TAU, nph, endpoint=False),
                                             indexing="ij")]
    n = np.stack([np.sin(th) * np.cos(ph), np.cos(th), np.sin(th) * np.sin(ph)], -1)
    p = WEED_AT + n * MOUND - np.array([0.0, 0.06, 0.0])
    lit = np.clip(fish.unit(n / MOUND).dot(fish.LIGHT), 0, 1)
    return [p, p, STEM * (0.14 + 0.30 * lit[:, None]), 0.10 + 0.25 * lit,
            np.full(len(p), BACK), np.zeros(len(p), bool)]


def hash01(a, b):
    return np.modf(np.abs(np.sin(a * 127.1 + b * 311.7) * 43758.5453))[0]


def sand_points():
    """Specks and ripple marks on the bottom, with a flag for which are ripples."""
    ix, iz = np.meshgrid(np.arange(-34, 35), np.arange(-12, 42), indexing="ij")
    ix, iz = ix.ravel(), iz.ravel()
    h = hash01(ix, iz)
    keep = h > 0.52
    x, z = ix * 0.13 + 0.09 * hash01(iz, ix), iz * 0.13 + 0.09 * h
    ripple = np.sin(x * 4.2 + 1.6 * np.sin(z * 2.1)) > 0.72
    keep |= ripple & (hash01(ix + 7, iz) > 0.35)
    pts = np.stack([x, FLOOR + 0.03 * np.sin(x * 1.7 + z), z], -1)[keep]
    return pts, ripple[keep]


_corals = {}


def coral_shape(seed):
    """A branching coral, grown once: every branch forks into two shorter ones.

    Lengths are in units of the first branch, so the shape never depends on the zoom.
    """
    if seed not in _corals:
        rng = np.random.default_rng(seed)
        twigs, buds = [], []

        def grow(x, y, ang, length, level):
            if level == 0:
                buds.append((x, y, rng.random() < 0.6))
                return
            x1, y1 = x + length * np.sin(ang), y + length * np.cos(ang)
            twigs.append((x, y, x1, y1, level))
            spread = 0.35 + 0.3 * rng.random()
            grow(x1, y1, ang - spread, length * (0.62 + 0.2 * rng.random()), level - 1)
            grow(x1, y1, ang + spread * (0.7 + 0.5 * rng.random()),
                 length * (0.62 + 0.2 * rng.random()), level - 1)
            if rng.random() < 0.3:
                grow(x1, y1, ang + 0.1 * rng.normal(), length * 0.6, level - 1)

        grow(0.0, 0.0, 0.0, 1.0, 5)
        _corals[seed] = (np.array(twigs), np.array(buds))
    return _corals[seed]
