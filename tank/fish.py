"""The clownfish itself: geometry, shading and the body wave.

Adapted from ASCII Lab's clownfish.py (sapozhkov/ascii-lab-public). Two changes
from the original:

  * point densities are cut to roughly what a 160x60 character grid can resolve.
    The original sampled the body at 280x120 = 33,600 points to fill maybe 300
    cells, which cost 21ms a frame for detail no glyph could show.
  * the pose comes from a live swimmer each frame instead of a baked track.

SWIM is module state that sway() reads, kept as a dict the way the original had
it: body, fins and eyes all need the same pose, and threading it through every
helper buys nothing.
"""
import numpy as np

TAU = 2 * np.pi

# How finely each part is sampled: (along the part, around or across it). The GPU
# rasterises these as points, so density is nearly free; the original's figures are in
# brackets. What matters is covering every cell the part lands on without holes.
N_BODY = (200, 88)        # (280, 120)
N_DORSAL = (84, 26)       # (110, 30)
N_ANAL = (64, 20)         # (80, 26)
N_PELVIC = (38, 20)       # (46, 26)
N_TAIL = (96, 40)         # (110, 46)
N_PECT = (58, 30)         # (70, 36)
N_EYE = (10, 28)          # (9, 28)

RAMP = "$QB%8&0#@oauenjzt[]{}<>rcvx+=1il!;:,"       # heaviest letters first
FISH, FIN, WEED, BACK = 1, 2, 3, 4                 # who owns a cell

LIGHT = np.array([-0.45, 0.75, -0.48])
LIGHT = LIGHT / np.linalg.norm(LIGHT)

CW, CH = 8, 12                                     # character cell, in pixels


def glyph_id(ch):
    return ord(ch) - 32


RAMP_G = np.array([glyph_id(c) for c in RAMP])


def ease(x):
    x = np.clip(x, 0.0, 1.0)
    return (3 - 2 * x) * x * x


def rgb(*c):
    return np.array(c, float) / 255


def unit(v):
    return v / np.maximum(np.sqrt((v * v).sum(-1, keepdims=True)), 1e-9)


def rot_y(p, a):
    c, s = np.cos(a), np.sin(a)
    x, y, z = np.moveaxis(p, -1, 0)
    return np.stack([c * x + s * z, y, -s * x + c * z], -1)


def rot_x(p, a):
    c, s = np.cos(a), np.sin(a)
    x, y, z = np.moveaxis(p, -1, 0)
    return np.stack([x, c * y - s * z, s * y + c * z], -1)


def rot_z(p, a):
    c, s = np.cos(a), np.sin(a)
    x, y, z = np.moveaxis(p, -1, 0)
    return np.stack([c * x - s * y, s * x + c * y, z], -1)


def line_glyph(dx, dy):
    # which slash lies closest to a direction on the screen, judged in cells
    dx, dy = dx / CW, dy / CH
    steep, flat = np.abs(dy) > 2.0 * np.abs(dx), np.abs(dx) > 2.0 * np.abs(dy)
    return np.select([steep, flat, dx * dy < 0],
                     [glyph_id("|"), glyph_id("-"), glyph_id("/")], glyph_id("\\"))


# ---- the body ----------------------------------------------------------------------
ORANGE, DEEP, FACE = rgb(255, 122, 22), rgb(238, 66, 18), rgb(255, 168, 52)
WHITE, DARK, RIM = rgb(250, 248, 240), rgb(84, 62, 112), rgb(205, 222, 255)
PROFILE_S = np.array([0.0, 0.03, 0.08, 0.16, 0.30, 0.45, 0.60, 0.75, 0.88, 0.96, 1.0])
PROFILE_H = np.array([0.0, 0.075, 0.125, 0.175, 0.222, 0.238, 0.218, 0.165, 0.10, 0.078, 0.075])
PROFILE_SLOPE = np.gradient(PROFILE_H, PROFILE_S)
TAIL_ROOT = 0.53                        # where the tail fin starts, in the fish's own x
PECT_HZ = 1.3
SURGE = 22.0                            # seconds the water's slow sway takes to come round
W1, W2, W3 = 3 * TAU / SURGE, 4 * TAU / SURGE, 8 * TAU / SURGE

# the pose being drawn, written once a frame by the swimmer
SWIM = dict(effort=1.0, turn=0.0, phase=0.0, hz=0.95, t=0.0, brake=0.0)


