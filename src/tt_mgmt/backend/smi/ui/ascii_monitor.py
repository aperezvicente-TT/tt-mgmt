# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
# SPDX-License-Identifier: Apache-2.0

"""rocm-smi / nvidia-smi style ASCII monitor for TT devices.

One row per device, one fact per column. No Rich, no color, no Unicode.
In watch mode, supports tabbed views switched with number keys:
    1 Overview   2 Telemetry   3 Memory   4 Fabric   q quit
"""

from __future__ import annotations

import os
import select
import sys
import termios
import threading
import time
import tty
from datetime import datetime
from typing import Callable, List, Sequence


_CLEAR = "\033[2J\033[H"

# Overview column widths — one row per ASIC, grouped under a board banner.
_OVERVIEW_COLS = (
    ("ASIC",    14),   # display_id + [L]/[R]
    ("KMD",      3),
    ("LID",      3),
    ("BDF",     12),   # 0000:01:00.0
    ("Fan",      4),
    ("Temp",     5),
    ("Pwr/Cap", 11),
    ("AICLK",    8),
    ("DRAM",    17),
    ("L1-Util",  7),
    ("Status",  10),
)
_OVERVIEW_WIDTHS = tuple(w for _, w in _OVERVIEW_COLS)


def _frame_width(widths: Sequence[int]) -> int:
    return 1 + sum(w + 3 for w in widths)


_TOTAL = _frame_width(_OVERVIEW_WIDTHS)


_TAB_WIDTHS_MAP = {}  # populated after column tuples are defined below


# --- low-level row/sep helpers ----------------------------------------------


def _sep(widths: Sequence[int], ch: str = "-") -> str:
    return "+" + "+".join(ch * (w + 2) for w in widths) + "+"


def _row(cells: Sequence[str], widths: Sequence[int]) -> str:
    parts = []
    for c, w in zip(cells, widths):
        parts.append(" " + c[:w].ljust(w) + " ")
    return "|" + "|".join(parts) + "|"


def _banner_sep(width: int = None) -> str:
    w = width if width is not None else _TOTAL
    return "+" + "-" * (w - 2) + "+"


def _banner(text: str, width: int = None) -> str:
    w = width if width is not None else _TOTAL
    return "|" + (" " + text).ljust(w - 2) + "|"


# --- formatting helpers -----------------------------------------------------


def _lid(d) -> str:
    """Logical ID for local devices, '-' for remotes."""
    if getattr(d, "is_remote", False):
        return "-"
    lid = getattr(d, "logical_id", -1)
    return str(lid) if isinstance(lid, int) and lid >= 0 else "-"


def _kmd(d) -> str:
    if getattr(d, "is_remote", False):
        return "-"
    n = getattr(d, "pci_ordinal", -1)
    return str(n) if isinstance(n, int) and n >= 0 else "-"


def _bdf(d) -> str:
    if getattr(d, "is_remote", False):
        return "-"
    return getattr(d, "pci_bdf", None) or "-"


def _role_tag(d) -> str:
    """'[L]'/'[R]' for Wormhole (where remote topology matters), empty otherwise."""
    arch = getattr(d, "arch_name", "") or ""
    if "Wormhole" not in arch:
        return ""
    return " [R]" if getattr(d, "is_remote", False) else " [L]"


def _asic(d) -> str:
    """ASIC display_id + role tag — uniquely identifies the chip."""
    did = getattr(d, "display_id", None) or str(getattr(d, "chip_id", "?"))
    return f"{did}{_role_tag(d)}"


def _board_type(arch_name: str, num_chips: int) -> str:
    """Infer board product name from arch + chip count. Mirrors Dashboard._board_type."""
    if "Wormhole" in arch_name:
        return "N300" if num_chips >= 2 else "N150"
    if "Blackhole" in arch_name:
        return "P300A" if num_chips >= 2 else "P150A"
    return "?"


def _group_by_board(devices):
    """Yield (board_id, arch_name, board_type, [devices]) preserving Rich's ordering."""
    from collections import OrderedDict
    boards = OrderedDict()
    for d in sorted(devices, key=lambda x: (getattr(x, "board_id", 0), getattr(x, "is_remote", False))):
        key = (getattr(d, "board_id", 0), d.arch_name or "?")
        boards.setdefault(key, []).append(d)
    for (bid, arch), devs in boards.items():
        yield bid, arch, _board_type(arch, len(devs)), devs


def _fmt(v, spec, dash="-"):
    try:
        if v is None:
            return dash
        if isinstance(v, (int, float)) and v < 0:
            return dash
        return format(v, spec)
    except Exception:
        return dash


