#!/usr/bin/env python3
"""Main CLI entry point for tt-mgmt."""

import os
import sys
import click

from tt_mgmt import ui
from tt_mgmt.commands import device, smi, env, fabric, record, plot, erisc, doctor

console = ui.get_console()


_HELP_FLAGS = {'--help', '-h', '--version'}


def _extract_global_ui_flags():
    """Strip --ascii / --no-color from argv wherever they appear so they work
    after subcommands too (e.g. `tt-mgmt smi --ascii`). Click only honors
    group-level flags before the subcommand."""
    ascii_seen = False
    no_color_seen = False
    filtered = []
    for arg in sys.argv:
        if arg == '--ascii':
            ascii_seen = True
        elif arg == '--no-color':
            no_color_seen = True
        else:
            filtered.append(arg)
    if ascii_seen or no_color_seen:
        sys.argv[:] = filtered
        ui.configure(
            ascii=True if ascii_seen else None,
            no_color=True if no_color_seen else None,
        )


_extract_global_ui_flags()


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
@click.option(
    "-v", "--verbose",
    is_flag=True,
    default=False,
    help="Enable verbose UMD logging (default: errors only).",
)
@click.option(
    "--ascii",
    "ascii_mode",
    is_flag=True,
    default=False,
    envvar="TT_MGMT_ASCII",
    help="Use ASCII box-drawing characters instead of Unicode (for serial consoles, CI logs).",
)
@click.option(
    "--no-color",
    is_flag=True,
    default=False,
    envvar="TT_MGMT_NO_COLOR",
    help="Disable colored output (also respects NO_COLOR env var).",
)
@click.pass_context
def main(ctx, backend, fabric_endpoint, verbose, ascii_mode, no_color, **kwargs):
    """TT-MGMT: Tenstorrent Control and Management

    Running without a subcommand starts the interactive shell.
    Subcommands can also be invoked directly:

    \b
        tt-mgmt smi
        tt-mgmt smi status
        tt-mgmt device list
        tt-mgmt device info 0

    The --backend flag (or TT_MGMT_BACKEND env var) selects which
    telemetry source to use:

    \b
        auto   Try UMD first, fall back to sysfs (default)
        umd    Use UMD TopologyDiscovery (full telemetry + remote devices)
        sysfs  Read from /sys/class/tenstorrent/ + hwmon (lightweight, no UMD)
    """
    ctx.ensure_object(dict)
    if ctx.resilient_parsing:
        return

    # Apply UI flags before grabbing the console — flags override env defaults.
    if ascii_mode or no_color:
        ui.configure(
            ascii=True if ascii_mode else None,
            no_color=True if no_color else None,
        )
    global console
    console = ui.get_console()
    ctx.obj.setdefault('console', console)
    ctx.obj['ascii'] = ui.is_ascii()
    ctx.obj['no_color'] = ui.is_no_color()

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

    if verbose:
        ctx.obj['verbose'] = True
    else:
        ctx.obj.setdefault('verbose', False)

    skip_backend = any(a in _HELP_FLAGS for a in sys.argv)

    def _init_backend():
        try:
            from tt_mgmt.backend.smi.core import set_backend
            set_backend(
                ctx.obj['backend'],
                fabric_endpoint=ctx.obj.get('fabric_endpoint'),
                verbose=ctx.obj.get('verbose', False),
            )
        except Exception:
            pass

    if not skip_backend:
        _init_backend()

    if ctx.invoked_subcommand is None:
        from tt_mgmt.interactive import run_interactive_mode
        run_interactive_mode(console, main, parent_ctx_obj=ctx.obj)


# Register command groups
main.add_command(device.device)
main.add_command(smi.smi)
main.add_command(env.env)
main.add_command(fabric.fabric)
main.add_command(record.record)
main.add_command(plot.plot)
main.add_command(erisc.erisc)
main.add_command(doctor.doctor)


if __name__ == '__main__':
    main()