def set_pose(effort, turn, phase, hz, t, brake):
    SWIM.update(effort=effort, turn=turn, phase=phase, hz=hz, t=t, brake=brake)


def half_height(s):
    return np.interp(s, PROFILE_S, PROFILE_H)


def half_width(s):
    return half_height(s) * (0.58 - 0.22 * s)


def sway(s, t):
    # the body bends sideways as a wave running head to tail; s may exceed 1 on the tail fin.
    # The head swings a little the other way; a turn bends the whole spine into a C.
    beat = SWIM["phase"] - TAU * min(SWIM["hz"], 1.0) * (SWIM["t"] - t)
    wave = (0.014 + 0.105 * s ** 2) * np.sin(beat - 3.4 * s)
    return SWIM["effort"] * wave + SWIM["turn"] * (s - 0.25) ** 2 * np.sign(s - 0.25)


def stripes(s, v):
    # 0 orange, 1 white, 2 dark border; v is the height on the flank, -1 belly .. 1 back
    bulge = np.exp(-(v / 0.55) ** 2)
    bands = [(0.215 + 0.035 * v * v, 0.335 + 0.02 * v * v),
             (0.515 - 0.055 * bulge, 0.655 - 0.02 * bulge),
             (0.905, 0.985)]
    kind = np.zeros(np.broadcast(s, v).shape, int)
    for a, b in bands:
        kind = np.where((s > a - 0.016) & (s < b + 0.016), 2, kind)
        kind = np.where((s > a) & (s < b), 1, kind)
    return kind


# Most of the fish is identical every frame: the parameter grids, the profile, the
# surface normals, the stripes and the base colours. Only the body wave and the pose
# move. Rebuilding all of it per frame cost 6.9ms for nothing.
_const = {}


def _body_const():
    if "body" not in _const:
        ns, nth = N_BODY
        s, th = np.meshgrid(np.linspace(0.0, 1.0, ns),
                            np.linspace(0, TAU, nth, endpoint=False), indexing="ij")
        s, th = s.ravel(), th.ravel()
        h, w = half_height(s), half_width(s)
        v = np.sin(th)
        base_p = np.stack([s - 0.45, h * v, w * np.cos(th)], -1)   # z gets the wave added
        n = unit(np.stack([-np.interp(s, PROFILE_S, PROFILE_SLOPE) * w, w * v,
                           h * np.cos(th)], -1))
        kind = stripes(s, v)
        base = ORANGE * (1 - 0.5 * ease(v[:, None] * 0.9)) + DEEP * 0.5 * ease(v[:, None] * 0.9)
        base = np.where((s < 0.2)[:, None], base * 0.45 + FACE * 0.55, base)
        col = np.select([(kind == 1)[:, None], (kind == 2)[:, None]], [WHITE, DARK], base)
        _const["body"] = (s, base_p, n, col, kind)
    return _const["body"]


def body(t):
    s, base_p, n, col, kind = _body_const()
    p = base_p.copy()
    p[:, 2] += sway(s, t)
    return p, n, col, kind


def _sheet_const(which, *args):
    """The unmoving part of a fin sheet: its grid, its rest shape, its ray offset."""
    key = (which,) + args
    if key not in _const:
        if which == "dorsal":
            s0, s1, tall, lean = args
            na, nb = N_DORSAL
            a, b = [x.ravel() for x in np.meshgrid(np.linspace(0, 1, na),
                                                   np.linspace(0, 1, nb), indexing="ij")]
            ss = s0 + (s1 - s0) * a
            reach = tall * np.sin(np.pi * a ** 0.8) ** 0.8
            root = np.stack([ss - 0.45, half_height(ss) * 0.93, 0 * ss], -1)
            out = np.stack([lean * reach, reach, 0 * ss], -1)
        elif which == "anal":
            na, nb = N_ANAL
            a, b = [x.ravel() for x in np.meshgrid(np.linspace(0, 1, na),
                                                   np.linspace(0, 1, nb), indexing="ij")]
            ss = 0.62 + 0.25 * a
            reach = 0.135 * np.sin(np.pi * np.clip(a * 0.9 + 0.1, 0, 1)) ** 0.6
            out = np.stack([0.7 * reach, -reach, 0 * ss], -1)
            root = np.stack([ss - 0.45, -half_height(ss) * 0.93, 0 * ss], -1)
        else:                                   # pelvic
            side, = args
            na, nb = N_PELVIC
            a, b = [x.ravel() for x in np.meshgrid(np.linspace(0, 1, na),
                                                   np.linspace(0, 1, nb), indexing="ij")]
            ss = 0.37 + 0.09 * a
            reach = 0.17 * (0.55 + 0.45 * np.sin(np.pi * a) ** 0.7)
            out = np.stack([0.55 * reach, -reach, side * 0.25 * reach], -1)
            root = np.stack([ss - 0.45, -half_height(ss) * 0.9, side * 0.04 + 0 * ss], -1)
        _const[key] = (a, b, root + out * b[:, None], unit(out) * 0.03)
    return _const[key]


