"""Pass 1: the scene drawn into a cell buffer on the GPU.

The CPU renderer spends nearly all its time in two places, and both are things a GPU
does natively:

  * `draw_arms` -- 14ms median and up to 165ms when the anemone spreads out, because it
    loops over arms in Python and builds a (rows x cols x samples) distance array per
    arm. Here each arm is a screen-space tube: two triangles per segment, and the
    fragment shader does the same rim/fill/tip/glow maths per cell.
  * `_depth_pass` -- projecting a point cloud, sorting it back to front and scattering
    it into a z-buffer. That is the hardware's day job, so the points go in as GL_POINTS
    at cell resolution and the depth test picks the winner.

Everything renders at cols x rows (160x60) -- one fragment per character cell -- into
two colour targets: the cell's ink, and its glyph plus who owns it. CellPainter then
blows that up to real pixels.

Depth: the scene's z runs from about -1.5 (nearest) to 6 (far), with 99 meaning empty.
It maps to [0,1] as (z + 2) / 12 with far clamped, so the ordinary GL_LESS test keeps
the nearest, exactly as the CPU `depth < zbuf` did.
"""
import numpy as np

from . import fish, reef, render
from .fish import CH, CW

G_PIPE, G_DASH, G_SLASH, G_BACK = 92, 13, 15, 60

COMMON = """
#version 330
uniform vec2  u_centre;
uniform float u_scale;
uniform float u_mid_y;
uniform vec2  u_screen;     // pixels
uniform vec2  u_grid;       // cols, rows
const float CAM_D = 7.0;
const vec2  CELL = vec2(%d.0, %d.0);

float depth01(float z) { return clamp((z + 2.0) / 12.0, 0.0, 1.0); }

// world point -> pixel position, and the depth we will test with
vec3 project(vec3 v) {
    float shrink = u_scale * CAM_D / (CAM_D + v.z);
    return vec3(u_screen.x * 0.5 + shrink * (v.x - u_centre.x),
                u_mid_y - shrink * (v.y - u_centre.y),
                depth01(v.z));
}

float shrink_at(float z) { return u_scale * CAM_D / (CAM_D + z); }

// a pixel position -> clip space, snapped to the centre of its character cell so a
// point lands in exactly the cell the CPU renderer would have chosen
vec4 cell_clip(vec2 px, float d) {
    vec2 cell = floor(px / CELL) + 0.5;
    return vec4(cell.x / u_grid.x * 2.0 - 1.0,
                1.0 - cell.y / u_grid.y * 2.0,
                d * 2.0 - 1.0, 1.0);
}

int line_glyph(float dx, float dy) {
    dx /= CELL.x; dy /= CELL.y;
    if (abs(dy) > 2.0 * abs(dx)) return %d;
    if (abs(dx) > 2.0 * abs(dy)) return %d;
    return (dx * dy < 0.0) ? %d : %d;
}
""" % (CW, CH, G_PIPE, G_DASH, G_SLASH, G_BACK)

