# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
# SPDX-License-Identifier: Apache-2.0

"""CLI command: tt-mgmt plot — visualize recorded metrics."""

import json
import os
import statistics
import webbrowser
from collections import defaultdict
from pathlib import Path

import click


def _load_jsonl(path: str):
    """Load a JSONL recording, returning (meta, samples, footer)."""
    meta = None
    footer = None
    samples = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("_meta"):
                meta = obj
            elif obj.get("_footer"):
                footer = obj
            else:
                samples.append(obj)
    return meta, samples, footer


def _is_per_pid_format(samples: list) -> bool:
    """Return True if samples come from a per-pid JSONL file (flat process records)."""
    for s in samples[:5]:
        if "pid" in s and ("cpu_percent" in s or "vm_rss_kb" in s):
            return True
    return False


def _extract_series(samples, device_filter=None, pid_filter=None):
    """Extract per-device and per-process time series from samples.

    Handles two JSONL formats:
      • Main format  — {"ts":…, "devices":[{telemetry, memory, processes:[…]}]}
      • Per-pid format — {"ts":…, "pid":…, "cpu_percent":…, "devices":[{dram,l1,…}]}

    Returns:
        timestamps: list of float
        device_series: dict[device_id] -> dict[metric_name] -> list[value]
        process_series: dict[pid] -> dict[metric_name] -> list[(ts, value)]
    """
    timestamps = []
    device_series = defaultdict(lambda: defaultdict(list))
    process_series = defaultdict(lambda: defaultdict(list))

    per_pid = _is_per_pid_format(samples)

    for sample in samples:
        ts = sample.get("ts", 0)
        timestamps.append(ts)

        if per_pid:
            # ── Per-pid format ────────────────────────────────────────────────
            pid = sample.get("pid")
            if not pid:
                continue
            if pid_filter is not None and pid not in pid_filter:
                continue

            # Process-level metrics (one per sample)
            ps = process_series[pid]
            ps["_name"] = sample.get("name", str(pid))
            ps["_cmdline"] = sample.get("cmdline", "")
            ps["cpu_percent"].append((ts, sample.get("cpu_percent")))
            ps["vm_rss_kb"].append((ts, sample.get("vm_rss_kb")))
            ps["vm_virt_kb"].append((ts, sample.get("vm_virt_kb")))
            ps["num_threads"].append((ts, sample.get("num_threads")))
            ps["runtime"].append((ts, sample.get("runtime")))

            # Device telemetry/memory (from each dev_entry) and per-device process allocations
            for dev_entry in sample.get("devices", []):
                dev_id = dev_entry.get("display_id") or dev_entry.get("chip_id", "?")
                telem = dev_entry.get("telemetry", {})
                mem = dev_entry.get("memory", {})

                # Device-level series (telemetry + memory)
                d_series = device_series[dev_id]
                d_series["temperature"].append(telem.get("temperature"))
                d_series["power"].append(telem.get("power"))
                d_series["input_power_w"].append(telem.get("input_power_w"))
                d_series["voltage_mv"].append(telem.get("voltage_mv"))
                d_series["current_ma"].append(telem.get("current_ma"))
                d_series["tdc_limit_a"].append(telem.get("tdc_limit_a"))
                d_series["tdp_limit_w"].append(telem.get("tdp_limit_w"))
                d_series["aiclk_mhz"].append(telem.get("aiclk_mhz"))
                d_series["aiclk_limit_mhz"].append(telem.get("aiclk_limit_mhz"))
                d_series["fan_speed_rpm"].append(telem.get("fan_speed_rpm"))
                d_series["used_dram"].append(mem.get("used_dram"))
                d_series["used_trace"].append(mem.get("used_trace"))
                d_series["used_l1"].append(mem.get("used_l1"))
                d_series["used_l1_small"].append(mem.get("used_l1_small"))
                d_series["used_cb"].append(mem.get("used_cb"))
                d_series["total_dram"].append(mem.get("total_dram"))
                d_series["total_l1"].append(mem.get("total_l1"))

                # Per-device process allocations (keyed by pid@dev_id to avoid mixing)
                proc_dev_key = f"{pid}@{dev_id}"
                ps_dev = process_series[proc_dev_key]
                ps_dev["_name"] = f"{sample.get('name', pid)} ({dev_id})"
                ps_dev["_cmdline"] = sample.get("cmdline", "")
                ps_dev["dram"].append((ts, dev_entry.get("dram")))
                ps_dev["l1"].append((ts, dev_entry.get("l1")))
                ps_dev["l1_small"].append((ts, dev_entry.get("l1_small")))
                ps_dev["trace"].append((ts, dev_entry.get("trace")))
                ps_dev["cb"].append((ts, dev_entry.get("cb")))

        else:
            # ── Main format ───────────────────────────────────────────────────
            for dev_idx, dev in enumerate(sample.get("devices", [])):
                if device_filter is not None and dev_idx not in device_filter:
                    continue
                dev_id = dev.get("display_id") or dev.get("chip_id", "?")

                telem = dev.get("telemetry", {})
                mem   = dev.get("memory", {})

                series = device_series[dev_id]
                # Telemetry
                series["temperature"].append(telem.get("temperature"))
                series["power"].append(telem.get("power"))
                series["input_power_w"].append(telem.get("input_power_w"))
                series["voltage_mv"].append(telem.get("voltage_mv"))
                series["current_ma"].append(telem.get("current_ma"))
                series["tdc_limit_a"].append(telem.get("tdc_limit_a"))
                series["tdp_limit_w"].append(telem.get("tdp_limit_w"))
                series["aiclk_mhz"].append(telem.get("aiclk_mhz"))
                series["aiclk_limit_mhz"].append(telem.get("aiclk_limit_mhz"))
                series["fan_speed_rpm"].append(telem.get("fan_speed_rpm"))
                # Memory
                series["used_dram"].append(mem.get("used_dram"))
                series["used_trace"].append(mem.get("used_trace"))
                series["used_l1"].append(mem.get("used_l1"))
                series["used_l1_small"].append(mem.get("used_l1_small"))
                series["used_cb"].append(mem.get("used_cb"))
                series["total_dram"].append(mem.get("total_dram"))
                series["total_l1"].append(mem.get("total_l1"))

                for proc in dev.get("processes", []):
                    pid = proc.get("pid")
                    if not pid:
                        continue
                    if pid_filter is not None and pid not in pid_filter:
                        continue
                    ps = process_series[pid]
                    ps["_name"]    = proc.get("name", str(pid))
                    ps["_cmdline"] = proc.get("cmdline", "")
                    ps["cpu_percent"].append((ts, proc.get("cpu_percent")))
                    ps["vm_rss_kb"].append((ts, proc.get("vm_rss_kb")))
                    ps["vm_virt_kb"].append((ts, proc.get("vm_virt_kb")))
                    ps["num_threads"].append((ts, proc.get("num_threads")))
                    ps["dram"].append((ts, proc.get("dram")))
                    ps["l1"].append((ts, proc.get("l1")))
                    ps["l1_small"].append((ts, proc.get("l1_small")))
                    ps["trace"].append((ts, proc.get("trace")))
                    ps["cb"].append((ts, proc.get("cb")))

    return timestamps, dict(device_series), dict(process_series)


