# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
# SPDX-License-Identifier: Apache-2.0

"""Core API for tt-mgmt -- thin Python wrapper around the native DeviceManager."""

import os
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import native


# Module-level DeviceManager singleton, created lazily.
_manager = None
_current_backend: str | None = None


_fabric_endpoint: str | None = None


def _get_manager(backend: str = "auto", fabric_endpoint: str | None = None):
    """Get or create the DeviceManager singleton."""
    global _manager, _current_backend, _fabric_endpoint
    if _manager is None:
        if native is None:
            raise RuntimeError("Native backend not built. Install tt-mgmt with C++ backend.")
        if fabric_endpoint:
            _manager = native.DeviceManager.create_with_fabric(fabric_endpoint)
        elif backend == "sysfs":
            _manager = native.DeviceManager.create_sysfs()
        elif backend == "umd":
            _manager = native.DeviceManager.create_default()
        else:
            _manager = native.DeviceManager.create_auto()
        _current_backend = backend
        _fabric_endpoint = fabric_endpoint
    return _manager


def set_backend(backend: str, fabric_endpoint: str | None = None):
    """Set the backend, only recreating the manager if the choice changed."""
    global _manager, _current_backend, _fabric_endpoint
    if (_manager is not None
            and _current_backend == backend
            and _fabric_endpoint == fabric_endpoint):
        return
    _manager = None
    _current_backend = None
    _fabric_endpoint = None
    _get_manager(backend, fabric_endpoint=fabric_endpoint)


def get_backend() -> str:
    """Return the currently active backend name."""
    return _current_backend or "auto"


class Device:
    """Thin proxy around native DeviceInfo with convenience properties."""

    def __init__(self, native_dev):
        self._native = native_dev

    # -- Identity (forwarded from native DeviceInfo) --
    @property
    def chip_id(self): return self._native.chip_id
    @property
    def asic_id(self): return self._native.asic_id
    @property
    def board_id(self): return self._native.board_id
    @property
    def pci_ordinal(self): return self._native.pci_ordinal
    @property
    def logical_id(self): return self._native.logical_id
    @property
    def pci_bdf(self): return self._native.pci_bdf
    @property
    def arch_name(self): return self._native.arch_name
    @property
    def is_remote(self): return self._native.is_remote
    @property
    def serial(self): return self._native.serial
    @property
    def card_type(self): return self._native.card_type
    @property
    def display_id(self): return self._native.display_id
    @property
    def tray_id(self): return self._native.tray_id
    @property
    def chip_in_tray(self): return self._native.chip_in_tray

    # -- Telemetry (forwarded from native TelemetryData) --
    @property
    def temperature(self): return self._native.telemetry.temperature
    @property
    def power(self): return self._native.telemetry.power
    @property
    def voltage_mv(self): return self._native.telemetry.voltage_mv
    @property
    def current_ma(self): return self._native.telemetry.current_ma
    @property
    def aiclk_mhz(self): return self._native.telemetry.aiclk_mhz
    @property
    def vreg_temperature(self): return self._native.telemetry.vreg_temperature
    @property
    def board_temperature(self): return self._native.telemetry.board_temperature
    @property
    def axiclk_mhz(self): return self._native.telemetry.axiclk_mhz
    @property
    def arcclk_mhz(self): return self._native.telemetry.arcclk_mhz
    @property
    def ddr_speed_mhz(self): return self._native.telemetry.ddr_speed_mhz
    @property
    def fan_speed_pct(self): return self._native.telemetry.fan_speed_pct
    @property
    def fan_speed_rpm(self): return self._native.telemetry.fan_speed_rpm
    @property
    def tdp_limit_w(self): return self._native.telemetry.tdp_limit_w
    @property
    def tdc_limit_a(self): return self._native.telemetry.tdc_limit_a
    @property
    def aiclk_limit_mhz(self): return self._native.telemetry.aiclk_limit_mhz
    @property
    def input_power_w(self): return self._native.telemetry.input_power_w
    @property
    def max_gddr_temp(self): return self._native.telemetry.max_gddr_temp
    @property
    def gddr01_temp(self): return self._native.telemetry.gddr01_temp
    @property
    def gddr23_temp(self): return self._native.telemetry.gddr23_temp
    @property
    def gddr45_temp(self): return self._native.telemetry.gddr45_temp
    @property
    def gddr67_temp(self): return self._native.telemetry.gddr67_temp
    @property
    def telemetry_status(self): return self._native.telemetry.status

    # -- Firmware --
    @property
    def firmware(self): return self._native.firmware

    # -- Memory --
    @property
    def total_dram(self): return self._native.total_dram
    @property
    def used_dram(self): return self._native.used_dram
    @property
    def total_l1(self): return self._native.total_l1
    @property
    def used_l1(self): return self._native.used_l1
    @property
    def used_l1_small(self): return self._native.used_l1_small
    @property
    def used_trace(self): return self._native.used_trace
    @property
    def used_cb(self): return self._native.used_cb
    @property
    def has_shm(self): return self._native.has_shm

    # -- Topology (populated by UMD backend) --
    @property
    def eth_connections(self):
        return [
            {
                "local_channel": c.local_channel,
                "remote_chip_id": c.remote_chip_id,
                "remote_channel": c.remote_channel,
                "is_exit_link": c.is_exit_link,
            }
            for c in self._native.eth_connections
        ]

    @property
    def eth_coord(self):
        c = self._native.eth_coord
        return {"cluster_id": c.cluster_id, "x": c.x, "y": c.y, "rack": c.rack, "shelf": c.shelf}

    @property
    def active_eth_channels(self): return self._native.active_eth_channels
    @property
    def idle_eth_channels(self): return self._native.idle_eth_channels
    @property
    def is_mmio_capable(self): return self._native.is_mmio_capable

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists but owned by another user

    @property
    def processes(self):
        return [
            {
                "pid": p.pid,
                "name": p.name,
                "cmdline": p.cmdline,
                "registered": p.registered_to_device,
                # TT device allocations
                "dram": p.dram_allocated,
                "l1": p.l1_allocated,
                "l1_small": p.l1_small_allocated,
                "trace": p.trace_allocated,
                "cb": p.cb_allocated,
                # CPU / host-side metrics
                "runtime": p.runtime_seconds,
                "cpu_percent": p.cpu_percent,
                "vm_rss_kb": p.vm_rss_kb,
                "vm_virt_kb": p.vm_virt_kb,
                "vm_swap_kb": p.vm_swap_kb,
                "num_threads": p.num_threads,
            }
            for p in self._native.processes
            if self._pid_alive(p.pid)
        ]

    # -- Computed properties --
    @property
    def dram_utilization(self) -> float:
        if self.total_dram == 0:
            return 0.0
        return (self.used_dram / self.total_dram) * 100.0

    @property
    def l1_utilization(self) -> float:
        if self.total_l1 == 0:
            return 0.0
        total_l1_used = self.used_l1 + self.used_l1_small + self.used_cb
        return (total_l1_used / self.total_l1) * 100.0


