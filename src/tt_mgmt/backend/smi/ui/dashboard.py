# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
# SPDX-License-Identifier: Apache-2.0

"""Rich-based dashboard for TT-SMI."""

from collections import OrderedDict
from datetime import datetime
from typing import List
from rich.console import Console, Group
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich import box
import time
import sys
import os
import signal
import threading

from .graphs import GraphWindow
import termios
import tty
import select

from ..core import Device, format_bytes


def format_runtime(seconds):
    """Format runtime in human-readable format."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m{secs:02d}s"
    elif seconds < 86400:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h{mins:02d}m"
    else:
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        return f"{days}d{hours:02d}h"


class Dashboard:
    """Live dashboard using Rich library."""

    def __init__(self, console: Console = None):
        self.console = console or Console()
        self.selected_pid_index = 0
        self.available_pids = []
        self._kill_pid = None
        self._force_kill_pid = None
        self._quit = False
        self._kb_thread = None
        self._force_refresh = False
        self._last_key_time = 0
        self._last_devices = None  # Keep when enumeration returns [] after chip reset
        self._all_error_since: float | None = None  # When all devices first went to Error
        # Per-device GDDR activity tracking: device_id -> (gddr01, gddr23, gddr45, gddr67, last_change_time)
        self._gddr_snapshot: dict = {}
        self._active_tab = 1  # 1 = Overview, 2 = Telemetry, 3 = Fabric, 4 = Graphs
        self._graph_window = GraphWindow(self.console, history_size=100)

    def _keyboard_reader(self):
        """Background keyboard reader thread using os.read for lower-level control."""
        if not sys.stdin.isatty():
            return
            
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        
        try:
            tty.setcbreak(fd)
            
            # Debug file
            debug = open("/tmp/ttsmi_keys.txt", "w")
            debug.write("Keyboard reader started\n")
            debug.flush()
            
            while not self._quit:
                if select.select([fd], [], [], 0.1)[0]:
                    try:
                        ch = os.read(fd, 1).decode('utf-8', errors='ignore')
                        debug.write(f"Got key: {repr(ch)}\n")
                        debug.flush()
                        
                        if ch == '\x1b':  # Escape sequence
                            # Read rest of arrow key sequence
                            next_chars = ""
                            if select.select([fd], [], [], 0.01)[0]:
                                next_chars += os.read(fd, 1).decode('utf-8', errors='ignore')
                            if select.select([fd], [], [], 0.01)[0]:
                                next_chars += os.read(fd, 1).decode('utf-8', errors='ignore')
                            
                            debug.write(f"Escape sequence: {repr(next_chars)}\n")
                            debug.flush()
                            
                            if next_chars == '[A' and self.available_pids:  # Up
                                debug.write(f"UP! Moving from {self.selected_pid_index}\n")
                                self.selected_pid_index = max(0, self.selected_pid_index - 1)
                                debug.write(f"UP! Now at {self.selected_pid_index}\n")
                                debug.flush()
                                self._force_refresh = True
                            elif next_chars == '[B' and self.available_pids:  # Down
                                debug.write(f"DOWN! Moving from {self.selected_pid_index}\n")
                                self.selected_pid_index = min(len(self.available_pids) - 1, self.selected_pid_index + 1)
                                debug.write(f"DOWN! Now at {self.selected_pid_index}\n")
                                debug.flush()
                                self._force_refresh = True
                        elif ch == '1':
                            if self._active_tab != 1:
                                self._active_tab = 1
                                self._force_refresh = True
                        elif ch == '2':
                            if self._active_tab != 2:
                                self._active_tab = 2
                                self._force_refresh = True
                        elif ch == '3':
                            if self._active_tab != 3:
                                self._active_tab = 3
                                self._force_refresh = True
                        elif ch == '4':
                            if self._active_tab != 4:
                                self._active_tab = 4
                                self._force_refresh = True
                        elif ch == 'k' and self.available_pids:  # k = SIGKILL
                            debug.write(f"KILL (SIGKILL) requested for PID index {self.selected_pid_index}: {self.available_pids[self.selected_pid_index]}\n")
                            debug.flush()
                            self._force_kill_pid = self.available_pids[self.selected_pid_index]
                        elif ch.lower() == 'q':
                            debug.write("QUIT requested!\n")
                            debug.flush()
                            self._quit = True
                    except Exception as e:
                        debug.write(f"Read error: {e}\n")
                        debug.flush()
            
            debug.close()
                        
        except Exception as e:
            try:
                with open("/tmp/ttsmi_keys.txt", "a") as f:
                    f.write(f"THREAD ERROR: {e}\n")
            except:
                pass
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            except Exception:
                pass
    
    # After this many consecutive seconds of all-device errors, restart the process.
    AUTO_RESTART_ERROR_SEC = 20

    def render_header(self, reconnect_in: int | None = None) -> Panel:
        """Render header panel with tab bar."""
        now = datetime.now().strftime("%a %b %d %H:%M:%S %Y")
        header_text = Text()
        header_text.append("Tenstorrent System Management Interface", style="cyan")
        header_text.append(f"\n{now}", style="white")
        if reconnect_in is not None:
            header_text.append(
                f"  ⟳ All devices in error — reconnecting in {reconnect_in}s...",
                style="bold yellow",
            )
        # Tab bar
        header_text.append("\n")
        for tab_num, tab_name in [(1, "Overview"), (2, "Telemetry"), (3, "Fabric"), (4, "Graphs")]:
            if self._active_tab == tab_num:
                header_text.append(f" [{tab_num}] {tab_name} ", style="bold black on cyan")
            else:
                header_text.append(f" [{tab_num}] {tab_name} ", style="dim")
        return Panel(header_text, box=box.DOUBLE, border_style="cyan")

    @staticmethod
    def _board_type(arch_name: str, num_chips: int) -> str:
        """Infer board product name from architecture and chip count."""
        if "Wormhole" in arch_name:
            return "N300" if num_chips >= 2 else "N150"
        if "Blackhole" in arch_name:
            return "P300A" if num_chips >= 2 else "P150A"
        return "?"

    @staticmethod
    def _group_by_board(devices: List[Device]) -> List[tuple]:
        """Return list of (board_id, arch_name, board_type, [devices]) sorted by board_id.

        Devices with the same board_id are on the same physical PCB.
        Within each board, local (non-remote) chip comes first.
        """
        boards: dict = OrderedDict()
        for dev in sorted(devices, key=lambda d: (d.board_id, d.is_remote)):
            key = (dev.board_id, dev.arch_name)
            if key not in boards:
                boards[key] = []
            boards[key].append(dev)
        result = []
        for (bid, arch), devs in boards.items():
            btype = Dashboard._board_type(arch, len(devs))
            result.append((bid, arch, btype, devs))
        return result

    def render_device_table(self, devices: List[Device]) -> Table:
        """Render main device table grouped by board, ASICs nested within."""

        table = Table(box=box.ROUNDED, show_header=True, header_style="bold")

        table.add_column("Board / ASIC", style="cyan", no_wrap=True, header_style="bold cyan", justify="center")
        table.add_column("KMD", width=3, justify="center", header_style="bold")
        table.add_column("LID", width=3, justify="center", header_style="bold")
        table.add_column("BDF", width=12, justify="center", header_style="bold")
        table.add_column("Temp", width=5, justify="center", header_style="bold")
        table.add_column("Power", width=5, justify="center", header_style="bold")
        table.add_column("AICLK", width=10, justify="center", header_style="bold")
        table.add_column("DRAM Usage", width=22, justify="center", header_style="bold")
        table.add_column("L1 Usage", width=22, justify="center", header_style="bold")
        table.add_column("Status", width=6, justify="center", header_style="bold")

        for board_id, arch_name, board_type, board_devs in self._group_by_board(devices):
            board_label = f"[bold]{board_type}[/bold] [dim]({arch_name})[/dim]"
            table.add_row(
                Text.from_markup(board_label),
                Text(""), Text(""), Text(""), Text(""), Text(""), Text(""), Text(""), Text(""), Text(""),
                style="on grey11",
            )

            is_wh = "Wormhole" in arch_name
            peer_active = any(
                not d.is_remote and (d.telemetry_status or "").strip() == "Active"
                for d in board_devs
            )
            for dev in board_devs:
                # Temperature
                if dev.temperature >= 0:
                    temp_str = f"{int(dev.temperature)}°C"
                    temp_style = "red" if dev.temperature > 80 else "yellow" if dev.temperature > 70 else "green"
                else:
                    temp_str = "N/A"
                    temp_style = "dim"

                # Power
                if dev.power >= 0:
                    power_str = f"{int(dev.power)}W"
                    power_style = "yellow"
                else:
                    power_str = "N/A"
                    power_style = "dim"

                # AICLK
                if dev.aiclk_mhz > 0:
                    aiclk_str = f"{dev.aiclk_mhz} MHz"
                    aiclk_style = "white"
                else:
                    aiclk_str = "N/A"
                    aiclk_style = "dim"

                # DRAM usage
                if dev.has_shm:
                    dram_total = dev.used_dram + dev.used_trace
                    dram_str = f"{format_bytes(dram_total)} / {format_bytes(dev.total_dram)}"
                    dram_pct = (dram_total / dev.total_dram * 100) if dev.total_dram > 0 else 0
                    dram_style = (
                        "red" if dram_pct > 90 else "yellow" if dram_pct > 70 else "green" if dram_total > 0 else "white"
                    )
                else:
                    dram_str = f"0B / {format_bytes(dev.total_dram)}"
                    dram_style = "dim"

                # L1 usage
                if dev.has_shm:
                    l1_total = dev.used_l1 + dev.used_l1_small + dev.used_cb
                    l1_str = f"{format_bytes(l1_total)} / {format_bytes(dev.total_l1)}"
                    l1_pct = (l1_total / dev.total_l1 * 100) if dev.total_l1 > 0 else 0
                    l1_style = "red" if l1_pct > 90 else "yellow" if l1_pct > 70 else "green" if l1_total > 0 else "white"
                else:
                    l1_str = f"0B / {format_bytes(dev.total_l1)}"
                    l1_style = "dim"

                # Status — remote devices inherit Active from their local peer
                status_str = (dev.telemetry_status or "Unknown")[:8].strip()
                if dev.is_remote and peer_active:
                    status_str = "Active"
                if status_str == "Active":
                    status_style = "bold green"
                elif status_str == "Idle":
                    status_style = "dim"
                elif status_str in ("OK",):
                    status_style = "green"
                elif status_str == "Error":
                    status_style = "red"
                else:
                    status_style = "yellow"

                # Only label L/R for WH boards where topology matters
                if is_wh:
                    role_tag = " [R]" if dev.is_remote else " [L]"
                else:
                    role_tag = ""
                asic_label = f"  └─ {dev.display_id}{role_tag}"

                # KMD ID, Logical ID and BDF columns
                if dev.is_remote:
                    kmd_str, kmd_style = "-", "dim italic"
                    lid_str, lid_style = "-", "dim"
                    bdf_str, bdf_style = "-", "dim"
                elif dev.pci_ordinal >= 0:
                    kmd_str, kmd_style = str(dev.pci_ordinal), "white"
                    lid_str = str(dev.logical_id) if dev.logical_id >= 0 else "-"
                    lid_style = "bright_blue"
                    bdf_str, bdf_style = dev.pci_bdf or "-", "dim"
                else:
                    kmd_str, kmd_style = "-", "dim"
                    lid_str, lid_style = "-", "dim"
                    bdf_str, bdf_style = "-", "dim"

                table.add_row(
                    Text(asic_label, style="cyan"),
                    Text(kmd_str, style=kmd_style),
                    Text(lid_str, style=lid_style),
                    Text(bdf_str, style=bdf_style),
                    Text(temp_str, style=temp_style),
                    Text(power_str, style=power_style),
                    Text(aiclk_str, style=aiclk_style),
                    Text(dram_str, style=dram_style),
                    Text(l1_str, style=l1_style),
                    Text(status_str, style=status_style),
                )

        return table

    @staticmethod
    def _fmt_temp(val: float) -> Text:
        """Format temperature, returning '-' for unavailable (val < 0)."""
        if val < 0:
            return Text("-", style="dim")
        style = "red" if val > 80 else "yellow" if val > 70 else "green"
        return Text(f"{int(val)}°C", style=style)

    @staticmethod
    def _fmt_mhz(val: int) -> Text:
        """Format MHz value."""
        if val == 0:
            return Text("-", style="dim")
        return Text(f"{val}", style="white")

    def render_wh_telemetry_table(self, devices: List[Device]) -> Table:
        """Wormhole-specific detailed telemetry table, grouped by board."""
        table = Table(
            title="Wormhole Telemetry",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold",
        )
        table.add_column("Board / ASIC", style="cyan", width=22)
        table.add_column("ASIC T", width=7, justify="center")
        table.add_column("Brd T", width=7, justify="center")
        table.add_column("Vcore", width=7, justify="center")
        table.add_column("TDC", width=8, justify="center")
        table.add_column("TDP", width=7, justify="center")
        table.add_column("AICLK\nMHz", width=7, justify="center")
        table.add_column("AXICLK\nMHz", width=7, justify="center")
        table.add_column("ARCCLK\nMHz", width=7, justify="center")
        table.add_column("GDDR\nGbps", width=8, justify="center")
        table.add_column("Fan RPM", width=8, justify="center")

        empty11 = [Text("")] * 10

        for board_id, _arch, board_type, board_devs in self._group_by_board(devices):
            table.add_row(
                Text.from_markup(f"[bold]Board {board_id:016x}[/bold]  [dim]{board_type}[/dim]"),
                *empty11,
                style="on grey11",
            )
            for dev in board_devs:
                vcore = Text(f"{dev.voltage_mv}mV", style="white") if dev.voltage_mv > 0 else Text("-", style="dim")
                tdc = Text(f"{dev.current_ma}A", style="white") if dev.current_ma > 0 else Text("-", style="dim")
                tdp = Text(f"{int(dev.power)}W", style="yellow") if dev.power >= 0 else Text("-", style="dim")
                ddr = Text(f"{dev.ddr_speed_mhz / 1000:.1f}", style="white") if dev.ddr_speed_mhz > 0 else Text("-", style="dim")
                fan = Text(f"{dev.fan_speed_rpm}", style="white") if dev.fan_speed_rpm > 0 else Text("-", style="dim")
                role_tag = " [R]" if dev.is_remote else " [L]"
                table.add_row(
                    Text(f"  └─ {dev.display_id}{role_tag}", style="cyan"),
                    self._fmt_temp(dev.temperature),
                    self._fmt_temp(dev.board_temperature),
                    vcore,
                    tdc,
                    tdp,
                    self._fmt_mhz(dev.aiclk_mhz),
                    self._fmt_mhz(dev.axiclk_mhz),
                    self._fmt_mhz(dev.arcclk_mhz),
                    ddr,
                    fan,
                )
        return table

    def render_bh_telemetry_table(self, devices: List[Device]) -> Table:
        """Blackhole-specific detailed telemetry table, grouped by board."""
        table = Table(
            title="Blackhole Telemetry",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold",
        )
        table.add_column("Board / ASIC", style="cyan", width=22)
        table.add_column("ASIC T", width=7, justify="center")
        table.add_column("Vcore", width=7, justify="center")
        table.add_column("TDC", width=8, justify="center")
        table.add_column("TDP", width=7, justify="center")
        table.add_column("In Pwr", width=8, justify="center")
        table.add_column("AICLK\nMHz", width=7, justify="center")
        table.add_column("AXICLK\nMHz", width=7, justify="center")
        table.add_column("ARCCLK\nMHz", width=7, justify="center")
        table.add_column("GDDR\nGbps", width=8, justify="center")
        table.add_column("Fan RPM", width=8, justify="center")

        empty11 = [Text("")] * 10

        for board_id, _arch, board_type, board_devs in self._group_by_board(devices):
            table.add_row(
                Text.from_markup(f"[bold]Board {board_id:016x}[/bold]  [dim]{board_type}[/dim]"),
                *empty11,
                style="on grey11",
            )
            for dev in board_devs:
                vcore = Text(f"{dev.voltage_mv}mV", style="white") if dev.voltage_mv > 0 else Text("-", style="dim")
                tdc = Text(f"{dev.current_ma}A", style="white") if dev.current_ma > 0 else Text("-", style="dim")
                tdp = Text(f"{int(dev.power)}W", style="yellow") if dev.power >= 0 else Text("-", style="dim")
                in_pwr = Text(f"{dev.input_power_w}W", style="magenta") if dev.input_power_w > 0 else Text("-", style="dim")
                ddr = Text(f"{dev.ddr_speed_mhz / 1000:.1f}", style="white") if dev.ddr_speed_mhz > 0 else Text("-", style="dim")
                fan = Text(f"{dev.fan_speed_rpm}", style="white") if dev.fan_speed_rpm > 0 else Text("-", style="dim")
                table.add_row(
                    Text(f"  └─ {dev.display_id}", style="cyan"),
                    self._fmt_temp(dev.temperature),
                    vcore,
                    tdc,
                    tdp,
                    in_pwr,
                    self._fmt_mhz(dev.aiclk_mhz),
                    self._fmt_mhz(dev.axiclk_mhz),
                    self._fmt_mhz(dev.arcclk_mhz),
                    ddr,
                    fan,
                )
        return table

    # Seconds without a GDDR temp change before marking the readings as idle/frozen.
    GDDR_IDLE_THRESHOLD_SEC = 5.0

    def render_bh_gddr_table(self, devices: List[Device]) -> Table:
        """Blackhole GDDR temperature breakdown table (per-pair max temps).

        Values are greyed out and labelled 'idle' when the GDDR has been in
        self-refresh long enough that ARC stopped polling temps (frozen readings).
        """
        now = time.time()

        def _pair_max(packed: int) -> int:
            return max(
                (packed >> 0) & 0xFF,
                (packed >> 8) & 0xFF,
                (packed >> 16) & 0xFF,
                (packed >> 24) & 0xFF,
            )

        # Update snapshot and determine active/idle per device.
        activity: dict[str, bool] = {}  # device_id -> True if active
        for dev in devices:
            key = dev.display_id
            sig = (dev.gddr01_temp, dev.gddr23_temp, dev.gddr45_temp, dev.gddr67_temp)
            prev = self._gddr_snapshot.get(key)
            if prev is None or prev[0] != sig:
                self._gddr_snapshot[key] = (sig, now)
                activity[key] = True
            else:
                elapsed = now - prev[1]
                activity[key] = elapsed < self.GDDR_IDLE_THRESHOLD_SEC

        table = Table(
            title="Blackhole GDDR Temperatures",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold",
        )
        table.add_column("Board / ASIC", style="cyan", width=22)
        table.add_column("State", width=7, justify="center")
        table.add_column("Max GDDR", width=9, justify="center")
        table.add_column("GDDR 0/1", width=9, justify="center")
        table.add_column("GDDR 2/3", width=9, justify="center")
        table.add_column("GDDR 4/5", width=9, justify="center")
        table.add_column("GDDR 6/7", width=9, justify="center")

        empty7 = [Text("")] * 6

        for board_id, _arch, board_type, board_devs in self._group_by_board(devices):
            table.add_row(
                Text.from_markup(f"[bold]Board {board_id:016x}[/bold]  [dim]{board_type}[/dim]"),
                *empty7,
                style="on grey11",
            )
            for dev in board_devs:
                key = dev.display_id
                active = activity.get(key, True)

                def gddr_cell(packed: int) -> Text:
                    if packed == 0:
                        return Text("-", style="dim")
                    t = _pair_max(packed)
                    if not active:
                        return Text(f"{t}°C", style="dim")
                    style = "red" if t > 80 else "yellow" if t > 70 else "green"
                    return Text(f"{t}°C", style=style)

                state = Text("active", style="green") if active else Text("idle", style="dim")
                max_cell = (
                    self._fmt_temp(dev.max_gddr_temp) if (active and dev.max_gddr_temp > 0)
                    else Text(f"{dev.max_gddr_temp}°C" if dev.max_gddr_temp > 0 else "-", style="dim")
                )

                table.add_row(
                    Text(f"  └─ {dev.display_id}", style="cyan"),
                    state,
                    max_cell,
                    gddr_cell(dev.gddr01_temp),
                    gddr_cell(dev.gddr23_temp),
                    gddr_cell(dev.gddr45_temp),
                    gddr_cell(dev.gddr67_temp),
                )
        return table

    @staticmethod
    def _fmt_mib(kb: int, warn_gib: int = 0) -> Text:
        """Format a kB value as MiB or GiB, with optional color threshold (in GiB)."""
        if kb == 0:
            return Text("0", style="dim")
        mib = kb / 1024
        if mib >= 1024:
            s = f"{mib / 1024:.1f}G"
            style = "red" if warn_gib and mib / 1024 > warn_gib else "white"
        else:
            s = f"{int(mib)}M"
            style = "white"
        return Text(s, style=style)

    @staticmethod
    def _fmt_swap(kb: int) -> Text:
        if kb == 0:
            return Text("0", style="dim")
        mib = kb / 1024
        s = f"{mib / 1024:.1f}G" if mib >= 1024 else f"{int(mib)}M"
        return Text(s, style="bold red")

    @staticmethod
    def _proc_display_name(proc: dict, max_len: int = 16) -> str:
        """Return the best short display name for a process.

        Prefers the first meaningful token of cmdline over the raw comm name
        (e.g. shows "my_model.py" instead of "python3").
        """
        cmdline = proc.get("cmdline", "")
        if cmdline:
            tokens = cmdline.split()
            # Skip interpreter argv[0], take the script/binary that follows
            interpreters = {"python", "python3", "python3.10", "python3.11", "python3.12",
                            "bash", "sh", "zsh", "perl", "ruby", "node"}
            for i, tok in enumerate(tokens):
                base = tok.rsplit("/", 1)[-1]
                if base not in interpreters and i > 0:
                    name = base
                    break
            else:
                name = tokens[0].rsplit("/", 1)[-1] if tokens else proc.get("name", "?")
        else:
            name = proc.get("name", "?")
        return name[:max_len - 3] + "..." if len(name) > max_len else name

    def render_process_table(self, devices: List[Device], interactive=False) -> Table:
        """Render per-process memory usage table (shows only devices with active processes)."""
        devices_with_processes = [dev for dev in devices if dev.processes]

        if not devices_with_processes:
            return None

        # Build list of available PIDs for interactive mode
        from collections import defaultdict
        pid_groups_temp = defaultdict(list)
        for dev in devices_with_processes:
            for proc in dev.processes:
                pid_groups_temp[proc["pid"]].append((dev, proc))
        self.available_pids = sorted(pid_groups_temp.keys())

        if self.available_pids:
            self.selected_pid_index = max(0, min(self.selected_pid_index, len(self.available_pids) - 1))

        selected_pid = self.available_pids[self.selected_pid_index] if self.available_pids and interactive else None

        title_text = f"Per-Process Usage ({len(devices_with_processes)} of {len(devices)} devices active)"
        if interactive and self.available_pids:
            title_text += (
                f"  [1-4: tab | ↑/↓: select [{self.selected_pid_index+1}/{len(self.available_pids)}]"
                " | k: kill-9 | q: quit]"
            )

        table = Table(
            title=title_text,
            box=box.ROUNDED,
            show_header=True,
            header_style="bold",
        )

        table.add_column("Dev",      style="cyan", width=12)
        table.add_column("PID",      width=6,  justify="center")
        table.add_column("Process",  width=10)
        table.add_column("Runtime",  width=8,  justify="center")
        table.add_column("CPU%",     width=6,  justify="center")
        table.add_column("Thr",      width=4,  justify="center")
        table.add_column("RSS",      width=7,  justify="center")
        table.add_column("Virt",     width=7,  justify="center")
        table.add_column("DRAM",     width=10, justify="center")
        table.add_column("L1",       width=8,  justify="center")
        table.add_column("L1 Small",    width=8,  justify="center")
        table.add_column("Trace",    width=8,  justify="center")
        table.add_column("CB",       width=8,  justify="center")

        # Group processes by PID
        pid_groups = defaultdict(list)
        for dev in devices_with_processes:
            for proc in dev.processes:
                pid_groups[proc["pid"]].append((dev, proc))

        sorted_pids = sorted(pid_groups.keys())

        has_processes = False
        for pid in sorted_pids:
            has_processes = True
            devices_for_pid = pid_groups[pid]

            has_allocations = any(
                p["dram"] > 0 or p["l1"] > 0 or p["l1_small"] > 0
                or p["trace"] > 0 or p["cb"] > 0
                for _, p in devices_for_pid
            )

            if not has_allocations and len(devices_for_pid) > 1:
                # Compact row: PID spans all devices with no allocations
                dev, proc = devices_for_pid[0]
                proc_name  = self._proc_display_name(proc)
                lids = sorted(
                    d.logical_id for d, _ in devices_for_pid if d.logical_id >= 0
                )
                dev_info = f"LID {','.join(str(l) for l in lids)}" if lids else f"All ({len(devices_for_pid)})"
                runtime_str = format_runtime(proc.get("runtime", 0))
                cpu_str    = f"{proc.get('cpu_percent', 0.0):.1f}"
                thr_str    = str(proc.get("num_threads", 0)) if proc.get("num_threads") else "-"
                rss_cell   = self._fmt_mib(proc.get("vm_rss_kb", 0))
                virt_cell  = self._fmt_mib(proc.get("vm_virt_kb", 0))

                hl = "bold white on blue" if (interactive and pid == selected_pid) else None
                def _t(s, extra="white"):
                    return Text(s, style=hl or extra)

                table.add_row(
                    _t(dev_info),
                    _t(str(proc["pid"])),
                    _t(proc_name),
                    _t(runtime_str),
                    _t(cpu_str),
                    _t(thr_str),
                    rss_cell  if not hl else Text(rss_cell.plain,  style=hl),
                    virt_cell if not hl else Text(virt_cell.plain, style=hl),
                    _t("0B", "dim"),
                    _t("0B", "dim"),
                    _t("0B", "dim"),
                    _t("0B", "dim"),
                    _t("0B", "dim"),
                )
            else:
                active_devices = [
                    (dev, proc) for dev, proc in devices_for_pid
                    if proc["dram"] > 0 or proc["l1"] > 0 or proc["l1_small"] > 0
                    or proc["trace"] > 0 or proc["cb"] > 0
                ]
                rows_to_show = active_devices if active_devices else devices_for_pid
                for dev, proc in sorted(rows_to_show, key=lambda x: x[0].display_id):
                    proc_name   = self._proc_display_name(proc)
                    runtime_str = format_runtime(proc.get("runtime", 0))
                    cpu_str     = f"{proc.get('cpu_percent', 0.0):.1f}"
                    thr_str     = str(proc.get("num_threads", 0)) if proc.get("num_threads") else "-"
                    rss_cell    = self._fmt_mib(proc.get("vm_rss_kb", 0))
                    virt_cell   = self._fmt_mib(proc.get("vm_virt_kb", 0))

                    hl = "bold white on blue" if (interactive and pid == selected_pid) else None

                    if hl:
                        table.add_row(
                            Text(dev.display_id, style=hl),
                            Text(str(proc["pid"]), style=hl),
                            Text(proc_name, style=hl),
                            Text(runtime_str, style=hl),
                            Text(cpu_str, style=hl),
                            Text(thr_str, style=hl),
                            Text(rss_cell.plain,  style=hl),
                            Text(virt_cell.plain, style=hl),
                            Text(format_bytes(proc["dram"]),     style=hl),
                            Text(format_bytes(proc["l1"]),       style=hl),
                            Text(format_bytes(proc["l1_small"]), style=hl),
                            Text(format_bytes(proc["trace"]),    style=hl),
                            Text(format_bytes(proc["cb"]),       style=hl),
                        )
                    else:
                        table.add_row(
                            dev.display_id,
                            str(proc["pid"]),
                            proc_name,
                            runtime_str,
                            cpu_str,
                            Text(thr_str, style="dim" if thr_str == "-" else "white"),
                            rss_cell,
                            Text(virt_cell.plain, style="dim"),
                            Text(format_bytes(proc["dram"]),     style="green" if proc["dram"]     > 0 else "dim"),
                            Text(format_bytes(proc["l1"]),       style="green" if proc["l1"]       > 0 else "dim"),
                            Text(format_bytes(proc["l1_small"]), style="green" if proc["l1_small"] > 0 else "dim"),
                            Text(format_bytes(proc["trace"]),    style="green" if proc["trace"]    > 0 else "dim"),
                            Text(format_bytes(proc["cb"]),       style="green" if proc["cb"]       > 0 else "dim"),
                        )

        if not has_processes:
            return None

        return table

    def render_snapshot(self, devices: List[Device], interactive=False,
                        reconnect_in: int | None = None):
        """Render complete snapshot, switching between tabs based on self._active_tab."""
        header = self.render_header(reconnect_in=reconnect_in)
        if self._active_tab == 4:
            return self._render_graphs_tab(devices, header)
        if self._active_tab == 3:
            return self._render_fabric_tab(devices, header)
        if self._active_tab == 2:
            return self._render_telemetry_tab(devices, header)
        return self._render_overview_tab(devices, header, interactive)

    def _render_overview_tab(self, devices: List[Device], header, interactive: bool) -> Group:
        """Tab 1: device overview table + process table."""
        parts = [header, self.render_device_table(devices)]
        proc_table = self.render_process_table(devices, interactive=interactive)
        if proc_table:
            parts.append(proc_table)
        return Group(*parts)

    def _render_telemetry_tab(self, devices: List[Device], header) -> Group:
        """Tab 2: per-architecture detailed telemetry + GDDR tables."""
        wh_devs = [d for d in devices if "Wormhole" in d.arch_name]
        bh_devs = [d for d in devices if "Blackhole" in d.arch_name]

        parts = [header]
        if wh_devs:
            parts.append(self.render_wh_telemetry_table(wh_devs))
        if bh_devs:
            parts.append(self.render_bh_telemetry_table(bh_devs))
            parts.append(self.render_bh_gddr_table(bh_devs))

        return Group(*parts)

    def _render_graphs_tab(self, devices: List[Device], header) -> Layout:
        """Tab 4: live telemetry graphs (nvtop-style)."""
        gw = self._graph_window
        num_devices = len(devices)

        terminal_height = self.console.height
        terminal_width = self.console.width

        header_size = 5
        per_device_overhead = 9
        rows, cols = gw._calculate_optimal_layout(num_devices, terminal_width, terminal_height)

        available_height = terminal_height - header_size
        height_per_device = available_height // max(rows, 1)
        chart_height = max(3, height_per_device - per_device_overhead)

        per_device_width_overhead = 13
        available_width = terminal_width // max(cols, 1)
        chart_width = max(10, available_width - per_device_width_overhead)

        panels = []
        for dev in devices:
            did = dev.display_id if hasattr(dev, "display_id") else str(dev.chip_id)
            panels.append(gw.render_device_card(did, dev, chart_height, chart_width))

        root = Layout()
        root.split_column(
            Layout(header, name="header", size=header_size),
            Layout(name="devices"),
        )

        if num_devices <= 1:
            root["devices"].update(panels[0] if panels else Panel("No devices"))
        elif num_devices == 2:
            root["devices"].split_row(Layout(panels[0]), Layout(panels[1]))
        else:
            col_names = [f"col{i}" for i in range(cols)]
            root["devices"].split_row(*[Layout(name=n) for n in col_names])
            for ci, cn in enumerate(col_names):
                col_panels = [panels[i] for i in range(ci, num_devices, cols)]
                if col_panels:
                    root[cn].split_column(*[Layout(p) for p in col_panels])

        return root

    def render_fabric_link_table(self, devices: List[Device]) -> Table:
        """Render per-board ethernet connectivity summary."""
        table = Table(
            title="Ethernet Connectivity",
            box=box.ROUNDED, show_header=True, header_style="bold",
        )
        table.add_column("Board / ASIC", style="cyan", width=24)
        table.add_column("Arch", width=12)
        table.add_column("Active", justify="right", width=6)
        table.add_column("Idle", justify="right", width=6)
        table.add_column("In-Cluster", justify="right", width=10)
        table.add_column("Exit", justify="right", width=6)
        table.add_column("Coord", width=20)

        for board_id, arch_name, board_type, board_devs in self._group_by_board(devices):
            board_label = f"[bold]Board {board_id:016x}[/bold]  [dim]{board_type}[/dim]"
            table.add_row(
                Text.from_markup(board_label),
                Text(arch_name, style="bold"),
                Text(""), Text(""), Text(""), Text(""), Text(""),
                style="on grey11",
            )

            is_wh = "Wormhole" in arch_name
            for dev in board_devs:
                conns = dev.eth_connections
                in_cluster = sum(1 for c in conns if not c["is_exit_link"])
                exit_links = sum(1 for c in conns if c["is_exit_link"])

                coord = dev.eth_coord
                if coord["cluster_id"] >= 0:
                    coord_str = f"c{coord['cluster_id']} ({coord['x']},{coord['y']}) r{coord['rack']}s{coord['shelf']}"
                else:
                    coord_str = "-"

                role = ""
                if is_wh:
                    role = " [R]" if dev.is_remote else " [L]"
                asic_label = f"  └─ {dev.display_id}{role}"

                active_style = "green" if dev.active_eth_channels > 0 else "dim"
                exit_style = "bold yellow" if exit_links > 0 else "dim"

                table.add_row(
                    Text(asic_label, style="cyan"),
                    Text(""),
                    Text(str(dev.active_eth_channels), style=active_style),
                    Text(str(dev.idle_eth_channels), style="dim"),
                    Text(str(in_cluster), style="white"),
                    Text(str(exit_links), style=exit_style),
                    Text(coord_str, style="dim"),
                )

        return table

    def render_exit_links_table(self, devices: List[Device]) -> Table | None:
        """Render table of exit links (connections going off-host). Returns None if no exit links."""
        chip_id_to_display = {dev.chip_id: dev.display_id for dev in devices}

        rows = []
        for dev in devices:
            for conn in dev.eth_connections:
                if conn["is_exit_link"]:
                    remote_display = chip_id_to_display.get(
                        conn["remote_chip_id"], f"0x{conn['remote_chip_id']:x}"
                    )
                    rows.append((dev.display_id, conn["local_channel"], remote_display, conn["remote_channel"]))

        if not rows:
            return None

        table = Table(
            title="Exit Links (off-host connections)",
            box=box.ROUNDED, show_header=True, header_style="bold",
        )
        table.add_column("Local Device", style="cyan")
        table.add_column("Local Ch", justify="right")
        table.add_column("Remote ID", style="yellow")
        table.add_column("Remote Ch", justify="right")

        for local_dev, local_ch, remote_dev, remote_ch in rows:
            table.add_row(local_dev, str(local_ch), remote_dev, str(remote_ch))

        return table

    def render_cluster_table(self) -> Table | None:
        """Render multi-host cluster topology from fabric manager. Returns None if unavailable."""
        try:
            from tt_mgmt.backend.smi.core import has_fabric, get_cluster_topology
            if not has_fabric():
                return None
            info = get_cluster_topology()
            if info["error"] or not info["hosts"]:
                return None
        except Exception:
            return None

        table = Table(
            title=f"Cluster Topology ({len(info['hosts'])} hosts, "
                  f"{info['total_cross_host_links']} cross-host links)",
            box=box.ROUNDED, show_header=True, header_style="bold",
        )
        table.add_column("Host", style="cyan")
        table.add_column("ASICs", justify="right", width=6)
        table.add_column("Arch", style="dim", width=12)
        table.add_column("Connected To")
        table.add_column("Status")

        for host in info["hosts"]:
            peer_count = len(host["connected_hosts"])
            status = Text("Connected", style="green") if peer_count > 0 else Text("Isolated", style="bold red")
            table.add_row(
                host["host_name"],
                str(host["asic_count"]),
                host["arch"],
                ", ".join(host["connected_hosts"]) or "-",
                status,
            )
        return table

    def _render_fabric_tab(self, devices: List[Device], header) -> Group:
        """Tab 3: ethernet connectivity per board + exit links + cluster view."""
        parts = [header, self.render_fabric_link_table(devices)]
        exit_table = self.render_exit_links_table(devices)
        if exit_table:
            parts.append(exit_table)
        cluster_table = self.render_cluster_table()
        if cluster_table:
            parts.append(cluster_table)
        return Group(*parts)

    def _check_auto_restart(self, devices: List[Device]) -> int | None:
        """Track all-error duration. Returns seconds until restart, or None if healthy.
        Calls os.execv to restart the process when the threshold is exceeded.
        """
        healthy = {"ok", "active", "idle"}
        all_error = bool(devices) and all(
            (d.telemetry_status or "").lower() not in healthy for d in devices
        )
        if all_error:
            if self._all_error_since is None:
                self._all_error_since = time.time()
            elapsed = time.time() - self._all_error_since
            remaining = int(self.AUTO_RESTART_ERROR_SEC - elapsed)
            if remaining <= 0:
                # Restart the process cleanly — gets fresh file descriptors / UMD state
                os.execv(sys.executable, [sys.executable] + sys.argv)
            return max(0, remaining)
        else:
            self._all_error_since = None
            return None

    def print_snapshot(self, devices: List[Device]):
        """Print a single snapshot (non-watch mode)."""
        self.console.print(self.render_header())
        self.console.print(self.render_device_table(devices))

        wh_devs = [d for d in devices if "Wormhole" in d.arch_name]
        bh_devs = [d for d in devices if "Blackhole" in d.arch_name]
        if wh_devs:
            self.console.print(self.render_wh_telemetry_table(wh_devs))
        if bh_devs:
            self.console.print(self.render_bh_telemetry_table(bh_devs))
            self.console.print(self.render_bh_gddr_table(bh_devs))

        proc_table = self.render_process_table(devices)
        if proc_table:
            self.console.print("\n")
            self.console.print(proc_table)

    def watch(
        self,
        get_devices_func,
        refresh_ms: int = 1000,
        update_telemetry_parallel_func=None,
        update_memory_func=None,
        graph_window=None,
        interactive=True,
        start_tab: int | None = None,
    ):
        """Live watch mode with auto-refresh.

        Args:
            get_devices_func: Function to get device list
            refresh_ms: Refresh interval in milliseconds
            update_telemetry_parallel_func: Function to update telemetry in parallel
            update_memory_func: Function to update memory stats
            graph_window: Deprecated, graphs are now built-in as tab 4
            interactive: Enable interactive mode (keyboard controls)
            start_tab: Initial tab to display (1-4)
        """
        if start_tab is not None:
            self._active_tab = start_tab
        # Import cleanup function once for efficiency
        from ..core import cleanup_dead_processes

        try:
            devices = get_devices_func()
            if devices:
                self._last_devices = devices
            elif self._last_devices:
                devices = self._last_devices
            if update_telemetry_parallel_func:
                try:
                    update_telemetry_parallel_func(devices, timeout=1.0)
                except Exception:
                    # Initial telemetry update failed (devices may be resetting)
                    pass
            if update_memory_func:
                for dev in devices:
                    try:
                        update_memory_func(dev)
                    except Exception:
                        pass

            # Calculate optimal screen refresh rate based on data refresh interval
            # Cap at 10 FPS (100ms) for smooth updates without excessive CPU
            screen_refresh_rate = min(10, max(2, 1000 / refresh_ms))

            # Table view with tab switching (graphs are built-in as tab 4)
            if interactive:
                with open("/tmp/ttsmi_debug.txt", "w") as f:
                    f.write(f"Interactive mode: {interactive}\n")
                    f.write(f"stdin.isatty(): {sys.stdin.isatty()}\n")
                    f.write(f"Starting keyboard thread...\n")

                self._kb_thread = threading.Thread(target=self._keyboard_reader, daemon=True)
                self._kb_thread.start()

                with open("/tmp/ttsmi_debug.txt", "a") as f:
                    f.write(f"Keyboard thread started: {self._kb_thread.is_alive()}\n")

                try:
                    self.console.clear()
                    with Live(
                        self.render_snapshot(devices, interactive=True),
                        refresh_per_second=screen_refresh_rate,
                        console=self.console,
                        screen=False,
                    ) as live:
                        last_update = time.time()

                        while not self._quit:
                            if self._kill_pid:
                                try:
                                    os.kill(self._kill_pid, signal.SIGTERM)
                                    self._kill_pid = None
                                    time.sleep(1.0)
                                except Exception:
                                    self._kill_pid = None

                            if self._force_kill_pid:
                                try:
                                    os.kill(self._force_kill_pid, signal.SIGKILL)
                                    self._force_kill_pid = None
                                    time.sleep(0.3)
                                except Exception:
                                    self._force_kill_pid = None

                            if self._force_refresh:
                                self._force_refresh = False
                                live.update(self.render_snapshot(devices, interactive=True))
                                time.sleep(0.05)
                                continue

                            now = time.time()
                            if (now - last_update) < (refresh_ms / 1000.0):
                                time.sleep(0.05)
                                continue

                            last_update = now

                            try:
                                cleanup_dead_processes()
                            except Exception:
                                pass

                            try:
                                new_devices = get_devices_func()
                                if new_devices:
                                    if not self._last_devices or len(new_devices) >= len(self._last_devices):
                                        devices = new_devices
                                        self._last_devices = new_devices
                                    else:
                                        devices = self._last_devices
                                elif self._last_devices:
                                    devices = self._last_devices
                            except Exception:
                                pass

                            if update_telemetry_parallel_func:
                                try:
                                    update_telemetry_parallel_func(devices, timeout=1.0)
                                except Exception:
                                    for dev in devices:
                                        dev.telemetry_status = "Unavailable"

                            if update_memory_func:
                                for dev in devices:
                                    try:
                                        update_memory_func(dev)
                                    except Exception:
                                        pass

                            for dev in devices:
                                self._graph_window.update_device(dev)

                            reconnect_in = self._check_auto_restart(devices)
                            live.update(self.render_snapshot(devices, interactive=True,
                                                              reconnect_in=reconnect_in))

                finally:
                    self._quit = True
                    if self._kb_thread:
                        self._kb_thread.join(timeout=0.5)
            else:
                with Live(
                    self.render_snapshot(devices, interactive=False),
                    refresh_per_second=screen_refresh_rate,
                    console=self.console,
                    screen=True,
                ) as live:
                    while True:
                        time.sleep(refresh_ms / 1000.0)

                        try:
                            cleanup_dead_processes()
                        except Exception:
                            pass

                        try:
                            new_devices = get_devices_func()
                            if new_devices:
                                if not self._last_devices or len(new_devices) >= len(self._last_devices):
                                    devices = new_devices
                                    self._last_devices = new_devices
                                else:
                                    devices = self._last_devices
                            elif self._last_devices:
                                devices = self._last_devices
                        except Exception:
                            pass

                        if update_telemetry_parallel_func:
                            try:
                                update_telemetry_parallel_func(devices, timeout=1.0)
                            except Exception:
                                for dev in devices:
                                    dev.telemetry_status = "Unavailable"

                        if update_memory_func:
                            for dev in devices:
                                try:
                                    update_memory_func(dev)
                                except Exception:
                                    pass

                        for dev in devices:
                            self._graph_window.update_device(dev)

                        try:
                            reconnect_in = self._check_auto_restart(devices)
                            live.update(self.render_snapshot(devices, interactive=False,
                                                              reconnect_in=reconnect_in))
                        except Exception:
                            pass
        except KeyboardInterrupt:
            self._quit = True
            if self._kb_thread:
                self._kb_thread.join(timeout=0.5)
