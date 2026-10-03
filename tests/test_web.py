"""The browser demo, checked by the only means the repository has.

Nothing in `web/` had ever been run in a browser and no job read the directory, so the page
did not work: a CSS rule kept the loading panel over the canvas for good, a blocked CDN left
a dead page with no message, and every listener was live during a 45 MB load, dereferencing
objects that did not exist yet.

None of that needs a browser to catch. What it needs is somebody checking that the ids the
JavaScript looks up exist, that the classes it toggles mean something, that a rule does not
quietly beat `hidden`, and that each module parses. This is that, in the standard library,
plus `node --check` where node is on the runner (all three GitHub images have it).

It is not a substitute for opening the page. It is the floor under it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from quackd_microduck.sim3d import upstream_api as up
from tests.conftest import REPO

WEB = REPO / "web"
SRC = WEB / "src"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
CSS = (WEB / "style.css").read_text(encoding="utf-8")
MODULES = sorted(SRC.glob("*.js"))

# The path the page is served from: www.quackd.org/simulator, and /simulator on this project's
# own deployment. Every local reference in the HTML is absolute under it.
MOUNT = "/simulator"

# The other half of the product, one level up from the mount. Written absolute wherever this
# page points at it, and so written absolute here.
SITE = "https://www.quackd.org/"


def _unmount(reference: str) -> str:
    """A reference as the browser sees it, back to a path inside `web/`."""
    return reference[len(MOUNT) + 1 :] if reference.startswith(MOUNT + "/") else reference


def _js(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def _keydown() -> str:
    """The window-level keydown listener, from its opening line to the `});` that closes it in
    column zero. The goal box has a keydown of its own for Escape, but that one is indented
    behind `ui.goal.`, so anchoring both ends to column zero picks out the page's handler."""
    found = re.search(r'^addEventListener\("keydown".*?^\}\);', _js("app.js"), re.S | re.M)
    assert found, "app.js registers no window-level keydown listener"
    return found.group(0)


ALL_JS = "\n".join(p.read_text(encoding="utf-8") for p in MODULES)


# ── the page and the script agree about what is on it ───────────────────────────────────


def test_every_id_the_javascript_looks_up_exists_in_the_page() -> None:
    ids = set(re.findall(r'\$\("([\w-]+)"\)', ALL_JS))
    ids |= set(re.findall(r'getElementById\("([\w-]+)"\)', ALL_JS))
    ids |= set(re.findall(r'querySelector\("#([\w-]+)"\)', ALL_JS))
    assert ids, "the regexes found nothing, so this test is not testing anything"
    present = set(re.findall(r'id="([\w-]+)"', HTML))
    missing = sorted(ids - present)
    assert not missing, f"web/src/*.js looks up ids that index.html does not define: {missing}"


def test_every_class_the_javascript_toggles_is_defined_somewhere() -> None:
    used = set(re.findall(r'classList\.(?:add|remove|toggle)\("([\w-]+)"', ALL_JS))
    used |= set(re.findall(r'querySelectorAll\("\.([\w-]+)"\)', ALL_JS))
    known = set(re.findall(r"\.([a-zA-Z][\w-]*)\s*[,{:]", CSS)) | set(
        re.findall(r'class="([^"]+)"', HTML)
    )
    known = {c for group in known for c in group.split()}
    missing = sorted(used - known)
    assert not missing, f"the JavaScript toggles classes nothing defines: {missing}"


def test_nothing_the_javascript_hides_is_pinned_visible_by_a_rule() -> None:
    """`[hidden]` is a user-agent rule and loses to any author rule that sets `display` at the
    same specificity. `.overlay { display: grid }` beat it, so `loading.hidden = true` left
    the loading panel covering the canvas and the entire stage bar for good."""
    assert re.search(r"\[hidden\][^{]*\{[^}]*display:\s*none\s*!important", CSS), (
        "web/style.css needs `[hidden] { display: none !important; }`: without it any rule "
        "that sets `display` on an element the script hides silently keeps it on screen"
    )


def test_the_controls_that_need_a_loaded_duck_start_disabled() -> None:
    """Every listener is attached at module scope, but `duck`, `runtime` and `recorder` only
    exist after a 45 MB load. Pressing these during it used to throw."""
    for element in ("run", "reset", "record", "save", "stop-run"):
        pattern = rf'id="{element}"[^>]*\bdisabled\b'
        assert re.search(pattern, HTML), f"#{element} must start disabled and be enabled by boot"


def test_the_goal_form_cannot_navigate_away_with_what_was_typed_in_it() -> None:
    """If the module never evaluates, a plain form submits on Enter and puts the visitor's
    sentence into the URL and the Referer."""
    form = re.search(r"<form[^>]*id=\"goal-form\"[^>]*>", HTML)
    assert form and 'method="dialog"' in form.group(0)
    assert not re.search(r'<input id="goal"[^>]*\bname=', HTML)


# ── the brand on the page is the one this repository holds ──────────────────────────────


def test_the_header_wears_the_vendored_duck_mark_and_not_an_emoji() -> None:
    """The header and the favicon were both a 🦆: the emoji in a `<span aria-hidden>`, and the
    same emoji drawn into an inline-SVG data URI for the tab. A logo rendered out of the
    visitor's emoji font is a different logo on every machine that opens the page, and quackd
    has a mark of its own. It is in `web/assets` now, with the two icon sizes beside it."""
    chrome = HTML.split("</header>")[0]
    assert "🦆" not in chrome, (
        "the duck emoji is back in the head or the header. The mark is "
        "web/assets/duck-mark.png and the icons are the two PNGs beside it"
    )
    header = re.search(r"<header\b.*?</header>", HTML, re.S)
    assert header, "index.html has no <header>, so this test cannot see what it wears"
    assert re.search(rf'<img[^>]+src="{MOUNT}/assets/duck-mark\.png"', header.group(0)), (
        "the header does not carry web/assets/duck-mark.png"
    )
    icon = re.search(r'<link[^>]*rel="icon"[^>]*>', HTML)
    assert icon and f"{MOUNT}/assets/favicon-96.png" in icon.group(0), (
        "the tab icon is not the vendored PNG favicon"
    )
    assert f'href="{MOUNT}/assets/apple-touch-icon.png"' in HTML, (
        "nothing links the home-screen icon"
    )


