#!/usr/bin/env python3
"""Interactive REPL for tt-mgmt with built-in tab completion."""

import shlex

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from rich.console import Console

import click


def build_completer(main_cli):
    """Build completion tree by walking the Click command graph."""
    def walk(cmd):
        if isinstance(cmd, click.Group):
            return {name: walk(sub) for name, sub in cmd.commands.items()}
        return None
    tree = walk(main_cli) or {}
    tree.update({'help': None, 'exit': None, 'quit': None})
    return NestedCompleter.from_nested_dict(tree)


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
        completer=build_completer(main_cli),
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
                for name, cmd in sorted(main_cli.commands.items()):
                    if isinstance(cmd, click.Group):
                        subs = ", ".join(sorted(cmd.commands.keys()))
                    else:
                        subs = ""
                    console.print(f"  [green]{name}[/green]   {subs}")
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
