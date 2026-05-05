# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
# SPDX-License-Identifier: Apache-2.0

"""
Real-time telemetry graphs for TT devices (nvtop-style).
Shows per-device line graphs with combined metrics like nvtop.
"""

import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Dict, List, Deque, Optional, Set
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich import box


def _cap_temp(arch):    return 80.0
def _cap_power(arch):   return 300.0 if "Blackhole" in arch else 100.0
def _cap_voltage(arch): return 1.2
def _cap_current(arch): return 250.0 if "Blackhole" in arch else 130.0
def _cap_aiclk(arch):   return 1500.0 if "Blackhole" in arch else 1000.0
def _cap_pct(arch):     return 100.0


AVAILABLE_METRICS = [
    ("temp",        "temperature",        "Temp °C",    "red",              _cap_temp),
    ("board_temp",  "board_temperature",  "Board °C",   "bright_red",       _cap_temp),
    ("vreg_temp",   "vreg_temperature",   "VReg °C",    "bright_magenta",   _cap_temp),
    ("power",       "power",              "Power W",    "yellow",           _cap_power),
    ("voltage",     "voltage",            "VCORE V",    "blue",             _cap_voltage),
    ("current",     "current",            "TDC A",      "magenta",          _cap_current),
    ("aiclk",       "aiclk",              "AICLK MHz",  "bright_blue",      _cap_aiclk),
    ("dram",        "dram_pct",           "DRAM %",     "cyan",             _cap_pct),
    ("l1",          "l1_pct",             "L1 %",       "green",            _cap_pct),
    ("l1_small",    "l1_small_pct",       "L1sm %",     "bright_green",     _cap_pct),
    ("trace",       "trace_pct",          "Trace %",    "bright_yellow",    _cap_pct),
    ("cb",          "cb_pct",             "CB %",       "white",            _cap_pct),
]
DEFAULT_SELECTED = {"temp", "power", "dram", "l1"}
VALID_METRIC_KEYS = {m[0] for m in AVAILABLE_METRICS}

_CONFIG_PATH = Path.home() / ".config" / "tt-mgmt" / "graphs.json"


def load_selected_metrics() -> Set[str]:
    """Read selected metrics from config; return DEFAULT_SELECTED on any error."""
    try:
        with open(_CONFIG_PATH, "r") as f:
            data = json.load(f)
        keys = data.get("selected_metrics", [])
        filtered = {k for k in keys if k in VALID_METRIC_KEYS}
        return filtered if filtered else set(DEFAULT_SELECTED)
    except Exception:
        return set(DEFAULT_SELECTED)


def save_selected_metrics(keys: Set[str]) -> None:
    """Atomically save selected metrics; swallow IO errors."""
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CONFIG_PATH.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump({"selected_metrics": sorted(keys)}, f)
        os.replace(tmp, _CONFIG_PATH)
    except Exception:
        pass