def _stats(values):
    """Compute min/avg/max/last for a list of numeric values (None-safe)."""
    clean = [v for v in values if v is not None and v >= 0]
    if not clean:
        return {"min": "-", "avg": "-", "max": "-", "last": "-"}
    return {
        "min": round(min(clean), 1),
        "avg": round(statistics.mean(clean), 1),
        "max": round(max(clean), 1),
        "last": round(clean[-1], 1),
    }


def _render_terminal_summary(meta, samples, timestamps, device_series, process_series, console):
    """Print a rich-table summary to the terminal."""
    from rich.table import Table
    from tt_mgmt import ui

    n = len(samples)
    if not n:
        console.print("[red]No samples found in recording.[/red]")
        return

    t0, t1 = timestamps[0], timestamps[-1]
    elapsed = t1 - t0
    interval = meta.get("interval_ms", "?") if meta else "?"

    console.print(f"\n[bold cyan]Recording Summary[/bold cyan]")
    console.print(f"  Samples: {n}  |  Duration: {elapsed:.1f}s  |  Interval: {interval}ms")
    if meta:
        console.print(f"  Host: {meta.get('hostname', '?')}  |  Started: {meta.get('start_time', '?')}")
        if meta.get("pid_filter"):
            console.print(f"  PID filter: {meta['pid_filter']}")
    console.print()

    # Device telemetry summary
    table = Table(title="Device Telemetry Summary", box=ui.get_box(), show_header=True, header_style="bold")
    table.add_column("Device", style="cyan")
    table.add_column("Metric")
    table.add_column("Min", justify="right")
    table.add_column("Avg", justify="right")
    table.add_column("Max", justify="right")
    table.add_column("Last", justify="right")

    metrics_to_show = [
        ("temperature",      "ASIC Temp (°C)"),
        ("power",             "Power (W)"),
        ("voltage_mv",        "VCORE (V)"),
        ("current_ma",        "TDC (A)"),
        ("aiclk_mhz",         "AICLK (MHz)"),
        ("used_dram",         "DRAM Used"),
        ("used_l1",           "L1 Used"),
        ("used_l1_small",     "L1 Small Used"),
        ("used_trace",        "Trace Used"),
        ("used_cb",           "CB Used"),
    ]

    _MEM_KEYS = {"used_dram", "used_l1", "used_l1_small", "used_trace", "used_cb"}
    _MV_KEYS   = {"voltage_mv"}
    _MA_KEYS   = {"current_ma"}

    for dev_id, series in sorted(device_series.items()):
        first = True
        for metric_key, metric_label in metrics_to_show:
            vals = series.get(metric_key, [])
            clean = [v for v in vals if v is not None and v >= 0]
            if not clean:
                continue
            s = _stats(vals)
            if s["min"] == "-":
                continue
            # Unit conversions for display
            if metric_key in _MEM_KEYS:
                for k in ("min", "avg", "max", "last"):
                    s[k] = f"{float(s[k]) / 1024 / 1024:.1f} MiB"
            elif metric_key in _MV_KEYS:
                for k in ("min", "avg", "max", "last"):
                    s[k] = f"{float(s[k]):.3f} mV"
            elif metric_key in _MA_KEYS:
                for k in ("min", "avg", "max", "last"):
                    s[k] = f"{float(s[k]):.1f} A"
            table.add_row(dev_id if first else "", metric_label,
                          str(s["min"]), str(s["avg"]), str(s["max"]), str(s["last"]))
            first = False
        table.add_section()

    console.print(table)

    # Process summary
    if not process_series:
        console.print("[dim]No process data found. If you used --per-pid-files, "
                      "run: tt-mgmt plot run_<pid>.jsonl[/dim]")

    if process_series:
        ptable = Table(title="Process Summary", box=ui.get_box(), show_header=True, header_style="bold")
        ptable.add_column("PID",          style="cyan")
        ptable.add_column("Name")
        ptable.add_column("CPU% avg",     justify="right")
        ptable.add_column("CPU% max",     justify="right")
        ptable.add_column("RSS max",      justify="right")
        ptable.add_column("Virt max",     justify="right")
        ptable.add_column("Threads max",  justify="right")
        ptable.add_column("DRAM max",     justify="right")
        ptable.add_column("L1 max",       justify="right")
        ptable.add_column("L1 Sm max",    justify="right")
        ptable.add_column("Trace max",    justify="right")
        ptable.add_column("CB max",       justify="right")

        def _mib(vals): return f"{max(vals)/1024/1024:.1f} MiB" if vals else "-"
        def _kib_to_mib(vals): return f"{max(vals)/1024:.0f} MiB" if vals else "-"

        for pid, ps in sorted(process_series.items(), key=lambda x: str(x[0])):
            name = ps.get("_name", str(pid))
            cpu_vals  = [v for _, v in ps.get("cpu_percent", []) if v is not None]
            rss_vals  = [v for _, v in ps.get("vm_rss_kb",   []) if v is not None]
            virt_vals = [v for _, v in ps.get("vm_virt_kb",  []) if v is not None]
            thr_vals  = [v for _, v in ps.get("num_threads", []) if v is not None]
            dram_vals = [v for _, v in ps.get("dram",     []) if v is not None]
            l1_vals   = [v for _, v in ps.get("l1",       []) if v is not None]
            l1s_vals  = [v for _, v in ps.get("l1_small", []) if v is not None]
            tr_vals   = [v for _, v in ps.get("trace",    []) if v is not None]
            cb_vals   = [v for _, v in ps.get("cb",       []) if v is not None]

            ptable.add_row(
                str(pid), name,
                f"{statistics.mean(cpu_vals):.1f}" if cpu_vals else "-",
                f"{max(cpu_vals):.1f}" if cpu_vals else "-",
                _kib_to_mib(rss_vals),
                _kib_to_mib(virt_vals),
                f"{max(thr_vals)}" if thr_vals else "-",
                _mib(dram_vals), _mib(l1_vals), _mib(l1s_vals), _mib(tr_vals), _mib(cb_vals),
            )

        console.print(ptable)


