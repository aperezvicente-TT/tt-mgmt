#!/usr/bin/env python3
# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
# SPDX-License-Identifier: Apache-2.0

"""
tt-mgmtd -- Tenstorrent Device Management Daemon.

Runs a DeviceManager and serves a JSON-RPC 2.0 API over a Unix socket,
with an optional HTTP/REST + Prometheus metrics endpoint.
"""

import argparse
import json
import os
import signal
import socket
import sys
import threading
import time
import struct
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

def _default_socket_path() -> str:
    """Return a writable socket path.

    Priority:
      1. TT_MGMT_SOCKET env var (explicit override)
      2. /run/tt-mgmt/tt-mgmtd.sock  (root / systemd)
      3. $XDG_RUNTIME_DIR/tt-mgmt/tt-mgmtd.sock  (per-user login session)
      4. /tmp/tt-mgmt-<uid>/tt-mgmtd.sock  (last resort)
    """
    if "TT_MGMT_SOCKET" in os.environ:
        return os.environ["TT_MGMT_SOCKET"]
    if os.getuid() == 0:
        return "/run/tt-mgmt/tt-mgmtd.sock"
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return os.path.join(xdg, "tt-mgmt", "tt-mgmtd.sock")
    return f"/tmp/tt-mgmt-{os.getuid()}/tt-mgmtd.sock"


SOCKET_PATH = _default_socket_path()
HTTP_PORT = int(os.environ.get("TT_MGMT_HTTP_PORT", "5391"))

# ---------------------------------------------------------------------------
# DeviceManager wrapper (uses native C++ backend)
# ---------------------------------------------------------------------------

_mgr = None
_mgr_lock = threading.Lock()


def _init_manager(backend: str = "auto"):
    global _mgr
    from tt_mgmt.backend.smi import native
    if backend == "sysfs":
        _mgr = native.DeviceManager.create_sysfs()
    elif backend == "umd":
        _mgr = native.DeviceManager.create_default()
    else:
        _mgr = native.DeviceManager.create_auto()


def _device_info_to_dict(dev):
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
            "power": dev.telemetry.power,
            "voltage_mv": dev.telemetry.voltage_mv,
            "current_ma": dev.telemetry.current_ma,
            "aiclk_mhz": dev.telemetry.aiclk_mhz,
            "vreg_temperature": dev.telemetry.vreg_temperature,
            "board_temperature": dev.telemetry.board_temperature,
            "axiclk_mhz": dev.telemetry.axiclk_mhz,
            "arcclk_mhz": dev.telemetry.arcclk_mhz,
            "ddr_speed_mhz": dev.telemetry.ddr_speed_mhz,
            "fan_speed_rpm": dev.telemetry.fan_speed_rpm,
            "tdp_limit_w": dev.telemetry.tdp_limit_w,
            "tdc_limit_a": dev.telemetry.tdc_limit_a,
            "aiclk_limit_mhz": dev.telemetry.aiclk_limit_mhz,
            "input_power_w": dev.telemetry.input_power_w,
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
        "processes": [
            {
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
            }
            for p in dev.processes
        ],
    }


def _process_to_dict(p, device_index: int, dev) -> dict:
    """Serialize a single ProcessMemory entry, annotated with device context."""
    return {
        "pid": p.pid,
        "name": p.name,
        "cmdline": p.cmdline,
        "registered": p.registered_to_device,
        # TT device memory allocations
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
        # Device context
        "device_index": device_index,
        "pci_bdf": dev.pci_bdf,
        "arch_name": dev.arch_name,
        "chip_id": dev.chip_id,
    }


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 dispatch
# ---------------------------------------------------------------------------