# ---- points: the fish, specks, crumbs ----------------------------------------------
# The whole per-frame transform lives here. The CPU sends the fish in its own frame and
# a dozen numbers; straightening the spine, rotating into the world and lighting the
# body are all done per vertex, which is what a vertex shader is for.
POINT_VERT = COMMON + """
in vec3 in_p;        // where it is, in the fish's own frame
in vec3 in_q;        // a little along its ray, for fin glyphs
in vec3 in_n;        // surface normal, body only
in vec3 in_col;
in float in_lum;
in float in_kind;    // 1 fish, 2 fin, 4 background
in float in_mode;    // 0 ramp, 1 ray, 2 explicit, 3 rock ramp,
                     // 4 explicit at a cell, 5 explicit in the world
in float in_shade;   // >= 0 body (the stripe kind), -1 fin, -2 eye

flat out vec3 v_col;
flat out float v_glyph;
flat out float v_kind;

uniform int u_ramp[36];
uniform int u_rock[11];
uniform mat3 u_spin;         // rot_y(rot_z(..)) for this frame
uniform vec3 u_place;
uniform float u_reach[76];   // how far along its axis a bent body really reaches
uniform float u_body_end;
uniform float u_facing;      // how square-on the face is to us
uniform vec3 u_light;
uniform vec3 u_shine_dir;

const float TAIL_ROOT = 0.53;
const float SPINE_SPAN = 1.5;

float ease(float x) { x = clamp(x, 0.0, 1.0); return (3.0 - 2.0 * x) * x * x; }

// a bent spine is no longer than a straight one
float spine_x(float x) {
    if (x > TAIL_ROOT) return u_body_end + x - TAIL_ROOT;   // the fin keeps its length
    float a = clamp((x + 0.45) / SPINE_SPAN * 75.0, 0.0, 75.0);
    int i = int(a);
    return mix(u_reach[i], u_reach[min(i + 1, 75)], a - float(i)) - 0.45;
}

vec3 posed(vec3 p) {
    p.x = spine_x(p.x);
    return u_spin * p + u_place;
}

void main() {
    vec3 col = in_col;
    float lum = in_lum;
    vec3 sp, sq;

    if (in_mode > 4.5) {
        // in the world, but not part of the fish: plankton and wake sparks
        sp = project(in_p);
        sq = sp;
    } else if (in_mode > 3.5) {
        // placed on the grid, not in the world: drifting specks and crumbs
        sp = vec3((in_p.x + 0.5) * CELL.x, (in_p.y + 0.5) * CELL.y, depth01(in_p.z));
        sq = sp;
    } else {
        vec3 wp = posed(in_p);
        vec3 wq = posed(in_q);
        if (in_shade < -1.5) {            // the eyes sit just proud of the flank
            wp.z -= 0.004;
            wq.z -= 0.004;
        }
        if (in_shade >= -0.5) {           // body: light it, and read its stripe
            vec3 n = u_spin * in_n;
            float len = length(n);
            n = (len > 1e-6) ? n / len : vec3(0.0);
            float lit = clamp(dot(n, u_light), 0.0, 1.0);
            float shine = pow(clamp(dot(n, u_shine_dir), 0.0, 1.0), 14.0)
                        * (0.25 + 0.75 * u_facing);
            float mul;
            if (in_shade > 1.5)        { lum = 0.42;                mul = 1.0; }
            else if (in_shade > 0.5)   { lum = 0.86 + 0.14 * lit;   mul = 0.82 + 0.18 * lit; }
            else                       { lum = 0.66 + 0.30 * lit;   mul = 0.42 + 0.58 * lit; }
            col = in_col * mul;
            if (in_shade <= 1.5) col += 0.75 * shine;    // not the dark stripe border
            col = clamp(col, 0.0, 1.0);
            lum = clamp(lum + 0.3 * shine, 0.0, 1.0);
        }
        sp = project(wp);
        sq = project(wq);
    }

    int g;
    if (in_mode > 3.5) {
        g = int(in_lum + 0.5);
    } else if (in_mode < 0.5) {
        g = u_ramp[clamp(int(pow(1.0 - lum, 1.3) * 35.0), 0, 35)];
    } else if (in_mode < 1.5) {
        g = line_glyph(sq.x - sp.x, sq.y - sp.y);
    } else if (in_mode < 2.5) {
        g = int(lum + 0.5);
    } else {
        g = u_rock[clamp(int(lum * 11.0), 0, 10)];
    }
    v_col = col;
    v_glyph = float(g);
    v_kind = in_kind;
    gl_PointSize = 1.0;
    gl_Position = cell_clip(sp.xy, sp.z);
}
"""

POINT_FRAG = """
#version 330
flat in vec3 v_col;
flat in float v_glyph;
flat in float v_kind;
layout(location = 0) out vec4 out_ink;
layout(location = 1) out vec4 out_meta;
void main() {
    out_ink = vec4(clamp(v_col, 0.0, 1.0), 1.0);
    out_meta = vec4(v_glyph / 255.0, v_kind / 255.0, 0.0, 1.0);
}
"""