def test_no_mark_in_web_assets_runs_off_its_own_canvas() -> None:
    """The bug this catches shipped twice: `duck-mark.png` and `favicon-96.png` were both
    exported with the crop window about twenty rows too high, so the duck's chin, lower jaw and
    mouth were sliced away by the bottom edge of the file. Opaque pixels on the last row are
    the tell, and it is invisible in review because the image still looks like a duck.

    It reached the repository README and the social card before anyone noticed, because both
    read the same file. `web/make_mark.py` refuses to write an export that fails this, and this
    is the same check standing over what is actually committed."""
    pytest.importorskip("PIL", reason="Pillow is not a runtime dependency of quackd")
    from PIL import Image

    marks = sorted((REPO / "web" / "assets").glob("*.png"))
    assert marks, "web/assets has no PNGs, so this test is not testing anything"
    for path in marks:
        image = Image.open(path).convert("RGBA")
        width, height = image.size
        ink = image.getbbox()
        assert ink is not None, f"{path.name} is entirely transparent"
        if ink == (0, 0, width, height):
            # a full-bleed icon: apple-touch-icon.png is a plate, edge to edge on purpose
            continue
        assert ink[0] > 0 and ink[1] > 0 and ink[2] < width and ink[3] < height, (
            f"{path.name} has ink at {ink} in a {width}x{height} canvas, so the art is cut off "
            f"by its own edge. Re-cut it with web/make_mark.py."
        )


def test_the_page_offers_a_way_back_to_the_site_that_mounts_it() -> None:
    """quackd-web links here from five places — the hero, the loop, the try section, the footer
    and the nav — and this page linked back zero times: the mark was not even a link and "What
    is this?" answered with a README, which is written for somebody who already decided to
    care. So a visitor arriving straight at /simulator, from a shared link or from search or
    from the `sitemap.xml` entry the landing page publishes, had no path to the product at all.

    The URL has to be absolute. This file is a static artifact copied into another project's
    build, and `web/serve.py` sends the local root straight back to `/simulator/`, so a bare
    `/` would loop in development and would mean whatever owns the root in production.
    """
    header = re.search(r"<header\b.*?</header>", HTML, re.S)
    assert header, "index.html has no <header>, so this test cannot see what it offers"
    assert f'href="{SITE}"' in header.group(0), (
        f"nothing in the header links to {SITE}, so a visitor who lands on the simulator has "
        f"no way to the landing page. It must be that absolute URL: a relative `/` is a loop "
        f"back to this page under web/serve.py"
    )
    heading = re.search(r"<h1\b.*?</h1>", HTML, re.S)
    assert heading and re.search(rf'<a[^>]+href="{SITE}"', heading.group(0)), (
        "the brand lockup is not the way home. A mark and a wordmark that return the visitor "
        "to the site is the convention every page on the web shares, and this h1 holds both"
    )


def test_every_asset_the_page_asks_for_is_a_file_in_this_directory() -> None:
    """The deploy is a copy of `web/` and nothing reads the HTML on the way out, so a mistyped
    `src` is a broken mark in production and a green build here. Every local reference the page
    makes has to resolve on disk, under the mount prefix the deploy serves it from."""
    referenced = set(re.findall(r'(?:src|href)="(?!https?:|data:|mailto:|#)([^"]+)"', HTML))
    assert referenced, "the regex found no local references, so this test is not testing anything"
    missing = sorted(ref for ref in referenced if not (WEB / _unmount(ref).split("?")[0]).is_file())
    assert not missing, f"index.html points at files that web/ does not hold: {missing}"


def test_every_local_reference_is_absolute_under_the_mount() -> None:
    """The page is served at www.quackd.org/simulator, and quackd-web sets `trailingSlash: false`
    — so the browser lands on `/simulator` with no slash, and a *relative* `style.css` resolves
    to `/style.css`, which is the landing page's root and not this directory at all. The HTML
    would arrive and every asset under it would 404. Relative paths cannot come back."""
    referenced = set(re.findall(r'(?:src|href)="(?!https?:|data:|mailto:|#)([^"]+)"', HTML))
    assert referenced, "the regex found no local references, so this test is not testing anything"
    relative = sorted(ref for ref in referenced if not ref.startswith(MOUNT + "/"))
    assert not relative, (
        f"these references are relative and will break behind the /simulator mount: {relative}"
    )


def test_the_deploy_answers_on_the_mount_it_tells_the_browser_to_use() -> None:
    """Two projects serve this page: quackd-web proxies /simulator/* through to this one, and
    this one is also reachable on its own deployment URL. The HTML asks for /simulator/... in
    both cases, so this project has to answer there as well as at its root, or the direct
    deployment serves an unstyled page with no script."""
    config = json.loads((REPO / "vercel.json").read_text(encoding="utf-8"))
    sources = {rule["source"] for rule in config.get("rewrites", [])}
    assert f"{MOUNT}/:path*" in sources, (
        f"vercel.json must rewrite {MOUNT}/:path* to /:path*, or every asset 404s on the "
        f"deployment's own URL: {sorted(sources)}"
    )


# ── two ways to drive, and both of them always live ─────────────────────────────────────


def test_the_keyboard_is_not_gated_behind_the_quackd_switch() -> None:
    """The switch used to be exclusive: `runtime.manual = !on` inside applyToggle, an early
    return on `ui.toggle.checked` at the top of the keydown handler, and the cockpit hidden
    while quackd was on. So the page's own argument arrived as an either/or, and a visitor had
    to flip a mode before a key did anything at all. The switch decides one thing now — whether
    anything here reads English — and the keyboard was never that layer."""
    app = _js("app.js")
    body = _keydown()
    for gate in ("toggle", "checked", "quackd-on"):
        assert gate not in body, (
            f"the keydown handler reads `{gate}`: driving must not depend on the switch, which "
            f"is the whole of the dual-control claim the page makes"
        )
    apply_toggle = re.search(r"^function applyToggle\(\).*?^\}", app, re.S | re.M)
    assert apply_toggle, "app.js has no applyToggle(), which is where the old gate lived"
    assert not re.search(r"runtime\.manual\s*=", apply_toggle.group(0)), (
        "applyToggle() takes the twist lease again; the lease belongs to the run, not the switch"
    )
    assert not re.search(r"manual\w*\.hidden\s*=", app), (
        "something hides the cockpit again. It is permanent, and its adjacency to the goal box "
        "is the argument the page makes by proximity instead of by copy"
    )
    assert not re.search(r'id="manual"[^>]*\bhidden\b', HTML), "#manual ships hidden"


