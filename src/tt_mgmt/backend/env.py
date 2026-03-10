"""Environment variable management backend for tt-mgmt.

Provides a curated catalog of TT_METAL_* / TT_* environment variables
and helpers for reading the current shell environment and persisting
a user profile to ~/.config/tt-mgmt/env.json.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Curated catalog
# ---------------------------------------------------------------------------

ENV_CATALOG: List[Dict] = [
    # -----------------------------------------------------------------------
    # Core / Paths
    # -----------------------------------------------------------------------
    {
        "name": "TT_METAL_HOME",
        "category": "core",
        "description": "Root directory of TT-Metal install; used by all runtime components",
        "default": "current working directory",
        "example": "/opt/tt-metal",
    },
    {
        "name": "TT_METAL_VISIBLE_DEVICES",
        "category": "core",
        "description": "Comma-separated device IDs exposed to the Metal runtime (e.g. '0,1')",
        "default": "all devices",
        "example": "0,1",
    },
    {
        "name": "TT_VISIBLE_DEVICES",
        "category": "core",
        "description": "UMD-level device ordinal filter (distinct from TT_METAL_VISIBLE_DEVICES)",
        "default": "all devices",
        "example": "0",
    },
    {
        "name": "TT_METAL_SLOW_DISPATCH_MODE",
        "category": "core",
        "description": "Enable synchronous (slow) dispatch instead of async command queues",
        "default": "unset (fast dispatch enabled)",
        "example": "1",
    },
    {
        "name": "TT_METAL_LOGS_PATH",
        "category": "core",
        "description": "Directory for all debug/log output (dprint, watcher, profiler)",
        "default": "current working directory",
        "example": "/tmp/tt_logs",
    },
    {
        "name": "TT_METAL_CACHE",
        "category": "core",
        "description": "Directory for caching compiled kernels",
        "default": "system temp directory",
        "example": "/tmp/tt_cache",
    },
    {
        "name": "TT_METAL_KERNEL_PATH",
        "category": "core",
        "description": "Path to kernel source files",
        "default": "<TT_METAL_HOME>/tt_metal/kernels",
        "example": "/opt/tt-metal/tt_metal/kernels",
    },
    {
        "name": "TT_METAL_SIMULATOR",
        "category": "core",
        "description": "Path to simulator executable; enables simulation mode instead of real hardware",
        "default": "unset (hardware mode)",
        "example": "/opt/tt-metal/build/simulator",
    },
    # -----------------------------------------------------------------------
    # Debug
    # -----------------------------------------------------------------------
    {
        "name": "TT_METAL_WATCHER",
        "category": "debug",
        "description": "Enable on-chip watcher; value is poll interval in ms (e.g. '100'). Detects hangs, asserts, stack overflow, bad NoC transactions",
        "default": "unset (disabled)",
        "example": "100",
    },
    {
        "name": "TT_METAL_WATCHER_DUMP_ALL",
        "category": "debug",
        "description": "Dump all watcher data including potentially unsafe state",
        "default": "unset (disabled)",
        "example": "1",
    },
    {
        "name": "TT_METAL_WATCHER_APPEND",
        "category": "debug",
        "description": "Append to watcher log file instead of overwriting",
        "default": "unset (overwrite)",
        "example": "1",
    },
    {
        "name": "TT_METAL_WATCHER_PHYS_COORDS",
        "category": "debug",
        "description": "Print physical instead of logical core coordinates in watcher output",
        "default": "unset (logical coords)",
        "example": "1",
    },
    {
        "name": "TT_METAL_DPRINT_CORES",
        "category": "debug",
        "description": "Worker cores to enable DPRINT on. Values: 'all', 'worker', 'dispatch', '(x,y)', or range '(x0,y0)-(x1,y1)'",
        "default": "unset (disabled)",
        "example": "all",
    },
    {
        "name": "TT_METAL_DPRINT_ETH_CORES",
        "category": "debug",
        "description": "Ethernet cores to enable DPRINT on (same syntax as DPRINT_CORES)",
        "default": "unset (disabled)",
        "example": "all",
    },
    {
        "name": "TT_METAL_DPRINT_RISCVS",
        "category": "debug",
        "description": "RISC-V processors to print from, e.g. 'BR+NCRISC+TRISC0'",
        "default": "all processors",
        "example": "BR",
    },
    {
        "name": "TT_METAL_DPRINT_FILE",
        "category": "debug",
        "description": "Output file path for DPRINT output",
        "default": "stdout",
        "example": "/tmp/dprint.log",
    },
    {
        "name": "TT_METAL_DPRINT_CHIPS",
        "category": "debug",
        "description": "Chip IDs to print from ('all' or comma-separated IDs)",
        "default": "all chips",
        "example": "0,1",
    },
    # -----------------------------------------------------------------------
    # Profiling
    # -----------------------------------------------------------------------
    {
        "name": "TT_METAL_DEVICE_PROFILER",
        "category": "profiling",
        "description": "Enable device-side profiling. Requires a Tracy-enabled build (-DTRACY_ENABLE)",
        "default": "unset (disabled)",
        "example": "1",
    },
    {
        "name": "TT_METAL_MEM_PROFILER",
        "category": "profiling",
        "description": "Enable memory/buffer usage profiling",
        "default": "unset (disabled)",
        "example": "1",
    },
    {
        "name": "TT_METAL_TRACE_PROFILER",
        "category": "profiling",
        "description": "Enable trace profiling (requires DEVICE_PROFILER=1)",
        "default": "unset (disabled)",
        "example": "1",
    },
    {
        "name": "TT_METAL_PROFILE_PERF_COUNTERS",
        "category": "profiling",
        "description": "Bitfield selecting perf counter groups: 1=FPU, 2=PACK, 4=UNPACK, 8=L1, 16=INSTRN, 31=all",
        "default": "0 (disabled)",
        "example": "31",
    },
    {
        "name": "TT_METAL_PROFILER_SYNC",
        "category": "profiling",
        "description": "Synchronous profiling for more accurate timing (requires DEVICE_PROFILER=1)",
        "default": "unset (disabled)",
        "example": "1",
    },
    {
        "name": "TT_METAL_DEVICE_PROFILER_DISPATCH",
        "category": "profiling",
        "description": "Also profile dispatch cores (requires DEVICE_PROFILER=1)",
        "default": "unset (disabled)",
        "example": "1",
    },
    {
        "name": "TT_METAL_NOC_DEBUG_DUMP",
        "category": "profiling",
        "description": "Continuously dump NoC debug packets to file",
        "default": "unset (disabled)",
        "example": "1",
    },
    {
        "name": "TT_METAL_ARC_DEBUG_BUFFER_SIZE",
        "category": "profiling",
        "description": "Size in bytes of DRAM buffer for ARC processor debug samples (0 = disabled)",
        "default": "0 (disabled)",
        "example": "1048576",
    },
    # -----------------------------------------------------------------------
    # Fabric / Multi-host
    # -----------------------------------------------------------------------
    {
        "name": "TT_MESH_ID",
        "category": "fabric",
        "description": "ID of the local mesh on this host in a multi-host distributed setup",
        "default": "0",
        "example": "1",
    },
    {
        "name": "TT_MESH_HOST_RANK",
        "category": "fabric",
        "description": "Rank of this host within its mesh for multi-host distributed workloads",
        "default": "0",
        "example": "1",
    },
    {
        "name": "TT_MESH_GRAPH_DESC_PATH",
        "category": "fabric",
        "description": "Custom fabric mesh graph descriptor path",
        "default": "default fabric config",
        "example": "/opt/tt-metal/mesh_graph.yaml",
    },
    {
        "name": "RELIABILITY_MODE",
        "category": "fabric",
        "description": "Fabric reliability mode: STRICT/0, RELAXED/1, DYNAMIC/2",
        "default": "system default",
        "example": "RELAXED",
    },
    {
        "name": "TT_METAL_FABRIC_BW_TELEMETRY",
        "category": "fabric",
        "description": "Enable coarse-grain fabric bandwidth telemetry (0-2% performance impact)",
        "default": "unset (disabled)",
        "example": "1",
    },
    {
        "name": "TT_METAL_FABRIC_TELEMETRY",
        "category": "fabric",
        "description": "Full fabric telemetry. '1'=all, or spec: 'chips=all;eth=0,2;erisc=all;stats=ROUTER_STATE|BANDWIDTH'",
        "default": "unset (disabled)",
        "example": "1",
    },
    # -----------------------------------------------------------------------
    # Build / JIT
    # -----------------------------------------------------------------------
    {
        "name": "TT_METAL_FORCE_JIT_COMPILE",
        "category": "build",
        "description": "Force JIT recompilation of kernels even when cached binaries are up-to-date",
        "default": "unset (uses cache)",
        "example": "1",
    },
    {
        "name": "TT_METAL_CCACHE_KERNEL_SUPPORT",
        "category": "build",
        "description": "Use ccache when compiling kernels (presence-based)",
        "default": "unset (disabled)",
        "example": "1",
    },
    {
        "name": "TT_METAL_RISCV_DEBUG_INFO",
        "category": "build",
        "description": "Include DWARF debug info (-g) in RISC-V kernel binaries",
        "default": "inherits from inspector setting",
        "example": "1",
    },
    {
        "name": "TT_METAL_LOG_KERNELS_COMPILE_COMMANDS",
        "category": "build",
        "description": "Log kernel compilation commands to stdout",
        "default": "unset (disabled)",
        "example": "1",
    },
    {
        "name": "TT_METAL_OPERATION_TIMEOUT_SECONDS",
        "category": "build",
        "description": "Timeout in seconds for device operations (0 = no timeout)",
        "default": "0 (no timeout)",
        "example": "300",
    },
    # -----------------------------------------------------------------------
    # Hardware
    # -----------------------------------------------------------------------
    {
        "name": "TT_METAL_ENABLE_ERISC_IRAM",
        "category": "hardware",
        "description": "Enable ERISC IRAM (0=disabled, 1=enabled). Auto-disabled when watcher or dprint are active",
        "default": "1 (enabled)",
        "example": "0",
    },
    {
        "name": "TT_METAL_DISABLE_MULTI_AERISC",
        "category": "hardware",
        "description": "Disable multi-ERISC (2-ERISC) mode on Blackhole; fall back to single ERISC",
        "default": "unset (2-ERISC enabled on BH)",
        "example": "1",
    },
    {
        "name": "TT_METAL_SKIP_ETH_CORES_WITH_RETRAIN",
        "category": "hardware",
        "description": "Skip Ethernet cores that are currently retraining",
        "default": "1 (skip enabled)",
        "example": "0",
    },
    {
        "name": "TT_METAL_ENABLE_HW_CACHE_INVALIDATION",
        "category": "hardware",
        "description": "Enable Blackhole HW L1 data cache pseudo-random invalidation (debug tool)",
        "default": "unset (disabled)",
        "example": "1",
    },
    {
        "name": "TT_METAL_DISABLE_RELAXED_MEM_ORDERING",
        "category": "hardware",
        "description": "Disable Blackhole relaxed memory ordering (load can bypass store to different address)",
        "default": "unset (relaxed ordering enabled)",
        "example": "1",
    },
    {
        "name": "TT_METAL_NUMA_BASED_AFFINITY",
        "category": "hardware",
        "description": "Bind threads to NUMA nodes based on device locality in DeviceManager",
        "default": "unset (disabled)",
        "example": "1",
    },
]

# Sorted set of all known category names
CATEGORIES = sorted({v["category"] for v in ENV_CATALOG})

# ---------------------------------------------------------------------------
# Profile helpers
# ---------------------------------------------------------------------------

_PROFILE_PATH = Path.home() / ".config" / "tt-mgmt" / "env.json"


def get_profile_path() -> Path:
    return _PROFILE_PATH


def load_profile() -> Dict[str, str]:
    """Load saved env vars from the user profile file."""
    if not _PROFILE_PATH.exists():
        return {}
    try:
        with open(_PROFILE_PATH) as f:
            data = json.load(f)
        return {k: str(v) for k, v in data.items() if isinstance(k, str)}
    except (json.JSONDecodeError, OSError):
        return {}


def save_profile(profile: Dict[str, str]) -> None:
    """Write the profile dict back to disk."""
    _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_PROFILE_PATH, "w") as f:
        json.dump(profile, f, indent=2)
        f.write("\n")


def set_var(name: str, value: str) -> None:
    """Add or update a variable in the saved profile."""
    profile = load_profile()
    profile[name] = value
    save_profile(profile)


def unset_var(name: str) -> bool:
    """Remove a variable from the saved profile. Returns True if it existed."""
    profile = load_profile()
    if name in profile:
        del profile[name]
        save_profile(profile)
        return True
    return False


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_live_tt_vars() -> Dict[str, str]:
    """Return all TT_* variables currently set in the shell environment."""
    return {k: v for k, v in os.environ.items() if k.startswith("TT_") or k == "RELIABILITY_MODE" or k == "ARCH_NAME"}


def get_catalog_entry(name: str) -> Optional[Dict]:
    """Return the catalog entry for a given variable name, or None."""
    for entry in ENV_CATALOG:
        if entry["name"] == name:
            return entry
    return None


def get_catalog_by_category(category: Optional[str] = None) -> List[Dict]:
    """Return catalog entries, optionally filtered by category."""
    if category is None:
        return ENV_CATALOG
    return [e for e in ENV_CATALOG if e["category"] == category]
