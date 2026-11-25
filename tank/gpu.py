"""The GPU side of the renderer.

The expensive half of the CPU renderer was never the clever part -- it was turning a
160x60 grid of glyph ids and colours into 1920x1080 pixels, which is 6.2MB written per
frame and memory-bound in numpy at about 13.7ms. That is exactly what a GPU does for a
living: a glyph atlas, a cell lookup, one multiply.

`CellPainter` is that step. It takes the same (chars, ink) grid the CPU renderer already
produces and draws it, so it can be compared against the CPU output pixel for pixel
before anything else moves across.
"""
import numpy as np

from .fish import CH, CW

CELL_VERT = """
#version 330
void main() {
    // one oversized triangle covering the screen; no vertex buffer needed
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

CELL_FRAG = """
#version 330
uniform sampler2DArray atlas;      // 96 glyphs, CW x CH each
uniform sampler2D cell_glyph;      // one glyph id per cell, 0..95 as 0..255/255
uniform sampler2D cell_ink;        // one colour per cell
uniform vec2 cell_px;              // pixels per cell
uniform vec2 screen;
uniform vec2 grid;
uniform int flip;                  // 1 when the source came from a bottom-up framebuffer
out vec4 frag;

void main() {
    // the grid counts rows from the top, gl_FragCoord from the bottom
    vec2 p = vec2(gl_FragCoord.x, screen.y - gl_FragCoord.y);
    vec2 cf = p / cell_px;
    ivec2 cell = ivec2(cf);
    if (flip == 1) cell.y = int(grid.y) - 1 - cell.y;
    int gi = int(texelFetch(cell_glyph, cell, 0).r * 255.0 + 0.5);
    if (gi == 0) {                        // a space: open water stays black
        frag = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }
    vec3 ink = texelFetch(cell_ink, cell, 0).rgb;
    float mask = texture(atlas, vec3(fract(cf), float(gi))).r;
    frag = vec4(ink * mask, 1.0);
}
"""


class CellPainter:
    """Draws a grid of glyph ids and colours as a screen full of lit letters."""

    def __init__(self, ctx, cam, atlas_f32):
        import moderngl
        self.ctx = ctx
        self.cam = cam
        layers = atlas_f32.shape[0]
        # texture_array wants (width, height, layers) with each layer row-major
        self.atlas = ctx.texture_array(
            (CW, CH, layers), 1,
            (np.clip(atlas_f32, 0, 1) * 255).astype(np.uint8).tobytes(), dtype="f1")
        self.atlas.filter = (moderngl.NEAREST, moderngl.NEAREST)

        self.glyph_tex = ctx.texture((cam.cols, cam.rows), 1, dtype="f1")
        self.glyph_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.ink_tex = ctx.texture((cam.cols, cam.rows), 3, dtype="f1")
        self.ink_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)

        self.prog = ctx.program(vertex_shader=CELL_VERT, fragment_shader=CELL_FRAG)
        self.prog["cell_px"].value = (float(CW), float(CH))
        self.prog["screen"].value = (float(cam.cols * CW), float(cam.rows * CH))
        self.prog["atlas"].value = 0
        self.prog["cell_glyph"].value = 1
        self.prog["cell_ink"].value = 2
        self.prog["grid"].value = (float(cam.cols), float(cam.rows))
        self.prog["flip"].value = 0
        self.vao = ctx.vertex_array(self.prog, [])

    def upload(self, chars, ink):
        """Hand over one frame's grid. ~150KB, which is nothing across PCIe."""
        self.glyph_tex.write(np.ascontiguousarray(chars, dtype=np.uint8))
        self.ink_tex.write(
            np.ascontiguousarray(np.clip(ink, 0, 1) * 255, dtype=np.uint8))

    def draw(self):
        """Draw from the grid uploaded with `upload` (numpy order, top row first)."""
        self.prog["flip"].value = 0
        self.atlas.use(0)
        self.glyph_tex.use(1)
        self.ink_tex.use(2)
        self.vao.render(mode=self.ctx.TRIANGLES, vertices=3)

    def draw_textures(self, ink_tex, meta_tex):
        """Draw straight from SceneRenderer's cell buffer, which is bottom-up."""
        self.prog["flip"].value = 1
        self.atlas.use(0)
        meta_tex.use(1)
        ink_tex.use(2)
        self.vao.render(mode=self.ctx.TRIANGLES, vertices=3)