def _handle_rpc(method: str, params: dict):
    """Dispatch a JSON-RPC method call. Returns the result or raises."""
    with _mgr_lock:
        if method == "device.list":
            devices = _mgr.discover()
            return [_device_info_to_dict(d) for d in devices]

        elif method == "device.count":
            return len(_mgr.discover())

        elif method == "device.info":
            idx = params.get("index", 0)
            devices = _mgr.discover()
            if idx < 0 or idx >= len(devices):
                raise ValueError(f"Device index {idx} out of range (0..{len(devices)-1})")
            dev = devices[idx]
            _mgr.update_telemetry(dev)
            _mgr.update_memory(dev)
            return _device_info_to_dict(dev)

        elif method == "telemetry.update":
            idx = params.get("index", 0)
            devices = _mgr.discover()
            if idx < 0 or idx >= len(devices):
                raise ValueError(f"Device index {idx} out of range")
            dev = devices[idx]
            ok = _mgr.update_telemetry(dev)
            return _device_info_to_dict(dev) if ok else {"error": "telemetry update failed"}

        elif method == "memory.update":
            idx = params.get("index", 0)
            devices = _mgr.discover()
            if idx < 0 or idx >= len(devices):
                raise ValueError(f"Device index {idx} out of range")
            dev = devices[idx]
            _mgr.update_memory(dev)
            return _device_info_to_dict(dev)

        elif method == "process.list":
            # Return all processes across all devices, each entry annotated with device info.
            # Optional params: {"device_index": <int>} to restrict to one device.
            devices = _mgr.discover()
            device_index = params.get("device_index", None)
            if device_index is not None:
                idx = int(device_index)
                if idx < 0 or idx >= len(devices):
                    raise ValueError(f"Device index {idx} out of range (0..{len(devices)-1})")
                dev = devices[idx]
                _mgr.update_memory(dev)
                result = []
                for p in dev.processes:
                    result.append(_process_to_dict(p, device_index=idx, dev=dev))
                return result
            else:
                result = []
                for i, dev in enumerate(devices):
                    _mgr.update_memory(dev)
                    for p in dev.processes:
                        result.append(_process_to_dict(p, device_index=i, dev=dev))
                return result

        elif method == "process.cleanup":
            cleaned = _mgr.cleanup_dead_processes()
            return {"cleaned": cleaned}

        elif method == "backend.name":
            return {"backend": _mgr.backend_name()}

        else:
            raise ValueError(f"Unknown method: {method}")


def _make_rpc_response(req_id, result=None, error=None):
    resp = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        resp["error"] = {"code": -32000, "message": str(error)}
    else:
        resp["result"] = result
    return resp


# ---------------------------------------------------------------------------
# Unix socket server (JSON-RPC 2.0, newline-delimited JSON framing)
# ---------------------------------------------------------------------------

def _handle_socket_client(conn: socket.socket, addr):
    """Handle one client connection: read newline-delimited JSON-RPC requests."""
    buf = b""
    try:
        while True:
            data = conn.recv(65536)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                    method = req.get("method", "")
                    params = req.get("params", {})
                    req_id = req.get("id")
                    result = _handle_rpc(method, params)
                    resp = _make_rpc_response(req_id, result=result)
                except Exception as e:
                    resp = _make_rpc_response(req.get("id") if isinstance(req, dict) else None, error=e)
                conn.sendall(json.dumps(resp).encode() + b"\n")
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        conn.close()


def _run_unix_socket_server(socket_path: str, shutdown_event: threading.Event):
    """Listen on a Unix socket and accept clients."""
    sock_dir = os.path.dirname(socket_path)
    if sock_dir:
        os.makedirs(sock_dir, exist_ok=True)
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(socket_path)
    os.chmod(socket_path, 0o660)
    srv.listen(16)
    srv.settimeout(1.0)

    print(f"[tt-mgmtd] Listening on {socket_path}", file=sys.stderr)

    while not shutdown_event.is_set():
        try:
            conn, addr = srv.accept()
            t = threading.Thread(target=_handle_socket_client, args=(conn, addr), daemon=True)
            t.start()
        except socket.timeout:
            continue
        except OSError:
            break

    srv.close()
    if os.path.exists(socket_path):
        os.unlink(socket_path)


# ---------------------------------------------------------------------------
# HTTP REST + Prometheus metrics server
# ---------------------------------------------------------------------------

