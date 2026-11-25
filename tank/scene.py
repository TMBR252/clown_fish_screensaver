"""One tank: the fish, its reef, and how to draw them. No platform code here.

A backend owns a window and an OpenGL context for one screen; it hands that context
over once, then calls `step` and `draw` each frame. Everything about *where* the
picture goes lives in the backend, so Windows and macOS share identical behaviour.
"""
import numpy as np

from . import fish, reef, render, swimmer


class Tank:
    """The living scene for a single screen."""

    def __init__(self, width, height, seed=None, bare=False):
        self.w, self.h = width, height
        self.bare = bare
        self.renderer = render.Renderer(width, height)
        self.cam = self.renderer.cam
        self.weed = reef.Anemone()
        self.weed.settle(240)          # let the arms find the surge before anyone looks
        lo, hi = self.cam.swim_box()
        self.fish = swimmer.Swimmer(lo, hi, seed)
        self.painter = None
        self.scene = None

    # ---- the GPU, when there is one -----------------------------------------------
    def use_gl(self, ctx):
        """Build the GPU renderer against a context the backend has made current."""
        from . import gpu, scene_gl
        self.painter = gpu.CellPainter(ctx, self.cam, render.atlas())
        self.scene = scene_gl.SceneRenderer(ctx, self.cam, self.renderer.bg,
                                            bare=self.bare)

    def drop_gl(self):
        """The context went away (the shell rebuilt the desktop, a screen changed)."""
        self.painter = self.scene = None

    @property
    def on_gpu(self):
        return self.scene is not None

    # ---- a frame -------------------------------------------------------------------
    def step(self, dt, cursor_px, moved):
        """Advance by `dt`. `cursor_px` is (x, y) within this screen, or None."""
        where = None if cursor_px is None else self.cam.unproject(*cursor_px)
        self.fish.attend(where, moved)
        self.fish.advance(dt)
        place, yaw, pitch = self.fish.pose()
        self.weed.advance(dt, place, self.fish.velocity, yaw, pitch)

    def _pose(self):
        t = self.fish.clock
        place, yaw, pitch = self.fish.pose()
        fish.set_pose(**self.fish.shape())
        spin = fish.pose_matrix(yaw, pitch)
        along, reach, body_end = fish.spine(t)
        return t, dict(spin=spin, place=place, reach=reach, body_end=body_end,
                       facing=fish.facing_of(spin))

    def draw_gl(self):
        """Draw into whatever framebuffer the backend has bound. Returns nothing."""
        t, pose = self._pose()
        const, moving = fish.local_const(), fish.local_moving(t)
        overlay = render.overlay_cells(self.cam, t, self.fish, specks=not self.bare)
        floor = None if self.bare else (render.sand_live(self.cam, t),
                                        render.coral_live(self.cam, t))
        ink_tex, meta_tex = self.scene.draw(t, const, moving, self.weed.curve(),
                                            overlay, pose, self.weed.motes(), floor)
        return ink_tex, meta_tex

    def present_gl(self, ctx):
        """Blow the cell buffer up to real pixels, on the screen framebuffer."""
        ink_tex, meta_tex = self.draw_gl()
        ctx.screen.use()
        ctx.viewport = (0, 0, self.w, self.h)
        self.painter.draw_textures(ink_tex, meta_tex)

    def draw_cpu(self):
        """The fallback: a BGR image for the backend to blit. Much slower."""
        chars, ink = self.renderer.grid(self.fish.clock, self.fish, self.weed.curve())
        return self.renderer.paint(chars, ink)


def cursor_for(screen_rect, pointer):
    """Where the pointer is inside one screen, or None if it is on another.

    `screen_rect` is (x, y, w, h) in whatever virtual-desktop coordinates the platform
    uses; `pointer` is (x, y) in the same space.
    """
    if pointer is None:
        return None
    x, y, w, h = screen_rect
    lx, ly = pointer[0] - x, pointer[1] - y
    if 0 <= lx < w and 0 <= ly < h:
        return (lx, ly)
    return None


def moved_enough(previous, pointer, slack=2):
    """Did the pointer actually move? Jitter of a pixel or two is not interest."""
    if previous is None or pointer is None:
        return False
    return abs(pointer[0] - previous[0]) + abs(pointer[1] - previous[1]) > slack