def test_a_key_that_moves_the_robot_takes_it_and_a_key_that_only_reads_does_not() -> None:
    """Barge-in is what makes the two controls live rather than merely both present: a drive
    key pressed during a run takes the robot at once and the transcript says which key did it,
    while `O` and the camera keys leave the run alone. The rule is exactly that — a key barges
    in if and only if it would move the robot."""
    app = _js("app.js")
    body = _keydown()

    def keyset(name: str) -> set[str]:
        found = re.search(rf"{name} = new Set\(\[([^\]]*)\]", app)
        assert found, f"app.js no longer declares {name}"
        return set(re.findall(r'"(\w+)"', found.group(1)))

    motor, read = keyset("MOTOR"), keyset("READ")
    assert {"KeyW", "KeyA", "KeyS", "KeyD", "Space"} <= motor, (
        "the drive keys are not all in MOTOR, so pressing one mid-run takes the robot from nobody"
    )
    assert not motor & read, "a key cannot both take the robot and leave the run alone"
    assert re.search(r"if \(motor\).*bargeIn\(", body), (
        "the keydown handler no longer barges in on a motor key"
    )
    assert "bargeIn" not in body.split("if (motor)")[0], (
        "something barges in before the motor test, so a read-only key would stop a run"
    )
    barge = re.search(r"^function bargeIn\(.*?^\}", app, re.S | re.M)
    assert barge, "app.js has no bargeIn()"
    for piece, why in (
        (".abort(", "cancel the run, which would otherwise race the hand for the twist"),
        ('giveTwistTo("hand")', "hand the twist over, which is the handover itself"),
        ('"handover"', "tell the transcript a key took the controls"),
    ):
        assert piece in barge.group(0), f"bargeIn() does not {why}"


def test_a_focused_control_keeps_the_keys_that_are_its_own() -> None:
    """WCAG 2.1.1: Space activates a focused button or `<summary>`, and W typed into the goal
    box is a letter. `preventDefault` ran before this guard once, and the drive keys took both.
    Now that the keyboard never sleeps, the guard is the only thing separating typing a
    sentence from driving."""
    app = _js("app.js")
    typing = re.search(r'TYPING = "([^"]+)"', app)
    assert typing, "app.js no longer declares the TYPING selector the guard reads"
    for control in ("input", "textarea", "button", "summary"):
        assert control in typing.group(1), f"{control} is not in TYPING, so it loses its own keys"
    # comments stripped: the handler's own comment names `preventDefault` while explaining
    # why the guard has to come first, and reading that as code inverts the order it describes
    body = re.sub(r"^\s*//.*$", "", _keydown(), flags=re.M)
    assert "TYPING" in body, "the keydown handler no longer defers to a focused control"
    assert body.index("TYPING") < body.index("preventDefault"), (
        "preventDefault runs before the TYPING guard, which is exactly how Space stopped "
        "activating focused buttons the first time"
    )


def test_an_abandoned_turn_stops_being_billed_for() -> None:
    """Barge-in and Stop abort the run, and the run's signal has to reach both the sleep inside
    a verb and the request in flight. It reached neither: a ten second `move` ran to completion
    after Stop, and the answer nobody wanted arrived seconds later against the visitor's key."""
    providers = _js("providers.js")
    calls = providers.count("await fetch(")
    assert calls, "providers.js makes no fetch, so this test is not testing anything"
    assert providers.count("signal,") == calls, (
        f"web/src/providers.js makes {calls} requests and passes the AbortSignal to "
        f"{providers.count('signal,')} of them"
    )
    assert re.search(r"verb\.run\([^)]*signal", _js("pilot.js")), (
        "pilot.js does not pass the signal into the verb, so Runtime.sleep's abort listener is "
        "dead code and an aborted verb runs to the end"
    )


# ── failures have somewhere to go ───────────────────────────────────────────────────────


def test_the_page_reports_a_failure_rather_than_freezing_on_one() -> None:
    app = _js("app.js")
    assert 'addEventListener("unhandledrejection"' in app
    assert 'addEventListener("error"' in app
    assert re.search(r"boot\(\)\.catch\(", app), "a bare boot() leaves a dead page and no message"


def test_no_module_reached_from_the_page_imports_a_cdn_statically() -> None:
    """A static import of a blocked host takes down the whole module graph before a single
    listener is attached: no page, no error, nothing to read. The heavy dependency is loaded
    inside boot, where a rejection can be shown."""
    app = _js("app.js")
    static_cdn = re.findall(r'^\s*import\s[^;]*from\s+"(https?://[^"]+)"', app, re.M)
    assert not static_cdn, f"app.js statically imports {static_cdn}; import it inside boot()"
    assert 'await import("./view.js")' in app, "three.js rides on view.js, so that one is dynamic"


# ── the key, which is the visitor's ─────────────────────────────────────────────────────


def test_the_api_key_is_never_stored_anywhere() -> None:
    """SECURITY.md says the key lives in the form field and the request, and nowhere else."""
    for banned in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
        assert banned not in ALL_JS, f"web/src/*.js touches {banned}; the key must not be stored"
    assert "console.log" not in ALL_JS, "a logged request is a logged key"


def test_switching_provider_clears_the_key_field() -> None:
    """Disabling the input left its value readable, so a key pasted for one vendor was still
    there when the visitor switched to a local server and went out as a bearer token to
    whatever host was in the free-text box."""
    assert re.search(r'ui\.key\.value\s*=\s*""', _js("app.js"))


def test_a_key_is_only_ever_sent_over_https_or_to_this_machine() -> None:
    providers = _js("providers.js")
    assert "export function localBaseUrl" in providers
    assert "localBaseUrl(" in providers.split("export function localBaseUrl")[0], (
        "localBaseUrl is defined but never used, so the base URL is still unchecked"
    )


# ── the browser asks on the API the model will actually answer on ───────────────────────

#: The 400 quoted rather than paraphrased, because both OpenAI clients match on its words.
#: tests/test_providers.py holds the same text for the Python half. It is handed to the
#: browser tests below too, so there is one copy of it on this side of the repo.
REFUSAL = (
    "Function tools with reasoning_effort are not supported for gpt-6-astra in "
    "/v1/chat/completions. To use function tools, use /v1/responses or set "
    "reasoning_effort to 'none'."
)


