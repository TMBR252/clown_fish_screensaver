# Notice on origin and licensing

Read this before reusing, forking or redistributing this project.

## What came from where

This repository is two things stacked together.

**The fish, and the reef it lives in**, are adapted from
[ASCII Lab](https://github.com/sapozhkov/ascii-lab-public) by
[@ascii.lab](https://www.instagram.com/ascii.lab/), file `01-clownfish/clownfish.py`.
That is the creative work: the clownfish's profile curves and colours, the wave that
runs down its body, the fins, the eyes, the anemone's cloth simulation, the rocks,
sand and corals, the character ramp, and — most of all — the swimming model, which
gives the fish a heading, a pitch, an effort, a tail beat, two pushes per beat and
quadratic drag.

Files substantially derived from it:

- `tank/fish.py` — body, fins, eyes, the body wave, the shading
- `tank/reef.py` — the anemone, rocks, sand, corals, plankton, wake sparks
- parts of `tank/render.py` — the glyph atlas, the character ramp, the light shafts
- `tank/swimmer.py` — the steering and thrust model, lifted out of the original's
  offline `swim_round()` so it can run live

**The Windows wallpaper harness** was written for this project:

- `tank/wallpaper.py` — the desktop window, the frame loop, one process per screen
- `tank/glctx.py` — the OpenGL context
- `tank/scene_gl.py`, `tank/gpu.py` — the GPU renderer
- the behaviour layer in `tank/swimmer.py` — wandering, and chasing the mouse pointer
- the setup, launchers and tooling

## The licensing problem, stated plainly

**At the time of writing, the ASCII Lab repository contains no licence file and no
licence statement.** Under copyright law that means all rights are reserved by its
author. A repository being public on GitHub lets you view and fork it *within GitHub*
under GitHub's Terms of Service; it does not grant a licence to redistribute it or to
publish derivative works elsewhere.

So:

- The MIT licence in this repository covers **only the harness written here**.
- It **cannot** grant you rights over the parts derived from ASCII Lab, because those
  rights were never granted to this project either.
- If you want to use the fish for anything beyond personal tinkering, the correct step
  is to ask the author for a licence.

This is not a theoretical objection. It is the ordinary situation with an unlicensed
public repository, and it is easily fixed by asking.

## Suggested message to the author

> Hello — I've built a Windows desktop wallpaper around your `01-clownfish` from
> ASCII Lab. It runs the swimmer live instead of from the baked track, renders on the
> GPU, and the fish chases the mouse pointer.
>
> I'd like to publish it, with clear credit back to you and to @ascii.lab. The
> repository currently has no licence file, so strictly speaking I have no right to
> distribute work derived from it. Would you be willing to add one — MIT, or anything
> you prefer? Or to tell me what terms you'd want for this use?
>
> Happy to take it down, change the credit, or not publish at all if you'd rather.

If a licence is granted, please update this file and the README to say so.
