"""Health checks for `tt-mgmt doctor`.

Each check is a callable returning a CheckResult. Checks are grouped into
categories (host, devices, mgmt) and rendered by the doctor command.
"""

from __future__ import annotations

import glob
import os
import socket
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional


PASS = "pass"
WARN = "warn"
ERROR = "error"
SKIP = "skip"


@dataclass
class CheckResult:
    name: str
    status: str
    message: str
    remediation: Optional[str] = None
    details: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Host checks
# ---------------------------------------------------------------------------

def check_kmd_loaded() -> CheckResult:
    sysfs = "/sys/class/tenstorrent"
    if not os.path.isdir(sysfs):
        return CheckResult(
            "KMD loaded",
            ERROR,
            "Tenstorrent kernel module not loaded",
            remediation="sudo modprobe tenstorrent  (install: https://github.com/tenstorrent/tt-kmd)",
        )

    devices = sorted(os.listdir(sysfs))
    version = _read_first_line("/sys/module/tenstorrent/version") or "unknown"
    if not devices:
        return CheckResult(
            "KMD loaded",
            ERROR,
            f"KMD v{version} loaded but no devices enumerated",
            remediation="check `dmesg | grep tenstorrent` for probe failures",
        )
    return CheckResult(
        "KMD loaded",
        PASS,
        f"KMD v{version}, {len(devices)} device(s) visible",
    )


def check_device_permissions() -> CheckResult:
    import stat
    candidates = sorted(glob.glob("/dev/tenstorrent/*"))
    nodes = []
    for n in candidates:
        try:
            if stat.S_ISCHR(os.stat(n).st_mode):
                nodes.append(n)
        except OSError:
            continue
    if not nodes:
        return CheckResult(
            "Device permissions",
            SKIP,
            "no /dev/tenstorrent/* nodes (KMD not loaded)",
        )
    unreadable = [n for n in nodes if not os.access(n, os.R_OK | os.W_OK)]
    if unreadable:
        return CheckResult(
            "Device permissions",
            ERROR,
            f"cannot open {len(unreadable)}/{len(nodes)} device node(s)",
            remediation="sudo usermod -aG tenstorrent $USER  (then log out/in)",
            details=unreadable,
        )
    return CheckResult(
        "Device permissions",
        PASS,
        f"{len(nodes)} device node(s) readable+writable",
    )


def check_hugepages_mount() -> CheckResult:
    mount_point = "/dev/hugepages-1G"
    if not os.path.isdir(mount_point):
        return CheckResult(
            "Hugepages mount",
            WARN,
            f"{mount_point} does not exist",
            remediation=f"sudo mkdir -p {mount_point} && add to /etc/fstab",
        )
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[1] == mount_point and parts[2] == "hugetlbfs":
                    opts = parts[3]
                    if _is_1g_pagesize(opts):
                        return CheckResult("Hugepages mount", PASS, f"{mount_point} mounted (1G pages)")
                    return CheckResult(
                        "Hugepages mount",
                        WARN,
                        f"{mount_point} mounted but page size is not 1G",
                        details=[opts],
                    )
    except OSError:
        pass
    return CheckResult(
        "Hugepages mount",
        WARN,
        f"{mount_point} not mounted",
        remediation="sudo mount -t hugetlbfs -o pagesize=1G nodev /dev/hugepages-1G",
    )


def check_hugepages_count(device_count: int) -> CheckResult:
    path = "/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages"
    raw = _read_first_line(path)
    if raw is None:
        return CheckResult(
            "Hugepages reserved",
            WARN,
            "1G hugepages not supported by kernel",
            remediation="enable hugepages: add `default_hugepagesz=1G hugepagesz=1G hugepages=N` to GRUB_CMDLINE_LINUX",
        )
    try:
        reserved = int(raw)
    except ValueError:
        return CheckResult("Hugepages reserved", WARN, f"unparseable nr_hugepages: {raw!r}")

    needed = max(device_count, 1)
    if reserved < needed:
        return CheckResult(
            "Hugepages reserved",
            WARN,
            f"{reserved} reserved, {needed} recommended for {device_count} device(s)",
            remediation=f"echo {needed} | sudo tee {path}",
        )
    return CheckResult(
        "Hugepages reserved",
        PASS,
        f"{reserved} × 1G hugepages reserved",
    )


# ---------------------------------------------------------------------------
# Per-device checks
# ---------------------------------------------------------------------------

def check_telemetry(devices) -> CheckResult:
    if not devices:
        return CheckResult("Telemetry", SKIP, "no devices")

    bad = []
    for d in devices:
        status = (d.telemetry_status or "").lower()
        if status in ("error", "timeout"):
            bad.append(f"{d.display_id or f'dev{d.chip_id}'}: {status}")

    if bad:
        return CheckResult(
            "Telemetry",
            ERROR,
            f"{len(bad)}/{len(devices)} device(s) not reporting telemetry",
            remediation="device may need reset: tt-smi -r <id>",
            details=bad,
        )
    return CheckResult(
        "Telemetry",
        PASS,
        f"{len(devices)} device(s) reporting telemetry",
    )