def _drive_providers(script: str) -> Any:
    """Run `script` as a module against web/src/providers.js and parse what it logs.

    The module URI and the 400 are injected rather than retyped, so a test cannot quietly
    assert against a different string from the one the code is matched on.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine; the GitHub runners all have one")
    prelude = (
        f"const MODULE = {json.dumps((SRC / 'providers.js').as_uri())};\n"
        f"const REFUSAL = {json.dumps(REFUSAL)};\n"
    )
    done = subprocess.run(
        [node, "--input-type=module"],
        input=prelude + script,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


#: A tool list and a two-turn history, in the shapes web/src/pilot.js really builds:
#: `history.push({ observation, call })` where `call` is `{ name, arguments }`.
#: An OpenAI model the catalogue does NOT route to Responses. The two tests below drive the
#: 400 reader, and a model the catalogue already routes would start on Responses and never
#: reach it. `tests/test_providers.py` keeps the same constant for the same reason.
UNHINTED = "gpt-5.6-sol"

_FIXTURES = f"""
const UNHINTED = "{UNHINTED}";
const tools = [
  {{ name: "walk", description: "walk a bit",
    input_schema: {{ type: "object", properties: {{ vx: {{ type: "number" }} }} }} }},
];
const history = [
  {{ observation: "obs one", call: {{ name: "walk", arguments: {{ vx: 0.2 }} }} }},
  {{ observation: "obs two", call: {{ name: "gaze", arguments: {{ bearing_deg: 30 }} }} }},
];
"""


def test_the_browser_moves_to_responses_when_chat_refuses_function_tools() -> None:
    """The half of 0.8's OpenAI fix that the page never got.

    Some reasoning models refuse function tools on `/v1/chat/completions` and name
    `/v1/responses` in the 400. Every verb here is a function tool, so that refusal is not a
    degraded path, it is no path. `quackd/agent/providers/openai.py` learned to read it and
    move the run; the browser has an OpenAI client of its own, kept speaking Chat Completions
    only, and put the raw vendor message in the transcript at the first call instead. Picking
    `gpt-6-astra` in the page was a demo that could not take one step.

    `fetch` is stubbed and the module driven through two steps, for the three things that
    matter: it moves, it stays moved, and what it sends on the second API is that API's
    shapes and not the first's.
    """
    got = _drive_providers(
        _FIXTURES
        + """
const { makeProvider } = await import(MODULE);

// A real Response, not a stand-in: its body can only be read once, which is the constraint
// the error path has to respect to report the 400 it just matched on.
const seen = [];
globalThis.fetch = async (url, init) => {
  seen.push({ url, body: JSON.parse(init.body) });
  if (url.endsWith("/chat/completions")) {
    return new Response(JSON.stringify({ error: { message: REFUSAL } }), { status: 400 });
  }
  return new Response(JSON.stringify({
    output: [
      { type: "reasoning", summary: [] },
      { type: "function_call", call_id: "c1", name: "walk", arguments: '{"vx": 0.2}' },
    ],
  }), { status: 200 });
};

const p = makeProvider({ provider: "openai", key: "sk-test", model: UNHINTED });
const first = await p.step({ system: "SYS", history: [], observation: "obs one", tools });
await p.step({ system: "SYS", history, observation: "obs three", tools });

// and a 400 that is not that one still surfaces, on a provider of its own
const probe = [];
globalThis.fetch = async (url) => {
  probe.push(url);
  return new Response(JSON.stringify({ error: { message: "model not found" } }), { status: 400 });
};
const q = makeProvider({ provider: "openai", key: "sk-test", model: "nope" });
let unrelated = "it did not throw";
try {
  await q.step({ system: "SYS", history: [], observation: "obs", tools });
} catch (error) {
  unrelated = error.message;
}

console.log(JSON.stringify({
  first,
  urls: seen.map((s) => s.url),
  switched: seen[1].body,
  replayed: seen[2].body,
  unrelated,
  probe,
}));
"""
    )

    assert got["first"] == {"name": "walk", "arguments": {"vx": 0.2}}, (
        "the page did not get a tool call out of the model the CLI drives fine"
    )
    assert [url.rsplit("/v1/", 1)[-1] for url in got["urls"]] == [
        "chat/completions",
        "responses",
        "responses",
    ], (
        f"the page asked {got['urls']}. It must try chat once, move, and stay moved: going "
        f"back to the API that refuses it pays a failed call every turn of the run"
    )

    switched = got["switched"]
    assert switched["instructions"] == "SYS" and "messages" not in switched, (
        "the system prompt goes in `instructions` on Responses, not in a system message"
    )
    assert switched["tool_choice"] == "required", "the page still has to insist on a call"
    assert switched["tools"][0]["name"] == "walk" and "function" not in switched["tools"][0], (
        "Responses tools are flat; the `function` wrapper is Chat Completions' shape"
    )

    items = got["replayed"]["input"]
    assert [item.get("type") or item.get("role") for item in items] == [
        "user",
        "function_call",
        "function_call_output",
        "user",
        "function_call",
        "function_call_output",
        "user",
    ], f"a replayed turn is three items, then the next observation, not {items}"
    for call, output in ((1, 2), (4, 5)):
        assert items[call]["call_id"] == items[output]["call_id"], (
            "a function_call_output has to quote the call_id of the call it answers, or the "
            "API 400s saying the output refers to a call that does not exist"
        )
    assert items[1]["call_id"] != items[4]["call_id"], (
        "two turns reused one call_id, so the second output answers the first call"
    )
    assert items[0]["content"][0]["type"] == "input_text", "Responses text parts are input_text"
    assert json.loads(items[1]["arguments"]) == {"vx": 0.2}, "arguments go back as a JSON string"

    assert "OpenAI said 400" in got["unrelated"] and "model not found" in got["unrelated"], (
        f"an unrelated 400 must reach the transcript intact, not as {got['unrelated']!r}"
    )
    assert len(got["probe"]) == 1 and got["probe"][0].endswith("/chat/completions"), (
        f"an unrelated 400 moved the run to an API nobody asked for: {got['probe']}"
    )


#: The 400 a Claude model answers a forced tool call with, quoted because both Anthropic
#: clients match on its words. tests/test_providers.py holds the same text for the Python half.
REFUSES_FORCED = 'tool_choice: type "tool" and "any" are not supported for this model.'


def test_the_browser_asks_claude_for_a_call_the_way_the_model_will_accept() -> None:
    """Claude Opus 5.5 is the page's Anthropic default, and it answers `tool_choice` `any` with
    a 400. The page used to force a call on every Claude model, so a visitor who picked
    Anthropic and pressed Run got that 400 as the whole transcript.

    Three things: the catalogue's mark reaches the page and the default is asked with `auto`
    from its first turn; a model the catalogue does not mark learns it from the 400 once and
    stays moved; and a `tool_choice` complaint that `auto` cannot fix still surfaces, after one
    request rather than two.
    """
    got = _drive_providers(
        _FIXTURES
        + f"const REFUSES_FORCED = {json.dumps(REFUSES_FORCED)};\n"
        + """
