# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
# SPDX-License-Identifier: Apache-2.0

"""
Metrics recorder for tt-mgmt.

Samples device telemetry, memory, and process metrics at a configurable
interval and writes them to JSONL or CSV output.  Works against the
tt-mgmtd daemon (socket/HTTP) or in embedded mode (direct C++ calls).

Usage — CLI:
    tt-mgmt record -o run.jsonl
    tt-mgmt record --pid 42317 --interval 500ms -o profile.jsonl
    tt-mgmt record --csv --groups telemetry,process -o results.csv
    tt-mgmt record --per-pid-files -o session.jsonl          # -> session_<pid>.jsonl per PID
    tt-mgmt record --exclude-pid 1,2 -o run.jsonl            # exclude specific PIDs

Usage — Python:
    from tt_mgmt.recorder import RecordingSession
    with RecordingSession(output="run.jsonl") as session:
        session.run()
"""

from __future__ import annotations

import enum
import json
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Dict, Iterator, List, Optional, Set


class MetricGroup(enum.Flag):
    DEVICE       = enum.auto()
    TELEMETRY    = enum.auto()
    MEMORY       = enum.auto()
    PROCESS      = enum.auto()
    PROCESS_ALLOC = enum.auto()
    GDDR         = enum.auto()
    FABRIC       = enum.auto()
    FIRMWARE     = enum.auto()

    @classmethod
    def default(cls) -> "MetricGroup":
        return cls.DEVICE | cls.TELEMETRY | cls.MEMORY | cls.PROCESS | cls.PROCESS_ALLOC

    @classmethod
    def all(cls) -> "MetricGroup":
        result = cls(0)
        for member in cls:
            result |= member
        return result

    @classmethod
    def from_names(cls, names: List[str]) -> "MetricGroup":
        result = cls(0)
        lookup = {m.name.lower(): m for m in cls}
        for n in names:
            key = n.strip().lower()
            if key not in lookup:
                raise ValueError(f"Unknown metric group: {n!r}. Available: {', '.join(lookup)}")
            result |= lookup[key]
        return result


def parse_duration(s: str) -> float:
    """Parse a human duration string (e.g. '5m', '1h30m', '90s') to seconds."""
    s = s.strip().lower()
    if not s:
        raise ValueError("Empty duration")
    total = 0.0
    buf = ""
    for ch in s:
        if ch.isdigit() or ch == '.':
            buf += ch
        elif ch in ('s', 'm', 'h', 'd'):
            if not buf:
                raise ValueError(f"Missing number before '{ch}' in duration: {s}")
            val = float(buf)
            buf = ""
            if ch == 's':
                total += val
            elif ch == 'm':
                total += val * 60
            elif ch == 'h':
                total += val * 3600
            elif ch == 'd':
                total += val * 86400
        else:
            raise ValueError(f"Unexpected character '{ch}' in duration: {s}")
    if buf:
        total += float(buf)
    return total


def parse_interval(s: str) -> float:
    """Parse an interval string (e.g. '500ms', '1s', '2.5s') to seconds.

    A bare number with no unit is treated as milliseconds (so ``-i 250`` means
    250 ms).  Use an explicit unit (``s``/``m``/``h``/``d``) for anything else.
    """
    s = s.strip().lower()
    if s.endswith("ms"):
        return float(s[:-2]) / 1000.0
    try:
        return float(s) / 1000.0
    except ValueError:
        pass
    return parse_duration(s)


@dataclass
class RecordingConfig:
    groups: MetricGroup = field(default_factory=MetricGroup.default)
    interval_sec: float = 1.0
    duration_sec: Optional[float] = None
    # pid_filter: only record processes with these PIDs (None = all).
    # When per_pid_files is True the recorder tracks every PID it sees and
    # writes a separate JSONL for each one.
    pid_filter: Optional[Set[int]] = None
    # PIDs to always exclude from recording (e.g. the recorder itself).
    pid_exclude: Set[int] = field(default_factory=set)
    # Process names (substring match) to always exclude — e.g. {"tt-mgmt", "bash"}.
    name_exclude: Set[str] = field(default_factory=set)
    output_path: Optional[str] = None
    csv_mode: bool = False
    max_size_bytes: Optional[int] = None
    # Write one JSONL file per PID instead of (or in addition to) the main file.
    per_pid_files: bool = False
    # Connection mode
    embedded: bool = False
    daemon: bool = False
    backend: str = "auto"