class TelemetryHistory:
    """Stores historical telemetry data for a single device."""

    def __init__(self, max_points: int = 60):
        """
        Args:
            max_points: Maximum number of data points (default 60)
        """
        self.max_points = max_points
        self.timestamps: Deque[float] = deque(maxlen=max_points)
        self.temperature: Deque[float] = deque(maxlen=max_points)      # ASIC °C
        self.board_temperature: Deque[float] = deque(maxlen=max_points) # Board °C
        self.vreg_temperature: Deque[float] = deque(maxlen=max_points)  # VReg °C
        self.power: Deque[float] = deque(maxlen=max_points)
        self.voltage: Deque[float] = deque(maxlen=max_points)           # VCORE V
        self.current: Deque[float] = deque(maxlen=max_points)           # TDC A
        self.aiclk: Deque[int] = deque(maxlen=max_points)
        self.memory_used: Deque[float] = deque(maxlen=max_points)  # Percentage
        self.dram_pct: Deque[float] = deque(maxlen=max_points)     # DRAM %
        self.l1_pct: Deque[float] = deque(maxlen=max_points)       # L1 %
        self.l1_small_pct: Deque[float] = deque(maxlen=max_points) # L1 Small %
        self.trace_pct: Deque[float] = deque(maxlen=max_points)    # Trace %
        self.cb_pct: Deque[float] = deque(maxlen=max_points)       # CB %

    def add_sample(self, device) -> None:
        """Add a telemetry sample from a device."""
        now = time.time()
        self.timestamps.append(now)

        def _safe(obj, attr, default=0.0):
            v = getattr(obj, attr, default)
            return v if v is not None and v >= 0 else default

        # Temperatures
        self.temperature.append(_safe(device, "temperature"))
        self.board_temperature.append(_safe(device, "board_temperature"))
        self.vreg_temperature.append(_safe(device, "vreg_temperature"))

        # Power
        self.power.append(_safe(device, "power"))

        # VCORE (mV → V)
        vcore_mv = _safe(device, "voltage_mv")
        self.voltage.append(vcore_mv / 1000.0 if vcore_mv > 0 else 0.0)

        # TDC current (mA → A)
        current_ma = _safe(device, "current_ma")
        self.current.append(float(current_ma) if current_ma > 0 else 0.0)

        # AICLK
        self.aiclk.append(int(_safe(device, "aiclk_mhz", 0)))

        # DRAM %
        total_dram = _safe(device, "total_dram")
        if total_dram > 0:
            dram_used = _safe(device, "used_dram") + _safe(device, "used_trace")
            self.dram_pct.append(dram_used / total_dram * 100.0)
            self.memory_used.append(dram_used / total_dram * 100.0)
        else:
            self.dram_pct.append(0.0)
            self.memory_used.append(0.0)

        # L1 sub-allocations %
        total_l1 = _safe(device, "total_l1")
        if total_l1 > 0:
            self.l1_pct.append((_safe(device, "used_l1") + _safe(device, "used_l1_small") + _safe(device, "used_cb")) / total_l1 * 100.0)
            self.l1_small_pct.append(_safe(device, "used_l1_small") / total_l1 * 100.0)
            self.trace_pct.append(_safe(device, "used_trace") / total_l1 * 100.0)
            self.cb_pct.append(_safe(device, "used_cb") / total_l1 * 100.0)
        else:
            self.l1_pct.append(0.0)
            self.l1_small_pct.append(0.0)
            self.trace_pct.append(0.0)
            self.cb_pct.append(0.0)


# (dx, dy, bit) for the 8 dots of a 2x4 braille cell — see U+2800 block.
_CELL_BITS = [
    (0, 0, 0x01), (0, 1, 0x02), (0, 2, 0x04), (0, 3, 0x40),
    (1, 0, 0x08), (1, 1, 0x10), (1, 2, 0x20), (1, 3, 0x80),
]


