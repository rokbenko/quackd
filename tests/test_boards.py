"""Which computer `quackd onboard` runs on: a Jetson finds itself, and sim is only ever asked for.

The boards here are files, as in `tests/jetson_fixtures.py`, and what is tested is the reading,
never a board. The Orin Nano is that file's own. The Thor, the Xavier and the Orin on JetPack 5
are written to its shape and read off no board, since nobody on this project has one: detection
keys on the chip in `compatible` and on the release line, and those are what they vary.
"""

from __future__ import annotations

import importlib.metadata
import platform
import re
import sys
import types
from collections.abc import Mapping
from typing import Any

import pytest

from quackd import boards, doctor
from quackd.boards import jetson, l4t, sim
from quackd.boards.jetson import JetsonBoard
from quackd.boards.sim import SimBoard
from tests.jetson_fixtures import COMPATIBLE, NUL, ORIN_NANO, RELEASE_36_4_3

PREFIX = (
    "quackd onboard runs on a robot's own computer, and this is not one it supports: "
    "it found no NVIDIA Jetson ("
)
SUFFIX = (
    "). To try it here as a simulation, which its passport will say, "
    "run quackd onboard --board sim."
)
"""The plan's sentence, around what was looked for, which the docs are to quote."""


def _release(major: int, revision: str) -> str:
    """The fixture's release line with another release in it, its shape kept."""
    return RELEASE_36_4_3.replace("R36 ", f"R{major} ").replace(
        "REVISION: 4.3", f"REVISION: {revision}"
    )


def _files(*, model: str | None, compatible: str | None, release: str | None) -> dict[str, str]:
    """A board's three files, NUL-terminated where the device tree's are."""
    files = {}
    if model is not None:
        files[jetson.MODEL] = model + NUL
    if compatible is not None:
        files[jetson.COMPATIBLE] = compatible + NUL
    if release is not None:
        files[jetson.RELEASE] = release
    return files


THOR_MODEL = "NVIDIA Jetson AGX Thor Developer Kit"
ORIN = _files(model=ORIN_NANO, compatible=COMPATIBLE, release=RELEASE_36_4_3)
THOR = _files(
    model=THOR_MODEL,
    # the board's own two entries are made up; the chip's is the one detection reads
    compatible=NUL.join(("nvidia,p3971-0000+p3834-0000", "nvidia,p3834-0000", "nvidia,tegra264")),
    release=_release(38, "2.0"),
)
XAVIER = _files(
    model="Jetson-AGX",
    compatible=NUL.join(("nvidia,p2972-0000", "nvidia,tegra194")),
    release=_release(35, "4.1"),
)
ORIN_ON_JETPACK_5 = _files(model=ORIN_NANO, compatible=COMPATIBLE, release=_release(35, "4.1"))
THOR_ON_JETPACK_7_2 = {**THOR, jetson.RELEASE: _release(39, "2.0")}
ORIN_ON_JETPACK_7_2 = {**ORIN, jetson.RELEASE: _release(39, "2.0")}
THOR_READING_AS_JETPACK_6 = {**THOR, jetson.RELEASE: RELEASE_36_4_3}
"""A Thor whose release file says JetPack 6, which no Thor runs: a container's, privileged."""
LOOKALIKE = {jetson.COMPATIBLE: "acme,nvidia,tegra234-shim" + NUL}
"""A compatible entry with a Tegra's name inside it, which is not a Tegra."""
RASPBERRY_PI = {
    jetson.MODEL: "Raspberry Pi 5 Model B Rev 1.0" + NUL,
    jetson.COMPATIBLE: NUL.join(("raspberrypi,5-model-b", "brcm,bcm2712")) + NUL,
}


def _chip(files: Mapping[str, str]) -> str:
    return files[jetson.COMPATIBLE].rstrip(NUL).split(NUL)[-1].removeprefix("nvidia,")


def _jetson(
    files: Mapping[str, str], folders: Mapping[str, list[str]] | None = None
) -> JetsonBoard:
    return JetsonBoard(read=files.get, listdir=(folders or {}).get)


