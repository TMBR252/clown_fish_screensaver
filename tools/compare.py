"""Render the same frame on the CPU and on the GPU, and diff them.

The GPU does the fish's spine, pose and lighting in a vertex shader, so this is the
check that it still agrees with the numpy renderer it replaced. Writes cpu_frame.png
and gpu_frame.png beside this script.

    .venv\\Scripts\\python.exe tools\\compare.py
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PIL import Image                                                # noqa: E402

from tank import fish, glctx, gpu, reef, render, scene_gl, swimmer   # noqa: E402

W, H = 1920, 1080
rd = render.Renderer(W, H)
weed = reef.Anemone()
weed.settle(240)
lo, hi = rd.cam.swim_box()
sw = swimmer.Swimmer(lo, hi, seed=5)
for _ in range(140):
    sw.attend(None, False)
    sw.advance(1 / 24)
    place, yaw, pitch = sw.pose()
    weed.advance(1 / 24, place, sw.velocity, yaw, pitch)

arms = weed.curve()
t = sw.clock
chars, ink = rd.grid(t, sw, arms)                  # CPU: build() poses and lights it
cpu = rd.paint(chars, ink)[..., ::-1].copy()

gl = glctx.OffscreenContext()
ctx = gl.ctx
scene = scene_gl.SceneRenderer(ctx, rd.cam, rd.bg)
painter = gpu.CellPainter(ctx, rd.cam, render.atlas())
screen = ctx.simple_framebuffer((W, H))

place, yaw, pitch = sw.pose()
fish.set_pose(**sw.shape())
const, moving = fish.local_const(), fish.local_moving(t)
along, reach, body_end = fish.spine(t)
spin = fish.pose_matrix(yaw, pitch)
pose = dict(spin=spin, place=place, reach=reach, body_end=body_end,
            facing=fish.facing_of(spin))
overlay = render.overlay_cells(rd.cam, t, sw)
floor = (render.sand_live(rd.cam, t), render.coral_live(rd.cam, t))
ink_t, meta_t = scene.draw(t, const, moving, arms, overlay, pose, weed.motes(), floor)
screen.use()
ctx.viewport = (0, 0, W, H)
ctx.clear(0.0, 0.0, 0.0, 1.0)
painter.draw_textures(ink_t, meta_t)
ctx.finish()
got = np.frombuffer(screen.read(components=3), np.uint8).reshape(H, W, 3)[::-1]

meta = np.frombuffer(scene.meta_b.read(), np.uint8).reshape(rd.cam.rows, rd.cam.cols, 4)
gchars = meta[..., 0][::-1]
both = (chars > 0) & (gchars > 0)
print("cells   CPU %4d   GPU %4d   both %4d  only CPU %3d  only GPU %3d"
      % ((chars > 0).sum(), (gchars > 0).sum(), both.sum(),
         ((chars > 0) & (gchars == 0)).sum(), ((chars == 0) & (gchars > 0)).sum()))
print("same glyph where both drew: %.1f%%" % (100 * (chars[both] == gchars[both]).mean()))
print("lit pixels  CPU %7d   GPU %7d" % ((cpu.max(2) > 8).sum(), (got.max(2) > 8).sum()))
print("mean bright CPU %7.3f   GPU %7.3f" % (cpu.mean(), got.mean()))
Image.fromarray(cpu).save(os.path.join(HERE, "cpu_frame.png"))
Image.fromarray(got).save(os.path.join(HERE, "gpu_frame.png"))
print("wrote cpu_frame.png and gpu_frame.png in %s" % HERE)