def get_devices() -> List[Device]:
    """Discover all Tenstorrent devices."""
    mgr = _get_manager()
    native_devices = mgr.discover()
    return [Device(d) for d in native_devices]


def update_telemetry(device: Device) -> bool:
    """Update telemetry for a single device."""
    mgr = _get_manager()
    return mgr.update_telemetry(device._native)


def update_telemetry_parallel(devices: List[Device], timeout: float = 1.0) -> None:
    """Update telemetry for all devices in parallel."""

    def update_one(dev: Device) -> None:
        try:
            success = update_telemetry(dev)
            if not success and dev.telemetry_status == "Unknown":
                dev._native.telemetry.status = "Error"
        except Exception:
            dev._native.telemetry.status = "Error"

    with ThreadPoolExecutor(max_workers=len(devices)) as executor:
        futures = {executor.submit(update_one, dev): dev for dev in devices}
        try:
            for future in as_completed(futures, timeout=timeout):
                try:
                    future.result(timeout=0.1)
                except Exception:
                    pass
        except TimeoutError:
            for future, dev in futures.items():
                if not future.done():
                    dev._native.telemetry.status = "Timeout"


def update_memory(device: Device) -> bool:
    """Update memory stats for a device."""
    mgr = _get_manager()
    return mgr.update_memory(device._native)


def cleanup_dead_processes() -> int:
    """Clean up dead processes from SHM. Returns number cleaned."""
    mgr = _get_manager()
    return mgr.cleanup_dead_processes()


def format_bytes(bytes_val: int) -> str:
    """Format bytes with units (KiB, MiB, GiB)."""
    return native.format_bytes(bytes_val)


# ---- Fabric (Phase 2) ----

def has_fabric() -> bool:
    """Whether a fabric manager is connected."""
    mgr = _get_manager()
    return mgr.has_fabric()


def get_cluster_topology():
    """Get cluster topology from fabric manager. Returns a FabricClusterInfo-like object."""
    mgr = _get_manager()
    info = mgr.get_cluster_topology()
    return {
        "connected": info.connected,
        "error": info.error,
        "total_cross_host_links": info.total_cross_host_links,
        "hosts": [
            {
                "host_name": h.host_name,
                "asic_count": h.asic_count,
                "arch": h.arch,
                "connected_hosts": list(h.connected_hosts),
            }
            for h in info.hosts
        ],
    }


def get_placements(mgd_textproto: str, host_ids: list | None = None):
    """Query valid placements for a mesh graph descriptor."""
    mgr = _get_manager()
    result = mgr.get_placements(mgd_textproto, host_ids or [])
    return {
        "success": result.success,
        "status": result.status,
        "error_message": result.error_message,
        "placements": [
            [
                {
                    "host_id": a.host_id,
                    "rank": a.rank,
                    "asic_ids": list(a.asic_ids),
                }
                for a in placement
            ]
            for placement in result.placements
        ],
    }