def _load_template():
    """Read the plotter_template.html shipped alongside this module."""
    tpl_path = Path(__file__).with_name("plotter_template.html")
    return tpl_path.read_text(encoding="utf-8")


def _build_tt_data(meta, timestamps, device_series, process_series):
    """Build the TT_DATA dict expected by the template's OfflineAdapter."""
    if not timestamps:
        return {}
    t0 = timestamps[0]
    rel = [round(t - t0, 3) for t in timestamps]
    elapsed = round(timestamps[-1] - t0, 1) if len(timestamps) > 1 else 0
    MiB = 1024 * 1024

    data_meta = {}
    if meta:
        data_meta = {
            "hostname": meta.get("hostname", ""),
            "start_time": meta.get("start_time", ""),
            "interval_ms": meta.get("interval_ms"),
            "duration": elapsed,
        }

    devices = {}
    for dev_id, series in sorted(device_series.items()):
        d = {}
        def _copy(src_key, dst_key, divisor=None, scale=None):
            vals = series.get(src_key, [])
            if divisor:
                d[dst_key] = [round(v / divisor, 3) if v is not None else None for v in vals]
            elif scale:
                d[dst_key] = [round(v * scale, 4) if v is not None else None for v in vals]
            else:
                d[dst_key] = vals

        _copy("temperature",      "temperature")
        _copy("power",            "power")
        _copy("input_power_w",    "input_power_w")
        _copy("voltage_mv",       "voltage_v",       scale=0.001)
        _copy("current_ma",       "current_a")
        _copy("aiclk_mhz",        "aiclk_mhz")
        _copy("fan_speed_rpm",     "fan_speed_rpm")
        _copy("used_dram",        "used_dram_mib",    divisor=MiB)
        _copy("used_trace",       "used_trace_mib",   divisor=MiB)
        _copy("used_l1",          "used_l1_mib",      divisor=MiB)
        _copy("used_l1_small",    "used_l1_small_mib",divisor=MiB)
        _copy("used_cb",          "used_cb_mib",      divisor=MiB)
        devices[dev_id] = d

    processes = {}
    for pid, ps in sorted(process_series.items(), key=lambda x: str(x[0])):
        p = {
            "name": ps.get("_name", str(pid)),
            "cmdline": ps.get("_cmdline", ""),
        }
        def _proc_copy(src_key, dst_key, divisor=None):
            pts = ps.get(src_key, [])
            if divisor:
                p[dst_key] = [round(v / divisor, 3) if v is not None else None for _, v in pts]
            else:
                p[dst_key] = [v for _, v in pts]

        _proc_copy("cpu_percent",  "cpu_percent")
        _proc_copy("vm_rss_kb",    "vm_rss_mib",   divisor=1024)
        _proc_copy("vm_virt_kb",   "vm_virt_mib",  divisor=1024)
        _proc_copy("num_threads",  "num_threads")
        _proc_copy("dram",         "dram_mib",     divisor=MiB)
        _proc_copy("l1",           "l1_mib",       divisor=MiB)
        _proc_copy("l1_small",     "l1_small_mib", divisor=MiB)
        _proc_copy("trace",        "trace_mib",    divisor=MiB)
        _proc_copy("cb",           "cb_mib",       divisor=MiB)
        processes[pid] = p

    return {
        "meta": data_meta,
        "timestamps": rel,
        "devices": devices,
        "processes": processes,
    }


