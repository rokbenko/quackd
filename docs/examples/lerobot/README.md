# Shoot-day examples for the SO-101 arm

One folder per experiment on the run sheet for the 23 September 2026 shoot that has a task file,
`e001` to `e165` (`e068`, `e119` and `e126` have none). Each holds the task file for that
experiment, or one per variant, and any picture it hands the model with `--image`, except four
that are yours to supply: the bill and the star count of `e106` and the two video thumbnails of
`e111`. Save them beside their files as `bill.png`, `stars.png`, `thumb-a.png` and `thumb-b.png`
before you run those files. `e041` hands the model the picture in `e034`. A few more are here to
print, and every other prop, the like button of `e077` among them, is yours to bring. A task
file's `name` is its experiment number and a slug, so every run directory says which experiment
it was, `runs/20260923-081500-e001-circle/`, and a `--run-name` on the line tells apart several
runs of one file.

Run them from the root of this checkout against the arm registered as `arm-01` with a rest pose
recorded, which is sections 06 and 07 of
[adapters/lerobot/first-run.md](../../adapters/lerobot/first-run.md):

```bash
quackd run docs/examples/lerobot/e001/circle.duck --robot arm-01 --by-hand --camera-url "opencv://1?name=front" --camera-url "opencv://2?name=top" --llm openai:gpt-6-sol --max-steps 16 --no-memory
```

With `--by-hand`, lift the arm so that every joint reads inside its travel before you press
Enter, above all a joint the run named as recorded past its travel. With one still past it,
quackd does not take hold, torque stays off and the run ends. On `arm-01` on 23 September that
joint was `shoulder_lift`, and the cure is to calibrate with the arm folded and record the rest
pose again, which is sections 05 and 07 of
[adapters/lerobot/first-run.md](../../adapters/lerobot/first-run.md).

A run whose rest move missed ends with the arm holding itself up. At a terminal it offers first
to take torque off where the arm stands: hold the arm and press Enter, or leave it for 60
seconds and torque stays on. After that, hold the arm and run `quackd robot release arm-01`,
because connecting takes torque off every motor for a moment.