def _mib(n: int) -> int:
    return int((n or 0) / (1024 * 1024))


def _fmt_runtime(sec: float) -> str:
    sec = int(sec or 0)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m{sec % 60:02d}s"
    if sec < 86400:
        return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"
    return f"{sec // 86400}d{(sec % 86400) // 3600:02d}h"


def _driver_str(devices) -> str:
    for d in devices:
        bundle = getattr(d.firmware, "fw_bundle_ver", None)
        if bundle:
            return f"UMD {bundle}"
    return "UMD"


# --- tab 1: overview --------------------------------------------------------


def _tab_overview(devices) -> List[str]:
    width = _frame_width(_OVERVIEW_WIDTHS)
    out = [_sep(_OVERVIEW_WIDTHS)]
    out.append(_row(tuple(label for label, _ in _OVERVIEW_COLS), _OVERVIEW_WIDTHS))
    out.append(_sep(_OVERVIEW_WIDTHS, ch="="))

    first = True
    for _bid, arch, btype, board_devs in _group_by_board(devices):
        if not first:
            out.append(_sep(_OVERVIEW_WIDTHS))
        first = False
        out.append(_banner(f"{btype} ({arch})", width))
        out.append(_sep(_OVERVIEW_WIDTHS))

        for d in board_devs:
            status = d.telemetry_status or "?"
            temp = _fmt(d.temperature, ".0f")
            power = _fmt(d.power, ".0f")
            plim = _fmt(d.tdp_limit_w, ".0f")
            aiclk = _fmt(d.aiclk_mhz, ".0f")
            pct = d.fan_speed_pct
            fan_cell = f"{pct}%" if isinstance(pct, int) and pct > 0 else "-"
            dram_used = _mib(d.used_dram)
            dram_tot = _mib(d.total_dram)
            l1_total = (d.used_l1 or 0) + (d.used_l1_small or 0) + (d.used_cb or 0)
            l1_pct = (l1_total / d.total_l1 * 100.0) if d.total_l1 else 0.0
            out.append(_row((
                _asic(d),
                _kmd(d),
                _lid(d),
                _bdf(d),
                fan_cell,
                f"{temp}C" if temp != "-" else "-",
                f"{power:>3}W /{plim:>3}W",
                f"{aiclk}MHz" if aiclk != "-" else "-",
                f"{dram_used:>5}/{dram_tot:>5} MiB",
                f"{l1_pct:>5.1f}%",
                status,
            ), _OVERVIEW_WIDTHS))
    out.append(_sep(_OVERVIEW_WIDTHS))
    return out


# --- tab 2: telemetry detail ------------------------------------------------

# Dev | ASIC | Board | VReg | VCORE | TDC | TDP | AICLK | AXICLK | ARCCLK | DDR | Fan RPM
_TELEM_COLS = (
    ("ASIC",    14),   # display_id + [L]/[R]
    ("T-ASIC",   6),
    ("T-Brd",    5),
    ("T-VReg",   6),
    ("VCORE",   6),
    ("TDC",     6),
    ("TDP",     6),
    ("AICLK",   8),
    ("AXICLK",  8),
    ("ARCCLK",  8),
    ("DDR",     8),
    ("Fan RPM", 8),
)
_TELEM_WIDTHS = tuple(w for _, w in _TELEM_COLS)


def _tab_telemetry(devices) -> List[str]:
    out = [_sep(_TELEM_WIDTHS)]
    out.append(_row(tuple(label for label, _ in _TELEM_COLS), _TELEM_WIDTHS))
    out.append(_sep(_TELEM_WIDTHS, ch="="))
    for d in devices:
        asic = _fmt(d.temperature, ".0f")
        board = _fmt(d.board_temperature, ".0f")
        vreg = _fmt(d.vreg_temperature, ".0f")
        vcore_mv = d.voltage_mv or 0
        # Note: field is named current_ma but firmware reports TDC in amps.
        tdc_a = d.current_ma or 0
        out.append(_row((
            _asic(d),
            f"{asic}C" if asic != "-" else "-",
            f"{board}C" if board != "-" else "-",
            f"{vreg}C" if vreg != "-" else "-",
            f"{vcore_mv/1000.0:.2f}V" if vcore_mv > 0 else "-",
            f"{tdc_a}A" if tdc_a > 0 else "-",
            f"{_fmt(d.tdp_limit_w, '.0f')}W",
            f"{_fmt(d.aiclk_mhz, '.0f')}MHz",
            f"{_fmt(d.axiclk_mhz, '.0f')}MHz",
            f"{_fmt(d.arcclk_mhz, '.0f')}MHz",
            f"{_fmt(d.ddr_speed_mhz, '.0f')}MHz",
            _fmt(d.fan_speed_rpm, "d") if isinstance(d.fan_speed_rpm, int) else _fmt(d.fan_speed_rpm, ".0f"),
        ), _TELEM_WIDTHS))
    out.append(_sep(_TELEM_WIDTHS))
    return out


