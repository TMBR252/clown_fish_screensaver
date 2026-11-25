"""Save one frame off the real GPU path, for looking at without running the tank.

Writes shot.png beside this script.

    .venv\\Scripts\\python.exe tools\\shot.py [width height]
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PIL import Image                                                # noqa: E402

from tank import fish, glctx, gpu, reef, render, scene_gl, swimmer   # noqa: E402

W = int(sys.argv[1]) if len(sys.argv) > 2 else 1920
H = int(sys.argv[2]) if len(sys.argv) > 2 else 1080

gl = glctx.OffscreenContext()
ctx = gl.ctx
rd = render.Renderer(W, H)
painter = gpu.CellPainter(ctx, rd.cam, render.atlas())
scene = scene_gl.SceneRenderer(ctx, rd.cam, rd.bg)
screen = ctx.simple_framebuffer((W, H))

weed = reef.Anemone()
weed.settle(260)
lo, hi = rd.cam.swim_box()
sw = swimmer.Swimmer(lo, hi, seed=5)

# let it swim a while, then catch it roughly side-on so the fish reads
for i in range(400):
    sw.attend(None, False)
    sw.advance(1 / 24)
    place, yaw, pitch = sw.pose()
    weed.advance(1 / 24, place, sw.velocity, yaw, pitch)
    if i > 180 and abs(np.cos(sw.a)) > 0.9:
        break

t = sw.clock
fish.set_pose(**sw.shape())
const, moving = fish.local_const(), fish.local_moving(t)
along, reach, body_end = fish.spine(t)
spin = fish.pose_matrix(yaw, pitch)
pose = dict(spin=spin, place=place, reach=reach, body_end=body_end,
            facing=fish.facing_of(spin))
overlay = render.overlay_cells(rd.cam, t, sw)
floor = (render.sand_live(rd.cam, t), render.coral_live(rd.cam, t))
ink_t, meta_t = scene.draw(t, const, moving, weed.curve(), overlay, pose,
                           weed.motes(), floor)
screen.use()
ctx.viewport = (0, 0, W, H)
ctx.clear(0.0, 0.0, 0.0, 1.0)
painter.draw_textures(ink_t, meta_t)
ctx.finish()

img = np.frombuffer(screen.read(components=3), np.uint8).reshape(H, W, 3)[::-1]
out = os.path.join(HERE, "shot.png")
Image.fromarray(img).save(out)
print("%dx%d, grid %dx%d, fish %d points -> %s"
      % (W, H, rd.cam.cols, rd.cam.rows, const["n"], out))