def _among(*extra: Any, files: Mapping[str, str]) -> dict[str, Any]:
    """quackd's own two boards, the Jetson reading `files`, and any others after them."""
    return {"jetson": _jetson(files), boards.SIM: SimBoard(), **{b.name: b for b in extra}}


# ── a Jetson finds itself ───────────────────────────────────────────────────────────────


def test_an_orin_on_jetpack_6_finds_itself() -> None:
    board, found = boards.detect(among=_among(files=ORIN))
    release = l4t.parse_l4t(RELEASE_36_4_3)
    assert board.name == "jetson" and release is not None
    assert found == boards.BoardFacts(
        kind="jetson",
        model=ORIN_NANO,
        soc=_chip(ORIN),
        release=f"L4T {release} (JetPack {l4t.jetpack_for(release)})",
    )
    assert found.release == "L4T 36.4.3 (JetPack 6.2)", "the design's own example"


@pytest.mark.parametrize("files", [THOR, THOR_ON_JETPACK_7_2], ids=["l4t-38", "l4t-39"])
def test_a_thor_on_jetpack_7_finds_itself(files: Mapping[str, str]) -> None:
    board, found = boards.detect(among=_among(files=files))
    release = l4t.parse_l4t(files[jetson.RELEASE])
    assert board.name == "jetson" and release is not None
    assert (found.kind, found.model, found.soc) == ("jetson", THOR_MODEL, _chip(THOR))
    assert found.release == f"L4T {release} (JetPack {l4t.jetpack_for(release)})"
    assert (l4t.jetpack_for(release) or "").startswith("7")


def test_a_xavier_is_refused_for_its_chip_in_the_plans_words() -> None:
    with pytest.raises(boards.Unsupported) as caught:
        boards.detect(among=_among(files=XAVIER))
    assert str(caught.value) == (
        "a Xavier (tegra194): quackd onboard supports Orin with JetPack 6 and Thor with JetPack 7"
    )


def test_an_orin_still_on_jetpack_5_is_refused_for_its_release() -> None:
    with pytest.raises(boards.Unsupported) as caught:
        _jetson(ORIN_ON_JETPACK_5).detect()
    release = l4t.parse_l4t(ORIN_ON_JETPACK_5[jetson.RELEASE])
    assert release is not None
    said = str(caught.value)
    assert said == (
        f"an Orin ({_chip(ORIN)}) on L4T {release} (JetPack {l4t.jetpack_for(release)}): "
        f"{jetson.SUPPORTS}"
    )
    assert "JetPack 5" in said


@pytest.mark.parametrize(
    "files", [ORIN_ON_JETPACK_7_2, THOR_READING_AS_JETPACK_6], ids=["orin-on-7", "thor-on-6"]
)
def test_a_release_is_taken_only_on_the_chip_it_is_supported_on(files: Mapping[str, str]) -> None:
    """Orin with JetPack 6 and Thor with JetPack 7, as the sentence says, and not the other two
    pairs: NVIDIA's JetPack 7.2 runs on Orin, which quackd onboard does not take, and a Thor that
    reads as JetPack 6 is reading a container's release file."""
    release = l4t.parse_l4t(files[jetson.RELEASE])
    with pytest.raises(boards.Unsupported, match=f"on L4T {release}") as caught:
        _jetson(files).detect()
    assert str(caught.value).endswith(jetson.SUPPORTS)


@pytest.mark.parametrize(
    ("files", "said"),
    [
        ({jetson.RELEASE: RELEASE_36_4_3}, "names no NVIDIA Tegra chip"),
        ({jetson.COMPATIBLE: COMPATIBLE + NUL}, f"with no {jetson.RELEASE}"),
        (
            {jetson.COMPATIBLE: COMPATIBLE + NUL, jetson.RELEASE: "something nobody has seen"},
            "names no L4T release",
        ),
        (_files(model=ORIN_NANO, compatible=COMPATIBLE, release=_release(40, "1")), "on L4T 40.1:"),
        (
            _files(model="x", compatible="nvidia,tegra239", release=RELEASE_36_4_3),
            "a Tegra quackd does not know (tegra239)",
        ),
    ],
    ids=["no-chip", "no-release", "unreadable-release", "unknown-l4t", "unknown-chip"],
)
def test_a_jetson_quackd_cannot_read_or_run_on_is_refused_saying_why(
    files: Mapping[str, str], said: str
) -> None:
    """Half a Jetson is a Jetson something hides (a container masks the device tree), and is
    refused rather than taken for a laptop; a major release or a chip quackd has not met is
    refused rather than guessed at."""
    with pytest.raises(boards.Unsupported, match=re.escape(said)):
        _jetson(files).detect()


