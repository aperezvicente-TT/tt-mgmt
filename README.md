# tt-mgmt

Production-ready monitoring and diagnostics for Tenstorrent accelerators —
standalone build against [tt-umd](https://github.com/aperezvicente-TT/tt-umd)
(no full `tt-metal` checkout required).

Five commands cover the full monitoring loop:

| Command  | What it's for                                                    |
|----------|------------------------------------------------------------------|
| `smi`    | Live TUI dashboard — temperature, power, clocks, per-process mem |
| `device` | Inventory: list devices, show full info per chip                 |
| `doctor` | One-shot health check across host, drivers, devices, daemon      |
| `record` | Sample telemetry to JSONL/CSV (workload profiling, post-mortem)  |
| `plot`   | Render recorded sessions as interactive HTML or terminal summary |

Plus a daemon (`tt-mgmtd`) that exposes the same telemetry as a Prometheus
scrape target with a prebuilt Grafana dashboard, for fleet-wide monitoring.

## Features

- Multi-architecture device discovery — Wormhole, Blackhole, Quasar; designed for **mixed BH+WH clusters**
- Real-time telemetry: temperature, power, AICLK, voltage, current, fan
- Per-process memory tracking (DRAM / L1 / L1-small / trace / CB) via shared memory
- Live TUI dashboard with graph support (`smi`)
- Telemetry recording to JSONL/CSV and offline HTML plotting
- Live-polling HTML dashboard (`plot --live`)
- Health-check command (`doctor`) with machine-readable output for CI/oncall
- Dual backend: full UMD or lightweight sysfs-only (no native build needed)
- **Daemon mode**: `tt-mgmtd` Prometheus exporter on `:5391`, systemd unit, prebuilt Grafana dashboard

## Quick start

```bash
git clone --recursive https://github.com/aperezvicente-TT/tt-mgmt.git
cd tt-mgmt
pip install -e .
# or:
uv pip install -e .

# Enable tab completion (current session)
eval "$(_TT_MGMT_COMPLETE=bash_source tt-mgmt)"

# Persistent completion (add to ~/.bashrc)
source ./setup_completion.sh
```

## Commands

### `smi` — Live system management interface

Real-time TUI dashboard: telemetry, clocks, memory, per-process allocations.
The single command you run on a host to *see what the chips are doing right now*.

```bash
tt-mgmt smi                      # Live TUI dashboard (Ctrl-C to exit)
tt-mgmt smi -i 200ms             # Faster refresh (default 1s)
tt-mgmt smi -g                   # Start on the Graphs tab
tt-mgmt smi status               # One-shot snapshot (no TUI)
tt-mgmt smi status --json        # Machine-readable snapshot for scripts
tt-mgmt smi cleanup              # Remove stale /dev/shm entries
```

### `device` — Inventory

Enumerate devices and inspect static/dynamic info per chip. Useful for asset
tracking, board-id correlation, and confirming `TT_VISIBLE_DEVICES` filtering.

```bash
tt-mgmt device list              # List all devices (local + remote)
tt-mgmt device list --format json
tt-mgmt device info 0            # Full info for device 0
```

### `doctor` — Health check

One-shot validation of host, drivers, hugepages, devices, and daemon state.
Designed for CI gates and oncall — exits non-zero on errors so you can wire it
into deployment pipelines.

```bash
tt-mgmt doctor                   # Human-readable check report
tt-mgmt doctor --verbose         # Show details for every check
tt-mgmt doctor --json            # Machine-readable for CI / monitoring
```

Exit codes: `0` all pass, `1` any error, `2` warnings only.

### `record` — Telemetry capture

Sample telemetry, memory, and per-process metrics to JSONL or CSV.
Use during workload runs for post-mortem analysis or longitudinal profiling.

```bash
tt-mgmt record -o session.jsonl                       # Until Ctrl-C
tt-mgmt record -i 500ms -d 5m -o run.jsonl            # 500ms samples, 5 min
tt-mgmt record -i 250 -o run.jsonl                    # Bare numbers = ms
tt-mgmt record --pid 1234 -o trace.jsonl              # Track a specific PID
tt-mgmt record --exclude-name tt-mgmt -o run.jsonl    # Skip tt-mgmt processes
tt-mgmt record --per-pid-files -o run.jsonl           # Separate file per PID
tt-mgmt record --all-groups -o full.jsonl             # Every metric group
tt-mgmt record --groups telemetry,memory -o t.jsonl   # Select groups
tt-mgmt record --csv -o metrics.csv                   # CSV (for pandas)
tt-mgmt record --max-size 100M -o big.jsonl           # Rotate at 100MB
```

Metric groups: `device`, `telemetry`, `memory`, `process`, `process_alloc`,
`gddr`, `fabric`, `firmware`. Default: everything except `gddr`/`fabric`/`firmware`.

### `plot` — Offline visualization

Turn recorded sessions into interactive HTML reports, or stream a live
auto-refreshing dashboard against a running daemon.

```bash
tt-mgmt plot session.jsonl                     # Interactive HTML report
tt-mgmt plot session.jsonl -t                  # Terminal summary tables
tt-mgmt plot session_<pid>.jsonl               # Per-PID file from --per-pid-files
tt-mgmt plot --live                            # Live-polling HTML dashboard
```

## Daemon mode (Prometheus + Grafana)

`tt-mgmtd` exposes telemetry as a Prometheus scrape target on `:5391`.

```bash
# Install systemd unit
sudo cp systemd/tt-mgmtd.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tt-mgmtd

# Or run manually
tt-mgmtd --backend=auto

# Point Prometheus at it (see prometheus.yml for a working example)
docker run -d --name prometheus --net=host \
  -v $(pwd)/prometheus.yml:/etc/prometheus/prometheus.yml \
  prom/prometheus \
  --config.file=/etc/prometheus/prometheus.yml \
  --storage.tsdb.min-block-duration=30s

# Import grafana-dashboard.json into Grafana for the prebuilt view.
```

`prometheus.yml` defaults to a single `localhost:5391` target; uncomment the `Multi-host` block for cluster-wide scraping.

## Backend selection

```bash
tt-mgmt --backend auto ...       # Try UMD, fall back to sysfs (default)
tt-mgmt --backend umd ...        # Full UMD (topology discovery, remote devices)
tt-mgmt --backend sysfs ...      # Lightweight: /sys/class/tenstorrent + hwmon
```

Or set `TT_MGMT_BACKEND=sysfs` in your environment.

## Prerequisites

- Python 3.9+
- C++20 compiler (GCC 11+ / Clang 14+)
- CMake 3.16+
- [uv](https://github.com/astral-sh/uv) or pip

## Repository layout

```
tt-mgmt/
├── cpp/
│   ├── bindings/          # nanobind Python ↔ C++ bridge
│   ├── include/           # Public C++ headers (device_manager, types)
│   └── src/
│       ├── providers/     # UMD, sysfs, SHM, fabric-manager backends
│       └── topology/      # Multi-arch topology discovery
├── src/tt_mgmt/
│   ├── cli.py             # Click entry point
│   ├── interactive.py     # Interactive shell (prompt-toolkit)
│   ├── recorder.py        # Metric recording engine
│   ├── daemon.py          # tt-mgmtd prometheus exporter
│   ├── commands/          # CLI command groups
│   ├── backend/           # Python backend layer
│   │   └── smi/           # SMI core + Rich TUI dashboard
│   └── api/               # REST client for daemon mode
├── systemd/               # tt-mgmtd.service
├── third_party/
│   └── tt-umd/            # Git submodule (only native dependency)
├── CMakeLists.txt
└── pyproject.toml
```

## Building with a custom tt-umd path

```bash
pip install -e . --config-settings cmake.args="-DTT_UMD_HOME=/path/to/tt-umd"
```

## Architecture

The C++ layer (`cpp/`) uses tt-umd directly for device discovery and telemetry:

- **DeviceManager** — provider-based architecture with pluggable backends
- **UMD providers** — topology discovery, ARC telemetry, PCIe device access
- **Sysfs providers** — lightweight reads from `/sys/class/tenstorrent/`
- **SHM provider** — reads per-process memory stats from `/dev/shm/tt_device_*`
- **Fabric provider** — optional gRPC integration with tt-fabric-manager

The Python layer communicates with the native extension via nanobind bindings
(`cpp/bindings/native.cpp`).

### Mixed-architecture clusters (BH + WH)

UMD's `TopologyDiscovery::discover()` validates board topology globally and aborts on mixed
architectures (`cluster_descriptor.cpp:1317` throws `Chips with differing architectures
detected`). Until that lands upstream, `tt-mgmt` ships two paths:

- **Production**: single `discover()` call combined with a `TT_VISIBLE_DEVICES` filter so only
  one architecture's devices participate in any given discovery. See `MULTI_ARCH_DISCOVERY.md`.
- **Patches** (`UMD_MULTI_ARCH.patch`, `UMD_PATCH_MIXED_ARCH.md`) — drop-in fixes for the
  bundled `third_party/tt-umd` submodule that lift the homogeneity check.
- **Proposed rearchitecture** (`UMD_MULTI_ARCH_REARCHITECTURE.md`) — per-architecture cluster
  isolation so each arch is discovered into its own descriptor; needs upstream UMD changes
  because `BlackholeTTDevice`/`WormholeTTDevice` constructors are `protected`.

See `BUILD_INSTRUCTIONS.md` for build details and `SCRIPTS.md` for the install scripts reference.

## License

See individual file headers for license information.