class _OutputSink:
    """Handles file output with optional rotation."""

    def __init__(self, path: Optional[str], csv_mode: bool, max_size: Optional[int]):
        self._base_path = path
        self._csv_mode = csv_mode
        self._max_size = max_size
        self._file_num = 0
        self._fh: Optional[IO] = None
        self._bytes_written = 0
        self._header_written = False

    def _current_path(self) -> str:
        if not self._base_path:
            return ""
        if self._max_size and self._file_num > 0:
            p = Path(self._base_path)
            return str(p.with_stem(f"{p.stem}_{self._file_num:03d}"))
        return self._base_path

    def open(self):
        if self._base_path:
            self._fh = open(self._current_path(), "w", buffering=1)
        else:
            self._fh = sys.stdout

    def close(self):
        if self._fh and self._fh is not sys.stdout:
            self._fh.close()
        self._fh = None

    def _maybe_rotate(self):
        if not self._max_size or not self._base_path:
            return
        if self._bytes_written >= self._max_size:
            self.close()
            self._file_num += 1
            self._bytes_written = 0
            self._header_written = False
            self.open()

    def write_line(self, line: str):
        self._maybe_rotate()
        data = line + "\n"
        self._fh.write(data)
        self._bytes_written += len(data.encode())

    def write_csv_header(self, fields: List[str]):
        if not self._header_written:
            self.write_line(",".join(fields))
            self._header_written = True

    @property
    def display_path(self) -> str:
        return self._base_path or "<stdout>"


class _PerPidSinkManager:
    """Creates and manages one _OutputSink per PID, writing per-PID JSONL files.

    File naming: if base_path is ``run.jsonl`` and PID is 12345, the file is
    ``run_12345.jsonl``.  With no base_path files are written to the current
    directory as ``pid_<pid>.jsonl``.
    """

    def __init__(self, base_path: Optional[str], max_size: Optional[int],
                 meta: Dict[str, Any]):
        self._base_path = base_path
        self._max_size = max_size
        self._meta = meta           # shared header fields (hostname, interval_ms, …)
        self._sinks: Dict[int, _OutputSink] = {}

    def _pid_path(self, pid: int) -> str:
        if self._base_path:
            p = Path(self._base_path)
            return str(p.with_stem(f"{p.stem}_{pid}"))
        return f"pid_{pid}.jsonl"

    def sink_for(self, pid: int, proc_info: Dict[str, Any]) -> _OutputSink:
        """Return the sink for *pid*, creating and writing the header if new."""
        if pid not in self._sinks:
            sink = _OutputSink(self._pid_path(pid), csv_mode=False,
                               max_size=self._max_size)
            sink.open()
            header = {**self._meta, "_meta": True, "pid": pid,
                      "name": proc_info.get("name", ""),
                      "cmdline": proc_info.get("cmdline", "")}
            sink.write_line(json.dumps(header, separators=(",", ":")))
            self._sinks[pid] = sink
        return self._sinks[pid]

    def write_footer(self, pid: int, total_samples: int, elapsed_sec: float):
        if pid in self._sinks:
            footer = {"_footer": True, "pid": pid,
                      "total_samples": total_samples,
                      "elapsed_sec": round(elapsed_sec, 3),
                      "end_time": time.strftime("%Y-%m-%dT%H:%M:%S")}
            self._sinks[pid].write_line(json.dumps(footer, separators=(",", ":")))

    def close_all(self, total_samples: int, elapsed_sec: float):
        for pid, sink in self._sinks.items():
            self.write_footer(pid, total_samples, elapsed_sec)
            sink.close()
        self._sinks.clear()

    @property
    def active_pids(self) -> Set[int]:
        return set(self._sinks.keys())

    @property
    def paths(self) -> List[str]:
        return [s.display_path for s in self._sinks.values()]


