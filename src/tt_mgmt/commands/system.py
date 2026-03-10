"""System-level commands."""

import click
from rich.table import Table

from tt_mgmt.backend import system_backend


@click.group()
def system():
    """System-level management commands.
    
    Query and manage the overall Tenstorrent system state.
    """
    pass


@system.command()
@click.pass_context
def status(ctx):
    """Show overall system status.
    
    Displays a summary of all devices and system health.
    
    Examples:
    
        \b
        # Show system status
        tt-mgmt system status
    """
    console = ctx.obj['console']
    
    try:
        status = system_backend.get_system_status()
        
        console.print(f"\n[bold cyan]System Status[/bold cyan]\n")
        console.print(f"[green]Total Devices:[/green] {status['total_devices']}")
        console.print(f"[green]Active Devices:[/green] {status['active_devices']}")
        console.print(f"[green]Driver Version:[/green] {status['driver_version']}")
        console.print(f"[green]System Health:[/green] {status['health']}")
        
    except Exception as e:
        console.print(f"[red]Error getting system status: {e}[/red]")
        raise click.Abort()


@system.command()
@click.pass_context
def topology(ctx):
    """Display system topology.
    
    Shows how devices are interconnected.
    
    Examples:
    
        \b
        # Show topology
        tt-mgmt system topology
    """
    console = ctx.obj['console']
    
    try:
        topology = system_backend.get_topology()
        
        table = Table(title="System Topology")
        table.add_column("Device", style="cyan")
        table.add_column("Connected To", style="green")
        table.add_column("Link Type", style="yellow")
        
        for conn in topology['connections']:
            table.add_row(
                str(conn['from']),
                str(conn['to']),
                conn['type']
            )
        
        console.print(table)
        
    except Exception as e:
        console.print(f"[red]Error getting topology: {e}[/red]")
        raise click.Abort()


@system.command()
@click.pass_context
def version(ctx):
    """Show version information for all components.
    
    Examples:
    
        \b
        # Show versions
        tt-mgmt system version
    """
    console = ctx.obj['console']
    
    try:
        versions = system_backend.get_versions()
        
        console.print(f"\n[bold cyan]Version Information[/bold cyan]\n")
        for component, version in versions.items():
            console.print(f"[green]{component}:[/green] {version}")
        
    except Exception as e:
        console.print(f"[red]Error getting version info: {e}[/red]")
        raise click.Abort()