# ---- fins --------------------------------------------------------------------------
def fins(t):
    parts = []

    def add(p, q, b, margin=0.74, rim=0.93, key=None):
        # colour and brightness come from b alone, so they are the same every frame
        shade = _const.get(("fincol", key, margin, rim))
        if shade is None:
            col = np.select([(b > rim)[:, None], (b > margin)[:, None]],
                            [RIM, DARK], ORANGE * 0.94 + FACE * 0.06)
            lum = np.select([b > rim, b > margin], [0.9, 0.5], 0.72 + 0.2 * b)
            shade = (col * 0.92, lum)       # the dimming build() used to apply
            _const[("fincol", key, margin, rim)] = shade
        parts.append((p, q, shade[0], shade[1]))

    # dorsal: a spiny front lobe and a soft rear lobe
    for s0, s1, tall, lean in [(0.25, 0.57, 0.15, 0.45), (0.53, 0.88, 0.125, 0.85)]:
        a, b, base_p, qoff = _sheet_const("dorsal", s0, s1, tall, lean)
        p = base_p.copy()
        p[:, 2] = sway(p[:, 0] + 0.45, t - 0.16 * b) + \
            0.05 * b ** 1.5 * np.sin(TAU * 1.1 * t - 8 * a - 3 * (s0 > 0.4))
        add(p, p + qoff, b, margin=0.84, rim=0.96, key=("dorsal", s0))

    # anal fin, under the rear half
    a, b, base_p, qoff = _sheet_const("anal")
    p = base_p.copy()
    p[:, 2] = sway(p[:, 0] + 0.45, t - 0.18 * b) + \
        0.045 * b ** 1.5 * np.sin(TAU * 1.1 * t - 7 * a + 1.0)
    add(p, p + qoff, b, margin=0.72, key="anal")

    # pelvic fins, a pair under the chest
    for side in (-1, 1):
        a, b, base_p, qoff = _sheet_const("pelvic", side)
        s = 0.37 + 0.09 * a
        p = base_p.copy()
        p[:, 2] += sway(s, t - 0.2 * b) + 0.04 * b ** 2 * np.sin(TAU * 0.8 * t + side + 2 * a)
        p[:, 0] += 0.05 * b ** 2 * np.sin(TAU * 0.8 * t + 1.5 + side)
        add(p, p + qoff, b, margin=0.78, rim=2.0, key=("pelvic", side))

    # the tail: a rounded fan that carries the body wave further. The fin is a blade of fixed
    # length; it bends with the wave rather than stretching with it, so walk out from the root
    # in small steps, each turned by the slope of the wave at that place.
    na, nb = N_TAIL
    a, b = [x.ravel() for x in np.meshgrid(np.linspace(-1, 1, na), np.linspace(0, 1, nb),
                                           indexing="ij")]
    phi = 0.95 * a
    r = b * 0.31 * (1 - 0.13 * a * a)
    out = np.linspace(0, 1, nb)
    here = 0.98 + 0.31 * out
    turn = np.clip(np.arctan(np.gradient(sway(here, t - 0.12 * out), here) * (1 + 0.3 * out)),
                   -0.95, 0.95)
    step = out[1] - out[0]
    far = np.concatenate([[0.0], np.cumsum(np.cos(turn[1:]) * step)])
    across = np.concatenate([[0.0], np.cumsum(np.sin(turn[1:]) * step)])
    j = np.arange(len(b)) % nb
    span = 0.31 * (1 - 0.13 * a * a)
    x, y = TAIL_ROOT + span * np.cos(phi) * far[j], 0.075 * a * (1 - b) + r * np.sin(phi)
    p = np.stack([x, y, sway(0.98, t) + span * across[j] +
                  0.03 * b ** 2 * np.sin(TAU * 0.95 * t - 4.2 + 2.5 * np.abs(a))], -1)
    ray = np.stack([np.cos(phi), np.sin(phi), 0 * phi], -1)
    add(p, p + ray * 0.03, b, margin=0.76, rim=0.94, key="tail")

    # pectorals: they beat like oars; only the tail pushes. Sides a third of a stroke apart.
    na, nb = N_PECT
    for side in (-1, 1):
        a, b = [x.ravel() for x in np.meshgrid(np.linspace(-1, 1, na), np.linspace(0, 1, nb),
                                               indexing="ij")]
        beat = TAU * PECT_HZ * t + (0.0 if side > 0 else 2.2) - 2.1 * b - 0.5 * np.abs(a)
        stroke = np.sin(beat) + 0.25 * np.sin(2 * beat + 0.6)      # quick pull, slow return
        tucked = min(SWIM["effort"] - 0.55, 1.0)                   # cruising: held closer
        open_ = 0.72 - 0.20 * tucked + 0.55 * SWIM["brake"] + (0.48 - 0.14 * tucked) * stroke
        back = np.array([0.72, -0.69, 0.0])
        outw = np.array([0.0, 0.0, float(side)])
        up = np.array([0.69, 0.72, 0.0])
        axis = back * np.cos(open_)[:, None] + outw * np.sin(open_)[:, None]
        phi = 0.8 * a
        r = b * 0.21 * (1 - 0.12 * a * a)
        hinge = np.array([0.375 - 0.45, -0.07, side * half_width(0.375) * 0.9])
        ray = axis * np.cos(phi)[:, None] + up * np.sin(phi)[:, None]
        p = hinge + ray * r[:, None] - outw * (0.07 * b ** 2 * np.cos(beat))[:, None]
        add(p, p + ray * 0.03, b, margin=0.74, rim=0.93, key=("pect", side))

    return [np.concatenate([part[i] for part in parts]) for i in range(4)]