# ---- the anemone: each arm a screen-space tube --------------------------------------
ARM_VERT = COMMON + """
in vec2 in_ndc;
in float in_depth;
in vec2 in_centre;      // the centreline point, in pixels
in float in_radius;     // in pixels
in float in_u;          // 0 at the root, 1 at the tip
in float in_j;          // sample index, for the broken rim
in float in_glow;
in float in_tone;       // how far back this arm sits
in float in_arm;

out vec2 v_centre;
out float v_radius, v_u, v_j, v_glow;
flat out float v_tone, v_arm;

void main() {
    v_centre = in_centre; v_radius = in_radius; v_u = in_u;
    v_j = in_j; v_glow = in_glow; v_tone = in_tone; v_arm = in_arm;
    gl_Position = vec4(in_ndc, in_depth * 2.0 - 1.0, 1.0);
}
"""

ARM_FRAG = """
#version 330
in vec2 v_centre;
in float v_radius, v_u, v_j, v_glow;
flat in float v_tone, v_arm;
layout(location = 0) out vec4 out_ink;
layout(location = 1) out vec4 out_meta;

uniform vec2 u_screen;
uniform vec2 u_grid;
uniform int u_fill[10];
uniform vec3 u_stem, u_tip, u_glowink;
const vec2 CELL = vec2(%d.0, %d.0);

float ease(float x) { x = clamp(x, 0.0, 1.0); return (3.0 - 2.0 * x) * x * x; }

int line_glyph(float dx, float dy) {
    dx /= CELL.x; dy /= CELL.y;
    if (abs(dy) > 2.0 * abs(dx)) return %d;
    if (abs(dx) > 2.0 * abs(dy)) return %d;
    return (dx * dy < 0.0) ? %d : %d;
}

void main() {
    // this fragment is one character cell; find its centre in pixels the way the CPU did
    float col = floor(gl_FragCoord.x);
    float row = floor(u_grid.y - gl_FragCoord.y);
    vec2 px = vec2((col + 0.5) * CELL.x, (row + 0.5) * CELL.y);

    float ox = px.x - v_centre.x;
    float oy = px.y - v_centre.y;
    float d = length(vec2(ox, oy)) / max(v_radius, 0.0001);
    if (d >= 1.0) discard;                       // outside the tube

    float tipness = ease((v_u - 0.30) / 0.62);
    vec3 tone = mix(u_stem, u_tip, tipness) * v_tone;
    float lit = clamp(0.5 - 0.5 * (ox * 0.8 + oy * 0.6) / max(v_radius, 1.0), 0.0, 1.0);
    bool rim = d > 0.70;
    bool broken = sin(v_j * 0.9 + v_arm * 2.1) > -0.86;
    float shine = v_glow * (0.55 + 0.45 * v_tone) * (0.25 + 0.75 * tipness);
    float value = 0.14 + 0.24 * lit + 0.42 * tipness * tipness + 0.25 * shine;

    int g;
    vec3 col3;
    if (rim) {
        g = line_glyph(-oy, ox);
        col3 = tone * (0.50 + 0.38 * lit + 0.22 * tipness) * (broken ? 1.0 : 0.45);
    } else {
        g = u_fill[clamp(int(value * 1.25 * 10.0), 0, 9)];
        col3 = tone * (0.16 + 0.26 * lit + 0.5 * tipness * tipness);
    }
    col3 = clamp(col3 + u_glowink * shine * (rim ? 0.62 : 0.34), 0.0, 1.0);
    out_ink = vec4(col3, 1.0);
    out_meta = vec4(float(g) / 255.0, 3.0 / 255.0, 0.0, 1.0);   // 3 = WEED
}
""" % (CW, CH, G_PIPE, G_DASH, G_SLASH, G_BACK)

# ---- the static background, blitted with its depth ----------------------------------
BG_VERT = """
#version 330
void main() {
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

BG_FRAG = """
#version 330
uniform sampler2D bg_ink;
uniform sampler2D bg_meta;
uniform sampler2D bg_depth;
uniform vec2 u_grid;
layout(location = 0) out vec4 out_ink;
layout(location = 1) out vec4 out_meta;
void main() {
    // uploaded with grid row 0 first; rasterising puts row 0 at the top of the
    // viewport, which is the LAST texel row, so read flipped
    ivec2 c = ivec2(gl_FragCoord.x, u_grid.y - gl_FragCoord.y);
    out_ink = vec4(texelFetch(bg_ink, c, 0).rgb, 1.0);
    out_meta = vec4(texelFetch(bg_meta, c, 0).rg, 0.0, 1.0);
    gl_FragDepth = texelFetch(bg_depth, c, 0).r;
}
"""

# ---- post: the fish's broken outline, then the light shafts -------------------------
POST_FRAG = """
#version 330
uniform sampler2D cell_ink;
uniform sampler2D cell_meta;
uniform vec2 u_grid;
uniform vec2 u_screen;
uniform float u_t;
uniform float u_scale;
uniform vec2 u_centre;
uniform float u_mid_y;
uniform float u_w1, u_w2, u_w3;
uniform int u_shafts;
layout(location = 0) out vec4 out_ink;
layout(location = 1) out vec4 out_meta;
const vec2 CELL = vec2(%d.0, %d.0);
const float FISH = 1.0, FIN = 2.0;

