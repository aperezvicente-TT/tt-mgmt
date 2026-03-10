"""Debug and diagnostic commands."""

import click

from tt_mgmt.backend import debug_backend


@click.group()
def debug():
    """Debug and diagnostic commands.
    
    Tools for troubleshooting and diagnostics.
    """
    pass


@debug.command()
@click.argument('device_id', type=int)
@click.pass_context
def validate(ctx, device_id):
    """Run validation tests on a device.
    
    DEVICE_ID: The device ID to validate
    
    Examples:
    
        \b
        # Validate device 0
        tt-mgmt debug validate 0
    """
    console = ctx.obj['console']
    
    try:
        with console.status("[bold green]Running validation tests..."):
            results = debug_backend.validate_device(device_id)
        
        console.print(f"\n[bold cyan]Validation Results - Device {device_id}[/bold cyan]\n")
        
        for test_name, result in results.items():
            status = "[green]PASS[/green]" if result['passed'] else "[red]FAIL[/red]"
            console.print(f"{status} {test_name}: {result['message']}")
        
    except Exception as e:
        console.print(f"[red]Error running validation: {e}[/red]")
        raise click.Abort()


@debug.command()
@click.argument('device_id', type=int)
@click.option('--output', type=click.Path(), help='Output file for logs')
@click.pass_context
def logs(ctx, device_id, output):
    """Collect debug logs from a device.
    
    DEVICE_ID: The device ID to collect logs from
    
    Examples:
    
        \b
        # Print logs to console
        tt-mgmt debug logs 0
        
        \b
        # Save logs to file
        tt-mgmt debug logs 0 --output device0.log
    """
    console = ctx.obj['console']
    
    try:
        logs = debug_backend.get_device_logs(device_id)
        
        if output:
            with open(output, 'w') as f:
                f.write(logs)
            console.print(f"[green]Logs saved to {output}[/green]")
        else:
            console.print(logs)
        
    except Exception as e:
        console.print(f"[red]Error getting logs: {e}[/red]")
        raise click.Abort()


@debug.command()
@click.pass_context
def health(ctx):
    """Run system health check.
    
    Examples:
    
        \b
        # Check system health
        tt-mgmt debug health
    """
    console = ctx.obj['console']
    
    try:
        with console.status("[bold green]Running health checks..."):
            health = debug_backend.health_check()
        
        console.print(f"\n[bold cyan]System Health Check[/bold cyan]\n")
        
        for check_name, result in health.items():
            icon = "✓" if result['healthy'] else "✗"
            color = "green" if result['healthy'] else "red"
            console.print(f"[{color}]{icon}[/{color}] {check_name}: {result['message']}")
        
    except Exception as e:
        console.print(f"[red]Error running health check: {e}[/red]")
        raise click.Abort()
