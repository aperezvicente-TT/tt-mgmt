"""Fabric / ethernet topology commands."""

import click
from rich.table import Table
from rich.text import Text
from rich import box


@click.group()
def fabric():
    """Ethernet fabric topology and connectivity."""
    pass


@fabric.command()
@click.pass_context
def status(ctx):
    """Show fabric summary: total links, active/idle/exit counts.

    Examples:

        \b
        tt-mgmt fabric status
    """
    console = ctx.obj['console']

    try:
        from tt_mgmt.backend.smi import get_devices, update_telemetry

        devices = get_devices()
        if not devices:
            console.print("[yellow]No devices found[/yellow]")
            return

        total_in_cluster = 0
        total_exit = 0
        total_active = 0
        total_idle = 0

        for dev in devices:
            conns = dev.eth_connections
            total_in_cluster += sum(1 for c in conns if not c["is_exit_link"])
            total_exit += sum(1 for c in conns if c["is_exit_link"])
            total_active += dev.active_eth_channels
            total_idle += dev.idle_eth_channels

        console.print(f"\n[bold cyan]Fabric Status[/bold cyan]\n")
        console.print(f"  [green]Devices:[/green]            {len(devices)}")
        console.print(f"  [green]In-cluster links:[/green]   {total_in_cluster // 2} (bidirectional)")
        console.print(f"  [green]Exit links:[/green]         {total_exit}")
        console.print(f"  [green]Active channels:[/green]    {total_active}")
        console.print(f"  [green]Idle channels:[/green]      {total_idle}")
        console.print()

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise click.Abort()


@fabric.command()
@click.option('--device', 'device_filter', type=str, default=None,
              help='Filter by device display ID (substring match)')
@click.option('--exit-only', is_flag=True, help='Show only exit links (off-host)')
@click.pass_context
def links(ctx, device_filter, exit_only):
    """Show per-device ethernet link table.

    Examples:

        \b
        tt-mgmt fabric links
        tt-mgmt fabric links --exit-only
        tt-mgmt fabric links --device 8a26
    """
    console = ctx.obj['console']

    try:
        from tt_mgmt.backend.smi import get_devices

        devices = get_devices()
        if not devices:
            console.print("[yellow]No devices found[/yellow]")
            return

        chip_id_to_display = {dev.chip_id: dev.display_id for dev in devices}

        table = Table(title="Ethernet Links", box=box.ROUNDED, show_header=True, header_style="bold")
        table.add_column("Device", style="cyan")
        table.add_column("Arch", style="dim")
        table.add_column("Local Ch", justify="right")
        table.add_column("Remote Device", style="cyan")
        table.add_column("Remote Ch", justify="right")
        table.add_column("Type", style="yellow")

        row_count = 0
        for dev in devices:
            if device_filter and device_filter not in dev.display_id:
                continue
            for conn in dev.eth_connections:
                if exit_only and not conn["is_exit_link"]:
                    continue
                remote_display = chip_id_to_display.get(
                    conn["remote_chip_id"], f"0x{conn['remote_chip_id']:x}"
                )
                link_type = "exit" if conn["is_exit_link"] else "local"
                type_style = "bold red" if conn["is_exit_link"] else "green"
                table.add_row(
                    dev.display_id,
                    dev.arch_name,
                    str(conn["local_channel"]),
                    remote_display,
                    str(conn["remote_channel"]),
                    Text(link_type, style=type_style),
                )
                row_count += 1

        if row_count == 0:
            console.print("[yellow]No ethernet links found[/yellow]")
        else:
            console.print(table)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise click.Abort()


@fabric.command()
@click.pass_context
def topology(ctx):
    """Board-grouped connectivity view: which boards connect to which.

    Examples:

        \b
        tt-mgmt fabric topology
    """
    console = ctx.obj['console']

    try:
        from tt_mgmt.backend.smi import get_devices
        from collections import defaultdict

        devices = get_devices()
        if not devices:
            console.print("[yellow]No devices found[/yellow]")
            return

        table = Table(
            title="Board Ethernet Connectivity", box=box.ROUNDED,
            show_header=True, header_style="bold",
        )
        table.add_column("Board / ASIC", style="cyan", width=24)
        table.add_column("Arch", width=12)
        table.add_column("Active", justify="right", width=6)
        table.add_column("Idle", justify="right", width=6)
        table.add_column("In-Cluster", justify="right", width=10)
        table.add_column("Exit", justify="right", width=6)
        table.add_column("Coord", width=18)

        boards = defaultdict(list)
        for dev in devices:
            boards[dev.board_id].append(dev)

        for board_id, board_devs in sorted(boards.items()):
            arch = board_devs[0].arch_name
            is_wh = "Wormhole" in arch
            if is_wh:
                btype = "N300" if len(board_devs) >= 2 else "N150"
            elif "Blackhole" in arch:
                btype = "P300A" if len(board_devs) >= 2 else "P150A"
            else:
                btype = "?"

            table.add_row(
                Text(f"Board {board_id:016x}", style="bold"),
                Text(f"{arch}  {btype}", style="bold"),
                "", "", "", "", "",
                style="on grey11",
            )

            for dev in board_devs:
                conns = dev.eth_connections
                in_cluster = sum(1 for c in conns if not c["is_exit_link"])
                exit_links = sum(1 for c in conns if c["is_exit_link"])

                coord = dev.eth_coord
                if coord["cluster_id"] >= 0:
                    coord_str = f"c{coord['cluster_id']} ({coord['x']},{coord['y']}) r{coord['rack']}s{coord['shelf']}"
                else:
                    coord_str = "-"

                role = ""
                if is_wh:
                    role = " [R]" if dev.is_remote else " [L]"
                label = f"  └─ {dev.display_id}{role}"

                table.add_row(
                    Text(label),
                    Text(""),
                    str(dev.active_eth_channels),
                    str(dev.idle_eth_channels),
                    str(in_cluster),
                    str(exit_links),
                    Text(coord_str, style="dim"),
                )

        console.print(table)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise click.Abort()


