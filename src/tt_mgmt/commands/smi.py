"""SMI (System Management Interface) commands - Device monitoring and telemetry."""

import click
from rich.console import Console


@click.group()
def smi():
    """System Management Interface - Device monitoring and telemetry.
    
    Monitor devices, memory usage, processes, and telemetry in real-time.
    Similar to nvidia-smi for Tenstorrent devices.
    
    Examples:
    
        \b
        # Single snapshot
        tt-mgmt smi monitor
        
        \b
        # Watch mode (live updates)
        tt-mgmt smi monitor -w
        
        \b
        # Watch mode with graphs
        tt-mgmt smi monitor -w -g
        
        \b
        # JSON output
        tt-mgmt smi monitor --json
        
        \b
        # Show telemetry only
        tt-mgmt smi telemetry
        
        \b
        # Show memory stats only
        tt-mgmt smi memory
        
        \b
        # List processes
        tt-mgmt smi processes
        
        \b
        # Clean up dead processes
        tt-mgmt smi cleanup
    """
    pass


@smi.command()
@click.option("-w", "--watch", is_flag=True, help="Watch mode (continuous updates)")
@click.option("-r", "--refresh", default=500, type=int, help="Refresh interval in milliseconds")
@click.option("-g", "--graph", is_flag=True, help="Start on Graphs tab (watch mode only)")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def monitor(ctx, watch, refresh, graph, output_json):
    """Full monitoring dashboard (temperature, power, memory, processes).
    
    This is the main SMI monitoring interface, similar to nvidia-smi.
    Shows device telemetry, memory utilization, and running processes.
    
    Examples:
    
        \b
        # Single snapshot
        tt-mgmt smi monitor
        
        \b
        # Watch mode (updates every 500ms)
        tt-mgmt smi monitor -w
        
        \b
        # Watch mode with graphs
        tt-mgmt smi monitor -w -g
        
        \b
        # Custom refresh rate
        tt-mgmt smi monitor -w -r 1000
        
        \b
        # JSON output for automation
        tt-mgmt smi monitor --json
    """
    console = ctx.obj['console']
    
    try:
        from tt_mgmt.backend.smi import get_devices, update_telemetry_parallel, update_memory, cleanup_dead_processes
        from tt_mgmt.backend.smi.ui import Dashboard
    except ImportError as e:
        console.print(f"[red]Error: SMI backend not available: {e}[/red]")
        console.print("[yellow]The C++ backend may not be built yet.[/yellow]")
        raise click.Abort()

    try:
        cleaned = cleanup_dead_processes()
        if cleaned > 0 and not watch:
            console.print(f"[green]Cleaned up {cleaned} dead process(es)[/green]")

        devices = get_devices()

        if not devices:
            console.print("[red]No Tenstorrent devices found![/red]")
            raise click.Abort()

        try:
            update_telemetry_parallel(devices, timeout=1.0)
        except Exception as e:
            if not watch:
                console.print(f"[yellow]Warning: Telemetry update failed: {e}[/yellow]")

        for dev in devices:
            try:
                update_memory(dev)
            except Exception:
                pass

        if output_json:
            import json
            data = {
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
            console.print_json(data=data)
            return

        dashboard = Dashboard(console)

        if watch:
            dashboard.watch(
                lambda: get_devices(),
                refresh_ms=refresh,
                update_telemetry_parallel_func=update_telemetry_parallel,
                update_memory_func=update_memory,
                start_tab=4 if graph else None,
            )
        else:
            if graph:
                console.print("[yellow]Warning: Graph mode only available in watch mode (-w)[/yellow]")
            dashboard.print_snapshot(devices)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise click.Abort()


@smi.command()
@click.option("--device", "-d", type=int, help="Specific device ID")
@click.pass_context
def telemetry(ctx, device):
    """Show device telemetry (temperature, power, clock).
    
    Displays telemetry information for all devices or a specific device.
    
    Examples:
    
        \b
        # All devices
        tt-mgmt smi telemetry
        
        \b
        # Specific device
        tt-mgmt smi telemetry -d 0
    """
    console = ctx.obj['console']
    console.print("[yellow]Telemetry command - To be implemented[/yellow]")
    console.print("Will show temperature, power, voltage, current, and clock frequency")


@smi.command()
@click.option("--device", "-d", type=int, help="Specific device ID")
@click.pass_context
def memory(ctx, device):
    """Show memory utilization (DRAM, L1, trace, CB).
    
    Displays memory usage for all devices or a specific device.
    
    Examples:
    
        \b
        # All devices
        tt-mgmt smi memory
        
        \b
        # Specific device
        tt-mgmt smi memory -d 0
    """
    console = ctx.obj['console']
    console.print("[yellow]Memory command - To be implemented[/yellow]")
    console.print("Will show DRAM, L1, L1 small, trace, and circular buffer usage")


@smi.command()
@click.option("--device", "-d", type=int, help="Specific device ID")
@click.pass_context
def processes(ctx, device):
    """Show processes using Tenstorrent devices.
    
    Lists all processes that have allocated memory on devices.
    
    Examples:
    
        \b
        # All devices
        tt-mgmt smi processes
        
        \b
        # Specific device
        tt-mgmt smi processes -d 0
    """
    console = ctx.obj['console']
    console.print("[yellow]Processes command - To be implemented[/yellow]")
    console.print("Will show PID, name, memory allocated, and CPU usage")


@smi.command()
@click.pass_context
def cleanup(ctx):
    """Clean up dead processes from shared memory.
    
    Removes entries for processes that are no longer running but still
    have allocations tracked in shared memory.
    
    Examples:
    
        \b
        # Clean up dead processes
        tt-mgmt smi cleanup
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
