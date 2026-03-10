"""Memory management commands."""

import click
from rich.table import Table

from tt_mgmt.backend import memory_backend


@click.group()
def memory():
    """Memory management commands.
    
    Query and manage device memory (DRAM, L1, etc.).
    """
    pass


@memory.command()
@click.argument('device_id', type=int)
@click.option('--type', 'mem_type', 
              type=click.Choice(['dram', 'l1', 'all']), 
              default='all',
              help='Memory type to query')
@click.pass_context
def stats(ctx, device_id, mem_type):
    """Show memory statistics for a device.
    
    DEVICE_ID: The device ID to query
    
    Examples:
    
        \b
        # Show all memory stats
        tt-mgmt memory stats 0
        
        \b
        # Show only DRAM stats
        tt-mgmt memory stats 0 --type dram
    """
    console = ctx.obj['console']
    
    try:
        stats = memory_backend.get_memory_stats(device_id, mem_type)
        
        table = Table(title=f"Memory Statistics - Device {device_id}")
        table.add_column("Type", style="cyan")
        table.add_column("Used", style="yellow")
        table.add_column("Total", style="green")
        table.add_column("Percentage", style="magenta")
        
        for mem in stats:
            used = mem['used']
            total = mem['total']
            pct = (used / total * 100) if total > 0 else 0
            
            table.add_row(
                mem['type'],
                f"{used:.1f} MB",
                f"{total:.1f} MB",
                f"{pct:.1f}%"
            )
        
        console.print(table)
        
    except Exception as e:
        console.print(f"[red]Error getting memory stats: {e}[/red]")
        raise click.Abort()


@memory.command()
@click.argument('device_id', type=int)
@click.option('--force', is_flag=True, help='Force clear without confirmation')
@click.pass_context
def clear(ctx, device_id, force):
    """Clear device memory allocations.
    
    DEVICE_ID: The device ID to clear
    
    Examples:
    
        \b
        # Clear memory (with confirmation)
        tt-mgmt memory clear 0
        
        \b
        # Force clear
        tt-mgmt memory clear 0 --force
    """
    console = ctx.obj['console']
    
    if not force:
        if not click.confirm(f'Clear memory for device {device_id}?'):
            console.print("[yellow]Clear cancelled[/yellow]")
            return
    
    try:
        memory_backend.clear_memory(device_id)
        console.print(f"[green]Memory cleared for device {device_id}[/green]")
    
    except Exception as e:
        console.print(f"[red]Error clearing memory: {e}[/red]")
        raise click.Abort()


@memory.command()
@click.argument('device_id', type=int)
@click.pass_context
def allocations(ctx, device_id):
    """List active memory allocations.
    
    DEVICE_ID: The device ID to query
    
    Examples:
    
        \b
        # List allocations
        tt-mgmt memory allocations 0
    """
    console = ctx.obj['console']
    
    try:
        allocs = memory_backend.get_allocations(device_id)
        
        if not allocs:
            console.print("[yellow]No active allocations[/yellow]")
            return
        
        table = Table(title=f"Memory Allocations - Device {device_id}")
        table.add_column("Address", style="cyan")
        table.add_column("Size", style="yellow")
        table.add_column("Type", style="green")
        table.add_column("Owner", style="magenta")
        
        for alloc in allocs:
            table.add_row(
                f"0x{alloc['address']:08x}",
                f"{alloc['size']} bytes",
                alloc['type'],
                alloc.get('owner', 'Unknown')
            )
        
        console.print(table)
        
    except Exception as e:
        console.print(f"[red]Error getting allocations: {e}[/red]")
        raise click.Abort()
