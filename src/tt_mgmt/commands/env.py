"""Environment variable management commands."""

import json
import os

import click
from rich.table import Table
from rich.text import Text

from tt_mgmt.backend import env_backend
from tt_mgmt.backend.env import CATEGORIES


# Category display colors
_CAT_COLORS = {
    "core":      "cyan",
    "debug":     "yellow",
    "profiling": "magenta",
    "fabric":    "blue",
    "build":     "green",
    "hardware":  "red",
}


def _cat_label(category: str) -> Text:
    color = _CAT_COLORS.get(category, "white")
    return Text(category, style=color)


@click.group()
def env():
    """Manage TT_METAL_* / TT_* environment variables.

    Display the curated variable catalog, inspect what is currently set
    in the shell, and persist a profile to ~/.config/tt-mgmt/env.json
    so that settings can be exported back into any shell session.

    \b
    Quick start:
        tt-mgmt env show                     # browse all known vars
        tt-mgmt env show --category debug    # filter by category
        tt-mgmt env list                     # what is set right now
        tt-mgmt env set TT_METAL_HOME /opt/tt-metal
        eval $(tt-mgmt env export)           # apply profile to current shell
    """
    pass


# ---------------------------------------------------------------------------
# env list
# ---------------------------------------------------------------------------

@env.command("list")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--all", "show_all",
    is_flag=True,
    default=False,
    help="Include all TT_* vars, not just known catalog entries.",
)
@click.pass_context
def list_cmd(ctx, fmt, show_all):
    """Show TT_* variables currently set in the shell environment.

    By default only variables that appear in the known catalog are shown.
    Use --all to include every TT_* var found in the environment.

    \b
    Examples:
        tt-mgmt env list
        tt-mgmt env list --all
        tt-mgmt env list --format json
    """
    console = ctx.obj["console"]
    live = env_backend.get_live_tt_vars()

    if not show_all:
        known_names = {e["name"] for e in env_backend.ENV_CATALOG}
        live = {k: v for k, v in live.items() if k in known_names}

    if fmt == "json":
        console.print_json(json.dumps(live, indent=2))
        return

    if not live:
        console.print("[dim]No TT_* variables are currently set in the environment.[/dim]")
        return

    table = Table(title="Active TT Environment Variables", show_lines=False)
    table.add_column("Variable", style="bold", no_wrap=True)
    table.add_column("Value", style="green")
    table.add_column("Category", no_wrap=True)

    for name in sorted(live):
        entry = env_backend.get_catalog_entry(name)
        cat_text = _cat_label(entry["category"]) if entry else Text("unknown", style="dim")
        table.add_row(name, live[name], cat_text)

    console.print(table)
    console.print(f"\n[dim]{len(live)} variable(s) set.[/dim]")


# ---------------------------------------------------------------------------
# env show
# ---------------------------------------------------------------------------

@env.command("show")
@click.option(
    "--category", "-c",
    type=click.Choice(CATEGORIES),
    default=None,
    help="Filter by category.",
)
@click.option(
    "--set-only",
    is_flag=True,
    default=False,
    help="Only show variables that are currently set in the environment.",
)
@click.option(
    "--profile",
    is_flag=True,
    default=False,
    help="Show saved profile values instead of live shell values.",
)
@click.pass_context
def show_cmd(ctx, category, set_only, profile):
    """Browse the curated catalog of known TT_* variables.

    Each row shows the variable name, its category, the current value
    (green if set, dim if relying on default), and a short description.

    \b
    Examples:
        tt-mgmt env show
        tt-mgmt env show --category debug
        tt-mgmt env show --set-only
        tt-mgmt env show --profile
    """
    console = ctx.obj["console"]
    entries = env_backend.get_catalog_by_category(category)

    if profile:
        values = env_backend.load_profile()
        source_label = "Profile (~/.config/tt-mgmt/env.json)"
    else:
        values = env_backend.get_live_tt_vars()
        source_label = "Live shell environment"

    if set_only:
        entries = [e for e in entries if e["name"] in values]

    if not entries:
        console.print("[dim]No variables match the current filters.[/dim]")
        return

    cat_header = f"  Category: [bold]{category}[/bold]" if category else ""
    title = f"TT Environment Variable Catalog — {source_label}{cat_header}"

    table = Table(title=title, show_lines=True, expand=True)
    table.add_column("Variable", style="bold", no_wrap=True, min_width=38)
    table.add_column("Cat", no_wrap=True, min_width=9)
    table.add_column("Current Value", min_width=20)
    table.add_column("Default", style="dim", min_width=18)
    table.add_column("Description")

    for entry in entries:
        name = entry["name"]
        cat_text = _cat_label(entry["category"])
        default_str = entry.get("default", "—")

        if name in values:
            val_text = Text(values[name], style="bold green")
        else:
            val_text = Text("(not set)", style="dim")

        table.add_row(name, cat_text, val_text, default_str, entry["description"])

    console.print(table)

    set_count = sum(1 for e in entries if e["name"] in values)
    console.print(
        f"\n[dim]{set_count}/{len(entries)} variable(s) set "
        f"({source_label}).[/dim]"
    )

    if not profile:
        saved = env_backend.load_profile()
        if saved:
            console.print(
                f"[dim]Profile at {env_backend.get_profile_path()} "
                f"contains {len(saved)} saved variable(s). "
                "Run [bold]tt-mgmt env show --profile[/bold] to view them.[/dim]"
            )


# ---------------------------------------------------------------------------
# env set
# ---------------------------------------------------------------------------

