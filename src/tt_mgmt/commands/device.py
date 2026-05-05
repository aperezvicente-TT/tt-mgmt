"""Device management commands."""

import builtins
from collections import defaultdict

import click
from rich.table import Table

from tt_mgmt import ui

from tt_mgmt.backend import device_backend


def _infer_board_type(arch: str, num_chips: int) -> str:
    if "Wormhole" in arch:
        return "N300" if num_chips >= 2 else "N150"
    if "Blackhole" in arch:
        return "P300A" if num_chips >= 2 else "P150A"
    return arch


@click.group()
def device():
    """Device management (list, info)."""
    pass


@device.command()
@click.option('--format', type=click.Choice(['table', 'json', 'yaml']), default='table',
              help='Output format')
@click.option('--show-remote/--no-show-remote', default=True, help='Include remote devices')
@click.pass_context
def list(ctx, format, show_remote):
    """List all available Tenstorrent devices.
    
    Shows device ID, architecture, status, and basic telemetry.
    
    Examples:
    
        \b
        tt-mgmt device list
        tt-mgmt device list --format json
        tt-mgmt device list --no-show-remote
    """
    console = ctx.obj['console']
    
    try:
        devices = device_backend.list_devices(include_remote=show_remote)
        
        if not devices:
            console.print("[yellow]No devices found[/yellow]")
            return
        
        if format == 'table':
            # Compute board type per device by grouping on (board_id, arch)
            board_groups = defaultdict(builtins.list)
            for dev in devices:
                board_groups[(dev.get('board_id', 0), dev['arch'])].append(dev)

            board_type_map = {}
            for (bid, arch), group in board_groups.items():
                btype = group[0].get('card_type', '') or _infer_board_type(arch, len(group))
                for d in group:
                    board_type_map[d['id']] = btype

            table = Table(title="Tenstorrent Devices", box=ui.get_box())
            table.add_column("Logical ID", style="bright_blue")
            table.add_column("Board", style="green")
            table.add_column("ASIC ID", style="dim")
            table.add_column("Status", style="magenta")
            table.add_column("Temperature", style="yellow")
            table.add_column("Power", style="red")

            for dev in devices:
                logical_id = str(dev['logical_id']) if dev.get('logical_id', -1) >= 0 else "-"

                btype = board_type_map.get(dev['id'], '?')
                board_str = f"{btype} {dev['arch']}"

                table.add_row(
                    logical_id,
                    board_str,
                    dev.get('display_id', ''),
                    dev['status'],
                    f"{dev['temp']:.1f}°C" if dev.get('temp') is not None else "N/A",
                    f"{dev['power']:.1f}W" if dev.get('power') is not None else "N/A"
                )
            
            console.print(table)
        
        elif format == 'json':
            console.print_json(data={"devices": devices})
        
        elif format == 'yaml':
            import yaml
            console.print(yaml.dump({"devices": devices}, default_flow_style=False))
    
    except Exception as e:
        console.print(f"[red]Error listing devices: {e}[/red]")
        raise click.Abort()


@device.command()
@click.argument('device_id', type=int)
@click.option('--verbose', is_flag=True, help='Show detailed information')
@click.pass_context
def info(ctx, device_id, verbose):
    """Show detailed information about a specific device.
    
    DEVICE_ID: The device index (use 'tt-mgmt device list' to see IDs)
    
    Examples:
    
        \b
        tt-mgmt device info 0
        tt-mgmt device info 0 --verbose
    """
    console = ctx.obj['console']
    
    try:
        info = device_backend.get_device_info(device_id, verbose=verbose)
        
        console.print(f"\n[bold cyan]Device {device_id} Information[/bold cyan]\n")
        console.print(f"[green]Architecture:[/green] {info['arch']}")
        console.print(f"[green]Status:[/green] {info['status']}")
        console.print(f"[green]Temperature:[/green] {info['temp']:.1f}°C")
        console.print(f"[green]Power:[/green] {info['power']:.1f}W")
        console.print(f"[green]Clock (AICLK):[/green] {info['aiclk']} MHz")
        
        if verbose:
            console.print(f"\n[bold]Memory:[/bold]")
            console.print(f"  DRAM: {info['memory']['dram']['used']}/{info['memory']['dram']['total']} MB")
            console.print(f"  L1: {info['memory']['l1']['used']}/{info['memory']['l1']['total']} MB")
            
            console.print(f"\n[bold]Processes:[/bold]")
            for proc in info.get('processes', []):
                console.print(f"  PID {proc['pid']}: {proc['name']}")
    
    except Exception as e:
        console.print(f"[red]Error getting device info: {e}[/red]")
        raise click.Abort()


