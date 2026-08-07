"""Fan control commands."""

import click
from rich.table import Table

from tt_mgmt import ui
from tt_mgmt.backend import fan as fan_backend


def _board_options(f):
    """--board / --board-id / --all, shared by set and auto."""
    f = click.option('--board', 'board_index', type=int, default=None,
                     help='Board index as shown by `tt-mgmt fan status`')(f)
    f = click.option('--board-id', 'board_id', type=str, default=None,
                     help='Board id, e.g. 0x100014611919009')(f)
    f = click.option('--all', 'all_boards', is_flag=True,
                     help='Apply to every fan-capable board')(f)
    return f


def _select(board_index, board_id, all_boards):
    if not all_boards and board_index is None and board_id is None:
        raise click.UsageError(
            "Pick a board with --board/--board-id, or pass --all. "
            "Run `tt-mgmt fan status` to see the list.")
    parsed_id = int(board_id, 0) if board_id is not None else None
    return fan_backend.resolve_boards(
        board_index=None if all_boards else board_index,
        board_id=None if all_boards else parsed_id)


def _rpm_text(rpm_pair):
    return " / ".join("absent" if r == 0 else f"{r}" for r in rpm_pair)


def _print_boards(console, boards):
    table = Table(title="Fan Status", box=ui.get_box())
    table.add_column("Board", style="bright_blue")
    table.add_column("Card", style="green")
    # fold rather than ellipsize: a truncated board id can't be passed to --board-id
    table.add_column("Board ID", style="dim", overflow="fold")
    table.add_column("Mode", style="magenta")
    table.add_column("Target", justify="right")
    table.add_column("RPM", justify="right")
    table.add_column("Per-ASIC target", style="dim")

    for board in boards:
        per_asic = ", ".join(
            f"{'remote' if a['is_remote'] else 'local'} {a['target_pct']}%"
            for a in board['asics'])
        table.add_row(
            str(board['index']),
            board['card_type'],
            f"0x{board['board_id']:x}",
            "[yellow]forced[/yellow]" if board['forced'] else "curve",
            f"{board['effective_pct']}%",
            _rpm_text(board['rpm']),
            per_asic,
        )
    console.print(table)


@click.group()
def fan():
    """Fan control (status, set, auto)."""
    pass


@fan.command()
@click.pass_context
def status(ctx):
    """Show fan state for every board that supports fan control.

    Target is the PWM duty cycle the firmware is commanding, not a measured
    value; RPM is what the M3 tachometer actually reports.
    """
    console = ctx.obj['console']

    if not fan_backend.available():
        console.print("[yellow]Fan control unavailable — needs the UMD backend.[/yellow]")
        return

    boards = fan_backend.list_boards()
    if not boards:
        console.print("[yellow]No fan-capable boards found (Wormhole only).[/yellow]")
        return

    _print_boards(console, boards)


@fan.command('set')
@click.argument('percent', type=click.IntRange(0, 100))
@_board_options
@click.pass_context
def set_speed(ctx, percent, board_index, board_id, all_boards):
    """Force a board's fan to PERCENT duty cycle (0-100).

    The setting is held in ARC RAM: it is lost on chip reset or reboot, and
    thermal trip still overrides it. Use `tt-mgmt fan auto` to release.

    Examples:

        \b
        tt-mgmt fan set 80 --board 0
        tt-mgmt fan set 100 --all
    """
    console = ctx.obj['console']
    try:
        boards = _select(board_index, board_id, all_boards)
        fan_backend.set_speed(boards, percent)
    except click.UsageError:
        raise
    except Exception as e:
        console.print(f"[red]Error setting fan speed: {e}[/red]")
        raise click.Abort()

    console.print(f"[green]Forced {len(boards)} board(s) to {percent}%[/green]")
    _print_boards(console, fan_backend.list_boards())


@fan.command()
@_board_options
@click.pass_context
def auto(ctx, board_index, board_id, all_boards):
    """Release a board's fan back to the firmware thermal curve.

    Examples:

        \b
        tt-mgmt fan auto --board 0
        tt-mgmt fan auto --all
    """
    console = ctx.obj['console']
    try:
        boards = _select(board_index, board_id, all_boards)
        fan_backend.set_speed(boards, None)
    except click.UsageError:
        raise
    except Exception as e:
        console.print(f"[red]Error releasing fan control: {e}[/red]")
        raise click.Abort()

    console.print(f"[green]Released {len(boards)} board(s) to curve control[/green]")
    console.print("[dim]The commanded target updates on the next thermal loop pass "
                  "(~10s).[/dim]")
    _print_boards(console, fan_backend.list_boards())