const { makeProvider } = await import(MODULE);
const reply = (status, body) => new Response(JSON.stringify(body), { status });
const ok = { content: [{ type: "tool_use", id: "x", name: "walk", input: { vx: 0.2 } }] };

const sent = [];
globalThis.fetch = async (url, init) => {
  sent.push(JSON.parse(init.body));
  return reply(200, ok);
};
const marked = makeProvider({ provider: "anthropic", key: "sk-test" });
const markedCall = await marked.step({ system: "SYS", history: [], observation: "obs", tools });

const learned = [];
globalThis.fetch = async (url, init) => {
  const body = JSON.parse(init.body);
  learned.push(body.tool_choice.type);
  if (body.tool_choice.type === "any") return reply(400, { error: { message: REFUSES_FORCED } });
  return reply(200, ok);
};
const unmarked = makeProvider({
  provider: "anthropic", key: "sk-test", model: "claude-not-in-this-build",
});
await unmarked.step({ system: "SYS", history: [], observation: "obs", tools });
await unmarked.step({ system: "SYS", history, observation: "obs three", tools });

const probe = [];
globalThis.fetch = async (url, init) => {
  probe.push(JSON.parse(init.body).tool_choice.type);
  return reply(400, { error: { message: "tool_choice.name: Input should be a declared tool" } });
};
const q = makeProvider({ provider: "anthropic", key: "sk-test", model: "claude-opus-5" });
let unrelated = "it did not throw";
try {
  await q.step({ system: "SYS", history: [], observation: "obs", tools });
} catch (error) {
  unrelated = error.message;
}

console.log(JSON.stringify({
  model: sent[0].model, choice: sent[0].tool_choice, markedCall, learned, probe, unrelated,
}));
"""
    )

    assert got["model"] == "claude-opus-5-5", "the page's Anthropic default is not the CLI's"
    assert got["choice"] == {"type": "auto", "disable_parallel_tool_use": True}, (
        f"the default was asked with {got['choice']}, which it answers with a 400"
    )
    assert got["markedCall"] == {"name": "walk", "arguments": {"vx": 0.2}}
    assert got["learned"] == ["any", "auto", "auto"], (
        f"an unmarked model was asked {got['learned']}: it must be forced once, move to auto, "
        f"and stay moved, or every turn pays a failed call against the visitor's own key"
    )
    assert got["probe"] == ["any"] and "Anthropic said 400" in got["unrelated"], (
        f"a tool_choice complaint auto cannot fix was retried or swallowed: {got}"
    )


def test_the_browser_asks_deepseek_with_thinking_off_and_its_call_insisted_on() -> None:
    """DeepSeek thinks by default, and thinking mode refuses `required` and wants every earlier
    turn's reasoning sent back, which this page never keeps. So the page turns thinking off the
    way `deepseek.py` does, and a vendor's extra fields sit under quackd's own, never over."""
    got = _drive_providers(
        _FIXTURES
        + """
const { makeProvider } = await import(MODULE);
let sent = null;
globalThis.fetch = async (url, init) => {
  sent = JSON.parse(init.body);
  return new Response(JSON.stringify({
    choices: [{ message: { tool_calls: [
      { id: "c1", type: "function", function: { name: "walk", arguments: "{}" } },
    ] } }],
  }), { status: 200 });
};
const p = makeProvider({ provider: "deepseek", key: "sk-test", model: "deepseek-flash" });
await p.step({ system: "SYS", history, observation: "obs three", tools });
console.log(JSON.stringify(sent));
"""
    )
    assert got["thinking"] == {"type": "disabled"}
    assert got["tool_choice"] == "required"
    assert got["model"] == "deepseek-flash" and got["messages"][0]["content"] == "SYS"


def test_the_browser_still_sends_the_chat_shapes_on_the_path_almost_everyone_uses() -> None:
    """The default path, which the Responses work rewrote and nothing guarded.

    Adding the second API meant lifting the Chat Completions body out into its own function
    and giving the replayed tool calls ids from the turn index rather than from the length of
    the array being built. Every visitor on `gpt-5`, and every local server, still goes this
    way, and a mistake in either edit would be invisible until somebody opened the page. The
    id pairing is checked across two turns on purpose: one turn cannot tell a correct scheme
    from one that hands both turns the same handle.
    """
    got = _drive_providers(
        _FIXTURES
        + """
const { makeProvider } = await import(MODULE);
let sent = null;
globalThis.fetch = async (url, init) => {
  sent = { url, body: JSON.parse(init.body) };
  return new Response(JSON.stringify({
    choices: [{ message: { tool_calls: [
      { id: "call_9", type: "function", function: { name: "walk", arguments: '{"vx": 0.3}' } },
    ] } }],
  }), { status: 200 });
};
const p = makeProvider({ provider: "openai", key: "sk-test", model: "gpt-5" });
const call = await p.step({ system: "SYS", history, observation: "obs three", tools });
console.log(JSON.stringify({ call, url: sent.url, body: sent.body }));
"""
    )

    assert got["url"].endswith("/v1/chat/completions"), (
        f"a model that never refused anything was sent to {got['url']}"
    )
    assert got["call"] == {"name": "walk", "arguments": {"vx": 0.3}}

    body = got["body"]
    assert body["tool_choice"] == "required"
    assert body["tools"][0]["function"]["name"] == "walk", (
        "Chat Completions nests the tool under `function`; flat is the Responses shape"
    )
    messages = body["messages"]
    assert [m["role"] for m in messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "user",
        "assistant",
        "tool",
        "user",
    ], f"the chat transcript is the wrong shape: {[m['role'] for m in messages]}"
    assert messages[0]["content"] == "SYS", "the system prompt is a system message here"
    for assistant, result in ((2, 3), (5, 6)):
        assert messages[assistant]["tool_calls"][0]["id"] == messages[result]["tool_call_id"], (
            "a tool message has to quote the id of the call it answers"
        )
    assert messages[2]["tool_calls"][0]["id"] != messages[5]["tool_calls"][0]["id"], (
        "both turns were given the same tool call id"
    )


def test_the_browser_gives_up_rather_than_looping_when_responses_fails_too() -> None:
    """The switch retries once, and the retry is the only one there is.

    `step` moves APIs inside a `for (;;)`, so the thing worth proving is that it cannot spin:
    a Responses call that fails in turn has to reach the transcript, not be retried against
    the visitor's key forever. The second half covers the other way Responses can disappoint,
    which is a perfectly good 200 with no `function_call` anywhere in `output`.
    """
    got = _drive_providers(
        _FIXTURES
        + """
