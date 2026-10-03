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
before the pilot's first call, at connect or on the rest move. The other 213 files have not run
on an arm, and nothing quackd changed after that afternoon has run on one either. The budgets
are estimates, sized so that a run which goes well finishes well inside them, and the task files
are plain text: [reference/duck-spec.md](../../reference/duck-spec.md) is what every field means.