# --- tab 3: memory detail ---------------------------------------------------

_MEM_COLS = (
    ("ASIC",      14),  # display_id + [L]/[R]
    ("DRAM",      22),  # used/total MiB
    ("Trace",     11),  # trace is DRAM-resident
    ("L1 small",  11),
    ("L1 cb",     11),
    ("L1",        22),  # used/total MiB
    ("L1 %",       6),
)
_MEM_WIDTHS = tuple(w for _, w in _MEM_COLS)


def _fmt_mib(n: int) -> str:
    mib = (n or 0) / (1024 * 1024)
    return f"{mib:.1f} MiB"


def _fmt_used_total(used: int, total: int) -> str:
    u = (used or 0) / (1024 * 1024)
    t = (total or 0) / (1024 * 1024)
    return f"{u:>8.1f} /{t:>8.1f} MiB"


def _tab_memory(devices) -> List[str]:
    out = [_sep(_MEM_WIDTHS)]
    out.append(_row(tuple(label for label, _ in _MEM_COLS), _MEM_WIDTHS))
    out.append(_sep(_MEM_WIDTHS, ch="="))
    for d in devices:
        l1_used = (d.used_l1 or 0) + (d.used_l1_small or 0) + (d.used_cb or 0)
        l1_pct = (l1_used / d.total_l1 * 100.0) if d.total_l1 else 0.0
        out.append(_row((
            _asic(d),
            _fmt_used_total(d.used_dram, d.total_dram),
            _fmt_mib(d.used_trace),
            _fmt_mib(d.used_l1_small),
            _fmt_mib(d.used_cb),
            _fmt_used_total(l1_used, d.total_l1),
            f"{l1_pct:>4.1f}%",
        ), _MEM_WIDTHS))
    out.append(_sep(_MEM_WIDTHS))
    return out


# --- tab 4: fabric ----------------------------------------------------------

_FABRIC_COLS = (
    ("ASIC",      14),  # display_id + [L]/[R]
    ("Arch",      13),
    ("MMIO",      5),
    ("ETH coord", 20),
    ("Active ch", 10),
    ("Idle ch",   9),
    ("Peers",     30),
)
_FABRIC_WIDTHS = tuple(w for _, w in _FABRIC_COLS)


def _tab_fabric(devices) -> List[str]:
    out = [_sep(_FABRIC_WIDTHS)]
    out.append(_row(tuple(label for label, _ in _FABRIC_COLS), _FABRIC_WIDTHS))
    out.append(_sep(_FABRIC_WIDTHS, ch="="))
    for d in devices:
        arch = (d.arch_name or "?").replace("_", " ")
        mmio = "yes" if d.is_mmio_capable else "no"
        coord = d.eth_coord
        if coord:
            coord_s = f"c{coord.get('cluster_id', '-')} x{coord.get('x', '-')} y{coord.get('y', '-')}"
        else:
            coord_s = "-"
        conns = d.eth_connections or []
        peer_summary = f"{len(conns)} link(s)" if conns else "-"
        out.append(_row((
            _asic(d),
            arch,
            mmio,
            coord_s,
            str(d.active_eth_channels),
            str(d.idle_eth_channels),
            peer_summary,
        ), _FABRIC_WIDTHS))
    out.append(_sep(_FABRIC_WIDTHS))
    return out


# --- process table (shown under every tab) ----------------------------------


def _process_block(devices, frame_width: int) -> List[str]:
    fixed = (14, 8, 8, 10)
    name_w = frame_width - 1 - sum(w + 3 for w in fixed) - 3
    widths = fixed + (name_w,)

    out = [_banner_sep(frame_width), _banner("Processes:", frame_width), _banner_sep(frame_width)]
    out.append(_row(("ASIC", "PID", "Runtime", "Mem", "Process name"), widths))
    out.append(_sep(widths, ch="="))

    any_proc = False
    for d in devices:
        for p in d.processes or []:
            any_proc = True
            cmdline = (p.get("cmdline") or p.get("name") or "?").replace("\n", " ").replace("\r", " ")
            mem_bytes = (
                (p.get("dram", 0) or 0)
                + (p.get("l1", 0) or 0)
                + (p.get("l1_small", 0) or 0)
                + (p.get("cb", 0) or 0)
            )
            mem_mib = mem_bytes / (1024 * 1024)
            out.append(_row((
                _asic(d),
                str(p.get("pid", "-")),
                _fmt_runtime(p.get("runtime", 0)),
                f"{mem_mib:>4.0f} MiB",
                cmdline,
            ), widths))
    if not any_proc:
        out.append(_row(("-", "-", "-", "-", "No running processes"), widths))
    out.append(_sep(widths))
    return out


