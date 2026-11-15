# Places: naming a spot and walking back to it

A robot that says where it is can name the spots it stands on and walk back to them. This guide
drives a mock through it, so nothing here needs a robot: `rosbridge:mock`, a wheeled base held in
memory whose odometry is the pose it adds up from the twists it is sent. A mock shows the verbs
and the CLI working, not how a robot behaves. The Microduck's own mock reports a scripted pose that
walking does not change, so every place marked on it is the spot it stands on, and `go_to_place`
there has nowhere to walk. Why it works the way it does is in
[concepts/places.md](../concepts/places.md).

```bash
roboglot drive --robot rosbridge:mock
```

The base starts where its odometry began, and nothing is marked yet. This session was piped, a
line at a time, with the scripted pilot:

```
$ printf 'mark_place sofa\nforward\nforward\nturn left\nforward\nmap\ngo_to_place sofa\nquit\n' | roboglot --no-color drive --robot rosbridge:mock --llm fake
drive    base-01: type a command, a goal, or help; Enter brakes
exact    mark_place(name='sofa') (13.404 ms)
result   ok: marked 'sofa' where the robot stands, (0.00, 0.00) by its odometry. Odometry drifts with every metre walked and begins again if the robot is picked up or falls, so the place is as good as the walk since it was marked
exact    forward: move(duration_s=1, vx=0.15) (0.42 ms)
result   ok: walked vx=0.15 vy=-0.00 wz=0.00 for 1.0s
exact    forward: move(duration_s=1, vx=0.15) (0.55 ms)
result   ok: walked vx=0.15 vy=-0.00 wz=0.00 for 1.0s
exact    turn left: move(duration_s=1.571, vx=0, wz=0.5) (0.297 ms)
result   ok: walked vx=0.00 vy=-0.00 wz=0.50 for 1.6s
exact    forward: move(duration_s=1, vx=0.15) (0.304 ms)
result   ok: walked vx=0.15 vy=-0.00 wz=0.00 for 1.0s
exact    map (0.317 ms)
result   ok: where: odometry(0.41, 0.11, 0.79 rad, walked 0.5 m), by the mock base's odometry
places:
- sofa: 0.42 m away, 150° to the left
no map to draw: the mock base's odometry keeps none, only a position
exact    go_to_place(name='sofa') (0.366 ms)
result   ok: go_to_place: 0.02 m from 'sofa' by its odometry, which drifts, facing within 4° of the way it faced there; walked 0.4 m in 8 s, 1 stop, 0 re-plans
session  quit (0.215 ms)
result   ok: goodbye
record   runs\20261115-132241-drive
```

What each step shows:

- **`mark_place sofa`** named the spot the base stands on from its own reading. It takes no
  coordinates: a place is always where the robot stood. On odometry it also says what the place
  is worth: as much as the walk since it was marked.
- **`map`** says where the base is and by what, and every place with how far and which way. A
  body that keeps a map draws it too, and odometry keeps none, so here `map` says so.
- **`go_to_place sofa`** walked back on odometry, in a straight line, since there is no map to
  plan round anything. It says how close it got by what, and that it is facing the way it faced
  when the place was marked.

The places are kept beside the robot's memory and keyed as it is. A place on odometry holds for as
long as that odometry runs, and the mock's begins again at every connect, so the next session on
it finds `sofa` orphaned, as the next section shows.

## From the command line

`roboglot places` and `roboglot map` do the same from a shell, with no pilot and no drive session.
`places add` marks the spot the robot stands on, as `mark_place` does, and `map` says what the
`map` verb says, and draws the map where the robot keeps one. Both connect to the robot, with no
camera, and nothing moves, and both take `--robot`, `--address`, `--token` and `--map-address` as
`drive` does. `places list` and `places rm` read and change the place file and connect nothing:

```
$ roboglot places add sofa --note "the grey one" --robot rosbridge:mock
✓ rosbridge:mock: marked 'sofa' where the robot stands, (0.00, 0.00) by its odometry. Odometry
drifts with every metre walked and begins again if the robot is picked up or falls, so the place is
as good as the walk since it was marked
$ roboglot places list --robot rosbridge:mock
rosbridge:mock  1 place  ~/.roboglot/memory/rosbridge-mock.places.jsonl
places, each in the frame it was marked in
┌──────┬────────────┬────────┬──────────┬────────────────────────┬────────────┬──────────────┐
│ name │   x, y (m) │ facing │ by       │ frame                  │ marked     │ note         │
├──────┼────────────┼────────┼──────────┼────────────────────────┼────────────┼──────────────┤
│ sofa │ 0.00, 0.00 │     0° │ odometry │ odom:mock:5d92e4dbe405 │ 2026-11-15 │ the grey one │
└──────┴────────────┴────────┴──────────┴────────────────────────┴────────────┴──────────────┘
roboglot map says which of them can be walked to now
$ roboglot map --robot rosbridge:mock
where: odometry(0.00, 0.00, 0.00 rad, walked 0.0 m), by the mock base's odometry
places:
- sofa: orphaned, it was marked on odometry that has started again since
no map to draw: the mock base's odometry keeps none, only a position
$ roboglot places rm sofa --robot rosbridge:mock
✓ rosbridge:mock: forgot 'sofa'
```

`places list` prints the file's full path, which this page writes from your home directory as
`~`. It reads the file and nothing else, so it cannot say which places can be walked to now:
`roboglot map` connects and says. Its frame column is what each place belongs to. The `map` after
it made a connection of its own, so the mock's odometry had begun again, and `sofa` is orphaned:
kept and listed, with why, and never walked to. A frame whose map was wiped says so too.
`--cells` sets how many columns a map is drawn in, 10 to 80, where the robot keeps one, and
`--watch` draws it all again every second until Ctrl+C. On a real Microduck, `--map-address` says
where its `mapd` answers, as it does on `run`.

`--wipe` forgets the map first, every stop it has looked from, and every place marked in it is
orphaned from then on, in every session after. It asks the person at the terminal, naming those
places, and nothing else in Roboglot wipes a map, a pilot or a script included:

```
$ roboglot map --wipe --robot rosbridge:mock < /dev/null
✗ error: --wipe asks the person at the terminal first, and there is no terminal here: run it in one
  a map is never wiped by a pilot or a script
```

## On a real robot

A real Microduck says where it is by its odometry, today, which drifts as it walks and begins
again when it is picked up: `go_to_place` walks the straight line there, no further than one
`move` could walk blind, and says the reading drifts. Places marked on one stretch of odometry are
orphaned once it begins again, and listed with why. A Microduck that runs `mapd` beside its robotd
keeps a map, which `map` draws, and no robotd release carries `mapd` yet. A rosbridge base reads
`/odom`, and MOSS its cockpit's pose, which its own `go_to_place` drives to by the rover's
planner. Which frame each body's places belong to, and for how long, is in
[concepts/places.md](../concepts/places.md).