def test_a_jetson_whose_model_cannot_be_read_is_named_by_its_chip() -> None:
    found = _jetson({k: v for k, v in ORIN.items() if k != jetson.MODEL}).detect()
    assert found is not None and found.model == f"NVIDIA Jetson {jetson.CHIPS[_chip(ORIN)]}"


def test_the_supported_pairs_are_orin_with_jetpack_6_and_thor_with_jetpack_7() -> None:
    taken = {
        jetson.CHIPS[chip]: {l4t.JETPACK_MAJOR[major] for major in majors}
        for chip, majors in jetson.SUPPORTED.items()
    }
    assert taken == {"Orin": {"6.x"}, "Thor": {"7.x"}}


# ── no Jetson, and sim ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("system", "files"),
    [("Linux", {}), ("Linux", RASPBERRY_PI), ("Linux", LOOKALIKE), ("Windows", {}), ("Darwin", {})],
    ids=["linux", "raspberry-pi", "lookalike", "windows", "macos"],
)
def test_a_computer_with_no_jetson_is_not_a_robot_in_the_plans_sentence(
    monkeypatch: pytest.MonkeyPatch, system: str, files: Mapping[str, str]
) -> None:
    monkeypatch.setattr(platform, "system", lambda: system)
    for named in (None, "jetson"):  # found by itself, or asked for with --board jetson
        with pytest.raises(boards.NotARobot) as caught:
            boards.detect(named, among=_among(files=files))
        said = str(caught.value)
        assert said.startswith(PREFIX) and said.endswith(SUFFIX), said
        looked = said[len(PREFIX) : -len(SUFFIX)]
        if system == "Linux":
            assert jetson.COMPATIBLE in looked and jetson.RELEASE in looked, looked
        else:
            assert looked == f"this computer runs {'macOS' if system == 'Darwin' else system}"


def test_sim_is_never_detected_and_answers_when_named() -> None:
    assert boards.boards()[boards.SIM].explicit_only
    with pytest.raises(boards.NotARobot):
        boards.detect(among={boards.SIM: SimBoard()})
    board, found = boards.detect(boards.SIM)
    assert (board.name, board.radio_kind) == (boards.SIM, "sim")
    assert found.passport() == {"kind": "sim", "model": "a computer standing in for a robot's own"}
    assert found.model == sim.MODEL and board.radio().ok


def test_detecting_this_computer_never_comes_back_as_sim() -> None:
    """The real files, through the default path: whatever this machine is, it is never sim."""
    try:
        board, _ = boards.detect()
    except (boards.NotARobot, boards.Unsupported):
        return
    assert board.name != boards.SIM


# ── plugins ─────────────────────────────────────────────────────────────────────────────


class _Acme:
    """A plugin's board that knows every computer it is asked about."""

    name = "acme"
    explicit_only = False
    radio_kind = "bluez"

    def detect(self) -> boards.BoardFacts | None:
        return boards.BoardFacts(kind="other", model="Acme robot computer")

    def radio(self) -> boards.RadioCheck:
        return boards.RadioCheck(ok=True, adapter="hci0")


class _Zeta(_Acme):
    """A plugin's board that knows no computer."""

    name = "zeta"

    def detect(self) -> boards.BoardFacts | None:
        return None


class _Hidden(_Zeta):
    """A plugin's board that is only ever asked for by name, as sim is."""

    name = "hidden"
    explicit_only = True


class _Crashing(_Acme):
    """A plugin's board that fails whenever it looks."""

    def detect(self) -> boards.BoardFacts | None:
        raise PermissionError("no access to the bus")