const { makeProvider } = await import(MODULE);

const urls = [];
globalThis.fetch = async (url) => {
  urls.push(url);
  if (url.endsWith("/chat/completions")) {
    return new Response(JSON.stringify({ error: { message: REFUSAL } }), { status: 400 });
  }
  return new Response(JSON.stringify({ error: { message: "responses is unhappy" } }),
                      { status: 400 });
};
const p = makeProvider({ provider: "openai", key: "k", model: UNHINTED });
let gaveUp = "it did not throw";
try {
  await p.step({ system: "SYS", history: [], observation: "obs", tools });
} catch (error) {
  gaveUp = error.message;
}

const quiet = [];
globalThis.fetch = async (url) => {
  quiet.push(url);
  if (url.endsWith("/chat/completions")) {
    return new Response(JSON.stringify({ error: { message: REFUSAL } }), { status: 400 });
  }
  return new Response(JSON.stringify({ output: [
    { type: "reasoning", summary: [] },
    { type: "message", content: [{ type: "output_text", text: "I would rather talk" }] },
  ] }), { status: 200 });
};
const q = makeProvider({ provider: "openai", key: "k", model: UNHINTED });
let noCall = "it did not throw";
try {
  await q.step({ system: "SYS", history: [], observation: "obs", tools });
} catch (error) {
  noCall = error.message;
}

console.log(JSON.stringify({ urls, gaveUp, quiet, noCall }));
"""
    )

    assert [url.rsplit("/v1/", 1)[-1] for url in got["urls"]] == [
        "chat/completions",
        "responses",
    ], f"the retry is not bounded at one: the page made {got['urls']}"
    assert "OpenAI said 400" in got["gaveUp"] and "responses is unhappy" in got["gaveUp"], (
        f"a failing Responses call has to surface, not be swallowed: {got['gaveUp']!r}"
    )
    assert len(got["quiet"]) == 2, f"the no-call answer was retried: {got['quiet']}"
    assert "answered without calling a tool" in got["noCall"], (
        f"a Responses answer with no function_call must end the run cleanly: {got['noCall']!r}"
    )


def test_the_browser_and_python_agree_on_which_400_means_responses() -> None:
    """Two OpenAI clients, one rule, and no way for either to notice the other drifted.

    Both match on what the API said rather than on a model name, because the list of models
    that behave this way is not this project's to keep. Both require both halves, so an
    unrelated 400 that happens to name one of them does not move a run onto a different API.

    Both predicates are RUN over the same messages rather than read for the right words. A
    substring check passes happily on an `and` quietly turned into an `or`, which is exactly
    the drift worth catching: it would move a run on any 400 that said `responses`.
    """
    from quackd.agent.providers.openai import _wants_the_responses_api

    class Vendor(Exception):
        """What the SDK raises: a message, and a status code hanging off it."""

        def __init__(self, status: int, message: str) -> None:
            super().__init__(message)
            self.status_code = status

    cases = [
        ("the refusal itself", 400, REFUSAL, True),
        ("the same words shouted", 400, REFUSAL.upper(), True),
        ("only the first half", 400, "Function tools are not supported for this model.", False),
        ("only the second half", 400, "Use /v1/responses for this one.", False),
        ("an unrelated 400", 400, "context_length_exceeded: too many tokens", False),
        ("neither half", 400, "Incorrect API key provided.", False),
        ("the right words, the wrong status", 500, REFUSAL, False),
    ]
    browser = _drive_providers(
        "const { wantsTheResponsesApi } = await import(MODULE);\n"
        f"const cases = {json.dumps([[status, detail] for _, status, detail, _ in cases])};\n"
        "console.log(JSON.stringify("
        "cases.map(([status, detail]) => wantsTheResponsesApi(status, detail))));"
    )
    assert len(browser) == len(cases)
    for (name, status, detail, expected), in_browser in zip(cases, browser, strict=True):
        in_python = _wants_the_responses_api(Vendor(status, detail))
        assert in_python is expected, f"the Python predicate gets {name} wrong"
        assert in_browser is expected, f"the browser predicate gets {name} wrong"


def test_the_browser_and_python_start_the_same_models_on_the_same_api() -> None:
    """The other half of the rule above, and the half that costs nothing to get right.

    Reading the 400 is the fallback. Where the catalogue already records that a model will not
    take function tools on Chat Completions, both clients open on Responses and neither spends a
    call finding out. Run in the browser rather than read, so a `startingApi` that quietly stopped
    consulting the catalogue would fail here instead of costing every visitor a wasted call.
    """
    from quackd.agent.providers.catalogue import models_for
    from quackd.agent.providers.openai import OpenAIProvider

    ids = [m.id for m in models_for("openai")]
    browser = _drive_providers(
        """
const { startingApi } = await import(MODULE);
const ids = IDS;
console.log(JSON.stringify(ids.map((id) => startingApi("openai", id))));
""".replace("IDS", json.dumps(ids))
    )

    class Stub:
        def __init__(self) -> None:
            self.chat = self.responses = None

    for model_id, in_browser in zip(ids, browser, strict=True):
        in_python = OpenAIProvider(model=model_id, client=Stub()).api
        assert in_browser == in_python, (
            f"{model_id} starts on {in_browser} in the page and {in_python} in the CLI"
        )
    assert "responses" in browser, "no model routes to Responses, so this test proves nothing"


def test_the_browser_asks_each_vendor_for_a_tool_call_the_way_python_does() -> None:
    """`tool_choice` is not one word shared by eleven vendors.

    Mistral's guide documents `any`; Cohere's compatibility endpoint documents no such
    parameter at all and the field is omitted; Z.ai supports only `auto`. The
    page and the CLI each hold a copy of that table, and a copy that drifts is a vendor that
    starts refusing tool calls in one of the two places only.
    """
    import importlib

    from quackd.agent.providers.factory import OPENAI_COMPATIBLE

    offered = _drive_providers(
        """
const { PROVIDERS } = await import(MODULE);
console.log(JSON.stringify(Object.fromEntries(
  Object.entries(PROVIDERS).map(([key, spec]) => [key, spec.toolChoice ?? null]))));