def _generate_html(meta, samples, timestamps, device_series, process_series, output_path):
    """Generate a self-contained HTML file with the interactive plotter template."""
    tt_data = _build_tt_data(meta, timestamps, device_series, process_series)
    template = _load_template()
    inject = f"window.TT_DATA = {json.dumps(tt_data, separators=(',', ':'))};"
    html = template.replace(
        "window.TT_DATA = window.TT_DATA || null;",
        inject,
        1,
    )
    with open(output_path, "w") as f:
        f.write(html)


def _generate_live_html(daemon_url, interval_ms, output_path):
    """Generate an HTML file configured for live polling."""
    template = _load_template()
    inject = json.dumps({"url": daemon_url, "interval": interval_ms}, separators=(",", ":"))
    html = template.replace(
        "window.TT_LIVE = window.TT_LIVE || null;",
        f"window.TT_LIVE = {inject};",
        1,
    )
    with open(output_path, "w") as f:
        f.write(html)


@click.command()
@click.argument("recording", type=click.Path(exists=True), required=False, default=None)
@click.option("-o", "--output", type=str, default=None,
              help="Output HTML file path. Default: <recording>.html")
@click.option("--device", type=str, default=None,
              help="Comma-separated device indices to include in the plot. Default: all.")
@click.option("--pid", type=str, default=None,
              help="Comma-separated PIDs to include in process charts. Default: all.")