def _eye_const():
    if "eyes" not in _const:
        out = []
        nu, nk = N_EYE
        for side in (-1, 1):
            u, k = [x.ravel() for x in np.meshgrid(np.linspace(0, 1, nu),
                                                   np.linspace(0, TAU, nk), indexing="ij")]
            r = 0.041 * u
            ss = 0.115 + r * np.cos(k)
            y = 0.05 + r * np.sin(k)
            z = side * (half_width(ss) * np.sqrt(np.clip(1 - (y / half_height(ss)) ** 2,
                                                         0.05, 1)) + 0.03)
            col = np.where((u < 0.6)[:, None], rgb(58, 34, 30), rgb(255, 190, 96))
            col = np.where(((u > 0.15) & (u < 0.42) & (np.abs(k - 2.2) < 0.45))[:, None],
                           rgb(235, 225, 215), col)
            out.append((ss, np.stack([ss - 0.45, y, z], -1), col,
                        np.where(u < 0.6, 0.62, 0.72)))
        _const["eyes"] = [np.concatenate([o[i] for o in out]) for i in range(4)]
    return _const["eyes"]


def eyes(t):
    s, base_p, col, lum = _eye_const()
    p = base_p.copy()
    p[:, 2] += sway(s, t)
    return p, col, lum


SHINE_DIR = unit(LIGHT + np.array([0.0, 0.0, -1.0]))
SPINE_N = 76                 # samples of the bent-length table
SPINE_SPAN = 1.5

# what in_shade means on a vertex: >= 0 is body, carrying its stripe kind
SHADE_FIN, SHADE_EYE = -1.0, -2.0


def spine(t):
    """How far along its own axis each point of the body really sits.

    A bent spine is no longer than a straight one. Without this the sideways wave adds
    to every length and the tail fin looks stretched in a sprint.
    """
    along = np.linspace(0.0, SPINE_SPAN, SPINE_N)
    slope = np.gradient(sway(along, t), along)
    reach = np.concatenate([[0.0],
                            np.cumsum((along[1] - along[0]) / np.sqrt(1 + slope[1:] ** 2))])
    return along, reach, float(np.interp(0.98, along, reach) - 0.45)


