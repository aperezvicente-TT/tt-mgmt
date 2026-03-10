# tt-mgmt

Tenstorrent device management CLI — standalone build against
[tt-umd](https://github.com/aperezvicente-TT/tt-umd) (no full
`tt-metal` checkout required).

## Features

- Multi-architecture device discovery (Wormhole, Blackhole, Quasar)
- Real-time telemetry: temperature, power, AICLK, voltage, current
- Per-process memory tracking (DRAM / L1 / L1-small / trace / CB) via shared memory
- Live TUI dashboard with graph support (`smi monitor -w`)
- Telemetry recording to JSONL/CSV and offline HTML plotting
- Ethernet fabric topology and link status
- Environment variable catalog and profile management
- Interactive shell with tab completion
- Dual backend: full UMD or lightweight sysfs-only (no native build needed)

## Quick start

```bash
git clone --recursive https://github.com/aperezvicente-TT/tt-mgmt.git
cd tt-mgmt
pip install -e .
or
uv pip install -e .

# Enable tab completion (current session)
eval "$(_TT_MGMT_COMPLETE=bash_source tt-mgmt)"

# Persistent completion (add to ~/.bashrc)
source ./setup_completion.sh
```

## Commands

### `smi` — System Management Interface

```bash
tt-mgmt smi monitor              # One-shot device overview
tt-mgmt smi monitor -w           # Live dashboard (watch mode)
tt-mgmt smi monitor -w -r 200    # Live dashboard, 200ms refresh
tt-mgmt smi monitor --json       # JSON output
tt-mgmt smi telemetry            # Temperature, power, clock per device
tt-mgmt smi memory               # DRAM/L1 utilization per device
tt-mgmt smi processes            # Processes using TT devices
tt-mgmt smi cleanup              # Remove stale shared-memory entries
```

### `device` — Device management

```bash
tt-mgmt device list              # List all devices (local + remote)
tt-mgmt device list --format json
tt-mgmt device info 0            # Detailed info for device 0
```


### `fabric` — Ethernet fabric topology

```bash
tt-mgmt fabric status            # Link summary (active/idle/exit)
tt-mgmt fabric links             # Per-device ethernet link table
tt-mgmt fabric topology          # Board-grouped connectivity view
tt-mgmt fabric cluster           # Multi-host cluster (needs fabric manager)
```

### `record` — Telemetry recording

```bash
tt-mgmt record -o session.jsonl                  # Record until Ctrl-C
tt-mgmt record -i 500ms -d 5m -o run.jsonl       # 500ms samples for 5 min
tt-mgmt record --pid 1234 -o trace.jsonl         # Track specific PID
tt-mgmt record --exclude-name tt-mgmt -o run.jsonl # Skip processes by name
tt-mgmt record --exclude-pid 5678 -o run.jsonl   # Skip specific PIDs
tt-mgmt record --per-pid-files -o run.jsonl       # Separate file per PID
tt-mgmt record --all-groups -o full.jsonl         # All metric groups
tt-mgmt record --groups telemetry,memory -o t.jsonl  # Select metric groups
tt-mgmt record --csv -o metrics.csv               # CSV output
tt-mgmt record --max-size 100M -o big.jsonl       # Rotate at 100MB
```

### `plot` — Offline plotting

```bash
tt-mgmt plot session.jsonl                     # Generate interactive HTML
tt-mgmt plot session.jsonl -t                  # Terminal summary table
tt-mgmt plot --live                            # Live-polling HTML dashboard
```



### Interactive mode

```bash
tt-mgmt                          # Launches interactive shell
```

All commands are available inside the shell with tab completion.

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

## License

See individual file headers for license information.