def _extract_sample(
    device_data: Dict[str, Any],
    groups: MetricGroup,
    ts: float,
) -> Dict[str, Any]:
    """Extract fields from a device.info dict according to the enabled groups.

    *ts* is embedded as ``"ts"`` in the returned dict so that each device
    record is self-contained with its own timestamp.
    """
    out: Dict[str, Any] = {"ts": round(ts, 3)}

    if MetricGroup.DEVICE in groups:
        for k in ("chip_id", "display_id", "pci_bdf", "arch_name", "is_remote", "pci_ordinal"):
            if k in device_data:
                out[k] = device_data[k]

    if MetricGroup.TELEMETRY in groups:
        telem = device_data.get("telemetry", {})
        out["telemetry"] = {
            k: telem[k] for k in (
                "temperature", "board_temperature", "vreg_temperature",
                "power", "input_power_w",
                "voltage_mv", "current_ma", "tdc_limit_a", "tdp_limit_w",
                "aiclk_mhz", "aiclk_limit_mhz",
                "axiclk_mhz", "arcclk_mhz", "ddr_speed_mhz",
                "fan_speed_rpm", "status",
            ) if k in telem
        }

    if MetricGroup.MEMORY in groups:
        mem = device_data.get("memory", {})
        out["memory"] = {
            k: mem[k] for k in (
                "total_dram", "used_dram", "total_l1", "used_l1",
                "used_l1_small", "used_trace", "used_cb",
            ) if k in mem
        }

    if MetricGroup.GDDR in groups:
        for k in ("max_gddr_temp", "gddr01_temp", "gddr23_temp", "gddr45_temp", "gddr67_temp"):
            telem = device_data.get("telemetry", {})
            if k in telem:
                out.setdefault("gddr", {})[k] = telem[k]

    if MetricGroup.FIRMWARE in groups:
        fw = device_data.get("firmware", {})
        if fw:
            out["firmware"] = fw

    if MetricGroup.FABRIC in groups:
        for k in ("eth_connections", "active_eth_channels", "idle_eth_channels", "eth_coord"):
            if k in device_data:
                out.setdefault("fabric", {})[k] = device_data[k]

    if (MetricGroup.PROCESS in groups) or (MetricGroup.PROCESS_ALLOC in groups):
        procs_raw = device_data.get("processes", [])
        procs_out = []
        for p in procs_raw:
            entry: Dict[str, Any] = {}
            if MetricGroup.PROCESS in groups:
                for k in ("pid", "name", "cmdline", "registered", "runtime",
                           "cpu_percent", "vm_rss_kb", "vm_virt_kb", "vm_swap_kb", "num_threads"):
                    if k in p:
                        entry[k] = p[k]
            if MetricGroup.PROCESS_ALLOC in groups:
                for k in ("dram", "l1", "l1_small", "trace", "cb"):
                    if k in p:
                        entry[k] = p[k]
            if entry:
                procs_out.append(entry)
        if procs_out:
            out["processes"] = procs_out

    return out


# Flat CSV field order for per-device rows
_CSV_DEVICE_FIELDS = [
    "timestamp", "seq", "device", "arch", "pci_bdf",
    "temperature", "power", "voltage_mv", "current_ma", "aiclk_mhz",
    "used_dram", "total_dram", "used_l1", "total_l1",
]
_CSV_PROCESS_FIELDS = [
    "timestamp", "seq", "device", "pid", "process", "cmdline",
    "cpu_percent", "num_threads", "vm_rss_kb", "vm_virt_kb", "vm_swap_kb",
    "dram", "l1", "cb", "runtime",
]


