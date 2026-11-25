"""What the fish decides to do, and the physics that carries it out.

The physics here is the original's, unchanged in substance. In clownfish.py it lived
inside `swim_round()`, which ran it offline for three laps of a fixed thirteen-point
round and froze the result into a lookup table; `swimmer(t)` then replayed that table
forever. Running it live is a matter of deleting the recording step, not of inventing
new motion: a heading, a pitch, an effort, a tail beat, two pushes per beat, and
quadratic drag.

What is new is the top layer: where the fish decides to go.

  * Left alone it wanders. Random marks inside a box, with the occasional pause to
    hover. This replaces the fixed round.
  * When the mouse moves it becomes prey. The original already had this behaviour
    written for a sinking morsel -- the fish sees it, holds a beat while its head
    whips round, then sprints flat out and bites. The cursor is simply the morsel.
  * A cursor that stops moving stops being interesting; after a few seconds the fish
    goes back to wandering. Catching it earns a short refractory pause, so the fish
    does not sit welded to a cursor that keeps twitching.
"""
import numpy as np

TAU = 2 * np.pi
STEP = 1 / 60                   # the integrator's fixed tick
MAX_CATCHUP = 0.25              # after a stall, drop time rather than fast-forward

THRUST, DRAG2, DRAG1 = 2.1, 2.2, 0.6
MOUTH = 0.45                    # the mouth is this far ahead of the middle of the fish

# The box the fish keeps itself inside comes from the camera (Camera.swim_box), so a
# wider or taller monitor simply gets more water rather than a fish that swims off it.

WANDER_EFFORT = (0.52, 0.95)    # how hard it tries between marks, drawn per mark
WANDER_REACH = 0.30             # near enough to a mark to call it reached
REST_CHANCE = 0.22              # how often a reached mark earns a pause
REST_FOR = (0.6, 2.2)
CIRCLE_GIVEUP = 1.6             # seconds spent orbiting a mark it cannot hit before moving on

CHASE_EFFORT = 1.95             # a burst: the tail works flat out
STARE_FOR = 0.34                # it sees the cursor: this long held still, turning to face it
INTEREST_TAU = 2.1              # seconds for interest in a still cursor to fade
CHASE_ABOVE = 0.30              # chase while interest is above this
BITE = 0.15                     # how close the mouth must come to catch it
REFRACTORY = 1.5                # after a catch, this long uninterested
CRUMB_LIFE = 0.7


