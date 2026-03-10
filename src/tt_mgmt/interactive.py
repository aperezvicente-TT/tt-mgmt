#!/usr/bin/env python3
"""Interactive REPL for tt-mgmt with built-in tab completion."""

import shlex

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from rich.console import Console

import click


def build_completer():
    """Build completion tree from registered commands."""
    return NestedCompleter.from_nested_dict({
        'device': {
            'list': None,
            'info': None,
            'reset': None,
            'monitor': None,
        },
        'system': {
            'status': None,
            'topology': None,
            'version': None,
        },
        'memory': {
            'stats': None,
            'dump': None,
            'clear': None,
        },
        'debug': {
            'info': None,
            'dump-regs': None,
            'enable': None,
            'disable': None,
        },
        'smi': {
            'monitor': None,
            'telemetry': None,
            'memory': None,
            'processes': None,
            'cleanup': None,
        },
        'env': {
            'list': None,
            'show': None,
            'set': None,
            'unset': None,
            'export': None,
            'profile': None,
        },
        'fabric': {
            'status': None,
            'links': None,
            'topology': None,
            'cluster': None,
            'placement': None,
            'health': None,
        },
        'help': None,
        'exit': None,
        'quit': None,
    })


def run_interactive_mode(console: Console, main_cli, *, parent_ctx_obj: dict | None = None):
    """Interactive REPL with tab completion and command history."""
    console.print("\n[bold cyan]╔══════════════════════════════════════╗[/bold cyan]")
    console.print("[bold cyan]║[/bold cyan]  [bold white]tt-mgmt[/bold white]  Tenstorrent Management  [bold cyan]║[/bold cyan]")
    console.print("[bold cyan]╚══════════════════════════════════════╝[/bold cyan]\n")
    console.print("[green]✓[/green] TAB completion active")
    console.print("[green]✓[/green] History enabled (↑/↓)")
    console.print("[dim]Type 'help' for commands, 'exit' or Ctrl+D to quit.[/dim]\n")

    base_obj = dict(parent_ctx_obj) if parent_ctx_obj else {'console': console}

    session = PromptSession(
        completer=build_completer(),
        history=InMemoryHistory(),
        auto_suggest=AutoSuggestFromHistory(),
        enable_history_search=True,
    )

    while True:
        try:
            cmd_line = session.prompt('tt-mgmt> ')

            if not cmd_line.strip():
                continue

            if cmd_line.strip().lower() in ['exit', 'quit']:
                console.print("\n[green]Goodbye![/green]\n")
                break

            if cmd_line.strip().lower() == 'help':
                console.print("\n[bold cyan]Commands:[/bold cyan]\n")
                console.print("  [green]device[/green]   list, info, reset, monitor")
                console.print("  [green]system[/green]   status, topology, version")
                console.print("  [green]memory[/green]   stats, dump, clear")
                console.print("  [green]debug[/green]    info, dump-regs, enable, disable")
                console.print("  [green]smi[/green]      monitor, telemetry, memory, processes, cleanup")
                console.print("  [green]env[/green]      list, show, set, unset, export, profile")
                console.print("  [green]fabric[/green]   status, links, topology, cluster, placement, health")
                console.print("\n  [dim]TAB to autocomplete  |  exit / Ctrl+D to quit[/dim]\n")
                continue

            try:
                args = shlex.split(cmd_line)
                try:
                    main_cli.main(args, standalone_mode=False, obj=dict(base_obj))
                except SystemExit:
                    pass
            except click.ClickException as e:
                e.show()
            except click.exceptions.Exit:
                pass
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

        except KeyboardInterrupt:
            console.print()
            continue

        except EOFError:
            console.print("\n[green]Goodbye![/green]\n")
            break
