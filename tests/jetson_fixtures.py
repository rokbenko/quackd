"""A Jetson made of files, shared by every test that reads one.

Two readers need the same board: the host daemon's `/board` (`bridge/jetson/`), which ships the
files raw, and `quackd doctor`, which parses what the daemon ships. Building the board once, here,
means the two cannot drift into agreeing with two different boards.

The thing tested is always the reading and never the hardware. The one fact no fixture can
supply is whether a real Orin's files look like these; nobody on this project has one, and
`docs/guides/jetson.md` says what to send back.
"""

from __future__ import annotations

from pathlib import Path

NUL = chr(0)
TAB = chr(9)
NL = chr(10)

ORIN_NANO = "NVIDIA Jetson Orin Nano Developer Kit"
COMPATIBLE = NUL.join(("nvidia,p3768-0000+p3767-0005", "nvidia,p3767-0005", "nvidia,tegra234"))
RELEASE_36_4_3 = (
    "# R36 (release), REVISION: 4.3, GCID: 38968081, BOARD: generic, EABI: aarch64, "
    "DATE: Wed Jan  8 01:51:37 UTC 2025"
)
MEMINFO = "MemTotal:        7650336 kB\nMemAvailable:    5123456 kB\nSwapTotal:       1017852 kB\n"


def swap_line(*fields: str) -> str:
    return TAB.join(fields) + NL


SWAPS_HEADER = swap_line("Filename", "", "", "", "Type", "", "Size", "", "Used", "", "Priority")
ZRAM_SWAPS = SWAPS_HEADER + swap_line("/dev/zram0", "partition", "1017852", "0", "5")
NVME_SWAPS = SWAPS_HEADER + swap_line("/ssd/16GB.swap", "file", "16777212", "0", "-2")
NO_SWAPS = SWAPS_HEADER

NVPMODEL_Q = "NV Power Mode: 15W\n0\n"
"""What `nvpmodel -q` prints: the mode's name on a labelled line, then its number."""

TEGRASTATS_LINE = (
    "09-24-2026 10:15:32 RAM 2467/7471MB (lfb 2x4MB) SWAP 0/994MB (cached 0MB) "
    "CPU [2%@729,1%@729,0%@729,0%@729,1%@729,0%@729] EMC_FREQ 0%@2133 GR3D_FREQ 0%@[305] "
    "NVDEC off NVJPG off NVJPG1 off VIC off OFA off APE 200 cpu@47.5C soc2@46.8C soc0@46.4C "
    "gpu@46.2C tj@47.5C soc1@46.6C VDD_IN 4462mW/4462mW VDD_CPU_GPU_CV 480mW/480mW "
    "VDD_SOC 1400mW/1400mW"
)
"""One line of `tegrastats --interval 500` on an Orin Nano under JetPack 6, idle GPU.

Written to the shape of an Orin's line from memory of published ones, not captured from a
board, because this project has none. Its numbers agree with `MEMINFO` and
`ZRAM_SWAPS` (7650336 kB is 7471 MB, 1017852 kB is 994 MB), so a test reading both sources
cannot pass by reading the wrong one. `GR3D_FREQ 0%` is the GPU's load."""


def write(root: Path, rel: str, text: str) -> None:
    """Bytes exactly, never `write_text`: on Windows that turns every newline into CRLF, and a
    board's files are LF. doctor's parsers split lines and never noticed; the host daemon
    ships the bytes as they are, and a test comparing them would have."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def tegra_tree(
    root: Path,
    *,
    release: str | None = RELEASE_36_4_3,
    swaps: str = ZRAM_SWAPS,
    gpu_node: bool = True,
    device_tree: bool = True,
) -> Path:
    """A board, as files. The NUL terminators are real: `/proc/device-tree/*` are the device
    tree's own bytes, and the first version of doctor's reader put one in a Rich cell."""
    if device_tree:
        write(root, "proc/device-tree/model", ORIN_NANO + NUL)
        write(root, "proc/device-tree/compatible", COMPATIBLE + NUL)
    if release is not None:
        write(root, "etc/nv_tegra_release", release)
    write(root, "proc/meminfo", MEMINFO)
    write(root, "proc/swaps", swaps)
    if gpu_node:
        (root / "dev/nvgpu/igpu0").mkdir(parents=True)
    return root