@env.command("set")
@click.argument("name")
@click.argument("value")
@click.pass_context
def set_cmd(ctx, name, value):
    """Save a variable to the persistent profile.

    NAME and VALUE are stored in ~/.config/tt-mgmt/env.json.
    Use 'tt-mgmt env export' to generate shell export lines from the profile.

    \b
    Examples:
        tt-mgmt env set TT_METAL_HOME /opt/tt-metal
        tt-mgmt env set TT_METAL_VISIBLE_DEVICES 0,1
        tt-mgmt env set TT_METAL_WATCHER 100
    """
    console = ctx.obj["console"]
    name = name.upper()

    entry = env_backend.get_catalog_entry(name)
    if entry is None:
        console.print(
            f"[yellow]Warning:[/yellow] [bold]{name}[/bold] is not in the known catalog "
            "(saving anyway)."
        )

    env_backend.set_var(name, value)
    console.print(
        f"[green]Saved:[/green] [bold]{name}[/bold] = [green]{value}[/green]\n"
        f"[dim]Profile: {env_backend.get_profile_path()}[/dim]"
    )
    if entry:
        console.print(f"[dim]{entry['description']}[/dim]")


# ---------------------------------------------------------------------------
# env unset
# ---------------------------------------------------------------------------

@env.command("unset")
@click.argument("name")
@click.pass_context
def unset_cmd(ctx, name):
    """Remove a variable from the persistent profile.

    \b
    Examples:
        tt-mgmt env unset TT_METAL_WATCHER
    """
    console = ctx.obj["console"]
    name = name.upper()
    removed = env_backend.unset_var(name)

    if removed:
        console.print(f"[green]Removed[/green] [bold]{name}[/bold] from profile.")
    else:
        console.print(
            f"[yellow]{name}[/yellow] was not found in the profile "
            f"({env_backend.get_profile_path()})."
        )


# ---------------------------------------------------------------------------
# env export
# ---------------------------------------------------------------------------

@env.command("export")
@click.option(
    "--format", "fmt",
    type=click.Choice(["shell", "json", "dotenv"]),
    default="shell",
    show_default=True,
    help=(
        "Output format: 'shell' = export VAR=VALUE lines, "
        "'json' = JSON object, "
        "'dotenv' = VAR=VALUE (no export keyword)."
    ),
)
@click.option(
    "--merge-live/--no-merge-live",
    default=False,
    help="Also include currently-set TT_* vars that are not in the profile.",
)
@click.pass_context
def export_cmd(ctx, fmt, merge_live):
    """Print the saved profile as shell export statements.

    The output is designed to be eval'd in the current shell so that the
    saved profile becomes active for subsequent commands.

    \b
    Examples:
        eval $(tt-mgmt env export)
        tt-mgmt env export >> ~/.bashrc
        tt-mgmt env export --format json
        tt-mgmt env export --format dotenv > .env
    """
    console = ctx.obj["console"]
    profile = env_backend.load_profile()

    if merge_live:
        live = env_backend.get_live_tt_vars()
        merged = dict(live)
        merged.update(profile)
        data = merged
    else:
        data = profile

    if not data:
        console.print(
            "[yellow]Profile is empty.[/yellow] "
            "Use [bold]tt-mgmt env set VAR VALUE[/bold] to add variables.",
            err=True,
        )
        return

    if fmt == "json":
        # Use print (not console.print) so output is clean for redirection
        click.echo(json.dumps(data, indent=2))
        return

    if fmt == "dotenv":
        for name, value in sorted(data.items()):
            click.echo(f"{name}={_shell_quote(value)}")
        return

    # Default: shell export lines
    for name, value in sorted(data.items()):
        click.echo(f"export {name}={_shell_quote(value)}")


# ---------------------------------------------------------------------------
# env profile
# ---------------------------------------------------------------------------

@env.command("profile")
@click.option(
    "--clear", is_flag=True, default=False,
    help="Clear all saved variables from the profile (prompts for confirmation).",
)
@click.pass_context
def profile_cmd(ctx, clear):
    """Show or manage the saved profile at ~/.config/tt-mgmt/env.json.

    \b
    Examples:
        tt-mgmt env profile
        tt-mgmt env profile --clear
    """
    console = ctx.obj["console"]
    path = env_backend.get_profile_path()

    if clear:
        if not path.exists():
            console.print("[dim]Profile does not exist; nothing to clear.[/dim]")
            return
        click.confirm(f"Clear all variables from {path}?", abort=True)
        env_backend.save_profile({})
        console.print(f"[green]Profile cleared:[/green] {path}")
        return

    profile = env_backend.load_profile()

    console.print(f"\n[bold cyan]Saved Profile[/bold cyan]  [dim]{path}[/dim]\n")

    if not profile:
        console.print(
            "[dim]Profile is empty. Use [bold]tt-mgmt env set VAR VALUE[/bold] "
            "to add variables.[/dim]"
        )
        return

    table = Table(show_lines=False)
    table.add_column("Variable", style="bold", no_wrap=True)
    table.add_column("Saved Value", style="green")
    table.add_column("Live Shell Value")

    live = env_backend.get_live_tt_vars()

    for name in sorted(profile):
        live_val = live.get(name)
        if live_val is None:
            live_text = Text("(not set)", style="dim")
        elif live_val == profile[name]:
            live_text = Text(live_val, style="dim green")
        else:
            live_text = Text(live_val, style="yellow")

        table.add_row(name, profile[name], live_text)

    console.print(table)
    console.print(
        f"\n[dim]{len(profile)} variable(s) saved. "
        "Run [bold]eval $(tt-mgmt env export)[/bold] to apply to current shell.[/dim]"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _shell_quote(value: str) -> str:
    """Single-quote a value for safe shell use, escaping embedded single quotes."""
    escaped = value.replace("'", "'\\''")
    return f"'{escaped}'"