# --- tab bar + render -------------------------------------------------------


_TAB_LABELS = {1: "Overview", 2: "Telemetry", 3: "Memory", 4: "Fabric"}


def _tab_bar(active: int) -> str:
    parts = []
    for n in (1, 2, 3, 4):
        label = f" {n} {_TAB_LABELS[n]} "
        parts.append(f"[{label}]" if n == active else f" {label} ")
    parts.append("   [ q quit ]")
    return "".join(parts)


_TAB_RENDERERS = {1: _tab_overview, 2: _tab_telemetry, 3: _tab_memory, 4: _tab_fabric}
_TAB_WIDTHS = {
    1: _frame_width(_OVERVIEW_WIDTHS),
    2: _frame_width(_TELEM_WIDTHS),
    3: _frame_width(_MEM_WIDTHS),
    4: _frame_width(_FABRIC_WIDTHS),
}


def render(devices, active_tab: int = 1, interactive: bool = False) -> str:
    lines: List[str] = []
    stamp = datetime.now().strftime("%a %b %d %H:%M:%S %Y")
    driver = _driver_str(devices)
    width = _TAB_WIDTHS.get(active_tab, _TOTAL)

    lines.append(stamp)
    lines.append(_banner_sep(width))
    header = f"TT-SMI   Driver Version: {driver}   Devices: {len(devices)}"
    lines.append(_banner(header, width))
    lines.append(_banner_sep(width))
    if interactive:
        lines.append(_tab_bar(active_tab))
        lines.append("")

    renderer = _TAB_RENDERERS.get(active_tab, _tab_overview)
    lines += renderer(devices)
    lines.append("")
    lines += _process_block(devices, frame_width=width)
    return "\n".join(lines)


def print_snapshot(devices, stream=None) -> None:
    (stream or sys.stdout).write(render(devices, active_tab=1, interactive=False) + "\n")


# --- watch loop with keyboard -----------------------------------------------


class _KeyReader:
    """Background thread that updates shared state from single keypresses."""

    def __init__(self):
        self.active_tab = 1
        self.quit = False
        self.dirty = False
        self._thread = None
        self._fd = None
        self._old_settings = None

    def start(self):
        if not sys.stdin.isatty():
            return
        self._fd = sys.stdin.fileno()
        self._old_settings = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._fd is not None and self._old_settings is not None:
            try:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_settings)
            except Exception:
                pass

    def _run(self):
        try:
            while not self.quit:
                if not select.select([self._fd], [], [], 0.1)[0]:
                    continue
                try:
                    ch = os.read(self._fd, 1).decode("utf-8", errors="ignore")
                except Exception:
                    continue
                if ch in "1234":
                    n = int(ch)
                    if n != self.active_tab:
                        self.active_tab = n
                        self.dirty = True
                elif ch.lower() == "q":
                    self.quit = True
        except Exception:
            pass


def watch(
    get_devices_fn: Callable,
    refresh_ms: int = 1000,
    update_telemetry_parallel_func: Callable = None,
    update_memory_func: Callable = None,
    no_clear: bool = False,
    stream=None,
) -> None:
    """Live refresh loop with tabbed views. Press 1-4 to switch, q to quit."""
    out = stream or sys.stdout
    keys = _KeyReader()
    keys.start()
    first = True
    interactive = sys.stdin.isatty() and not no_clear
    try:
        while not keys.quit:
            devices = get_devices_fn() or []
            if update_telemetry_parallel_func and devices:
                try:
                    update_telemetry_parallel_func(devices, timeout=1.0)
                except Exception:
                    pass
            if update_memory_func:
                for d in devices:
                    try:
                        update_memory_func(d)
                    except Exception:
                        pass

            if not no_clear and not first:
                out.write(_CLEAR)
            first = False
            out.write(render(devices, active_tab=keys.active_tab, interactive=interactive) + "\n")
            out.flush()

            # Sleep in short slices so tab switches feel responsive.
            deadline = time.monotonic() + refresh_ms / 1000.0
            while time.monotonic() < deadline and not keys.quit:
                if keys.dirty:
                    keys.dirty = False
                    break
                time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        keys.stop()