@click.option("--terminal", "-t", is_flag=True,
              help="Print a summary table to the terminal instead of generating HTML.")
@click.option("--no-open", is_flag=True,
              help="Don't auto-open the HTML file in a browser.")
@click.option("--live", is_flag=True,
              help="Generate a live-polling HTML dashboard (no recording file needed).")
@click.option("--daemon-url", type=str, default="http://localhost:7856/api/v1/devices",
              help="Daemon REST URL for --live mode. Default: http://localhost:7856/api/v1/devices")
@click.option("--interval", type=int, default=1000,
              help="Polling interval in ms for --live mode. Default: 1000")
@click.pass_context
def plot(ctx, recording, output, device, pid, terminal, no_open, live, daemon_url, interval):
    """Plot recorded metrics from a JSONL recording file, or launch a live dashboard.

    Reads a recording created by `tt-mgmt record` and generates either
    an interactive HTML report (default) or a terminal summary table.
    With --live, generates a live-polling dashboard that connects to
    the tt-mgmt daemon.

    \b
    Examples:
        # Interactive drag-and-drop plotter from a recording
        tt-mgmt plot run.jsonl

        # Save to specific file, don't open browser
        tt-mgmt plot run.jsonl -o report.html --no-open

        # Quick terminal summary (no files generated)
        tt-mgmt plot run.jsonl --terminal

        # Live dashboard polling the daemon every 500ms
        tt-mgmt plot --live --interval 500

        # Live dashboard against a remote host
        tt-mgmt plot --live --daemon-url http://192.168.1.10:7856/api/v1/devices
    """
    console = ctx.obj.get("console")
    if console is None:
        from tt_mgmt import ui
        console = ui.get_console()

    # ── Live mode ─────────────────────────────────────────────────────────
    if live:
        out = output or "tt-live-dashboard.html"
        _generate_live_html(daemon_url, interval, out)
        console.print(f"[green]Wrote live dashboard → {out}[/green]")
        console.print(f"  Polling: {daemon_url}  every {interval}ms")
        if not no_open:
            try:
                webbrowser.open(f"file://{os.path.abspath(out)}")
            except Exception:
                pass
        return

    # ── Offline mode (recording file required) ────────────────────────────
    if recording is None:
        console.print("[red]A recording file is required (or use --live).[/red]")
        raise click.Abort()

    meta, samples, footer = _load_jsonl(recording)

    if not samples:
        console.print("[red]No samples found in recording file.[/red]")
        raise click.Abort()

    device_filter = None
    if device:
        try:
            device_filter = set(int(x.strip()) for x in device.split(","))
        except ValueError:
            console.print("[red]--device must be comma-separated integers[/red]")
            raise click.Abort()

    pid_filter = None
    if pid:
        try:
            pid_filter = set(int(x.strip()) for x in pid.split(","))
        except ValueError:
            console.print("[red]--pid must be comma-separated integers[/red]")
            raise click.Abort()

    timestamps, device_series, process_series = _extract_series(
        samples, device_filter=device_filter, pid_filter=pid_filter,
    )

    if terminal:
        _render_terminal_summary(meta, samples, timestamps, device_series, process_series, console)
        return

    # Generate HTML
    if output is None:
        output = str(Path(recording).with_suffix(".html"))

    _generate_html(meta, samples, timestamps, device_series, process_series, output)
    console.print(f"[green]Wrote {output}[/green]  ({len(samples)} samples, {len(device_series)} devices)")

    if not no_open:
        try:
            webbrowser.open(f"file://{os.path.abspath(output)}")
        except Exception:
            pass