class _Impostor(_Acme):
    """A plugin's board that says it is a Jetson, which only quackd's own may."""

    def detect(self) -> boards.BoardFacts | None:
        return boards.BoardFacts(kind="jetson", model="NVIDIA Jetson AGX Thor", soc="tegra264")


class _Sloppy(_Acme):
    """A plugin's board that answers with something that is not a `BoardFacts`."""

    def detect(self) -> Any:
        return {"kind": "other", "model": "a dict"}


class _Unnamable(_Acme):
    """A plugin's board whose name cannot even be asked."""

    @property
    def name(self) -> str:  # type: ignore[override]
        raise RuntimeError("no name yet")


def _installed(monkeypatch: pytest.MonkeyPatch, plugins: Mapping[str, object]) -> None:
    """Plugins in the `quackd.boards` group: each name to a module whose `make()` returns the
    value given, or raises it."""
    points = []
    for i, (name, made) in enumerate(plugins.items()):
        module = types.ModuleType(f"quackd_test_board_plugin_{i}")

        def make(made: object = made) -> object:
            if isinstance(made, BaseException):
                raise made
            return made

        module.make = make  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, module.__name__, module)
        points.append(
            importlib.metadata.EntryPoint(
                name=name, value=module.__name__, group=boards.ENTRY_POINT_GROUP
            )
        )
    real = importlib.metadata.entry_points

    def entry_points(**kwargs: Any) -> Any:
        return points if kwargs.get("group") == boards.ENTRY_POINT_GROUP else real(**kwargs)

    monkeypatch.setattr(importlib.metadata, "entry_points", entry_points)


def test_a_plugin_cannot_take_jetson_or_sim(monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch, {"jetson": _Acme(), "SIM": _Acme(), "acme": _Acme()})
    found = boards.boards()
    assert list(found) == [*boards.OFFICIAL, boards.SIM, "acme"]
    assert isinstance(found["jetson"], JetsonBoard) and isinstance(found[boards.SIM], SimBoard)
    assert isinstance(found["acme"], _Acme)
    assert isinstance(boards.named(" Jetson "), JetsonBoard)


def test_a_plugin_that_fails_to_load_is_named_and_breaks_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _installed(monkeypatch, {"broken": RuntimeError("no such bus"), "junk": object()})
    assert list(boards.boards()) == [*boards.OFFICIAL, boards.SIM]
    with pytest.raises(boards.UnknownBoard, match="no such bus"):
        boards.named("broken")
    with pytest.raises(boards.UnknownBoard, match="not a board"):
        boards.named("junk")
    with pytest.raises(boards.UnknownBoard, match="--board takes jetson, sim"):
        boards.named("nothing")