118 of these files give the pilot a part to play, a feeling to show or a question to answer with
its body, rather than a change to make on the table. They are `duck: 4` files with `play: true`,
which adds one section to the pilot's prompt telling it to act the part out with the arm it has
([guides/play.md](../../guides/play.md)), so they need quackd 0.19 or later: an older one refuses
their version. They share a success line, a persona that begins `Game for anything: commit to the
bit` and a last step that asks for a reason saying what the arm did, except `e067/last-words` and
`e076/strike`, whose runs end without a declare and whose success line and last step say so. None
of them counts saying why the body cannot as a success any more. Their second step asks for the
act in a few clear poses. 27 of them keep the older second step instead, which asks for small,
deliberate moves: those where the run sheet puts a person, a held prop, a camera or a thing within
the arm's reach, has someone hold something up in the camera's view, rates the experiment as
contact or warns that a pose may strike the arm's own base, and `e089/less`, whose waves
are meant to shrink. That step and the section are requests no gate reads, and nothing keeps a
played arm away from a person ([guides/play.md](../../guides/play.md#what-does-not-change)), so
keep people beyond the arm's reach in these as in any played run. Run one with `--no-play` to
see it without the section. The lines they had before are in this folder's git history. The
other 105 keep every line they had and read what they always did: most ask for a result on the
table, a high five, a guess or a question answered in words, a stillness test, a trap or a
benchmark.
`e004/royalty`, `e004/mornings`, `e011/yoga` and `e022/pat-on-the-back`, which ran on the arm on 23
September, are among the 118 and no longer read what they read that day, and the 29 September
rehearsal below ran the five `e004` files as they read before.

The aim is that 90 to 95 percent of these files run as asked on the arm. By the run sheet's own
expectations 195 of the 223, 87 percent, should, short of the aim, and the other 28 are meant to
fall short: the sheet expects the arm not to do what they ask, and in most of them the miss is the
point. An entry that hedges, or expects some of a set to fall short without saying which, as the
idioms of `e079` do, counts as running. Eleven meet a limit of the arm or of how quackd drives it:
the six drawings of `e001`, `e019` and `e020` and the keys of `e014`, all done without inverse
kinematics, the point of `e130`, aimed without hand-eye calibration, the claw machine of `e039`,
the tower of `e100` and the tangled cable of `e109`. Four meet the seconds each model call takes:
the act of `e037`, asked for at once, the metronome of `e045`, which asks for a tick a second, the
red light of `e096`, which catches the arm still moving every round, and the fly of `e113`, gone
before each swing lands. Two ask for two things at the same time, the pat and rub of `e062` and the
wave and count of `e070`, which the sheet expects one after the other or not at all. Two end on one
of quackd's own limits: a range the validator refuses in `e084/give-110`, and the step budget of
`e120`, partway through its waves. Four are cut short by a person, as the experiment plans: the
hand of `e081`, gone before the high five lands, a cable pulled in `e116` and Ctrl-C in `e117`,
both partway through the raise they ask for, and a confirm declined in `e121`. Three ask for a
guess the sheet expects the pilot to refuse, on principle, in `e085`. Two end on an answer the
sheet expects to be wrong: `e131/my-left`, the harder of two runs it expects at least one wrong
answer from, and `e073`, whose header leaves the pilot only the colour detector, which reads orange
as a ball and blue as a person. Whether the other 195 run as asked has not been measured. That
takes every file once on a release, counting the runs that started, moved as asked and did not
disown a part.

Which cameras, which pilot and whether the arm starts from a pose you set by hand are flags on
the line rather than lines in a file, because the same task runs with one camera or two and with
any model. Each file opens with a comment carrying its first command. Check them all against the
arm's manifest before a session:

```bash
quackd validate "docs/examples/lerobot/*/*.duck" --robot lerobot:real
```

And rehearse them at home first, on the arm's simulator, which runs a file through the code that
drives the arm without the arm: `quackd robot twin arm-01` registers `arm-01-sim` on the arm's
own calibration, and `quackd preflight` runs each file on it once per seed
([adapters/lerobot/first-run.md](../../adapters/lerobot/first-run.md#16-between-visits-rehearse-on-the-simulator)).
With a real pilot every seed costs what a run costs. Only `e162` and `e165` have a
`<task>.sim.yaml` beside them, so every other run is judged on whether nothing escaped it and its
close reached the rest pose.

```bash
quackd preflight "docs/examples/lerobot/e00[1-5]/*.duck" --robot arm-01-sim --llm openai:gpt-6-sol --camera-url "opencv://1?name=front" --seeds 2
```

On 29 September that line passed all 12 files on the lab arm's twin, 24 runs for $1.06 of
`gpt-6-sol`. A pass says the code survived the file, not that the task was done. The simulator's
table holds a red cube and a pen and nothing else, so the `e001` drawings found no marker in the
gripper and no paper and their pilots declared failure. Both `e002` pilots said a switch sat
right below the gripper, where the table has none, and kept looking until their fourteen steps
ran out, which is how that task says to end. `e005/do-nothing` ends on its budget every time,
since its ten steps are the whole of it. And `e001/duck-picture` needs its picture: `--image`
would hand it to every file the pattern matches, so rehearse that one on its own, with the
`--image` in its header.

The two sidecars lay the cube their tasks start with between the open jaws, and check that it
was lifted as far as the task says, two centimetres for `e162` and a little for `e165`. `e163`
starts with the jaws open around a cube as well, but asks only whether the gripper holds it,
which no sidecar check measures, so it has none. Neither sidecar lays out on `arm-01-sim`.
`arm-01`'s fold puts the jaws down at the table with the gripper shut, so every connect is
refused:
`the scene lays cube between the jaws, and as the arm starts they are open narrower than cube`.
The same fold with the gripper open is refused as well, since closing the gripper from there
never brings the moving finger onto the cube:
`closing the gripper stops its moving finger 0.4 mm clear of cube, which it never touches on the way`.
Each sidecar says at its top what it needs, and each refusal says it too: an arm that starts with
its open jaws pointing down at the table around the cube, which neither the twin nor the generic
arm does.

`e145` and `e152` are the two exceptions: an MCP session loads them from the chat with
`robot_load_duckfile`, which is how their longer budgets reach a session that would otherwise
stop after five minutes.

None of these had run on an arm when they were written. On 23 September ten of them made 23 runs
on `arm-01`, on quackd 0.12.0: `e001/circle`, `e003/small-then-tall`, `e004/royalty`,
`e004/mornings`, `e005/do-nothing`, `e006/high-five`, `e011/yoga`, `e022/pat-on-the-back`,
`e114/as-fast-as-you-can` and `e116/slow-raise`. Every run of `e001`, `e022` and `e114` ended
before the pilot's first call, at connect or on the rest move. The other 213 files had not run
on an arm by then. Later runs of more of them on `arm-01`, on the lab's install, are what
[ADR-0055](../../adr/0055-a-pilot-can-play-a-part.md) answers, and nothing quackd changed after
that afternoon has run on an arm. The budgets are estimates, sized so that a run which goes well
finishes well inside them, and the task files are plain text:
[reference/duck-spec.md](../../reference/duck-spec.md) is what every field means.