"""
    )
    assert offered.get("openai") == "required", "OpenAI's own setting is the one that must not move"
    for name, in_browser in offered.items():
        if name not in OPENAI_COMPATIBLE:
            continue
        module = importlib.import_module(f"quackd.agent.providers.{name}")
        in_python = getattr(module, OPENAI_COMPATIBLE[name]).default_tool_choice
        assert in_browser == in_python, (
            f"the page asks {name} with tool_choice={in_browser!r} and the CLI with {in_python!r}"
        )


# ── the two copies of upstream's contract stay in step ──────────────────────────────────


def test_the_browser_pins_the_same_upstream_commits_python_does() -> None:
    """`docs/concepts/architecture.md` calls keeping this copy in step "the standing cost of it
    existing". This is that cost, paid once."""
    js = _js("microduck.js")
    for label, pin in (("microduck_rl", up.PIN), ("microduck-policies", up.POLICIES_PIN)):
        assert pin in js, (
            f"web/src/microduck.js does not pin {label} at {pin}, which is what "
            f"quackd/sim3d/upstream_api.py fetches. Paste it."
        )


def test_the_browser_uses_the_same_gait_numbers_python_does() -> None:
    from quackd_microduck.sim3d import gait

    js = _js("microduck.js")
    floor = re.search(r"GAIT_FLOOR\s*=\s*\{([^}]*)\}", js)
    assert floor, "web/src/microduck.js no longer declares GAIT_FLOOR"
    # compared as numbers, not as their spelling: 0.30 and 0.3 are the same floor
    got = {k: float(v) for k, v in re.findall(r"(\w+):\s*([\d.]+)", floor.group(1))}
    want = {"vx": gait.GAIT_FLOOR_VX, "vy": gait.GAIT_FLOOR_VY, "wz": gait.GAIT_FLOOR_WZ}
    assert got == want, (
        f"the browser walks on {got}, and quackd/sim3d/gait.py sends the real duck {want}"
    )
    achieved = re.search(r"ACHIEVED_FRACTION\s*=\s*([\d.]+)", js)
    assert achieved and float(achieved.group(1)) == gait.ACHIEVED_FRACTION


# ── the arena is quackd's, and still the arena the verbs aim at ─────────────────────────


def test_nobody_is_in_the_browser_arena_either() -> None:
    """The person marker is gone from both 3D worlds, and this is the browser's half.

    It was a plinth, a post and a head in the brand's purple, and `observe()` published a
    `person` label the example chip aimed at. All of it went with the Python cylinder, so what
    is checked here is absence in the three places a half-removal would survive: the arena XML,
    the observation, and the page's own copy. The 2D cartoon still has a person and is not
    this test's business.

    The copy matters as much as the code. A chip that asks a model to walk to somebody who is
    not there is a demo that fails in front of whoever clicked it.
    """
    js = _js("microduck.js")
    # Comments first: the module explains at some length who is NOT in the arena, and prose
    # about an absence must not read as the absence itself failing.
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    assert "person" not in code.lower(), (
        "microduck.js still has a person in its code; nobody is in this arena"
    )
    assert '<body name="person"' not in js, "the arena XML stands a person up again"
    assert 'label: "person"' not in js, "observe() publishes a `person` label again"
    page = (WEB / "index.html").read_text(encoding="utf-8")
    assert "person" not in page.lower(), "the page still promises a person in the arena"
    prompt = _js("pilot.js")
    assert "person marker" not in prompt, "the system prompt still describes a person marker"


def test_the_demo_claims_no_more_policies_than_it_downloads() -> None:
    """Two ONNX files load: one stands the duck up and one walks it. The kick is quackd's own
    scripted impulse, as the comment above it says, and copy promising a shelf of learned
    skills is a claim the download does not support."""
    # what is fetched, not what is named: the comment above `kick` names a third file,
    # `ball_kick_left.onnx`, precisely to say that it did nothing and is not used
    policies = set(re.findall(r"POLICIES\}/(\w+)\.onnx", _js("microduck.js")))
    assert policies == {"alpha_walking", "alpha_stand"}, (
        f"web/src/microduck.js loads {sorted(policies)}; the page and web/README.md say two"
    )
    readme = (WEB / "README.md").read_text(encoding="utf-8")
    for text, where in ((HTML, "index.html"), (readme, "web/README.md")):
        assert not re.search(r"nine[^.]{0,40}polic", text, re.I), (
            f"{where} promises nine policies, and this demo fetches {len(policies)}"
        )


# ── it parses ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_each_module_parses_as_javascript(module: Path) -> None:
    """The only thing here that would catch a typo. `node --check` alone parses as CommonJS
    and rejects `import`, so the source goes in on stdin as a module."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine; the GitHub runners all have one")
    done = subprocess.run(
        [node, "--input-type=module", "--check"],
        input=module.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
        encoding="utf-8",  # the sources carry em-dashes and the Windows locale is not utf-8
    )
    assert done.returncode == 0, f"{module.name} does not parse:\n{done.stderr}"


def test_the_page_loads_every_module_it_ships() -> None:
    """A module nothing imports is dead weight nobody will notice has rotted."""
    entry = re.search(rf'<script[^>]*src="{MOUNT}/(src/[\w.]+)"', HTML)
    assert entry, "index.html loads no module"
    reachable = {entry.group(1).split("/")[-1]}
    frontier = list(reachable)
    while frontier:
        current = frontier.pop()
        for name in re.findall(r'import[^;]*from\s+"\./([\w.]+)"', _js(current)):
            if name not in reachable:
                reachable.add(name)
                frontier.append(name)
        for name in re.findall(r'await import\("\./([\w.]+)"\)', _js(current)):
            if name not in reachable:
                reachable.add(name)
                frontier.append(name)
    orphans = sorted({p.name for p in MODULES} - reachable)
    assert not orphans, f"web/src holds modules the page never loads: {orphans}"


def test_the_deploy_config_serves_this_directory_and_builds_nothing() -> None:
    """`web/` has no build step on purpose: every dependency is a CDN URL fetched at run time,
    so the deploy is a copy. A host that guessed a framework from the Python at the repo root
    would try to build quackd instead."""
    config = json.loads((REPO / "vercel.json").read_text(encoding="utf-8"))
    assert config["outputDirectory"] == "web"
    assert config["framework"] is None, "no framework: this is a folder, not an app to build"
    for step in ("buildCommand", "installCommand"):
        assert config.get(step), f"{step} must be stubbed out, or the host builds the repo root"


def test_the_demo_says_what_it_stands_in_for() -> None:
    """The browser fakes perception geometrically rather than running Python's colour detector
    on a rendered frame, and it hashes nothing. Both are fine, undisclosed is not."""
    readme = (WEB / "README.md").read_text(encoding="utf-8")
    assert "geometric" in readme.lower() or "ground truth" in readme.lower(), (
        "web/README.md should say that perception here is geometric, not the colour detector "
        "Python runs on a rendered head-camera frame"
    )


def test_the_upstream_licence_is_named_on_the_page_that_downloads_it() -> None:
    """The visitor's browser fetches CC BY-NC-SA meshes. The page has to say so."""
    assert "BY-SA-NC" in HTML or "BY-NC-SA" in HTML