def _flatten_csv_device(ts: float, seq: int, dev: Dict) -> Dict[str, Any]:
    telem = dev.get("telemetry", {})
    mem = dev.get("memory", {})
    return {
        "timestamp": f"{ts:.3f}",
        "seq": seq,
        "device": dev.get("display_id", dev.get("chip_id", "")),
        "arch": dev.get("arch_name", ""),
        "pci_bdf": dev.get("pci_bdf", ""),
        "temperature": telem.get("temperature", ""),
        "power": telem.get("power", ""),
        "voltage_mv": telem.get("voltage_mv", ""),
        "current_ma": telem.get("current_ma", ""),
        "aiclk_mhz": telem.get("aiclk_mhz", ""),
        "used_dram": mem.get("used_dram", ""),
        "total_dram": mem.get("total_dram", ""),
        "used_l1": mem.get("used_l1", ""),
        "total_l1": mem.get("total_l1", ""),
    }


def _flatten_csv_process(ts: float, seq: int, dev_id: str, proc: Dict) -> Dict[str, Any]:
    return {
        "timestamp": f"{ts:.3f}",
        "seq": seq,
        "device": dev_id,
        "pid": proc.get("pid", ""),
        "process": proc.get("name", ""),
        "cmdline": proc.get("cmdline", ""),
        "cpu_percent": proc.get("cpu_percent", ""),
        "num_threads": proc.get("num_threads", ""),
        "vm_rss_kb": proc.get("vm_rss_kb", ""),
        "vm_virt_kb": proc.get("vm_virt_kb", ""),
        "vm_swap_kb": proc.get("vm_swap_kb", ""),
        "dram": proc.get("dram", ""),
        "l1": proc.get("l1", ""),
        "cb": proc.get("cb", ""),
        "runtime": proc.get("runtime", ""),
    }