class Swimmer:
    def __init__(self, box_lo, box_hi, seed=None):
        self.rng = np.random.default_rng(seed)
        self.lo = np.asarray(box_lo, float)
        self.hi = np.asarray(box_hi, float)
        self.p = (self.lo + self.hi) / 2 + np.array([0.4, 0.2, 0.0])
        self.a = np.pi                 # heading, radians in the xz plane
        self.th = 0.0                  # pitch, nose up positive
        self.v = 0.0                   # speed along the heading
        self.e = 0.0                   # effort, 0 = gliding
        self.phi = 0.0                 # tail beat phase
        self.spin = 0.0                # rate of change of heading, for the C-bend
        self.slowing = 0.0             # deceleration, for throwing the pectorals open
        self.clock = 0.0
        self.owed = 0.0

        self.mark = self._new_mark()
        self.mark_effort = 0.7
        self.rest = 0.0
        self.circling = 0.0

        self.cursor = None             # where the cursor is, in the world
        self.interest = 0.0
        self.stare = 0.0
        self.cooldown = 0.0
        self.crumbs = []               # (place, age) for a bite that just happened

    # ---- what it is aiming at ------------------------------------------------------
    def _new_mark(self):
        lo, hi = self.lo + 0.15, self.hi - 0.15
        return lo + self.rng.random(3) * (hi - lo)

    @property
    def chasing(self):
        return self.cursor is not None and self.interest > CHASE_ABOVE and self.cooldown <= 0

    @property
    def mouth(self):
        return self.p + MOUTH * np.array([np.cos(self.a) * np.cos(self.th),
                                          np.sin(self.th),
                                          np.sin(self.a) * np.cos(self.th)])

    def attend(self, cursor, moving):
        """Tell the fish where the cursor is and whether it just moved."""
        # The food sits exactly where the pointer is, even out at the very edge of the
        # screen. Clamping it into the fish's box made it snap to an invisible wall;
        # instead the fish aims at the nearest water it can reach and strains there.
        self.cursor = None if cursor is None else np.asarray(cursor, float)
        if moving and self.cursor is not None and self.cooldown <= 0:
            was = self.interest
            self.interest = 1.0
            if was <= CHASE_ABOVE:
                self.stare = STARE_FOR     # it has only just noticed: a held breath

    # ---- the physics ---------------------------------------------------------------
    def advance(self, dt):
        self.owed = min(self.owed + dt, MAX_CATCHUP)
        while self.owed >= STEP:
            self.owed -= STEP
            self._tick()

    def _tick(self):
        self.clock += STEP
        self.interest *= np.exp(-STEP / INTEREST_TAU)
        self.cooldown = max(0.0, self.cooldown - STEP)
        self.stare = max(0.0, self.stare - STEP)
        self.crumbs = [(q, age + STEP) for q, age in self.crumbs if age + STEP < CRUMB_LIFE]

        chasing = self.chasing
        if chasing:
            # aim at the closest point of its own water to the food, not at the food
            goal = np.clip(self.cursor, self.lo + 0.05, self.hi - 0.05)
            want = CHASE_EFFORT
        else:
            goal, want = self.mark, self.mark_effort

        # a fish that has drifted out of the box turns back into it before anything else
        outside = (self.p < self.lo) | (self.p > self.hi)
        if outside.any():
            goal = np.clip(self.p, self.lo + 0.35, self.hi - 0.35)
            want = max(want, 0.75)
            chasing = False

        to = goal - self.p
        dist = np.linalg.norm(to)

        if self.rest > 0:
            self.rest -= STEP
            want = 0.0
            if self.rest <= 0:
                self.mark, self.mark_effort = self._new_mark(), self._draw_effort()
        elif chasing:
            if self.stare > 0:
                want = 0.05                                  # seen it; not moving yet
            if np.linalg.norm(self.cursor - self.mouth) < BITE:
                self._bite()
        else:
            self.circling = self.circling + STEP if dist < 3 * WANDER_REACH else 0.0
            if dist < WANDER_REACH or self.circling > CIRCLE_GIVEUP:
                self.circling = 0.0
                if self.rng.random() < REST_CHANCE:
                    self.rest = self.rng.uniform(*REST_FOR)
                else:
                    self.mark, self.mark_effort = self._new_mark(), self._draw_effort()

        # steer: swing the heading round (never over the top), tip the nose along the path
        swing = (np.arctan2(to[2], to[0]) - self.a + np.pi) % TAU - np.pi
        eager = 0.35 if self.rest > 0 else 0.5 + 2.4 * self.e
        if self.stare > 0 and chasing:
            eager = max(eager, 2.6)                           # it can whip its head round at food
        want *= 0.40 + 0.60 * max(np.cos(swing), 0.0) ** 2    # eases off to make a tight turn

        a0, v0 = self.a, self.v
        self.a += np.clip(swing * eager, -1.9, 1.9) * STEP
        aim = 0.8 * np.arcsin(np.clip(to[1] / max(dist, 1e-6), -0.6, 0.6))
        self.th += (aim * (0.25 if self.rest > 0 else 1) - self.th) * 0.7 * eager * STEP
        grow = 2.2 if want < self.e else 1.1 + 2.2 * max(want - 1.2, 0)
        self.e += (want - self.e) * grow * STEP               # effort builds slowly, a burst fast
        hz = 0.70 + 1.05 * self.e
        self.phi += TAU * hz * STEP
        push = THRUST * self.e * (0.30 + 0.70 * np.sin(self.phi - 0.9) ** 2)   # twice per beat
        self.v += (push - DRAG2 * self.v * abs(self.v) - DRAG1 * self.v) * STEP
        self.p = self.p + self.v * STEP * np.array([np.cos(self.a) * np.cos(self.th),
                                                    np.sin(self.th),
                                                    np.sin(self.a) * np.cos(self.th)])
        self.spin = (self.a - a0) / STEP
        self.slowing = max(0.0, -(self.v - v0) / STEP)

    def _draw_effort(self):
        return self.rng.uniform(*WANDER_EFFORT)

    def _bite(self):
        where = self.mouth.copy()
        for k in range(6):
            way = np.array([np.cos(2.4 * k), np.sin(2.4 * k) - 0.3, 0.4 * np.sin(1.3 * k)])
            self.crumbs.append((where + way * 0.05, 0.0))
        self.interest = 0.0
        self.cooldown = REFRACTORY
        self.stare = 0.0
        self.mark, self.mark_effort = self._new_mark(), self._draw_effort()

    # ---- what the renderer needs ---------------------------------------------------
    @property
    def velocity(self):
        return self.v * np.array([np.cos(self.a) * np.cos(self.th),
                                  np.sin(self.th),
                                  np.sin(self.a) * np.cos(self.th)])

    def pose(self):
        """Place, yaw and nose-down pitch of the fish right now."""
        wag = 0.05 * (0.4 + self.e) * np.sin(self.phi + 0.8)      # the head answers the tail
        return self.p, np.pi - self.a + wag, -self.th

    def shape(self):
        """The arguments fish.set_pose wants: how the body is bent this frame."""
        return dict(effort=min(0.5 + 1.25 * self.e, 2.0),
                    turn=float(np.clip(0.11 * self.spin, -0.2, 0.2)),
                    phase=self.phi,
                    hz=0.70 + 1.05 * self.e,
                    t=self.clock,
                    brake=float(np.clip(1.6 * self.slowing, 0, 0.7)))
