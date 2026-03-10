#!/usr/bin/env python3
"""Main CLI entry point for tt-mgmt."""

import os
import sys
import click
from rich.console import Console

from tt_mgmt.commands import device, system, memory, debug, smi, env, fabric, record, plot

console = Console()


@click.group(invoke_without_command=True)
@click.version_option()
@click.option(
    "--backend",
    type=click.Choice(["auto", "umd", "sysfs"]),
    default=None,
    envvar="TT_MGMT_BACKEND",
    help="Device backend: auto (default), umd (full UMD), sysfs (lightweight, no UMD).",
)
@click.option(
    "--fabric-endpoint",
    type=str,
    default=None,
    envvar="TT_FABRIC_ENDPOINT",
    help="Fabric manager gRPC endpoint (e.g. localhost:50051). Enables cluster-level commands.",
)
@click.pass_context
def main(ctx, backend, fabric_endpoint, **kwargs):
    """TT-MGMT: Tenstorrent Control and Management

    Running without a subcommand starts the interactive shell.
    Subcommands can also be invoked directly:

    \b
        tt-mgmt smi monitor
        tt-mgmt device list
        tt-mgmt system status

    The --backend flag (or TT_MGMT_BACKEND env var) selects which
    telemetry source to use:

    \b
        auto   Try UMD first, fall back to sysfs (default)
        umd    Use UMD TopologyDiscovery (full telemetry + remote devices)
        sysfs  Read from /sys/class/tenstorrent/ + hwmon (lightweight, no UMD)
    """
    ctx.ensure_object(dict)
    ctx.obj.setdefault('console', console)

    # Use explicitly-provided --backend, or preserve what the parent context
    # already decided (important for interactive mode re-entry).
    if backend is not None:
        ctx.obj['backend'] = backend
    else:
        ctx.obj.setdefault('backend', 'auto')

    if fabric_endpoint is not None:
        ctx.obj['fabric_endpoint'] = fabric_endpoint
    else:
        ctx.obj.setdefault('fabric_endpoint', None)

    try:
        from tt_mgmt.backend.smi.core import set_backend
        set_backend(ctx.obj['backend'], fabric_endpoint=ctx.obj.get('fabric_endpoint'))
    except Exception:
        pass

    if ctx.invoked_subcommand is None:
        from tt_mgmt.interactive import run_interactive_mode
        run_interactive_mode(console, main, parent_ctx_obj=ctx.obj)


# Register command groups
main.add_command(device.device)
main.add_command(system.system)
main.add_command(memory.memory)
main.add_command(debug.debug)
main.add_command(smi.smi)
main.add_command(env.env)
main.add_command(fabric.fabric)
main.add_command(record.record)
main.add_command(plot.plot)


if __name__ == '__main__':
    main()