// takes texel coordinates, the same space this shader writes in
float kind_at(ivec2 t) {
    if (t.x < 0 || t.y < 0 || t.x >= int(u_grid.x) || t.y >= int(u_grid.y)) return 0.0;
    return floor(texelFetch(cell_meta, t, 0).g * 255.0 + 0.5);
}

float hash12(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

void main() {
    ivec2 t = ivec2(gl_FragCoord.xy);              // the texel we read and write
    int grid_row = int(u_grid.y) - 1 - t.y;        // our grid counts rows from the top
    ivec2 c = ivec2(t.x, grid_row);
    vec3 ink = texelFetch(cell_ink, t, 0).rgb;
    vec2 meta = texelFetch(cell_meta, t, 0).rg;
    float g = floor(meta.r * 255.0 + 0.5);
    float kind = floor(meta.g * 255.0 + 0.5);

    // where the fish's flank meets open water, draw the edge in its own tone
    if (kind == FISH) {
        // "up" on screen is +1 in texel space, because the buffer is bottom-up
        bool up = kind_at(t + ivec2(0, 1)) != FISH;
        bool dn = kind_at(t + ivec2(0, -1)) != FISH;
        bool lf = kind_at(t + ivec2(-1, 0)) != FISH;
        bool rt = kind_at(t + ivec2(1, 0)) != FISH;
        int e = -1;
        if (up && kind != FIN) e = 13;            // -
        if (dn) e = 63;                           // _
        if (lf) e = 8;                            // (
        if (lf && up) e = 15;                     // /
        if (lf && dn) e = 60;                     // backslash
        if (rt && up) e = 60;
        if (rt && dn) e = 15;
        bool near_fin = kind_at(t + ivec2(0,-1)) == FIN || kind_at(t + ivec2(0,1)) == FIN
                     || kind_at(t + ivec2(-1,0)) == FIN || kind_at(t + ivec2(1,0)) == FIN;
        if (e >= 0 && !near_fin && hash12(vec2(c) + floor(u_t * 3.0) * 17.0) > 0.12) {
            g = float(e);
            ink = ink * 0.78 + 0.1;               // a soft, broken line
        }
    }

    // slanted beams of sunlight; whatever is inside one is brighter
    float cx = (float(c.x) + 0.5) * CELL.x;
    float cy = (float(grid_row) + 0.5) * CELL.y;
    float beams[6] = float[6](-2.6, -1.35, -0.2, 0.95, 2.1, 3.2);
    float lit = 0.0;
    for (int k = 0; (k < 6) && (u_shafts == 1); ++k) {
        float foot = u_screen.x * 0.5
                   + 0.85 * u_scale * (beams[k] + 0.22 * sin(u_w1 * u_t + 1.7 * float(k))
                                       - u_centre.x);
        float lean = foot - 0.30 * (cy - u_mid_y);
        float wide = 60.0 + 22.0 * sin(u_w2 * u_t + 2.3 * float(k));
        float f = (cx - lean) / wide;
        lit += (0.65 + 0.35 * sin(u_w3 * u_t + 1.1 * float(k))) * exp(-f * f);
    }
    lit *= clamp(1.15 - 0.55 * cy / u_screen.y, 0.3, 1.0);
    ink = clamp(ink * (1.0 + 0.42 * lit), 0.0, 1.0);
    if (g == 0.0 && lit > 0.30 && (grid_row * 2 + c.x) %% 5 == 0) {
        g = 15.0;                                  // dust caught in the beam
        ink = vec3(46.0, 84.0, 108.0) / 255.0 * lit;
    }
    out_ink = vec4(ink, 1.0);
    out_meta = vec4(g / 255.0, kind / 255.0, 0.0, 1.0);
}
""" % (CW, CH)


class SceneRenderer:
    """Draws the whole scene into a cell buffer, ready for CellPainter."""

    def __init__(self, ctx, cam, bg, bare=False):
        import moderngl
        self.ctx, self.cam = ctx, cam
        self.bare = bare
        cols, rows = cam.cols, cam.rows

        def cellfbo():
            ink = ctx.texture((cols, rows), 4, dtype="f1")
            meta = ctx.texture((cols, rows), 4, dtype="f1")
            for t in (ink, meta):
                t.filter = (moderngl.NEAREST, moderngl.NEAREST)
            depth = ctx.depth_texture((cols, rows))
            return ctx.framebuffer([ink, meta], depth), ink, meta

        self.fbo_a, self.ink_a, self.meta_a = cellfbo()
        self.fbo_b, self.ink_b, self.meta_b = cellfbo()

        # the background never changes: bake it once, depth and all
        # 9.88 maps to 0.99, not 1.0: an empty cell must still lose the depth test to
        # anything real, but must not sit exactly on the cleared value
        zb = np.clip(bg["zbuf"], -2.0, 9.88)
        self.bg_ink = ctx.texture((cols, rows), 3,
                                  (np.clip(bg["ink"], 0, 1) * 255).astype(np.uint8).tobytes(),
                                  dtype="f1")
        meta = np.zeros((rows, cols, 2), np.uint8)
        meta[..., 0] = bg["chars"]
        meta[..., 1] = bg["owner"]
        self.bg_meta = ctx.texture((cols, rows), 2, meta.tobytes(), dtype="f1")
        self.bg_depth = ctx.texture((cols, rows), 1,
                                    np.ascontiguousarray((zb + 2.0) / 12.0, np.float32).tobytes(),
                                    dtype="f4")
        for t in (self.bg_ink, self.bg_meta, self.bg_depth):
            t.filter = (moderngl.NEAREST, moderngl.NEAREST)

        self.bg_prog = ctx.program(vertex_shader=BG_VERT, fragment_shader=BG_FRAG)
        for name, unit in (("bg_ink", 0), ("bg_meta", 1), ("bg_depth", 2)):
            self.bg_prog[name].value = unit
        self.bg_prog["u_grid"].value = (float(cols), float(rows))
        self.bg_vao = ctx.vertex_array(self.bg_prog, [])

        self.pt_prog = ctx.program(vertex_shader=POINT_VERT, fragment_shader=POINT_FRAG)
        self.pt_prog["u_ramp"].value = [int(v) for v in fish.RAMP_G]
        self.pt_prog["u_rock"].value = [int(v) for v in reef.ROCK_G]
        # two buffers: what moves, and what does not
        self.pt_buf = ctx.buffer(reserve=6 * 4 * 60000, dynamic=True)
        self.pt_fixed = ctx.buffer(reserve=10 * 4 * 60000, dynamic=True)
        self._moving = None
        self._fixed = None
        self._fixed_n = 0
        self.pt_vao = ctx.vertex_array(
            self.pt_prog,
            [(self.pt_buf, "3f 3f", "in_p", "in_q"),
             (self.pt_fixed, "3f 3f 1f 1f 1f 1f",
              "in_n", "in_col", "in_lum", "in_kind", "in_mode", "in_shade")])
        self.pt_prog["u_light"].value = tuple(float(v) for v in fish.LIGHT)
        self.pt_prog["u_shine_dir"].value = tuple(float(v) for v in fish.SHINE_DIR)

        self.arm_prog = ctx.program(vertex_shader=ARM_VERT, fragment_shader=ARM_FRAG)
        self.arm_prog["u_fill"].value = [int(v) for v in reef.FILL_G]
        self.arm_prog["u_stem"].value = tuple(float(v) for v in reef.STEM)
        self.arm_prog["u_tip"].value = tuple(float(v) for v in reef.TIP)
        self.arm_prog["u_glowink"].value = tuple(float(v) for v in reef.GLOW_INK)
        self.arm_buf = ctx.buffer(reserve=11 * 4 * 80000, dynamic=True)
        self.arm_vao = ctx.vertex_array(
            self.arm_prog,
            [(self.arm_buf, "2f 1f 2f 1f 1f 1f 1f 1f 1f",
              "in_ndc", "in_depth", "in_centre", "in_radius", "in_u", "in_j",
              "in_glow", "in_tone", "in_arm")])

        self.post_prog = ctx.program(vertex_shader=BG_VERT, fragment_shader=POST_FRAG)
        self.post_prog["cell_ink"].value = 0
        self.post_prog["cell_meta"].value = 1
        self.post_vao = ctx.vertex_array(self.post_prog, [])

        for prog in (self.pt_prog, self.arm_prog, self.post_prog):
            self._set_view(prog)
        self.post_prog["u_w1"].value = float(fish.W1)
        self.post_prog["u_w2"].value = float(fish.W2)
        self.post_prog["u_w3"].value = float(fish.W3)
        self.post_prog["u_shafts"].value = 0 if bare else 1

    def _set_view(self, prog):
        cam = self.cam
        for name, value in (("u_centre", (float(cam.centre[0]), float(cam.centre[1]))),
                            ("u_scale", float(cam.scale)),
                            ("u_mid_y", float(cam.mid_y)),
                            ("u_screen", (float(cam.w), float(cam.h))),
                            ("u_grid", (float(cam.cols), float(cam.rows)))):
            if name in prog:
                prog[name].value = value

    # ---- per frame -----------------------------------------------------------------
    def _points(self, const, moving, extras):
        """Fill the two vertex buffers and say how many vertices to draw.

        `extras` is a list of (positions, colours, glyphs, mode) for everything that is
        not the fish: the cursor morsel and drifting specks placed on the grid, and the
        plankton and wake sparks that live in the world.
        """
        nb, nf, n = const["nb"], const["nf"], const["n"]
        bp, fp, fq, ep = moving
        extra = sum(len(e[0]) for e in extras)
        need = n + extra
        if self._moving is None or len(self._moving) < need:
            self._moving = np.zeros((need + 8192, 6), np.float32)
            self._fixed = np.zeros((need + 8192, 10), np.float32)
            self._fixed_n = 0
        mv, fx = self._moving, self._fixed

        mv[0:nb, 0:3] = bp                      # the body: p and q are the same point
        mv[0:nb, 3:6] = bp
        mv[nb:nb + nf, 0:3] = fp                # fins: q is a step along the ray
        mv[nb:nb + nf, 3:6] = fq
        mv[nb + nf:n, 0:3] = ep
        mv[nb + nf:n, 3:6] = ep

        if self._fixed_n != n:                  # the fish: written once
            fx[:n, 0:3] = const["normal"]
            fx[:n, 3:6] = const["col"]
            fx[:n, 6] = const["lum"]
            fx[:n, 7] = const["kind"]
            fx[:n, 8] = np.where(const["ray"], 1.0, 0.0)
            fx[:n, 9] = const["shade"]
            self.pt_fixed.write(fx[:n])
            self._fixed_n = n

        at = n
        for pos, col, gly, mode in extras:
            k = len(pos)
            if not k:
                continue
            mv[at:at + k, 0:3] = pos
            mv[at:at + k, 3:6] = pos
            fx[at:at + k, 0:3] = 0.0
            fx[at:at + k, 3:6] = col
            fx[at:at + k, 6] = gly
            fx[at:at + k, 7] = 0.0
            fx[at:at + k, 8] = mode
            fx[at:at + k, 9] = fish.SHADE_FIN
            at += k
        if extra:
            # these move about, so their fixed half is rewritten; a few hundred
            # vertices, not thirty thousand
            self.pt_fixed.write(fx[n:need], offset=n * 10 * 4)
        return mv[:need], need

    def _arm_strips(self, arms):
        """Each arm segment becomes two triangles in screen space."""
        line, thick, tone, glow = arms
        n_arms, n_s = line.shape[0], line.shape[1]
        flat = line.reshape(-1, 3)
        sx, sy, shrink = self.cam.project(flat)
        sx = sx.reshape(n_arms, n_s); sy = sy.reshape(n_arms, n_s)
        shrink = shrink.reshape(n_arms, n_s)
        rad = thick[None, :] * shrink
        z = line[:, :, 2]

        # screen-space tangent, then the perpendicular the tube is widened along
        tx = np.gradient(sx, axis=1)
        ty = np.gradient(sy, axis=1)
        tl = np.maximum(np.hypot(tx, ty), 1e-6)
        px, py = -ty / tl, tx / tl
        ax, ay = sx + px * rad, sy + py * rad
        bx, by = sx - px * rad, sy - py * rad

        u = np.linspace(0, 1, n_s)[None, :].repeat(n_arms, 0)
        j = np.arange(n_s, dtype=float)[None, :].repeat(n_arms, 0)
        arm = np.arange(n_arms, dtype=float)[:, None].repeat(n_s, 1)
        tone_f = np.asarray(tone, float)[:, None].repeat(n_s, 1)
        depth = np.clip((z + 2.0) / 12.0, 0, 1)

        def side_of(cx, cy):
            # not snapped to cells: a quad corner carries the tube's true width, and the
            # fragment shader measures the distance from the centreline itself
            ndc_x = cx / (self.cam.cols * CW) * 2 - 1
            ndc_y = 1 - cy / (self.cam.rows * CH) * 2
            return np.stack([ndc_x, ndc_y, depth, sx, sy, rad, u, j, glow, tone_f, arm], -1)

        A, B = side_of(ax, ay), side_of(bx, by)            # (arms, samples, 11)
        a0, b0, a1, b1 = A[:, :-1], B[:, :-1], A[:, 1:], B[:, 1:]
        tri = np.stack([a0, b0, a1, b0, b1, a1], axis=2)   # two triangles per segment
        return np.ascontiguousarray(tri.reshape(-1, 11), np.float32)

    def draw(self, t, const, moving, arms, overlay=None, pose=None, motes=None,
             floor=None):
        import moderngl
        ctx = self.ctx
        self.fbo_a.use()
        ctx.viewport = (0, 0, self.cam.cols, self.cam.rows)
        ctx.clear(0.0, 0.0, 0.0, 1.0, depth=1.0)
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.BLEND)

        if not self.bare:
            # in bare mode the sand, rocks and corals are left out: they were drawn for
            # a black background and read as noise over a photographic wallpaper
            self.bg_ink.use(0); self.bg_meta.use(1); self.bg_depth.use(2)
            self.bg_vao.render(mode=ctx.TRIANGLES, vertices=3)

        if pose is not None:
            self.pt_prog["u_spin"].write(
                np.ascontiguousarray(pose["spin"].T, np.float32).tobytes())
            self.pt_prog["u_place"].value = tuple(float(v) for v in pose["place"])
            self.pt_prog["u_reach"].write(np.ascontiguousarray(pose["reach"], np.float32))
            self.pt_prog["u_body_end"].value = float(pose["body_end"])
            self.pt_prog["u_facing"].value = float(pose["facing"])
        extras = []
        if overlay is not None and len(overlay[0]):
            extras.append((overlay[0], overlay[1], overlay[2], 4.0))
        if motes is not None and len(motes[0]):
            extras.append((motes[0], motes[1], motes[2], 5.0))
        if floor is not None:
            sand, coral = floor
            if len(sand[0]):
                extras.append((sand[0], sand[1], sand[2], 5.0))      # world space
            if len(coral[0]):
                extras.append((coral[0], coral[1], coral[2], 4.0))   # already on the grid
        verts, count = self._points(const, moving, extras)
        self.pt_buf.write(verts)
        self.pt_vao.render(mode=ctx.POINTS, vertices=count)

        strips = self._arm_strips(arms)
        self.arm_buf.write(strips)
        self.arm_vao.render(mode=ctx.TRIANGLES, vertices=len(strips))

        # post: outline and light shafts, reading A and writing B
        self.fbo_b.use()
        ctx.disable(moderngl.DEPTH_TEST)
        self.post_prog["u_t"].value = float(t)
        self.ink_a.use(0); self.meta_a.use(1)
        self.post_vao.render(mode=ctx.TRIANGLES, vertices=3)
        return self.ink_b, self.meta_b