def check_thermal_clock(devices, temp_warn=85.0, temp_error=95.0) -> CheckResult:
    issues = []
    severity = PASS
    for d in devices:
        label = d.display_id or f"dev{d.chip_id}"
        try:
            temp = float(d.temperature) if d.temperature is not None else None
            aiclk = int(d.aiclk_mhz) if d.aiclk_mhz is not None else None
        except (TypeError, ValueError):
            continue

        if temp is not None and temp >= temp_error:
            issues.append(f"{label}: {temp:.1f}°C (critical)")
            severity = ERROR
        elif temp is not None and temp >= temp_warn:
            issues.append(f"{label}: {temp:.1f}°C (hot)")
            if severity == PASS:
                severity = WARN

        if aiclk == 0:
            issues.append(f"{label}: AICLK=0 MHz (FW unresponsive?)")
            severity = ERROR

    if not devices:
        return CheckResult("Temperature & clocks", SKIP, "no devices")
    if severity == PASS:
        return CheckResult(
            "Temperature & clocks",
            PASS,
            f"all {len(devices)} device(s) within nominal range",
        )
    return CheckResult(
        "Temperature & clocks",
        severity,
        f"{len(issues)} device(s) flagged",
        details=issues,
    )


def check_firmware_consistency(devices) -> CheckResult:
    if not devices:
        return CheckResult("Firmware versions", SKIP, "no devices")

    by_arch: dict[str, set] = {}
    missing = []
    for d in devices:
        fw = getattr(d.firmware, "fw_bundle_ver", "") or ""
        if not fw:
            missing.append(d.display_id or f"dev{d.chip_id}")
            continue
        by_arch.setdefault(d.arch_name, set()).add(fw)

    if missing and not by_arch:
        return CheckResult(
            "Firmware versions",
            SKIP,
            "backend does not report firmware versions",
        )
    if missing:
        return CheckResult(
            "Firmware versions",
            WARN,
            f"{len(missing)}/{len(devices)} device(s) report no firmware version",
            details=missing,
        )

    mismatched = {arch: vs for arch, vs in by_arch.items() if len(vs) > 1}
    if mismatched:
        details = [f"{arch}: {sorted(vs)}" for arch, vs in mismatched.items()]
        return CheckResult(
            "Firmware versions",
            WARN,
            "mismatched firmware versions across same-arch devices",
            remediation="reflash to a single bundle: tt-flash --fw-tar <bundle>",
            details=details,
        )

    summary = ", ".join(f"{arch}={next(iter(vs))}" for arch, vs in by_arch.items())
    return CheckResult("Firmware versions", PASS, summary)


# ---------------------------------------------------------------------------
# tt-mgmt self checks
# ---------------------------------------------------------------------------

def check_daemon(host: str = "127.0.0.1", port: int = 5391, timeout: float = 0.5) -> CheckResult:
    unit_present = _systemd_unit_loaded("tt-mgmtd.service")
    reachable = _tcp_probe(host, port, timeout)

    if not unit_present and not reachable:
        return CheckResult(
            "Daemon",
            SKIP,
            "tt-mgmtd not installed (optional Prometheus exporter)",
        )

    if reachable:
        active = _systemd_active("tt-mgmtd.service") if unit_present else "running"
        return CheckResult(
            "Daemon",
            PASS,
            f"tt-mgmtd reachable on :{port} ({active})",
        )

    return CheckResult(
        "Daemon",
        WARN,
        f"tt-mgmtd unit installed but :{port} unreachable",
        remediation="sudo systemctl status tt-mgmtd && sudo systemctl restart tt-mgmtd",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_1g_pagesize(mount_opts: str) -> bool:
    for opt in mount_opts.split(","):
        if not opt.startswith("pagesize="):
            continue
        v = opt[len("pagesize="):].strip()
        if v in ("1G", "1024M", "1073741824"):
            return True
    return False


def _read_first_line(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.readline().strip()
    except OSError:
        return None


def _tcp_probe(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _systemd_unit_loaded(unit: str) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "list-unit-files", "--no-legend", unit],
            capture_output=True, text=True, timeout=2,
        )
        return result.returncode == 0 and unit in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _systemd_active(unit: str) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=2,
        )
        return result.stdout.strip() or "unknown"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

@dataclass
class CheckGroup:
    name: str
    results: List[CheckResult]


def run_all(devices) -> List[CheckGroup]:
    """Run all v0 doctor checks and return grouped results."""
    host_checks = [
        check_kmd_loaded(),
        check_device_permissions(),
        check_hugepages_mount(),
        check_hugepages_count(len(devices)),
    ]
    device_checks = [
        check_telemetry(devices),
        check_thermal_clock(devices),
        check_firmware_consistency(devices),
    ]
    mgmt_checks = [
        check_daemon(),
    ]
    return [
        CheckGroup("Host", host_checks),
        CheckGroup("Devices", device_checks),
        CheckGroup("tt-mgmt", mgmt_checks),
    ]


def summarize(groups: List[CheckGroup]) -> dict:
    counts = {PASS: 0, WARN: 0, ERROR: 0, SKIP: 0}
    for g in groups:
        for r in g.results:
            counts[r.status] = counts.get(r.status, 0) + 1
    return counts
