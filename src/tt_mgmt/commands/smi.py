"""SMI (System Management Interface) commands - Device monitoring and telemetry."""

import re

import click


def _parse_interval(value: str) -> int:
    """Parse a human-friendly duration ('500ms', '1s', '2s') or bare int (ms) into ms."""
    if value is None:
        return 500
    s = str(value).strip().lower()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(ms|s)?", s)
    if not m:
        raise click.BadParameter(f"Invalid interval {value!r}. Use e.g. 500ms, 1s, 2s.")
    n = float(m.group(1))
    unit = m.group(2) or "ms"
    ms = int(n * 1000) if unit == "s" else int(n)
    if ms < 1:
        raise click.BadParameter("Interval must be at least 1ms.")
    return ms


def _load_backend(console):
    try:
        from tt_mgmt.backend.smi import (
            cleanup_dead_processes,
            get_devices,
            update_memory,
            update_telemetry_parallel,
        )
        from tt_mgmt.backend.smi.ui import Dashboard
        return cleanup_dead_processes, get_devices, update_memory, update_telemetry_parallel, Dashboard
    except ImportError as e:
        console.print(f"[red]Error: SMI backend not available: {e}[/red]")
        console.print("[yellow]The C++ backend may not be built yet.[/yellow]")
        raise click.Abort()


def _refresh_devices(get_devices, update_telemetry_parallel, update_memory, *, quiet, console):
    devices = get_devices()
    if not devices:
        console.print("[red]No Tenstorrent devices found![/red]")
        raise click.Abort()
    try:
        update_telemetry_parallel(devices, timeout=1.0)
    except Exception as e:
        if not quiet:
            console.print(f"[yellow]Warning: Telemetry update failed: {e}[/yellow]")
    for dev in devices:
        try:
            update_memory(dev)
        except Exception:
            pass
    return devices


def _devices_to_json(devices):
    return {
        "devices": [
            {
                "id": dev.display_id,
                "arch": dev.arch_name,
                "is_remote": dev.is_remote,
                "telemetry": {
                    "temperature": dev.temperature,
                    "power": dev.power,
                    "aiclk_mhz": dev.aiclk_mhz,
                    "status": dev.telemetry_status,
                },
                "memory": {
                    "dram": {"used": dev.used_dram, "total": dev.total_dram},
                    "l1": {"used": dev.used_l1, "total": dev.total_l1},
                    "l1_small": dev.used_l1_small,
                    "trace": dev.used_trace,
                    "cb": dev.used_cb,
                },
                "processes": dev.processes,
            }
            for dev in devices
        ]
    }


def _run_monitor(ctx, interval, graph, ascii_mode, no_clear, metrics):
    console = ctx.obj['console']
    refresh_ms = _parse_interval(interval)
    cleanup_dead_processes, get_devices, update_memory, update_telemetry_parallel, Dashboard = _load_backend(console)

    selected_metrics = None
    if metrics is not None:
        from tt_mgmt.backend.smi.ui.graphs import VALID_METRIC_KEYS, DEFAULT_SELECTED
        requested = {tok.strip() for tok in metrics.split(",") if tok.strip()}
        valid = requested & VALID_METRIC_KEYS
        if not valid:
            console.print(
                f"[yellow]Warning: --metrics had no valid keys "
                f"(got: {sorted(requested)}); falling back to defaults.[/yellow]"
            )
            selected_metrics = set(DEFAULT_SELECTED)
        else:
            selected_metrics = valid

    try:
        cleanup_dead_processes()

        _refresh_devices(
            get_devices, update_telemetry_parallel, update_memory,
            quiet=True, console=console,
        )

        if ascii_mode:
            from tt_mgmt.backend.smi.ui import ascii_monitor
            ascii_monitor.watch(
                get_devices,
                refresh_ms=refresh_ms,
                update_telemetry_parallel_func=update_telemetry_parallel,
                update_memory_func=update_memory,
                no_clear=no_clear,
            )
            return

        Dashboard(console, selected_metrics=selected_metrics).watch(
            lambda: get_devices(),
            refresh_ms=refresh_ms,
            update_telemetry_parallel_func=update_telemetry_parallel,
            update_memory_func=update_memory,
            start_tab=4 if graph else None,
        )
    except click.Abort:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise click.Abort()