def test_the_browser_refuses_the_arguments_python_refuses() -> None:
    """The check that started the habit of running this code rather than reading it.

    The verb schemas were sent to the vendor and never enforced locally, so a model that
    ignored one got what it asked for: `duration_s: 1e6` became ten million awaited slices and
    hung the tab, and `duration_s: "soon"` produced NaN, ran no loop and reported success.
    Python rejects both with pydantic before the verb runs.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine; the GitHub runners all have one")
    script = f"""
    import {{ checkParams, VERBS }} from {json.dumps((SRC / "pilot.js").as_uri())};
    const out = {{}};
    out.huge = checkParams(VERBS.move.params, {{ duration_s: 1e6 }});
    out.notANumber = checkParams(VERBS.move.params, {{ duration_s: "soon" }});
    out.outOfRange = checkParams(VERBS.move.params, {{ vx: 99, duration_s: 1 }});
    out.unknown = checkParams(VERBS.move.params, {{ nope: 1 }});
    out.fine = checkParams(VERBS.move.params, {{ duration_s: 2, vx: 0.2 }});
    out.empty = checkParams(VERBS.move.params, {{}});
    out.longSay = checkParams(VERBS.say.params, {{ text: "x".repeat(500) }});
    out.badLeg = checkParams(VERBS.kick.params, {{ leg: "middle" }});
    console.log(JSON.stringify(out));
    """
    done = subprocess.run(
        [node, "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout)
    assert "at most 10" in got["huge"]
    assert "finite number" in got["notANumber"]
    assert "at most 0.3" in got["outOfRange"]
    assert "nope" in got["unknown"]
    assert "at most 200" in got["longSay"]
    assert "left, right" in got["badLeg"]
    assert got["fine"] is None
    assert got["empty"] is None, "Python's MoveParams requires nothing, so neither may this"


# ── the two copies of the model list stay in step ───────────────────────────────────────


def test_the_browser_offers_the_same_models_python_does() -> None:
    """The generated half of the catalogue, checked the way the upstream pins are.

    `quackd/agent/providers/catalogue.py` is the single source of truth for model names and the
    browser cannot import it, so `web/build_catalogue.py` writes the copy the page reads. A
    generated file that nothing re-generates is just a stale file with a comment on it, so the
    generator is run here and its output compared against what is committed.

    Loaded by path rather than imported: `web/` is not a package, and this keeps the script a
    script, runnable as `python web/build_catalogue.py` the way its docstring promises.
    """
    import importlib.util

    script = WEB / "build_catalogue.py"
    spec = importlib.util.spec_from_file_location("build_catalogue", script)
    assert spec and spec.loader, f"{script} could not be loaded as a module"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # The committed file is read with its newlines normalised: git may check it out with CRLF
    # on Windows, and `render()` always produces LF. The difference that matters is the models.
    committed = (SRC / "catalogue.js").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert module.render() == committed, (
        "web/src/catalogue.js is not what web/build_catalogue.py produces, so the page offers a "
        "different model list from the CLI. Regenerate it and commit the result:\n\n"
        "    python web/build_catalogue.py\n"
    )


def test_every_vendor_the_page_cannot_offer_is_named_in_its_README() -> None:
    """A vendor may be missing from the dropdown, and may not be missing quietly.

    The page calls a vendor from the visitor's browser, so one that refuses a cross-origin
    preflight cannot be offered here however well it works under `quackd run`. GLM is that
    vendor today: `api.z.ai` answers the preflight with no `Access-Control-Allow-*` header at
    all. That is a fact about a third party on a particular day, not a decision this repository
    made, so what is enforced is not *which* vendors are absent but that each absence is
    written down with its reason. Otherwise the next one drops out in a diff nobody reads.
    """
    from quackd.agent.providers.catalogue import CLOUD_NAMES

    both = _drive_providers(
        """
const { PROVIDERS, NOT_FROM_A_BROWSER } = await import(MODULE);
console.log(JSON.stringify({ offered: Object.keys(PROVIDERS), excused: NOT_FROM_A_BROWSER }));
"""
    )
    offered = {name for name in both["offered"] if name in CLOUD_NAMES}
    assert "openai" in offered, "PROVIDERS came back empty, so this test is vacuous"
    absent = set(CLOUD_NAMES) - offered
    excused = both["excused"]
    # Matching the map to the gap is what makes this falsifiable. Searching the README for the
    # vendor's name would not: every catalogue name is already somewhere in that file, including
    # the ones the page does offer, so the check would pass whatever was deleted from PROVIDERS.
    assert set(excused) == absent, (
        f"web/src/providers.js offers {sorted(offered)} of {len(CLOUD_NAMES)} catalogue vendors "
        f"and excuses {sorted(excused)}. A vendor the page drops needs a line in "
        f"NOT_FROM_A_BROWSER saying why, and one it gains needs that line removed"
    )
    readme = (WEB / "README.md").read_text(encoding="utf-8").lower()
    for vendor, reason in excused.items():
        assert reason.strip(), f"{vendor} is excused with an empty reason"
        assert re.search(r"\d{4}-\d{2}-\d{2}", reason), (
            f"{vendor}'s reason has no date, and a third party's CORS policy is only true on a day"
        )
        assert vendor in readme, (
            f"web/README.md never mentions {vendor}, which the page cannot call"
        )


def test_the_dropdown_can_show_every_model_the_catalogue_holds() -> None:
    """`fillModels` builds one `<optgroup>` per status in STATUS_ORDER and fills it by filtering
    on that status, so a model whose status is not in STATUS_ORDER is in the data, is offered by
    the CLI, and is silently missing from the page. Nothing else would notice."""
    catalogue = _js("catalogue.js")
    order = re.search(r"STATUS_ORDER = \[([^\]]*)\]", catalogue)
    assert order, "web/src/catalogue.js no longer exports STATUS_ORDER"
    known = set(re.findall(r'"(\w+)"', order.group(1)))
    used = set(re.findall(r'"status": "(\w+)"', catalogue))
    assert used, "no model in web/src/catalogue.js carries a status, so this test is vacuous"
    assert used <= known, (
        f"web/src/catalogue.js holds models whose status STATUS_ORDER does not list: "
        f"{sorted(used - known)}. fillModels() groups by that order, so those models would "
        f"never appear in the dropdown"
    )