class _HTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress default logging

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json_response(self, code, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.rstrip("/")

        if path == "/api/v1/devices":
            try:
                result = _handle_rpc("device.list", {})
                self._json_response(200, result)
            except Exception as e:
                self._json_response(500, {"error": str(e)})

        elif path.startswith("/api/v1/devices/") and path.count("/") == 4:
            parts = path.split("/")
            try:
                idx = int(parts[4])
            except ValueError:
                self._json_response(400, {"error": "invalid device index"})
                return
            try:
                result = _handle_rpc("device.info", {"index": idx})
                self._json_response(200, result)
            except Exception as e:
                self._json_response(500, {"error": str(e)})

        elif path.startswith("/api/v1/devices/") and path.endswith("/telemetry"):
            parts = path.split("/")
            try:
                idx = int(parts[4])
            except ValueError:
                self._json_response(400, {"error": "invalid device index"})
                return
            try:
                result = _handle_rpc("telemetry.update", {"index": idx})
                self._json_response(200, result)
            except Exception as e:
                self._json_response(500, {"error": str(e)})

        elif path.startswith("/api/v1/devices/") and path.endswith("/processes"):
            parts = path.split("/")
            try:
                idx = int(parts[4])
            except ValueError:
                self._json_response(400, {"error": "invalid device index"})
                return
            try:
                result = _handle_rpc("process.list", {"device_index": idx})
                self._json_response(200, result)
            except Exception as e:
                self._json_response(500, {"error": str(e)})

        elif path == "/api/v1/processes":
            try:
                result = _handle_rpc("process.list", {})
                self._json_response(200, result)
            except Exception as e:
                self._json_response(500, {"error": str(e)})

        elif path == "/metrics":
            self._serve_prometheus_metrics()

        elif path == "/health":
            self._json_response(200, {"status": "ok"})

        else:
            self._json_response(404, {"error": "not found"})

    def _serve_prometheus_metrics(self):
        """Produce Prometheus text format metrics."""
        lines = []
        try:
            with _mgr_lock:
                devices = _mgr.discover()
                for i, dev in enumerate(devices):
                    _mgr.update_telemetry(dev)
                    _mgr.update_memory(dev)

                    dl = f'device="{i}",pci_bdf="{dev.pci_bdf}",arch="{dev.arch_name}"'

                    # -- Device telemetry --
                    if dev.telemetry.available:
                        if dev.telemetry.temperature >= 0:
                            lines.append(f'tt_temperature_celsius{{{dl}}} {dev.telemetry.temperature:.2f}')
                        vreg = getattr(dev.telemetry, "vreg_temperature", -1)
                        if vreg is not None and vreg >= 0:
                            lines.append(f'tt_vreg_temperature_celsius{{{dl}}} {vreg:.2f}')
                        if dev.telemetry.power >= 0:
                            lines.append(f'tt_power_watts{{{dl}}} {dev.telemetry.power:.2f}')
                        lines.append(f'tt_voltage_millivolts{{{dl}}} {dev.telemetry.voltage_mv}')
                        lines.append(f'tt_current_milliamps{{{dl}}} {dev.telemetry.current_ma}')
                        lines.append(f'tt_aiclk_mhz{{{dl}}} {dev.telemetry.aiclk_mhz}')
                        if dev.telemetry.fan_speed_rpm > 0:
                            lines.append(f'tt_fan_speed_rpm{{{dl}}} {dev.telemetry.fan_speed_rpm}')

                    # -- Device memory --
                    lines.append(f'tt_dram_total_bytes{{{dl}}} {dev.total_dram}')
                    lines.append(f'tt_dram_used_bytes{{{dl}}} {dev.used_dram}')
                    lines.append(f'tt_l1_total_bytes{{{dl}}} {dev.total_l1}')
                    lines.append(f'tt_l1_used_bytes{{{dl}}} {dev.used_l1}')
                    lines.append(f'tt_l1_small_used_bytes{{{dl}}} {getattr(dev, "used_l1_small", 0)}')
                    lines.append(f'tt_trace_used_bytes{{{dl}}} {getattr(dev, "used_trace", 0)}')
                    lines.append(f'tt_cb_used_bytes{{{dl}}} {getattr(dev, "used_cb", 0)}')

                    # -- Per-process metrics --
                    for p in dev.processes:
                        pl = f'{dl},pid="{p.pid}",process="{p.name}"'
                        lines.append(f'tt_process_cpu_percent{{{pl}}} {p.cpu_percent:.2f}')
                        lines.append(f'tt_process_runtime_seconds{{{pl}}} {p.runtime_seconds}')
                        lines.append(f'tt_process_vm_rss_bytes{{{pl}}} {p.vm_rss_kb * 1024}')
                        lines.append(f'tt_process_vm_virt_bytes{{{pl}}} {p.vm_virt_kb * 1024}')
                        lines.append(f'tt_process_vm_swap_bytes{{{pl}}} {p.vm_swap_kb * 1024}')
                        lines.append(f'tt_process_threads{{{pl}}} {p.num_threads}')
                        lines.append(f'tt_process_dram_bytes{{{pl}}} {p.dram_allocated}')
                        lines.append(f'tt_process_l1_bytes{{{pl}}} {p.l1_allocated}')
                        lines.append(f'tt_process_l1_small_bytes{{{pl}}} {getattr(p, "l1_small_allocated", 0)}')
                        lines.append(f'tt_process_trace_bytes{{{pl}}} {getattr(p, "trace_allocated", 0)}')
                        lines.append(f'tt_process_cb_bytes{{{pl}}} {p.cb_allocated}')

        except Exception as e:
            lines.append(f'# ERROR: {e}')

        body = "\n".join(lines).encode() + b"\n"
        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _run_http_server(port: int, shutdown_event: threading.Event):
    """Run the optional HTTP server for REST API + Prometheus metrics."""
    server = HTTPServer(("0.0.0.0", port), _HTTPHandler)
    server.timeout = 1.0
    print(f"[tt-mgmtd] HTTP server listening on :{port}", file=sys.stderr)
    while not shutdown_event.is_set():
        server.handle_request()
    server.server_close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Tenstorrent Device Management Daemon")
    parser.add_argument(
        "--backend", choices=["auto", "umd", "sysfs"], default="auto",
        help="Device backend (default: auto)")
    parser.add_argument(
        "--socket", default=SOCKET_PATH,
        help=f"Unix socket path (default: {SOCKET_PATH})")
    parser.add_argument(
        "--http-port", type=int, default=HTTP_PORT,
        help=f"HTTP port for REST/Prometheus (default: {HTTP_PORT}). 0 to disable.")
    parser.add_argument(
        "--no-http", action="store_true",
        help="Disable the HTTP server entirely.")
    args = parser.parse_args()

    print(f"[tt-mgmtd] Initializing with backend={args.backend}", file=sys.stderr)
    _init_manager(args.backend)

    shutdown = threading.Event()

    def on_signal(sig, frame):
        print(f"\n[tt-mgmtd] Shutting down...", file=sys.stderr)
        shutdown.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    # Start Unix socket server thread
    sock_thread = threading.Thread(
        target=_run_unix_socket_server, args=(args.socket, shutdown), daemon=True)
    sock_thread.start()

    # Start HTTP server thread (optional)
    http_thread = None
    if not args.no_http and args.http_port > 0:
        http_thread = threading.Thread(
            target=_run_http_server, args=(args.http_port, shutdown), daemon=True)
        http_thread.start()

    print(f"[tt-mgmtd] Ready. Backend: {_mgr.backend_name()}", file=sys.stderr)

    # Block until shutdown signal
    shutdown.wait()

    sock_thread.join(timeout=3)
    if http_thread:
        http_thread.join(timeout=3)

    print("[tt-mgmtd] Stopped.", file=sys.stderr)


if __name__ == "__main__":
    main()