@click.group(invoke_without_command=True)
@click.option("-i", "--interval", "interval", default="500ms", show_default=True,
              help="Refresh interval (e.g. 500ms, 1s, 2s).")
@click.option("-g", "--graph", is_flag=True, help="Start on the Graphs tab.")
@click.option("-a", "--ascii", "ascii_mode", is_flag=True,
              help="Plain ASCII output (no Rich, no color).")
@click.option("--no-clear", is_flag=True,
              help="In --ascii mode, append each refresh instead of repainting.")
@click.option("--metrics", default=None,
              help="Comma-separated metrics to plot (e.g. temp,power,aiclk). "
                   "Overrides saved selection for this run only.")
@click.pass_context
def smi(ctx, interval, graph, ascii_mode, no_clear, metrics):
    """System Management Interface - Live device dashboard.

    Running `tt-mgmt smi` (no subcommand) launches the live TUI dashboard,
    refreshing until you press Ctrl-C — the way `nvidia-smi` / `htop` work.

    \b
        tt-mgmt smi              Live dashboard
        tt-mgmt smi status       One-shot snapshot (prints and exits)
        tt-mgmt smi cleanup      Remove stale shared-memory entries

    Examples:

        \b
        tt-mgmt smi                      # Live dashboard
        tt-mgmt smi -i 1s                # Live dashboard, 1s refresh
        tt-mgmt smi -g                   # Start on the Graphs tab
        tt-mgmt smi status               # Snapshot
        tt-mgmt smi status --json        # Snapshot as JSON (for scripts)
    """
    if ctx.invoked_subcommand is None:
        _run_monitor(ctx, interval, graph, ascii_mode, no_clear, metrics)


@smi.command()
@click.option("--json", "output_json", is_flag=True, help="Print a JSON snapshot (for scripts).")
@click.option("-a", "--ascii", "ascii_mode", is_flag=True,
              help="Plain ASCII output (no Rich, no color) — nvidia-smi style.")
@click.pass_context
def status(ctx, output_json, ascii_mode):
    """One-shot device snapshot (temperature, power, memory, processes).

    Prints once and exits. For a live dashboard, use `tt-mgmt smi`.
    """
    console = ctx.obj['console']
    cleanup_dead_processes, get_devices, update_memory, update_telemetry_parallel, Dashboard = _load_backend(console)

    try:
        cleaned = cleanup_dead_processes()
        if cleaned > 0:
            console.print(f"[green]Cleaned up {cleaned} dead process(es)[/green]")

        devices = _refresh_devices(
            get_devices, update_telemetry_parallel, update_memory,
            quiet=False, console=console,
        )

        if output_json:
            console.print_json(data=_devices_to_json(devices))
            return

        if ascii_mode:
            from tt_mgmt.backend.smi.ui import ascii_monitor
            ascii_monitor.print_snapshot(devices)
            return

        Dashboard(console).print_snapshot(devices)
    except click.Abort:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise click.Abort()


@smi.command()
@click.pass_context
def cleanup(ctx):
    """Clean up dead processes from shared memory.

    Removes entries for processes that are no longer running but still
    have allocations tracked in shared memory.
    """
    console = ctx.obj['console']

    try:
        from tt_mgmt.backend.smi import cleanup_dead_processes

        cleaned = cleanup_dead_processes()
        console.print(f"[green]✓ Cleaned up {cleaned} dead process(es)[/green]")
    except ImportError as e:
        console.print(f"[red]Error: SMI backend not available: {e}[/red]")
        console.print("[yellow]Run 'pip install -e .' to build the C++ backend.[/yellow]")
        raise click.Abort()