# ---- Phase 2: Fabric Manager cluster commands ----


def _require_fabric(console):
    """Check that fabric manager is connected; print error and return False if not."""
    from tt_mgmt.backend.smi.core import has_fabric
    if not has_fabric():
        console.print(
            "[yellow]Fabric manager not connected.[/yellow]\n"
            "  Use [bold]--fabric-endpoint HOST:PORT[/bold] or set "
            "[bold]TT_FABRIC_ENDPOINT[/bold] to enable cluster commands."
        )
        return False
    return True


@fabric.command()
@click.pass_context
def cluster(ctx):
    """Multi-host cluster topology summary (requires fabric manager).

    Shows hosts, ASIC counts, cross-host links, and inter-host connectivity.

    Examples:

        \b
        tt-mgmt --fabric-endpoint localhost:50051 fabric cluster
    """
    console = ctx.obj['console']
    if not _require_fabric(console):
        return

    try:
        from tt_mgmt.backend.smi.core import get_cluster_topology

        info = get_cluster_topology()
        if info["error"]:
            console.print(f"[red]Error: {info['error']}[/red]")
            return

        console.print(f"\n[bold cyan]Cluster Topology[/bold cyan]  "
                       f"({len(info['hosts'])} hosts, "
                       f"{info['total_cross_host_links']} cross-host links)\n")

        table = Table(box=box.ROUNDED, show_header=True, header_style="bold")
        table.add_column("Host", style="cyan")
        table.add_column("ASICs", justify="right")
        table.add_column("Arch", style="dim")
        table.add_column("Connected To")

        for host in info["hosts"]:
            table.add_row(
                host["host_name"],
                str(host["asic_count"]),
                host["arch"],
                ", ".join(host["connected_hosts"]) or "-",
            )

        console.print(table)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise click.Abort()


@fabric.command()
@click.option('--mgd', required=True, type=click.Path(exists=True),
              help='Path to mesh graph descriptor (textproto) file.')
@click.option('--host', 'host_ids', multiple=True,
              help='Restrict to specific host IDs (can be repeated).')
@click.pass_context
def placement(ctx, mgd, host_ids):
    """Query valid placements for a mesh graph descriptor (requires fabric manager).

    Examples:

        \b
        tt-mgmt --fabric-endpoint localhost:50051 fabric placement --mgd my_mesh.textproto
        tt-mgmt fabric placement --mgd mesh.textproto --host host1 --host host2
    """
    console = ctx.obj['console']
    if not _require_fabric(console):
        return

    try:
        from tt_mgmt.backend.smi.core import get_placements

        with open(mgd, 'r') as f:
            mgd_text = f.read()

        result = get_placements(mgd_text, list(host_ids) if host_ids else None)

        if not result["success"]:
            console.print(f"[red]Placement failed: {result['status']}[/red]")
            if result["error_message"]:
                console.print(f"  [dim]{result['error_message']}[/dim]")
            return

        console.print(f"\n[bold green]Placement successful[/bold green]  "
                       f"({len(result['placements'])} valid placement(s))\n")

        for i, p_set in enumerate(result["placements"]):
            console.print(f"[bold]Placement {i + 1}:[/bold]")
            table = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
            table.add_column("Host", style="cyan")
            table.add_column("Rank", justify="right")
            table.add_column("ASICs")

            for assignment in p_set:
                asic_str = ", ".join(f"0x{a:x}" for a in assignment["asic_ids"])
                table.add_row(
                    assignment["host_id"],
                    str(assignment["rank"]),
                    asic_str,
                )
            console.print(table)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise click.Abort()


@fabric.command()
@click.pass_context
def health(ctx):
    """Per-host health status from fabric manager.

    Shows connected hosts, their status, and cluster health summary.

    Examples:

        \b
        tt-mgmt --fabric-endpoint localhost:50051 fabric health
    """
    console = ctx.obj['console']
    if not _require_fabric(console):
        return

    try:
        from tt_mgmt.backend.smi.core import get_cluster_topology

        info = get_cluster_topology()
        if info["error"]:
            console.print(f"[red]Error: {info['error']}[/red]")
            return

        console.print(f"\n[bold cyan]Cluster Health[/bold cyan]\n")

        total_asics = sum(h["asic_count"] for h in info["hosts"])
        all_connected = all(len(h["connected_hosts"]) > 0 for h in info["hosts"])

        if all_connected:
            console.print("  [bold green]Status: HEALTHY[/bold green]")
        else:
            console.print("  [bold yellow]Status: DEGRADED[/bold yellow]")

        console.print(f"  Hosts: {len(info['hosts'])}")
        console.print(f"  Total ASICs: {total_asics}")
        console.print(f"  Cross-host links: {info['total_cross_host_links']}")
        console.print()

        table = Table(box=box.ROUNDED, show_header=True, header_style="bold")
        table.add_column("Host", style="cyan")
        table.add_column("ASICs", justify="right")
        table.add_column("Peers", justify="right")
        table.add_column("Status")

        for host in info["hosts"]:
            peer_count = len(host["connected_hosts"])
            if peer_count > 0:
                status_text = Text("Connected", style="green")
            else:
                status_text = Text("Isolated", style="bold red")
            table.add_row(
                host["host_name"],
                str(host["asic_count"]),
                str(peer_count),
                status_text,
            )

        console.print(table)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise click.Abort()
