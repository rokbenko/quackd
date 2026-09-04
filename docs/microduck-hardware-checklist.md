# Microduck: the first hardware run

Nobody has run quackd against a physical Microduck. If you have one — your own, or somebody
else's for an afternoon — this page is the order to do it in. Every step has an abort
condition, and they are ordered so that the duck's feet do not touch the ground until step 9.

**This is a robot you are probably borrowing.** Nothing here installs anything on it, stops any
of its services, or needs `sudo`. Step 6 has an alternative that does, and it is marked, and it
is the one to skip unless whoever owns the duck has said yes.

**The gamepad is the real e-stop.** Upstream is explicit that `padd` has authority and quackd
does not arbitrate. Somebody should be holding it. quackd's `stop` is a request over a socket,
and step 8 is where you find out what happens when that socket is not there.

## 1. Reach the daemon at all

`robotd` listens on a unix socket, not a port, so from a laptop it is an ssh forward:

```bash
ssh -L 9870:/run/robotd.sock radxa@<duck>
```

Windows cannot open a unix socket at all, so this is not optional there — quackd says so and
names this command if you try.

> Abort if ssh does not come up. Everything below is this tunnel.

## 2. What the robot says it is

```bash
quackd doctor --robot microduck:jsonrpc --address tcp://127.0.0.1:9870
```

Read three things: that the handshake passed, what `robot.health` said, and the verb list.

> **Abort on an API version mismatch.** quackd refuses rather than guessing, and it prints
> both numbers. quackd is written against the version in
> [`docs/adapter-status.md`](adapter-status.md); a prototype can be ahead of public `main` as
> easily as behind it. If they differ, read the robot's own `duck-ipc-proto` before going on —
> the field shapes matter more than the number.

> Abort if `healthy` is false, or if the battery is low. A duck that browns out mid-walk
> teaches you nothing.

## 3. Does quackd actually see the robot?

```bash
quackd run microduck-lookout --robot microduck:jsonrpc --address tcp://127.0.0.1:9870 \
  --provider fake --dry-run --no-memory
```

Then read `report_state` in the transcript. **`posture` must not be `unknown`.**

`unknown` means no `robot.state` frames are arriving, and everything that decides whether the
duck may walk is reading nothing: `fallen` is `false` because nobody is looking, not because
the duck is upright. quackd refuses to walk in that state on purpose.

> Abort if posture is `unknown`. Check that `robot.subscribe` was accepted — `quackd doctor`
> prints the answer, including the robot's real skill list.

## 4. A dry run moves nothing

The command above already had `--dry-run`. Watch the duck while it runs: it should not twitch.

> Abort if anything moves. `--dry-run` is the promise the rest of this page rests on.

## 5. Sound, which moves no joints

```bash
quackd run microduck-lookout --robot microduck:jsonrpc --address tcp://127.0.0.1:9870 \
  --provider fake --no-memory
```

`say` and `quack` map text to one of seven duck tones. Nothing here moves a leg.

> Abort if the robot does not make a sound and `robot.sound` was accepted — something is
> answering that is not the robot you think.

## 6. Video

Frames are the reason most people are doing this, and upstream serves none over `robotd`'s
socket. The camera is an H.264 WebRTC track from `mediad` and nothing else.

```bash
ssh -L 8443:127.0.0.1:8443 radxa@<duck>          # in another terminal
quackd doctor --robot microduck:jsonrpc --address tcp://127.0.0.1:9870 \
  --camera-url webrtc://127.0.0.1:8443
```

`doctor` fetches a frame and prints its size. Needs `quackd[microduck-camera]`.

This takes the robot's one media session, so the browser console cannot be open at the same
time. It installs nothing on the duck.

> Abort if no frame arrives. Check `mediad` registered a producer — if its pipeline never
> reached PLAYING there is nothing to connect to, and quackd says so rather than hanging.
> ICE on a shared office network is the next thing to suspect.

**The alternative, only with permission:** a snapshot server on the duck itself needs
`sudo systemctl stop mediad` first, because `mediad` holds `/dev/video0` for the life of its
process. That stops their console and their WebRTC. Do not do this to a borrowed robot without
asking.

## 7. The head, on a stand

Put the duck on a stand. `gaze` runs upstream's gaze IK through `robot.look`; the answer says
whether it clamped.

> Abort if the head moves somewhere you did not ask for. The IK is upstream's, and a clamp is
> expected — an unexpected direction is not.

## 8. Pull the tunnel out, mid-walk

**Feet still off the ground.** Start a short walk, and kill the ssh tunnel while it is moving.

What should happen: `robot.move` notifications stop arriving and `robotd`'s deadman zeroes the
velocity. quackd's heartbeat fails, says it is sending a stop, and aborts the run — and it will
tell you if that stop could not be delivered, which is the honest outcome when the link is the
thing that broke.

> **Abort the whole session if the legs keep driving.** The deadman is the protection every
> step below this one depends on. Nothing else quackd does matters if it is not there.

## 9. Feet down

Now, and only now, put the duck on the floor and run a short walk. Hand on the gamepad.

```bash
quackd run find-and-kick --robot microduck:jsonrpc --address tcp://127.0.0.1:9870 \
  --camera-url webrtc://127.0.0.1:8443 --provider anthropic --memory-dir ./duck-day
```

Start with `--no-memory` for a clean first run, then `--memory-dir` so the campaign's notes stay
out of your simulator's.

## 10. Tell everyone

`microduck:jsonrpc` is 🧪 in [`docs/adapter-status.md`](adapter-status.md) because nobody has
done this. What flips it is a `quackd doctor` output and the first lines of a
`transcript.jsonl`, in an issue. The memory file is an index of what you tried, not evidence —
the transcript is the artefact.

What is most worth writing down, because it is what nobody can check without a duck:

- whether `posture` tracked the real robot, and whether `policy` said `sit` when it sat
  (quackd assumes it does, and says so in `extras.assumptions`)
- whether `robot.enable` brought a limp robot up, and how much it moved doing it
- whether `media.detections` arrived on the control datachannel — upstream's source says it
  does and upstream's design doc says it does not, and one of them is out of date
- what `robot.subscribe` listed in `skills`, which is where a robot's real skill set lives now
