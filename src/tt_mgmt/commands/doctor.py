"""`tt-mgmt doctor` -- one-shot health check across host, devices, and daemon."""

from __future__ import annotations

import json as _json
import sys

import click

from tt_mgmt.backend import checks


_GLYPH = {
    checks.PASS: ("✓", "green"),
    checks.WARN: ("⚠", "yellow"),
    checks.ERROR: ("✗", "red"),
    checks.SKIP: ("·", "dim"),
}


@click.command()
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.option("--verbose", "-v", is_flag=True, help="Show details for every check.")
@click.pass_context
def doctor(ctx, as_json, verbose):
    """Diagnose host, drivers, devices, and daemon health.

    Exit codes: 0 all pass, 1 any error, 2 warnings only.

    \b
    Examples:
        tt-mgmt doctor
        tt-mgmt doctor --verbose
        tt-mgmt doctor --json
    """
    devices = _safe_get_devices()

    groups = checks.run_all(devices)
    counts = checks.summarize(groups)

    if as_json:
        _emit_json(groups, counts)
    else:
        _emit_text(ctx.obj["console"], groups, counts, verbose=verbose)

    if counts.get(checks.ERROR, 0) > 0:
        sys.exit(1)
    if counts.get(checks.WARN, 0) > 0:
        sys.exit(2)
    sys.exit(0)


def _safe_get_devices():
    """Get devices + telemetry without crashing the doctor command itself."""
    try:
        from tt_mgmt.backend.smi import core as smi_core
        devices = smi_core.get_devices()
        if devices:
            smi_core.update_telemetry_parallel(devices, timeout=2.0)
        return devices
    except Exception:
        return []


def _emit_text(console, groups, counts, verbose: bool) -> None:
    for group in groups:
        console.print(f"\n[bold]{group.name}[/bold]")
        for r in group.results:
            glyph, color = _GLYPH.get(r.status, ("?", "white"))
            console.print(f"  [{color}]{glyph}[/{color}] {r.name}: {r.message}")
            if r.remediation and r.status in (checks.WARN, checks.ERROR):
                console.print(f"      [dim]→ {r.remediation}[/dim]")
            if r.details and (verbose or r.status in (checks.WARN, checks.ERROR)):
                for line in r.details:
                    console.print(f"      [dim]· {line}[/dim]")

    err = counts.get(checks.ERROR, 0)
    warn = counts.get(checks.WARN, 0)
    if err == 0 and warn == 0:
        console.print("\n[bold green]All checks passed.[/bold green]")
    else:
        parts = []
        if err:
            parts.append(f"[red]{err} error{'s' if err != 1 else ''}[/red]")
        if warn:
            parts.append(f"[yellow]{warn} warning{'s' if warn != 1 else ''}[/yellow]")
        console.print("\n" + ", ".join(parts))


def _emit_json(groups, counts) -> None:
    payload = {
        "summary": counts,
        "groups": [
            {
                "name": g.name,
                "checks": [
                    {
                        "name": r.name,
                        "status": r.status,
                        "message": r.message,
                        "remediation": r.remediation,
                        "details": r.details,
                    }
                    for r in g.results
                ],
            }
            for g in groups
        ],
    }
    print(_json.dumps(payload, indent=2))
