# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
# SPDX-License-Identifier: Apache-2.0

"""
Client library for tt-mgmtd.

Usage:
    import tt_mgmt.api

    # Connect to running daemon
    client = tt_mgmt.api.connect()

    # Or with explicit socket path
    client = tt_mgmt.api.connect(socket_path="/run/tt-mgmt/tt-mgmtd.sock")

    # Or embedded mode (no daemon needed)
    client = tt_mgmt.api.connect(embedded=True, backend="auto")

    devices = client.device_list()
    info = client.device_info(0)
    print(info["telemetry"]["temperature"])
"""

import json
import os
import socket
from typing import Any, Dict, List, Optional


def _default_socket_path() -> str:
    if "TT_MGMT_SOCKET" in os.environ:
        return os.environ["TT_MGMT_SOCKET"]
    if os.getuid() == 0:
        return "/run/tt-mgmt/tt-mgmtd.sock"
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return os.path.join(xdg, "tt-mgmt", "tt-mgmtd.sock")
    return f"/tmp/tt-mgmt-{os.getuid()}/tt-mgmtd.sock"


DEFAULT_SOCKET_PATH = _default_socket_path()


class TtMgmtClient:
    """Client for the tt-mgmtd JSON-RPC API."""

    def __init__(self, socket_path: Optional[str] = None, embedded: bool = False, backend: str = "auto"):
        self._embedded = embedded
        self._mgr = None
        self._sock_path = socket_path or DEFAULT_SOCKET_PATH

        if embedded:
            from tt_mgmt.backend.smi import native
            if backend == "sysfs":
                self._mgr = native.DeviceManager.create_sysfs()
            elif backend == "umd":
                self._mgr = native.DeviceManager.create_default()
            else:
                self._mgr = native.DeviceManager.create_auto()

    def _rpc_call(self, method: str, params: Optional[Dict] = None) -> Any:
        """Send a JSON-RPC request and return the result."""
        if self._embedded:
            return self._embedded_call(method, params or {})
        return self._socket_call(method, params or {})

    def _socket_call(self, method: str, params: dict) -> Any:
        """Send a JSON-RPC request over the Unix socket."""
        req = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        })
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self._sock_path)
            sock.sendall(req.encode() + b"\n")
            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(1048576)
                if not chunk:
                    break
                buf += chunk
        finally:
            sock.close()

        resp = json.loads(buf.strip())
        if "error" in resp:
            raise RuntimeError(resp["error"].get("message", str(resp["error"])))
        return resp.get("result")

    def _embedded_call(self, method: str, params: dict) -> Any:
        """Execute the RPC method in-process using the native DeviceManager."""
        if method == "device.list":
            return self._devices_to_dicts(self._mgr.discover())

        elif method == "device.count":
            return len(self._mgr.discover())

        elif method == "device.info":
            idx = params.get("index", 0)
            devices = self._mgr.discover()
            dev = devices[idx]
            self._mgr.update_telemetry(dev)
            self._mgr.update_memory(dev)
            result = self._device_to_dict(dev)
            result["processes"] = [self._process_to_dict(p, idx, dev) for p in dev.processes]
            return result

        elif method == "telemetry.update":
            idx = params.get("index", 0)
            devices = self._mgr.discover()
            dev = devices[idx]
            self._mgr.update_telemetry(dev)
            return self._device_to_dict(dev)

        elif method == "memory.update":
            idx = params.get("index", 0)
            devices = self._mgr.discover()
            dev = devices[idx]
            self._mgr.update_memory(dev)
            return self._device_to_dict(dev)

        elif method == "process.list":
            devices = self._mgr.discover()
            device_index = params.get("device_index")
            if device_index is not None:
                idx = int(device_index)
                dev = devices[idx]
                self._mgr.update_memory(dev)
                return [self._process_to_dict(p, idx, dev) for p in dev.processes]
            result = []
            for i, dev in enumerate(devices):
                self._mgr.update_memory(dev)
                for p in dev.processes:
                    result.append(self._process_to_dict(p, i, dev))
            return result

        elif method == "process.cleanup":
            return {"cleaned": self._mgr.cleanup_dead_processes()}

        elif method == "backend.name":
            return {"backend": self._mgr.backend_name()}

        else:
            raise ValueError(f"Unknown method: {method}")

    @staticmethod
    def _device_to_dict(dev):
        return {
            "chip_id": dev.chip_id,
            "asic_id": dev.asic_id,
            "board_id": dev.board_id,
            "pci_ordinal": dev.pci_ordinal,
            "pci_bdf": dev.pci_bdf,
            "arch_name": dev.arch_name,
            "is_remote": dev.is_remote,
            "serial": dev.serial,
            "card_type": dev.card_type,
            "display_id": dev.display_id,
            "telemetry": {
                "temperature": dev.telemetry.temperature,
                "board_temperature": dev.telemetry.board_temperature,
                "vreg_temperature": dev.telemetry.vreg_temperature,
                "power": dev.telemetry.power,
                "input_power_w": dev.telemetry.input_power_w,
                "voltage_mv": dev.telemetry.voltage_mv,
                "current_ma": dev.telemetry.current_ma,
                "tdc_limit_a": dev.telemetry.tdc_limit_a,
                "tdp_limit_w": dev.telemetry.tdp_limit_w,
                "aiclk_mhz": dev.telemetry.aiclk_mhz,
                "aiclk_limit_mhz": dev.telemetry.aiclk_limit_mhz,
                "axiclk_mhz": dev.telemetry.axiclk_mhz,
                "arcclk_mhz": dev.telemetry.arcclk_mhz,
                "ddr_speed_mhz": dev.telemetry.ddr_speed_mhz,
                "fan_speed_rpm": dev.telemetry.fan_speed_rpm,
                "status": dev.telemetry.status,
                "available": dev.telemetry.available,
            },
            "firmware": {
                "fw_bundle_ver": dev.firmware.fw_bundle_ver,
                "arc_fw_ver": dev.firmware.arc_fw_ver,
                "eth_fw_ver": dev.firmware.eth_fw_ver,
                "m3app_fw_ver": dev.firmware.m3app_fw_ver,
            },
            "memory": {
                "total_dram": dev.total_dram,
                "used_dram": dev.used_dram,
                "total_l1": dev.total_l1,
                "used_l1": dev.used_l1,
                "used_l1_small": dev.used_l1_small,
                "used_trace": dev.used_trace,
                "used_cb": dev.used_cb,
            },
            "has_shm": dev.has_shm,
        }

    @classmethod
    def _devices_to_dicts(cls, devices):
        return [cls._device_to_dict(d) for d in devices]

    @staticmethod
    def _process_to_dict(p, device_index, dev):
        return {
            "pid": p.pid,
            "name": p.name,
            "cmdline": p.cmdline,
            "registered": p.registered_to_device,
            "dram": p.dram_allocated,
            "l1": p.l1_allocated,
            "l1_small": p.l1_small_allocated,
            "trace": p.trace_allocated,
            "cb": p.cb_allocated,
            "runtime": p.runtime_seconds,
            "cpu_percent": p.cpu_percent,
            "vm_rss_kb": p.vm_rss_kb,
            "vm_virt_kb": p.vm_virt_kb,
            "vm_swap_kb": p.vm_swap_kb,
            "num_threads": p.num_threads,
            "device_index": device_index,
            "pci_bdf": dev.pci_bdf,
            "arch_name": dev.arch_name,
            "chip_id": dev.chip_id,
        }

    # ---- High-level API ----

    def device_list(self) -> List[Dict]:
        """List all detected devices."""
        return self._rpc_call("device.list")

    def device_count(self) -> int:
        """Get the number of detected devices."""
        return self._rpc_call("device.count")

    def device_info(self, index: int = 0) -> Dict:
        """Get full info (identity + telemetry + memory) for a device by index."""
        return self._rpc_call("device.info", {"index": index})

    def update_telemetry(self, index: int = 0) -> Dict:
        """Read fresh telemetry for a device."""
        return self._rpc_call("telemetry.update", {"index": index})

    def update_memory(self, index: int = 0) -> Dict:
        """Read fresh memory/process info for a device."""
        return self._rpc_call("memory.update", {"index": index})

    def process_list(self, device_index: Optional[int] = None) -> List[Dict]:
        """List processes across all devices, or for a specific device."""
        params = {}
        if device_index is not None:
            params["device_index"] = device_index
        return self._rpc_call("process.list", params)

    def cleanup_processes(self) -> int:
        """Clean up dead processes from SHM. Returns count cleaned."""
        result = self._rpc_call("process.cleanup")
        return result.get("cleaned", 0)

    def backend_name(self) -> str:
        """Get the name of the active backend."""
        result = self._rpc_call("backend.name")
        return result.get("backend", "unknown")


def connect(
    socket_path: Optional[str] = None,
    embedded: bool = False,
    backend: str = "auto",
) -> TtMgmtClient:
    """
    Connect to tt-mgmtd.

    Args:
        socket_path: Path to the Unix socket (default: /run/tt-mgmt/tt-mgmtd.sock)
        embedded: If True, run in-process with no daemon. Loads the native
                  backend directly (requires the C++ module to be built).
        backend: Backend selection for embedded mode: "auto", "umd", or "sysfs".

    Returns:
        A TtMgmtClient instance.
    """
    if not embedded and socket_path is None:
        socket_path = DEFAULT_SOCKET_PATH
        if not os.path.exists(socket_path):
            embedded = True

    return TtMgmtClient(socket_path=socket_path, embedded=embedded, backend=backend)