def _bresenham(x0, y0, x1, y1):
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        yield (x, y)
        if x == x1 and y == y1:
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def render_combined_graph(metrics: List[tuple], width: int = 60, height: int = 12) -> Text:
    """
    Render multiple metrics on the same graph using braille sub-cell dots.

    Args:
        metrics: List of (values, max_val, label, color, current_val) tuples
        width:   Graph width in characters
        height:  Graph height in lines

    Returns:
        Rich Text object with colored lines and legend overlaid
    """
    if not metrics or all(not m[0] for m in metrics):
        result = Text()
        for _ in range(height):
            result.append(" " * width + "\n")
        return result

    DOT_W = width * 2
    DOT_H = height * 4

    series = []
    for order, (values, max_val, label, color, current_val) in enumerate(metrics):
        if not values:
            continue
        if not max_val or max_val == 0:
            max_val = 100.0

        grid = [[0] * DOT_W for _ in range(DOT_H)]
        sampled = list(values)[-DOT_W:] if len(values) > DOT_W else list(values)
        n = len(sampled)
        x_scale = (DOT_W - 1) / (n - 1) if n > 1 else 0

        pts = []
        for i, v in enumerate(sampled):
            y_norm = max(0.0, min(1.0, v / max_val))
            y = int(round((1.0 - y_norm) * (DOT_H - 1)))
            y = max(0, min(DOT_H - 1, y))
            x = int(round(i * x_scale)) if n > 1 else DOT_W - 1
            pts.append((x, y))

        if len(pts) == 1:
            x, y = pts[0]
            grid[y][x] = 1
        else:
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                for (xx, yy) in _bresenham(x0, y0, x1, y1):
                    if 0 <= xx < DOT_W and 0 <= yy < DOT_H:
                        grid[yy][xx] = 1

        series.append((grid, color, order))

    out_cells = [[(" ", "white") for _ in range(width)] for _ in range(height)]
    for cy in range(height):
        for cx in range(width):
            mask = 0
            contributors = []
            base_y = cy * 4
            base_x = cx * 2
            for grid, color, order in series:
                series_mask = 0
                for (dx, dy, bit) in _CELL_BITS:
                    if grid[base_y + dy][base_x + dx]:
                        series_mask |= bit
                if series_mask:
                    mask |= series_mask
                    contributors.append((order, color))
            if mask:
                if len(contributors) == 1:
                    chosen = contributors[0][1]
                else:
                    contributors.sort()
                    chosen = contributors[cx % len(contributors)][1]
                out_cells[cy][cx] = (chr(0x2800 + mask), chosen)

    # --- Build Rich Text output ---
    result = Text()

    # Legend line — truncate so it never wraps beyond the canvas width.
    max_legend_chars = width + 2  # canvas + y-axis label column
    legend = Text()
    for idx, (values, max_val, label, color, current_val) in enumerate(metrics):
        if idx > 0:
            legend.append("  ", style="dim")
        legend.append(f"{label}: ", style="dim")
        legend.append(f"{current_val:.1f}", style=color + " bold")
    legend.truncate(max_legend_chars)
    result.append_text(legend)
    result.append("\n")

    # Y-axis labels at top / middle / bottom rows
    y_label_map = {0: "HI", height // 2: "MID", height - 1: "LO"}

    for row_idx, row in enumerate(out_cells):
        lbl = y_label_map.get(row_idx, "")
        result.append(lbl.rjust(4) + "│", style="dim")
        for char, color in row:
            result.append(char, style=color)
        if row_idx < height - 1:
            result.append("\n")

    return result


class GraphWindow:
    """
    Interactive graph window showing telemetry history (nvtop-style).
    """

    def __init__(self, console: Console, history_size: int = 100,
                 selected_metrics: Optional[Set[str]] = None):
        self.history: Dict[str, TelemetryHistory] = {}
        self.console = console
        self.history_size = history_size
        if selected_metrics is None:
            self.selected_metrics: Set[str] = load_selected_metrics()
        else:
            self.selected_metrics = set(selected_metrics)

    def set_selected_metrics(self, keys: Set[str]) -> None:
        """Replace selection and persist to config."""
        self.selected_metrics = set(keys)
        save_selected_metrics(self.selected_metrics)

    def _calculate_optimal_layout(self, num_devices: int, terminal_width: int, terminal_height: int) -> tuple:
        """
        Calculate optimal grid layout with preference for wide layouts (more columns, fewer rows).

        For monitoring dashboards, wider layouts (4×8 instead of 8×4) are preferred because:
        - More devices visible side-by-side
        - Better use of wide monitors
        - Easier horizontal scanning

        Returns:
            (rows, cols) tuple for optimal grid layout
        """
        # Simple strategy: Prefer wide layouts
        # For 32 devices: try 8 cols first, then 6, then 4

        if num_devices <= 2:
            return (1, num_devices)
        elif num_devices <= 4:
            return (2, 2)
        elif num_devices <= 8:
            return (2, 4)  # 2 rows × 4 columns (wide)
        elif num_devices <= 16:
            return (2, 8)  # 2 rows × 8 columns (very wide)
        elif num_devices <= 32:
            return (4, 8)  # 4 rows × 8 columns (WIDE for Galaxy systems)
        else:
            # For >32 devices, use 8 columns and calculate rows
            cols = 8
            rows = (num_devices + cols - 1) // cols
            return (rows, cols)

    def update_device(self, device) -> None:
        """Update telemetry history for a device."""
        device_id = device.display_id if hasattr(device, "display_id") else str(device.chip_id)

        if device_id not in self.history:
            self.history[device_id] = TelemetryHistory(max_points=self.history_size)

        self.history[device_id].add_sample(device)

    def render_device_card(self, device_id: str, device, chart_height: int = 30, chart_width: int = 60, card_width: int = 0) -> Panel:
        """
        Render a single device card with combined graphs (nvtop-style).

        Args:
            device_id: Device identifier
            device: Device object with telemetry
            chart_height: Height of chart in rows
            chart_width: Width of chart in columns (dynamic based on terminal size)
            card_width: Total card width (used to size bars so they don't wrap)
        """
        if device_id not in self.history:
            return Panel(f"No data for device {device_id}", title=device_id, border_style="cyan")

        hist = self.history[device_id]
        if len(hist.timestamps) < 2:
            return Panel(f"Collecting data...", title=f"[cyan bold]{device_id}[/]", border_style="cyan")

        layout = Table.grid(padding=0)
        layout.add_column(ratio=1)

        def fmt_sz(b):
            u = ["B", "KB", "MB", "GB", "TB"]
            i, v = 0, float(b)
            while v >= 1024 and i < len(u) - 1:
                v /= 1024.0; i += 1
            return f"{v:.1f}{u[i]}"

        temp_current  = hist.temperature[-1]  if hist.temperature  else 0
        power_current = hist.power[-1]        if hist.power        else 0
        aiclk_current = hist.aiclk[-1]        if hist.aiclk        else 0
        dram_current  = hist.dram_pct[-1]     if hist.dram_pct     else 0.0
        l1_current    = hist.l1_pct[-1]       if hist.l1_pct       else 0.0

        dram_used  = getattr(device, "used_dram", 0) + getattr(device, "used_trace", 0)
        dram_total = getattr(device, "total_dram", 0)
        dram_pct   = (dram_used / dram_total * 100.0) if dram_total > 0 else 0.0
        l1_used    = (getattr(device, "used_l1", 0) + getattr(device, "used_l1_small", 0)
                      + getattr(device, "used_cb", 0))
        l1_total   = getattr(device, "total_l1", 0)
        l1_pct     = (l1_used / l1_total * 100.0) if l1_total > 0 else 0.0

        # Content width inside the outer Panel (2 borders + 2 horizontal padding)
        content_width = card_width - 4 if card_width > 0 else 60

        # Compact header — use no_wrap to prevent line wrapping
        header = Text(no_wrap=True, overflow="ellipsis")
        header.append(getattr(device, "arch_name", "Unknown"), style="cyan bold")
        header.append("  ")
        header.append(f"T:{temp_current:.0f}°C",
                      style="red" if temp_current > 80 else "yellow" if temp_current > 70 else "green")
        header.append("  ")
        header.append(f"P:{power_current:.0f}W", style="yellow")
        header.append("  ")
        header.append(f"~{aiclk_current}MHz", style="blue")
        layout.add_row(header)

        # Dynamic bar width: fit "DRAM [" (6) + bar + "] " (2) + suffix within content_width
        dram_suffix = f"{fmt_sz(dram_used)}/{fmt_sz(dram_total)}"
        l1_suffix = f"{fmt_sz(l1_used)}/{fmt_sz(l1_total)}"
        bar_overhead = 6 + 2  # "DRAM [" + "] "
        max_suffix = max(len(dram_suffix), len(l1_suffix))
        bar_width = max(5, content_width - bar_overhead - max_suffix)

        # DRAM bar
        dbar_len = int(dram_pct / 100.0 * bar_width)
        dram_bar = Text(no_wrap=True, overflow="ellipsis")
        dram_bar.append("DRAM [", style="white")
        dram_bar.append("|" * dbar_len, style="green")
        dram_bar.append(" " * (bar_width - dbar_len), style="dim")
        dram_bar.append(f"] {dram_suffix}", style="white")
        layout.add_row(dram_bar)

        # L1 bar
        l1bar_len = int(l1_pct / 100.0 * bar_width)
        l1_bar = Text(no_wrap=True, overflow="ellipsis")
        l1_bar.append("L1   [", style="white")
        l1_bar.append("|" * l1bar_len, style="green")
        l1_bar.append(" " * (bar_width - l1bar_len), style="dim")
        l1_bar.append(f"] {l1_suffix}", style="white")
        layout.add_row(l1_bar)

        layout.add_row("")

        metrics = []
        arch = getattr(device, "arch_name", "")
        for key, attr, label, color, cap_fn in AVAILABLE_METRICS:
            if key not in self.selected_metrics:
                continue
            series = list(getattr(hist, attr))
            if not series:
                continue
            cap = cap_fn(arch)
            current_val = series[-1]
            metrics.append((series, cap, label, color, current_val))

        if not metrics:
            note = Text("No metrics selected — press 'm' to choose", style="dim")
            graph_panel = Panel(note, title="[cyan]Telemetry & Memory History[/]", border_style="cyan")
        else:
            combined_graph = render_combined_graph(metrics, width=chart_width, height=chart_height)
            graph_panel = Panel(combined_graph, title="[cyan]Telemetry & Memory History[/]", border_style="cyan")
        layout.add_row(graph_panel)

        return Panel(layout, title=f"[cyan bold]{device_id}[/]", border_style="cyan", padding=(0, 1))

    def render_all_devices(self, devices: List) -> Layout:
        """Render graphs for all devices in a grid layout (matrix style)."""
        root_layout = Layout()

        if len(devices) == 0:
            return Layout(Panel("No devices tracked yet", title="Telemetry Graphs"))

        # Get terminal size for dynamic height and width calculation
        terminal_height = self.console.height
        terminal_width = self.console.width
        num_devices = len(devices)

        # Calculate chart height based on terminal size and number of devices
        # Account for: header (3), device headers (~6 per device), panel borders (~2 per device), legend (~1)
        header_overhead = 3
        per_device_overhead = 9  # 2 outer borders + 4 rows (header/dram/l1/blank) + 2 graph-panel borders + 1 legend

        # Intelligently determine optimal rows and columns based on terminal size
        # This maximizes chart visibility while ensuring readability
        rows, cols = self._calculate_optimal_layout(num_devices, terminal_width, terminal_height)

        # Calculate available height per device.
        # IMPORTANT: the hard minimum must NOT exceed height_per_device - per_device_overhead,
        # otherwise the rendered card (chart_height + per_device_overhead lines) will be taller
        # than the Layout slot and the bottom of the card will be clipped.
        # Use max(3, ...) so the chart always has at least a few visible rows on small terminals
        # without ever causing overflow on larger ones.
        available_height = terminal_height - header_overhead
        height_per_device = available_height // rows
        chart_height = max(3, height_per_device - per_device_overhead)

        # Calculate available width per device.
        # Overhead breakdown (must be exact to avoid overflow → line-folding artifacts):
        #   outer device panel:  2 borders + 2 padding = 4
        #   inner graph panel:   2 borders + 2 padding = 4
        #   y-axis label+sep:    4 digits + "│"        = 5
        #   total:                                      = 13
        # IMPORTANT: the minimum floor must NOT exceed available_width - overhead,
        # otherwise the rendered content overflows the panel and Rich folds/truncates
        # lines, producing garbled characters (same class of bug as the height floor).
        per_device_width_overhead = 13
        available_width = terminal_width // cols
        chart_width = max(10, available_width - per_device_width_overhead)

        # Header
        root_layout.split_column(Layout(name="header", size=3), Layout(name="devices"))

        root_layout["header"].update(
            Panel(
                f"[cyan bold]TT-SMI Telemetry ({len(devices)} devices)[/]  Press Ctrl+C to return to table view",
                style="cyan",
            )
        )

        # Create device cards with dynamic height and width
        device_panels = []
        for dev in devices:
            device_id = dev.display_id if hasattr(dev, "display_id") else str(dev.chip_id)
            device_panels.append(self.render_device_card(device_id, dev, chart_height, chart_width, card_width=available_width))

        # Apply the calculated layout dynamically (rows × cols)
        num_devices = len(device_panels)

        if num_devices == 1:
            root_layout["devices"].update(device_panels[0])
        elif num_devices == 2:
            # 1×2 layout
            root_layout["devices"].split_row(Layout(device_panels[0]), Layout(device_panels[1]))
        else:
            # Use calculated rows/cols for optimal layout
            # For 32 devices: rows=4, cols=8 → 4 rows × 8 columns (WIDE)

            # Create column layouts
            col_names = [f"col{i}" for i in range(1, cols + 1)]
            root_layout["devices"].split_row(*[Layout(name=name) for name in col_names])

            # Distribute devices across columns (fills row-by-row)
            for col_idx, col_name in enumerate(col_names):
                # Each column gets devices at positions: col_idx, col_idx+cols, col_idx+2*cols, ...
                col_devices = [device_panels[i] for i in range(col_idx, num_devices, cols)]
                if col_devices:
                    root_layout[col_name].split_column(*[Layout(d) for d in col_devices])

        return root_layout
