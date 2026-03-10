# tt-mgmt

Tenstorrent Control and Management CLI — standalone build against
[tt-umd](https://github.com/aperezvicente-TT/tt-umd) only (no full
`tt-metal` checkout required).

## Features

- Device discovery across multiple architectures (Wormhole, Blackhole, Quasar)
- Per-device telemetry: temperature, power, AICLK, voltage, current
- Per-process memory tracking (DRAM / L1 / L1-small / trace / CB) via shared memory
- Live `smi monitor` dashboard (Rich TUI)
- Interactive shell with tab completion

## Prerequisites

- Python 3.9+
- C++20 compiler (GCC 11+ / Clang 14+)
- CMake 3.16+
- [uv](https://github.com/astral-sh/uv) or pip with `scikit-build-core` and `nanobind`

## Quick start

```bash
# Clone and initialise submodule
git clone git@github.com:aperezvicente-TT/tt-mgmt-main.git
cd tt-mgmt-main
git submodule update --init --recursive

# Install (builds the native extension automatically via scikit-build-core)
pip install -e .
# or
./install.sh

# Run
tt-mgmt smi monitor
tt-mgmt device list
tt-mgmt --interactive
```

## Repository layout

```
tt-mgmt-main/
├── cpp/
│   ├── bindings/        # nanobind Python extension entry point
│   ├── include/         # Public C++ headers (tt_device_hal)
│   └── src/
│       ├── device_hal/  # HAL adaptor layer
│       └── smi/         # tt-umd backend (device discovery, telemetry, SHM)
├── src/tt_mgmt/          # Python CLI and TUI
├── third_party/
│   └── tt-umd/          # git submodule — only dependency
├── CMakeLists.txt
└── pyproject.toml
```

## Building with a custom tt-umd path

If you already have a tt-umd checkout and don't want to use the submodule:

```bash
pip install -e . --config-settings cmake.args="-DTT_UMD_HOME=/path/to/tt-umd"
```

## Architecture

`tt_smi_backend.cpp` uses `tt-umd` directly:
- `MultiArchTopologyDiscovery` — discovers all attached chips across architectures
- `TTDevice` / `PCIDevice` — low-level device access
- `ArcTelemetryReader` / `FirmwareInfoProvider` — telemetry
- Shared-memory region (`/dev/shm/tt_device_*_memory`) for live Metal runtime stats

The Python layer (`src/tt_mgmt/`) communicates with the native extension via
`nanobind` bindings in `cpp/bindings/native.cpp`.
