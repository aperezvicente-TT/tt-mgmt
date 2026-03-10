# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
# SPDX-License-Identifier: Apache-2.0

"""CLI command: tt-mgmt record — metrics recording."""

import click


@click.command()
@click.option("-o", "--output", type=str, default=None,
              help="Output file path (default: stdout). Extension selects format unless --csv.")
@click.option("--csv", "csv_mode", is_flag=True,
              help="Force CSV output (default is JSONL).")
@click.option("-i", "--interval", type=str, default="1s",
              help="Sample interval (e.g. 500ms, 1s, 2.5s). Default: 1s.")
@click.option("-d", "--duration", type=str, default=None,
              help="Recording duration (e.g. 30s, 5m, 1h). Default: until Ctrl-C.")
@click.option("--pid", type=str, default=None,
              help="Comma-separated PIDs to track. Recording stops when all exit.")
@click.option("--exclude-pid", type=str, default=None,
              help="Comma-separated PIDs to always exclude from recording.")
@click.option("--exclude-name", type=str, default=None,
              help="Comma-separated process names (substring) to exclude. E.g. tt-mgmt,bash.")
@click.option("--per-pid-files", is_flag=True,
              help="Write a separate JSONL file for every PID seen (named <output>_<pid>.jsonl).")
@click.option("--groups", type=str, default=None,
              help="Comma-separated metric groups to record. "
                   "Available: device,telemetry,memory,process,process_alloc,gddr,fabric,firmware. "
                   "Default: device,telemetry,memory,process,process_alloc.")
@click.option("--exclude", type=str, default=None,
              help="Comma-separated metric groups to exclude from the default set.")
@click.option("--all-groups", is_flag=True, help="Record all metric groups.")
@click.option("--embedded", is_flag=True,
              help="Force embedded mode (no daemon). Creates its own DeviceManager.")
@click.option("--daemon", is_flag=True,
              help="Force daemon mode. Fail if tt-mgmtd is not running.")
@click.option("--max-size", type=str, default=None,
              help="Max output file size before rotation (e.g. 100M, 1G).")