def test_a_plugin_is_what_it_is_installed_as_or_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A board that calls itself by another name than its entry point, or one whose name cannot
    be asked, is not installed, and says why when it is asked for."""
    _installed(monkeypatch, {"acme": _Zeta(), "ghost": _Unnamable()})
    assert list(boards.boards()) == [*boards.OFFICIAL, boards.SIM]
    with pytest.raises(boards.UnknownBoard, match="calls itself 'zeta'"):
        boards.named("acme")
    with pytest.raises(boards.UnknownBoard, match="no name yet"):
        boards.named("ghost")


@pytest.mark.parametrize(
    "plugin", [_Crashing(), _Impostor(), _Sloppy()], ids=["crashing", "impostor", "sloppy"]
)
def test_a_plugin_that_fails_or_answers_as_only_quackds_own_may_takes_no_sentence_away(
    plugin: Any,
) -> None:
    """Found by itself, it is a board that does not know this computer, so a laptop still gets
    the sentence and a Xavier still gets its refusal; asked for by name, its failure is said."""
    with pytest.raises(boards.NotARobot) as caught:
        boards.detect(among=_among(plugin, files={}))
    assert str(caught.value).startswith(PREFIX) and str(caught.value).endswith(SUFFIX)
    with pytest.raises(boards.Unsupported, match="Xavier"):
        boards.detect(among=_among(plugin, files=XAVIER))
    with pytest.raises(boards.Unsupported, match=f"the {plugin.name} board"):
        boards.detect(plugin.name, among=_among(plugin, files={}))


def test_a_plugin_may_run_where_quackd_refuses_and_the_refusal_stands_without_one() -> None:
    board, found = boards.detect(among=_among(_Acme(), files=XAVIER))
    assert board.name == "acme" and found.kind == "other"
    with pytest.raises(boards.Unsupported, match="Xavier"):
        boards.detect(among=_among(_Zeta(), files=XAVIER))


def test_a_plugin_that_knows_no_computer_is_named_in_the_sentence() -> None:
    with pytest.raises(boards.NotARobot) as caught:
        boards.detect(among=_among(_Zeta(), files={}))
    said = str(caught.value)
    assert said.startswith(PREFIX) and said.endswith(SUFFIX) and "zeta" in said
    with pytest.raises(boards.NotARobot, match="it found no zeta board here"):
        boards.detect("zeta", among=_among(_Zeta(), files={}))
    with pytest.raises(boards.NotARobot) as hidden:
        boards.detect(among=_among(_Hidden(), files={}))
    assert "hidden" not in str(hidden.value), "a board only asked for by name was never asked"


# ── what a board gives the passport, and its radio ──────────────────────────────────────


@pytest.mark.parametrize("files", [ORIN, THOR], ids=["orin", "thor"])
def test_a_passport_carries_kind_model_and_chip_and_never_the_release(
    files: Mapping[str, str],
) -> None:
    found = _jetson(files).detect()
    assert found is not None and found.release
    assert found.passport() == {"kind": found.kind, "model": found.model, "soc": found.soc}
    assert "release" not in SimBoard().detect().passport()


def test_a_passport_cuts_a_long_model_and_has_no_kind_it_lacks_a_word_for() -> None:
    long = boards.BoardFacts(
        kind="other", model="m" * (2 * boards.MODEL_LIMIT), soc="s" * (2 * boards.SOC_LIMIT)
    )
    cut = long.passport()
    assert (len(cut["model"]), len(cut["soc"])) == (boards.MODEL_LIMIT, boards.SOC_LIMIT)
    words = "NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super"
    assert len(words) > boards.MODEL_LIMIT
    at_a_word = boards.BoardFacts(kind="jetson", model=words).passport()["model"]
    assert len(at_a_word) <= boards.MODEL_LIMIT and words.startswith(at_a_word + " ")
    with pytest.raises(ValueError, match="kind"):
        boards.BoardFacts(kind="raspberry-pi", model="Raspberry Pi 5")
    with pytest.raises(ValueError, match="model"):
        boards.BoardFacts(kind="other", model="  ")


def test_the_radio_is_the_first_hci_adapter_and_none_is_said_plainly() -> None:
    check = _jetson(ORIN, {jetson.BLUETOOTH: ["hci1", "hci0"]}).radio()
    assert check == boards.RadioCheck(ok=True, adapter="hci0")
    nothing: list[Mapping[str, list[str]]] = [{jetson.BLUETOOTH: []}, {}, {jetson.BLUETOOTH: ["x"]}]
    for folders in nothing:
        none = _jetson(ORIN, folders).radio()
        assert not none.ok and none.adapter is None and jetson.BLUETOOTH in (none.why or "")
    assert (JetsonBoard.radio_kind, SimBoard.radio_kind) == ("bluez", "sim")


def test_doctor_reads_the_moved_l4t_table_by_its_old_names() -> None:
    """tests/test_docs.py, test_doctor.py and test_host_client.py read these off doctor."""
    assert doctor._JETPACK_FOR_L4T is l4t.JETPACK_FOR_L4T
    assert doctor._parse_l4t is l4t.parse_l4t and doctor._jetpack_for is l4t.jetpack_for


def test_a_boards_kinds_are_the_kinds_a_passport_may_say() -> None:
    """Two lists of the same three words, one where a board is found and one where a passport is
    read; they are one list in two places, and this keeps them so."""
    from quackd.boards import KINDS
    from quackd.passport.document import BOARDS

    assert KINDS == BOARDS