def pose_matrix(yaw, pitch):
    """rot_y(rot_z(v)) as a single 3x3."""
    cz, sz = np.cos(pitch), np.sin(pitch)
    cy, sy = np.cos(yaw), np.sin(yaw)
    return np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]]).dot(
        np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]]))


def facing_of(spin):
    """How square-on the face is to us; drives the highlight sliding over the flank."""
    return float(ease((-spin.dot(np.array([-1.0, 0.0, 0.0]))[2] - 0.35) / 0.45))


def local_const():
    """Everything about a fish vertex that does not change from frame to frame.

    With the lighting done per vertex on the GPU, that is all of it except where the
    point is: normal, colour, brightness, which part it belongs to, and its stripe.
    """
    if "local" not in _const:
        bp, bn, bcol, bkind = body(0.0)
        fp, fq, fcol, flum = fins(0.0)
        ep, ecol, elum = eyes(0.0)
        nb, nf, ne = len(bp), len(fp), len(ep)
        _const["local"] = dict(
            nb=nb, nf=nf, ne=ne, n=nb + nf + ne,
            normal=np.concatenate([bn, np.zeros((nf + ne, 3))]),
            col=np.concatenate([bcol, fcol, ecol]),
            lum=np.concatenate([np.zeros(nb), flum, elum]),
            kind=np.concatenate([np.full(nb, FISH), np.full(nf, FIN),
                                 np.full(ne, FISH)]).astype(float),
            ray=np.concatenate([np.zeros(nb, bool), np.ones(nf, bool),
                                np.zeros(ne, bool)]),
            shade=np.concatenate([bkind.astype(float), np.full(nf, SHADE_FIN),
                                  np.full(ne, SHADE_EYE)]))
    return _const["local"]


def local_moving(t):
    """The only thing that moves: where each point is, in the fish's own frame."""
    bp, _, _, _ = body(t)
    fp, fq, _, _ = fins(t)
    ep, _, _ = eyes(t)
    return bp, fp, fq, ep


def build_local(t):
    """The fish in its own frame: (p, q, normal, colour, lum, kind, ray, shade)."""
    k = local_const()
    bp, fp, fq, ep = local_moving(t)
    return (np.concatenate([bp, fp, ep]), np.concatenate([bp, fq, ep]),
            k["normal"], k["col"], k["lum"], k["kind"], k["ray"], k["shade"])


def build(t, place, yaw, pitch):
    """Every point of the fish, posed and lit on the CPU.

    The GPU renderer does this in a vertex shader instead; this stays so the two can be
    compared, and so the tank still runs if OpenGL is unavailable.
    """
    P, Q, N, C, L, K, R, S = build_local(t)
    along, reach, body_end = spine(t)
    spin = pose_matrix(yaw, pitch)
    facing = facing_of(spin)

    def warp(p):
        p = p.copy()
        p[:, 0] = np.where(p[:, 0] > TAIL_ROOT,
                           body_end + p[:, 0] - TAIL_ROOT,
                           np.interp(p[:, 0] + 0.45, along, reach) - 0.45)
        return p.dot(spin.T) + place

    p, q = warp(P), warp(Q)
    eyes_at = S == SHADE_EYE
    p[eyes_at, 2] -= 0.004
    q[eyes_at, 2] -= 0.004

    col, lum = C.copy(), L.copy()
    on_body = S >= 0
    if on_body.any():
        n = np.nan_to_num(N.dot(spin.T))
        lit = np.clip(n.dot(LIGHT), 0, 1)
        shine = np.clip(n.dot(SHINE_DIR), 0, 1) ** 14 * (0.25 + 0.75 * facing)
        shade = 0.42 + 0.58 * lit
        blum = np.where(S == 2, 0.42, np.where(S == 1, 0.86 + 0.14 * lit, 0.66 + 0.3 * lit))
        bmul = np.where(S == 2, 1.0, np.where(S == 1, 0.82 + 0.18 * lit, shade))
        btone = np.clip(C * bmul[:, None] + 0.75 * shine[:, None] * (S != 2)[:, None], 0, 1)
        blum = np.clip(blum + 0.3 * shine, 0, 1)
        col[on_body] = btone[on_body]
        lum[on_body] = blum[on_body]
    return p, q, col, lum, K, R