class RecordingSession:
    """Metrics recording session.

    Can be used as a context manager or driven manually via start()/stop()/sample().
    """

    def __init__(self, config: Optional[RecordingConfig] = None, **kwargs):
        if config is None:
            config = RecordingConfig(**kwargs)
        self.config = config
        self._client = None
        self._sink: Optional[_OutputSink] = None
        self._pid_sinks: Optional[_PerPidSinkManager] = None
        self._seq = 0
        self._stop = False
        self._start_time = 0.0

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    def _connect(self):
        from tt_mgmt.api.client import connect
        if self.config.embedded:
            self._client = connect(embedded=True, backend=self.config.backend)
        elif self.config.daemon:
            self._client = connect(embedded=False, backend=self.config.backend)
        else:
            # Auto: connect() tries daemon first, falls back to embedded
            self._client = connect(backend=self.config.backend)

    def start(self):
        self._connect()
        self._sink = _OutputSink(
            self.config.output_path,
            self.config.csv_mode,
            self.config.max_size_bytes,
        )
        self._sink.open()
        self._seq = 0
        self._stop = False
        self._start_time = time.time()
        self._write_header()

        if self.config.per_pid_files:
            import platform
            shared_meta = {
                "version": 1,
                "hostname": platform.node(),
                "start_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "interval_ms": int(self.config.interval_sec * 1000),
                "groups": [g.name.lower() for g in MetricGroup if g in self.config.groups],
            }
            self._pid_sinks = _PerPidSinkManager(
                self.config.output_path,
                self.config.max_size_bytes,
                shared_meta,
            )

    def stop(self):
        self._write_footer()
        if self._sink:
            self._sink.close()
            self._sink = None
        if self._pid_sinks:
            elapsed = time.time() - self._start_time
            self._pid_sinks.close_all(self._seq, elapsed)
            self._pid_sinks = None

    def output_files(self) -> List[str]:
        """Return the list of output file paths written by this session."""
        paths: List[str] = []
        if self._sink and self.config.output_path:
            paths.append(self._sink.display_path)
        if self._pid_sinks:
            paths.extend(self._pid_sinks.paths)
        return paths

    def _write_header(self):
        if self.config.csv_mode:
            has_proc = bool(self.config.groups & (MetricGroup.PROCESS | MetricGroup.PROCESS_ALLOC))
            fields = _CSV_PROCESS_FIELDS if has_proc else _CSV_DEVICE_FIELDS
            self._sink.write_csv_header(fields)
        else:
            import platform
            meta = {
                "_meta": True,
                "version": 1,
                "hostname": platform.node(),
                "start_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "interval_ms": int(self.config.interval_sec * 1000),
                "groups": [g.name.lower() for g in MetricGroup if g in self.config.groups],
                "pid_filter": sorted(self.config.pid_filter) if self.config.pid_filter else None,
                "pid_exclude": sorted(self.config.pid_exclude) if self.config.pid_exclude else [],
                "name_exclude": sorted(self.config.name_exclude) if self.config.name_exclude else [],
                "per_pid_files": self.config.per_pid_files,
                "visible_devices": os.environ.get("TT_VISIBLE_DEVICES"),
                "cmdline": " ".join(sys.argv),
            }
            self._sink.write_line(json.dumps(meta, separators=(",", ":")))

    def _write_footer(self):
        if self.config.csv_mode or not self._sink:
            return
        footer = {
            "_footer": True,
            "total_samples": self._seq,
            "elapsed_sec": round(time.time() - self._start_time, 3),
            "end_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._sink.write_line(json.dumps(footer, separators=(",", ":")))

    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def sample(self) -> Dict[str, Any]:
        """Take a single sample and return it as a dict (also writes to output)."""
        ts = time.time()
        n_devices = self._client.device_count()
        devices = []

        # pid → {proc_info, devices_alloc} for per-PID file writing
        pid_records: Dict[int, Dict[str, Any]] = {}

        all_devs: List[Dict[str, Any]] = []
        for idx in range(n_devices):
            try:
                dev = self._client.device_info(idx)
                all_devs.append(dev)
            except Exception:
                continue

        for dev in all_devs:
            procs = dev.get("processes", [])

            # Apply exclusions first, then optional inclusion filter
            procs = [p for p in procs if p.get("pid") not in self.config.pid_exclude]
            if self.config.name_exclude:
                procs = [
                    p for p in procs
                    if not any(ex in (p.get("name") or "") for ex in self.config.name_exclude)
                ]
            if self.config.pid_filter:
                procs = [p for p in procs if p.get("pid") in self.config.pid_filter]

            dev["processes"] = procs

            # Only skip a device entirely when pid_filter is active and nothing matched.
            # Exclusion-only filters (pid_exclude / name_exclude) still emit the device
            # so that telemetry and memory metrics are captured even when all processes
            # on that device were excluded.
            if self.config.pid_filter and not procs:
                continue

            extracted = _extract_sample(dev, self.config.groups, ts)
            devices.append(extracted)

            # Accumulate per-PID device allocation entries
            if self.config.per_pid_files:
                for p in procs:
                    pid = p.get("pid")
                    if pid is None:
                        continue
                    if pid not in pid_records:
                        pid_records[pid] = {
                            "pid": pid,
                            "name": p.get("name", ""),
                            "cmdline": p.get("cmdline", ""),
                            "cpu_percent": p.get("cpu_percent", 0.0),
                            "num_threads": p.get("num_threads", 0),
                            "vm_rss_kb": p.get("vm_rss_kb", 0),
                            "vm_virt_kb": p.get("vm_virt_kb", 0),
                            "vm_swap_kb": p.get("vm_swap_kb", 0),
                            "runtime": p.get("runtime", 0),
                            "devices": [],
                        }
                    telem = dev.get("telemetry", {})
                    mem = dev.get("memory", {})
                    pid_records[pid]["devices"].append({
                        "chip_id": dev.get("chip_id", ""),
                        "display_id": dev.get("display_id", ""),
                        "is_remote": dev.get("is_remote", False),
                        "telemetry": {
                            k: telem.get(k) for k in (
                                "temperature", "vreg_temperature", "power", "input_power_w",
                                "voltage_mv", "current_ma", "tdc_limit_a", "tdp_limit_w",
                                "aiclk_mhz", "aiclk_limit_mhz", "fan_speed_rpm",
                            ) if k in telem
                        },
                        "memory": {
                            k: mem.get(k) for k in (
                                "total_dram", "used_dram", "total_l1", "used_l1",
                                "used_l1_small", "used_trace", "used_cb",
                            ) if k in mem
                        },
                        "dram": p.get("dram", 0),
                        "l1": p.get("l1", 0),
                        "l1_small": p.get("l1_small", 0),
                        "trace": p.get("trace", 0),
                        "cb": p.get("cb", 0),
                    })

        # Add remote devices (and any other selected devices) to per-PID records
        # when device has no processes for this PID — include telemetry so device metrics appear.
        if self.config.per_pid_files and pid_records:
            for dev in all_devs:
                dev.pop("_idx", None)
                procs = dev.get("processes", [])
                telem = dev.get("telemetry", {})
                mem = dev.get("memory", {})
                dev_id = dev.get("display_id") or dev.get("chip_id", "")
                for pid, rec in pid_records.items():
                    proc_on_dev = next((p for p in procs if p.get("pid") == pid), None)
                    if proc_on_dev is not None:
                        continue
                    rec["devices"].append({
                        "chip_id": dev.get("chip_id", ""),
                        "display_id": dev_id,
                        "is_remote": dev.get("is_remote", False),
                        "telemetry": {
                            k: telem.get(k) for k in (
                                "temperature", "vreg_temperature", "power", "input_power_w",
                                "voltage_mv", "current_ma", "tdc_limit_a", "tdp_limit_w",
                                "aiclk_mhz", "aiclk_limit_mhz", "fan_speed_rpm",
                            ) if k in telem
                        },
                        "memory": {
                            k: mem.get(k) for k in (
                                "total_dram", "used_dram", "total_l1", "used_l1",
                                "used_l1_small", "used_trace", "used_cb",
                            ) if k in mem
                        },
                        "dram": 0, "l1": 0, "l1_small": 0, "trace": 0, "cb": 0,
                    })

        sample = {"ts": round(ts, 3), "seq": self._seq, "devices": devices}
        self._seq += 1

        # Write to main sink
        if self._sink:
            if self.config.csv_mode:
                has_proc = bool(self.config.groups & (MetricGroup.PROCESS | MetricGroup.PROCESS_ALLOC))
                for dev in devices:
                    if has_proc:
                        dev_id = dev.get("display_id", dev.get("chip_id", ""))
                        for proc in dev.get("processes", []):
                            row = _flatten_csv_process(ts, self._seq - 1, dev_id, proc)
                            self._sink.write_line(",".join(str(row.get(f, "")) for f in _CSV_PROCESS_FIELDS))
                    else:
                        row = _flatten_csv_device(ts, self._seq - 1, dev)
                        self._sink.write_line(",".join(str(row.get(f, "")) for f in _CSV_DEVICE_FIELDS))
            else:
                self._sink.write_line(json.dumps(sample, separators=(",", ":")))

        # Write per-PID sinks
        if self._pid_sinks and pid_records:
            elapsed = ts - self._start_time
            for pid, rec in pid_records.items():
                sink = self._pid_sinks.sink_for(pid, rec)
                line = {"ts": round(ts, 3), "seq": self._seq - 1,
                        "elapsed_sec": round(elapsed, 3), **rec}
                sink.write_line(json.dumps(line, separators=(",", ":")))

        return sample

    def samples(self) -> Iterator[Dict[str, Any]]:
        """Generator that yields samples at the configured interval until stopped."""
        while not self._stop:
            t0 = time.monotonic()
            yield self.sample()

            if self.config.duration_sec is not None:
                elapsed = time.time() - self._start_time
                if elapsed >= self.config.duration_sec:
                    break

            # Stop when all tracked PIDs have exited (single or multiple)
            if self.config.pid_filter:
                if not any(self._pid_alive(p) for p in self.config.pid_filter):
                    break

            dt = time.monotonic() - t0
            sleep = self.config.interval_sec - dt
            if sleep > 0:
                time.sleep(sleep)

    def run(self):
        """Block until duration expires, PID exits, or SIGINT/SIGTERM."""
        prev_sigint = signal.getsignal(signal.SIGINT)
        prev_sigterm = signal.getsignal(signal.SIGTERM)

        def _on_signal(sig, frame):
            self._stop = True

        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)

        try:
            for _ in self.samples():
                pass
        finally:
            signal.signal(signal.SIGINT, prev_sigint)
            signal.signal(signal.SIGTERM, prev_sigterm)
