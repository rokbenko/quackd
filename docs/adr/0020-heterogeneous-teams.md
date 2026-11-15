# ADR-0020: Heterogeneous teams, spotter-judged success, and the sim head

**Status:** accepted, amended · **Date:** 2026-09-02 · Implemented in Phases 2 and 3 of 0.4 ([design](../design/multi-robot.md))

**Amended 2026-09-12:** the Reachy Mini adapter was removed ([ADR-0023](0023-reachy-mini.md),
now superseded), and it was the only "stationary head" embodiment Roboglot ever had. So
`StationaryHead`, `HEAD_POSES` and the rest of the "sim head" bullet below are gone from
`sim2d/world.py` and `sim2d/render.py`, and `team/runner.py`'s `make_sim_team` lost the
branch that paired a head with a Microduck. The Consequences note about a second simulated head
and `MAX_HEADS` goes with them. Everything else in this ADR about the auction and the role
machinery is untouched and still true: bids still carry a capability term, and role assignment
and spotter-judged success are still generic mechanisms keyed on a manifest's declared verbs
rather than on any one adapter. What is gone is any way to exercise them. A two-Microduck
`team.roles` duck, which the "a Microduck also satisfies spotter" consequence below implies
should work, passes `roboglot validate` and then fails at startup with `no live robot can take
the spotter role`: the coordinator judges eligibility before members report their vocabulary,
a latent bug this ADR's own choreography never hit because its spotter was the first to bid.
So nothing ships that reaches the role and auction path any more, now that
`reachy-spots-duck-kicks` is gone with the adapter it needed.

## Context

ADR-0015 gave teams a deterministic Contract Net auction over each duck's own camera
distance, homogeneous ducks, and success judged from sim ground truth. 0.4 puts a
stationary Reachy Mini head in the same arena as a Microduck: one robot can see but not
move, the other can move and kick. The choreography is "Reachy spots the ball, the duck
kicks it", and the honesty rule from the brief is that success is judged by the spotter
from its own fresh frames, never claimed by the actor.

## Decision

- **Bids carry a capability term.** `BidMsg` gains `role` and `provides` (the bidder's
  canonical verb names from its manifest). A robot bids only for roles whose `requires`
  its manifest satisfies (`team/capability.py`), and the coordinator re-checks every bid
  and logs `bid_rejected` for offenders (defence in depth once bids can arrive over a LAN).
  The cost stays the robot's own distance estimate, so no shared map is needed.
- **Role assignment** (`RoleAuction`): most-constrained role first, then role name; the
  winner is the lowest own distance with the member name as tie-break; per-role
  hysteresis. The **spotter is held for the run** (its reference frame must not change
  between kicks); the kicker is re-auctioned each cycle. Every ordering is a sort over
  strings or `(float, str)`, so decisions are independent of bid arrival order.
- **The actor reports, the spotter judges.** In role mode the kicker sends
  `RESULT kick_done` (not `kicked`), sidesteps out of the spotter's line, and never
  evaluates success. The coordinator orders `ROLE JUDGE`; the spotter sweeps its gaze
  around the last sighting with fresh frames and publishes a `VERDICT` (`moved`,
  `not_moved`, `lost`) with its own displacement estimate against its first sighting.
  `moved` is success, anything else is a miss and a re-auction (a rally keeps the same
  reference). The runner's ground-truth veto from ADR-0015 stays: a wrong `moved` is a
  failure, never a success. The judge applies a 0.15 m margin on top of `success_moved_m`
  (the size-based distance estimate quantizes in about 0.2 m steps beyond 1.5 m; with a
  0.05 m margin one seed's spotter called a 0.25 m kick "moved" and the veto failed the
  run, so the spotter is stricter and re-kicks instead).
- **Frame hints are arena-frame estimates, optional, on in sim and off on hardware.** A
  hint expressed in the receiver's frame cannot be computed by the spotter (it does not
  know the receiver's pose), so a hint is the spotter's arena-frame estimate of the target
  and the receiver localizes itself with its own `get_state()` pose. Hints only choose the
  pre-turn direction before the receiver's own `search_scan`. `frame_hints: auto` is on
  iff every member backend is `sim2d`. There is no computable relative frame between two
  robots on hardware; the spotter's verdict needs none, which is why a stationary spotter
  is the honest judge there too.
- **`StationaryHead` in sim2d** sits at a fixed pose from a constant table (`HEAD_POSES`,
  first entry the minus-y wall midpoint facing plus-y, chosen because it has line of
  sight on 10 of 10 seeds at spawn), is appended **after** every existing RNG draw and
  consumes none, is rendered in a low-saturation slate that no detector band can match
  (it can never forge a detection) but that humans can see in the GIF, and has a 180
  degree yaw range like the real head. Every head branch in `world.py` and `render.py` is
  guarded so `n_heads == 0` worlds are byte-identical, proven by golden fixtures recorded
  from `main` before the first edit. A fixed camera occluded by static scenery is an
  accepted, documented limitation, not worked around with ground truth.
- **Roles are closed in 0.4**: exactly `spotter` and `kicker`. The planner's one LLM call
  cannot touch them (`PLAN_TOOL` and `TUNABLE` are unchanged), so "at most one planner
  call per run" holds by construction and is pinned by `summary.planner.llm_calls`.
- The legacy homogeneous path is untouched: `team-kick` runs through the same
  `Auction`, the same member FSM and the same verb spellings, and a seed-3 golden of its
  summary and bus sequence proves it.

## Consequences

- `tasks/reachy-spots-duck-kicks.glot` ships at 10 of 10 seeds with scripted pilots,
  every message in `team.jsonl`, zero planner calls with the fake provider.
- Two new message kinds (`HINT`, `VERDICT`) and additive fields on existing ones;
  `team.jsonl` lines for old teams gain defaulted keys, nothing is renamed.
- A Microduck also satisfies `spotter` (it has `observe` and `gaze`); a two-duck
  spotter/kicker team is therefore valid and the auction decides.
- A second simulated head needs a second table entry and a recorder pane; `MAX_HEADS` is
  4 by table size, one is exercised.