@click.pass_context
def record(ctx, output, csv_mode, interval, duration, pid, exclude_pid, exclude_name,
           per_pid_files, groups, exclude, all_groups,
           embedded, daemon, max_size):
    """Record device metrics over time.

    Samples telemetry, memory, and process data at a configurable interval
    and writes to JSONL (default) or CSV.  Works against the running
    tt-mgmtd daemon or in standalone embedded mode.

    \b
    Examples:
        # Record all metrics to file, 1 sample/sec, until Ctrl-C
        tt-mgmt record -o run.jsonl

        # Profile a specific workload — stops when PID exits
        tt-mgmt record --pid 42317 -i 500ms -o profile.jsonl

        # Track multiple PIDs, stop when both exit
        tt-mgmt record --pid 42317,99001 -i 500ms -o profile.jsonl

        # Per-PID files: one JSONL per PID seen (run_42317.jsonl, run_99001.jsonl …)
        tt-mgmt record --per-pid-files -o run.jsonl

        # All PIDs except the daemon itself and a known system process
        tt-mgmt record --exclude-pid 1,2 --per-pid-files -o session.jsonl

        # Exclude all tt-mgmt processes by name (substring match)
        tt-mgmt record --exclude-name tt-mgmt --per-pid-files -o session.jsonl

        # Combine: exclude by name and extra PIDs
        tt-mgmt record --exclude-name tt-mgmt,bash --exclude-pid 1 -o run.jsonl

        # CSV output for pandas, only telemetry + process
        tt-mgmt record --csv --groups telemetry,process -o results.csv

        # Record only specific devices (filter by KMD ID / pci_ordinal)
        TT_VISIBLE_DEVICES=0,2 tt-mgmt record -d 5m -o session.jsonl

        # Everything including GDDR temps, rotate at 100MB
        tt-mgmt record --all-groups --max-size 100M -o long_run.jsonl

        # Stdout for piping
        tt-mgmt record -i 2s | jq '.devices[0].telemetry.temperature'
    """
    from tt_mgmt.recorder import (
        RecordingConfig, RecordingSession, MetricGroup,
        parse_interval, parse_duration,
    )

    console = ctx.obj.get("console")

    # Parse interval
    try:
        interval_sec = parse_interval(interval)
    except ValueError as e:
        click.echo(f"Error: invalid interval: {e}", err=True)
        raise click.Abort()

    # Parse duration
    duration_sec = None
    if duration:
        try:
            duration_sec = parse_duration(duration)
        except ValueError as e:
            click.echo(f"Error: invalid duration: {e}", err=True)
            raise click.Abort()

    # Parse PID filter (comma-separated, may be repeated)
    pid_filter = None
    if pid:
        try:
            pid_filter = {int(x.strip()) for x in pid.split(",")}
        except ValueError:
            click.echo("Error: --pid must be comma-separated integers", err=True)
            raise click.Abort()

    # Parse PID exclusion list
    pid_exclude: set = set()
    if exclude_pid:
        try:
            pid_exclude = {int(x.strip()) for x in exclude_pid.split(",")}
        except ValueError:
            click.echo("Error: --exclude-pid must be comma-separated integers", err=True)
            raise click.Abort()

    # Parse name exclusion list (substring match against process name)
    name_exclude: set = set()
    if exclude_name:
        name_exclude = {n.strip() for n in exclude_name.split(",") if n.strip()}

    # Parse metric groups
    if all_groups:
        metric_groups = MetricGroup.all()
    elif groups:
        try:
            metric_groups = MetricGroup.from_names(groups.split(","))
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            raise click.Abort()
    else:
        metric_groups = MetricGroup.default()

    if exclude:
        try:
            exclude_groups = MetricGroup.from_names(exclude.split(","))
            metric_groups = metric_groups & ~exclude_groups
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            raise click.Abort()

    # Auto-detect CSV from extension
    if output and output.endswith(".csv") and not csv_mode:
        csv_mode = True

    # Parse max-size
    max_size_bytes = None
    if max_size:
        s = max_size.strip().upper()
        try:
            if s.endswith("G"):
                max_size_bytes = int(float(s[:-1]) * 1024**3)
            elif s.endswith("M"):
                max_size_bytes = int(float(s[:-1]) * 1024**2)
            elif s.endswith("K"):
                max_size_bytes = int(float(s[:-1]) * 1024)
            else:
                max_size_bytes = int(s)
        except ValueError:
            click.echo(f"Error: invalid --max-size: {max_size}", err=True)
            raise click.Abort()

    config = RecordingConfig(
        groups=metric_groups,
        interval_sec=interval_sec,
        duration_sec=duration_sec,
        pid_filter=pid_filter,
        pid_exclude=pid_exclude,
        name_exclude=name_exclude,
        output_path=output,
        csv_mode=csv_mode,
        max_size_bytes=max_size_bytes,
        per_pid_files=per_pid_files,
        embedded=embedded,
        daemon=daemon,
        backend=ctx.obj.get("backend", "auto"),
    )

    # Print summary to stderr
    dest = output or "stdout"
    fmt = "CSV" if csv_mode else "JSONL"
    group_names = ", ".join(g.name.lower() for g in MetricGroup if g in metric_groups)
    dur_str = f"{duration}" if duration else "until Ctrl-C"
    parts = [
        f"Recording to {dest} ({fmt})",
        f"interval={interval}",
        f"duration={dur_str}",
        f"groups=[{group_names}]",
    ]
    if pid_filter:
        parts.append(f"pids={sorted(pid_filter)}")
    if pid_exclude:
        parts.append(f"exclude_pids={sorted(pid_exclude)}")
    if name_exclude:
        parts.append(f"exclude_names={sorted(name_exclude)}")
    if per_pid_files:
        parts.append("per-pid-files=on")
    # Show TT_VISIBLE_DEVICES if set
    import os
    vis = os.environ.get("TT_VISIBLE_DEVICES")
    if vis:
        parts.append(f"TT_VISIBLE_DEVICES={vis}")
    click.echo(" | ".join(parts), err=True)

    import time as _time

    output_files = []
    try:
        with RecordingSession(config=config) as session:
            session.run()
            n = session._seq
            elapsed = round(_time.time() - session._start_time, 1)
            output_files = session.output_files()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise click.Abort()

    click.echo(f"Done. {n} samples in {elapsed}s.", err=True)
    if output_files:
        click.echo("Files:", err=True)
        for f in sorted(output_files):
            click.echo(f"  {f}", err=True)
