# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
# SPDX-License-Identifier: Apache-2.0

"""ERISC firmware monitoring and diagnostics."""

import re
import struct
import time
import click


# Blackhole full ETH core layout: 14 cores at y=1
# Channel index maps to NOC x coordinate (from blackhole_140_arch.yaml):
#   eth: [1-1, 16-1, 2-1, 15-1, 3-1, 14-1, 4-1, 13-1, 5-1, 12-1, 6-1, 11-1, 7-1, 10-1]
_BH_ETH_CHANNEL_TO_NOC_X = [1, 16, 2, 15, 3, 14, 4, 13, 5, 12, 6, 11, 7, 10]

# Wormhole full ETH core layout: 16 cores at y=0 and y=6
# From budabackend soc_descriptors/wormhole_b0_80_arch.yaml and t6py wormhole bringup
_WH_ETH_LOCATIONS = [
    (9, 0), (1, 0), (8, 0), (2, 0), (7, 0), (3, 0), (6, 0), (4, 0),  # eth0-7
    (9, 6), (1, 6), (8, 6), (2, 6), (7, 6), (3, 6), (6, 6), (4, 6),  # eth8-15
]

# Cached harvesting mask and tt_umd device (lazy init, only created once)
_cached_harvest_mask = None
_cached_umd_device = None
_cached_umd_devs = None      # full {chip_id: TTDevice} map from discover()
_cached_arch = None


def _get_dm():
    """Get the DeviceManager singleton."""
    from tt_mgmt.backend.smi.core import _get_manager
    return _get_manager()


def _discover_umd_devs():
    """Discover and cache the full {chip_id: TTDevice} map (one discover() call)."""
    global _cached_umd_devs
    if _cached_umd_devs is not None:
        return _cached_umd_devs

    import tt_umd
    opts = tt_umd.TopologyDiscoveryOptions()
    for attr, val in [('no_wait_for_eth_training', True),
                      ('wait_on_ethernet_link_training', False),
                      ('no_eth_firmware_strictness', True),
                      ('low_power', True)]:
        if hasattr(opts, attr):
            setattr(opts, attr, val)
    for attr in ['cmfw_mismatch_action', 'cmfw_unsupported_action',
                 'eth_fw_mismatch_action', 'unexpected_routing_firmware_config',
                 'eth_fw_heartbeat_failure']:
        if hasattr(opts, attr):
            setattr(opts, attr, tt_umd.TopologyDiscoveryOptions.Action.IGNORE)
    try:
        _, devs = tt_umd.TopologyDiscovery.discover(opts, tt_umd.IODeviceType.PCIe)
    except TypeError:
        _, devs = tt_umd.TopologyDiscovery.discover(opts)
    _cached_umd_devs = devs
    return devs


def _get_umd_device(chip_id=None):
    """Get a tt_umd TTDevice — used for harvesting mask and ELF loading.

    With chip_id=None returns the first local device (backwards compatible).
    With chip_id set, returns that specific chip (raises if not present).
    """
    global _cached_umd_device
    devs = _discover_umd_devs()

    if chip_id is not None:
        if chip_id not in devs:
            import click
            raise click.ClickException(
                f"chip-id {chip_id} not found; available: {sorted(devs.keys())}")
        return devs[chip_id]

    if _cached_umd_device is not None:
        return _cached_umd_device
    for d in devs.values():
        if not d.is_remote():
            _cached_umd_device = d
            return d
    if devs:
        _cached_umd_device = next(iter(devs.values()))
    return _cached_umd_device


def _get_arch():
    """Detect device architecture (cached). Returns lowercase string like 'wormhole_b0' or 'blackhole'."""
    global _cached_arch
    if _cached_arch is not None:
        return _cached_arch

    # Try DeviceManager first (has device list with arch_name)
    try:
        from tt_mgmt.backend.smi.core import get_devices
        devs = get_devices()
        if devs:
            _cached_arch = devs[0].arch_name.lower()
            return _cached_arch
    except Exception:
        pass

    # Fall back to tt_umd device
    device = _get_umd_device()
    if device is not None:
        _cached_arch = str(device.get_arch()).lower()
        return _cached_arch

    _cached_arch = "unknown"
    return _cached_arch


def _is_wormhole():
    return "wormhole" in _get_arch()


def _get_eth_harvest_mask():
    """Get ETH harvesting mask (cached). Only calls tt_umd once."""
    global _cached_harvest_mask
    if _cached_harvest_mask is not None:
        return _cached_harvest_mask
    device = _get_umd_device()
    if device is None:
        return 0
    info = device.get_chip_info()
    _cached_harvest_mask = info.harvesting_masks.eth_harvesting_mask
    return _cached_harvest_mask


def _get_all_devices_eth_cores():
    """Get ETH cores for all devices (local + remote).

    Returns list of dicts: [{"chip_id": int, "arch": str, "label": str,
                             "is_remote": bool, "cores": [(noc_x, noc_y), ...]}]

    Note: chip_id for NOC access is the UMD local device index (0, 1, ...),
    NOT the hardware ASIC ID. Remote devices cannot be NOC-read from host.
    """
    results = []

    # Try DeviceManager path (knows about all devices)
    try:
        from tt_mgmt.backend.smi.core import get_devices
        dm = _get_dm()
        devs = get_devices()

        # UMD chip_id = sequential index: local devices first, then remote.
        # This matches the C++ get_tt_device_by_umd_chip_id() ordering.
        # Remote Wormhole devices are accessed via Ethernet routing through UMD.
        local_devs = [d for d in devs if not d.is_remote]
        remote_devs = [d for d in devs if d.is_remote]
        ordered_devs = local_devs + remote_devs
        dev_to_umd_idx = {id(d): i for i, d in enumerate(ordered_devs)}

        for dev in devs:
            is_remote = dev.is_remote
            arch = dev.arch_name.lower()
            umd_chip_id = dev_to_umd_idx[id(dev)]

            cores = []
            if hasattr(dm, 'get_eth_cores'):
                try:
                    cores = sorted(dm.get_eth_cores(umd_chip_id))
                except Exception:
                    pass
            if not cores:
                cores = _get_eth_cores_fallback(arch)

            results.append({
                "chip_id": umd_chip_id,
                "arch": arch,
                "arch_label": "Wormhole_B0" if "wormhole" in arch else "Blackhole" if "blackhole" in arch else arch,
                "label": "remote" if is_remote else "local",
                "is_remote": is_remote,
                "display_id": getattr(dev, 'display_id', str(umd_chip_id)),
                "cores": cores,
            })
    except Exception as e:
        # Fallback: single device, chip_id=0.  Use "blackhole" as default arch
        # to avoid calling _get_umd_device() which opens a second conflicting
        # UMD instance and can cause PCIe bus faults on Blackhole.
        import sys
        print(f"[tt_mgmt] WARNING: device enumeration failed: {e}", file=sys.stderr)
        arch = "blackhole"
        cores = _get_eth_cores_fallback(arch)
        results.append({
            "chip_id": 0,
            "arch": arch,
            "arch_label": "Blackhole",
            "label": "local",
            "is_remote": False,
            "display_id": "0",
            "cores": cores,
        })

    return results


def _get_eth_cores_fallback(arch):
    """Fallback ETH core list using architecture-specific hardcoded tables.

    Returns full (unharvested) set. Used when DeviceManager get_eth_cores
    is unavailable (e.g. for remote devices).
    """
    if "wormhole" in arch:
        return sorted(_WH_ETH_LOCATIONS)

    # Default: Blackhole (all 14 channels)
    return sorted((x, 1) for x in _BH_ETH_CHANNEL_TO_NOC_X)


def _get_eth_cores():
    """Get ETH cores for the first (local) device. Legacy single-device helper."""
    all_devs = _get_all_devices_eth_cores()
    if all_devs:
        return all_devs[0]["cores"]
    return []


def _get_eth_cores_umd(device):
    """ETH cores for a specific tt_umd device, derived from its own harvest mask.

    Uses ONLY the passed umd device (no DeviceManager). This matters for the
    load path, which already holds a umd device: opening a second provider
    (DeviceManager) on the same PCIe device bus-faults Blackhole.
    """
    arch = ""
    try:
        arch = str(device.get_arch()).lower()
    except Exception:
        pass
    if "wormhole" in arch:
        return sorted(_WH_ETH_LOCATIONS)

    # Blackhole: 14 ETH channels, filter out harvested ones.
    mask = 0
    try:
        mask = device.get_chip_info().harvesting_masks.eth_harvesting_mask
    except Exception:
        mask = 0
    cores = []
    for ch, x in enumerate(_BH_ETH_CHANNEL_TO_NOC_X):
        if mask & (1 << ch):
            continue
        cores.append((x, 1))
    return sorted(cores)


def _read_boot_results(noc_x, noc_y, chip_id=0, arch=None):
    """Read and parse boot_results from an ERISC core.

    Uses DeviceManager noc_read exclusively.  Does NOT fall back to the
    system tt_umd package — opening a second UMD instance on the same PCIe
    device causes bus faults and system resets on Blackhole.
    """
    if arch is None:
        arch = _get_arch()

    dm = _get_dm()
    from tt_mgmt.api.noc import NocAccess
    from tt_mgmt.api.erisc import EriscAccess
    return EriscAccess(NocAccess(dm), arch=arch).get_status(chip_id, noc_x, noc_y)


def _parse_noc_coord(location: str):
    """Parse 'X-Y', 'X,Y', or 'ethN' into (noc_x, noc_y).

    ethN uses the index from tt-mgmt erisc list (sorted, harvesting-aware).
    """
    m = re.match(r"^eth(\d+)$", location, re.IGNORECASE)
    if m:
        idx = int(m.group(1))
        cores = _get_eth_cores()
        if not cores:
            raise click.BadParameter("No ETH cores found on device.")
        if idx >= len(cores):
            raise click.BadParameter(
                f"eth{idx} out of range (device has eth0-eth{len(cores)-1}).")
        return cores[idx]

    m = re.match(r"^(\d+)[-,](\d+)$", location)
    if not m:
        raise click.BadParameter(
            f"'{location}' is not valid. Use X-Y (e.g. 4-1) or ethN (e.g. eth0)."
        )
    return int(m.group(1)), int(m.group(2))


def _resolve_locations(locations, all_cores=False):
    """Resolve location specs into list of (noc_x, noc_y).

    Accepts multiple 'X-Y', 'ethN' args, or --all for every ETH core.
    """
    if all_cores:
        cores = _get_eth_cores()
        if not cores:
            raise click.ClickException("No ETH cores found on device.")
        return cores
    if not locations:
        raise click.UsageError("Provide at least one location (X-Y or ethN) or use --all.")
    return [_parse_noc_coord(loc) for loc in locations]


def _print_status(status):
    """Format and print an EriscStatus."""
    lines = [
        f"({status.noc_x},{status.noc_y})",
        f"  postcode:     0x{status.postcode:08X}",
        f"  port_status:  {status.port_status} ({status.port_status_str})",
        f"  train_status: {status.train_status} ({status.train_status_str})",
        f"  train_speed:  {status.train_speed}",
        f"  heartbeat[0]: 0x{status.heartbeat[0]:08X}",
        f"  fw_version:   {status.fw_version}",
    ]
    if status.is_alive:
        lines.append(f"  -> RUNTIME FW alive (counter={status.heartbeat_counter})")
    elif status.heartbeat[0] == 0xDEADBEEF:
        lines.append("  -> FW just started (pre-loop)")
    elif status.heartbeat[0] == 0:
        lines.append("  -> No FW running")
    else:
        lines.append("  -> Unknown heartbeat pattern")
    click.echo("\n".join(lines))


def _is_harvested_err(err):
    """True if a NOC read failed because the core is harvested / untranslatable."""
    s = str(err).lower()
    return "no translation" in s or "harvested" in s


def _peek_one(noc_x, noc_y, chip_id=0):
    """Read+print one core, skipping harvested/invalid cores instead of crashing."""
    try:
        _print_status(_read_boot_results(noc_x, noc_y, chip_id=chip_id))
    except Exception as err:
        if _is_harvested_err(err):
            click.echo(f"({noc_x},{noc_y})\n  -> skipped (harvested / no NOC translation)")
        else:
            click.echo(f"({noc_x},{noc_y})\n  -> ERROR: {err}")


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

@click.group()
def erisc():
    """ERISC firmware monitoring and diagnostics.

    Read boot_results, check heartbeat, and monitor ETH core status
    on Tenstorrent devices.
    """
    pass


@erisc.command()
@click.argument("locations", nargs=-1)
@click.option("--all", "-a", "all_cores", is_flag=True, help="All ETH cores.")
@click.option("--chip-id", "-c", type=int, default=0,
              help="Target chip id (from `tt-mgmt erisc list`). Default: 0.")
@click.option("--watch", "-w", is_flag=False, flag_value=1.0, default=None, type=float,
              help="Watch mode: refresh every N seconds (default 1).")
def peek(locations, all_cores, chip_id, watch):
    """Read ERISC boot_results at NOC coordinate(s).

    \b
    Examples:
      tt-mgmt erisc peek 4-1
      tt-mgmt erisc peek eth0 eth1 eth5
      tt-mgmt erisc peek --all
      tt-mgmt erisc peek --chip-id 2 3-1 13-1
      tt-mgmt erisc peek 4-1 --watch
    """
    coords = _resolve_locations(locations, all_cores)

    if watch is not None:
        import sys
        sys.stdout.write("\033[?25l\033[2J")
        try:
            while True:
                sys.stdout.write("\033[H")
                for noc_x, noc_y in coords:
                    _peek_one(noc_x, noc_y, chip_id)
                sys.stdout.write("\033[J")
                sys.stdout.flush()
                time.sleep(watch)
        except KeyboardInterrupt:
            pass
        finally:
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()
    else:
        for noc_x, noc_y in coords:
            _peek_one(noc_x, noc_y, chip_id)


@erisc.command("read")
@click.argument("location")
@click.argument("addr", type=str)
@click.option("--size", "-s", default=4, type=int, help="Bytes to read (default 4).")
def read_cmd(location, addr, size):
    """Raw NOC memory read at a core coordinate.

    \b
    Examples:
      tt-mgmt erisc read 4-1 0x7CC00
      tt-mgmt erisc read eth0 0x7CC00 --size 128
    """
    noc_x, noc_y = _parse_noc_coord(location)
    address = int(addr, 0)

    device = _get_umd_device()
    if device is None:
        raise click.ClickException("No device found")
    data = device.noc_read(noc_x, noc_y, address, size)

    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        hex_str = " ".join(f"{b:02x}" for b in chunk)
        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        click.echo(f"  0x{address + offset:08X}: {hex_str:<48s} {ascii_str}")


# ---------------------------------------------------------------------------
# ERISC SerDes diagnostic scratchpad
# ---------------------------------------------------------------------------

# Debug scratchpad base address for Blackhole ERISC (DEBUG_BUF_ADDR + 496*4)
_BH_DIAG_SCRATCHPAD_BASE = 0x7CBC0

_DIAG_LABELS = {
    0:  "signal_detect",
    1:  "CDR_lock",
    2:  "LCPLL_lock",
    3:  "cfg (eth|serdes|lead|follow)",
    4:  "rate|width|train_speed",
    5:  "cmn_pstate_time (ms)",
    6:  "tx_ack_time (ms)",
    7:  "rx_ack_time (ms)",
    8:  "sigdet_time (ms)",
    9:  "rx_eq_assert_time",
    10: "cdr_lock_time",
    11: "serdes_postcode",
    12: "lcpll_fail_cnt",
    13: "AN_STAT[7:0]|ANLT_STATUS[15:8]|LP_REG1[31:16] (bit6=page_rx)",
    14: "LP_ADV_REG2[15:0]|LP_ADV_REG3[31:16]",
    15: "AN_time_ms[15:0]|LT_time_ms[31:16]",
}


@erisc.command()
@click.argument("locations", nargs=-1)
@click.option("--all", "-a", "all_cores", is_flag=True, help="All ETH cores.")
@click.option("--chip-id", "-c", type=int, default=None,
              help="Target chip id (from `tt-mgmt erisc list`). Default: first local device.")
@click.option("--count", "-n", default=16, type=int,
              help="Number of scratchpad entries to read (default 16).")
def diag(locations, all_cores, chip_id, count):
    """Read SerDes diagnostic scratchpad from ERISC core(s).

    \b
    Reads the debug_buf scratchpad registers written by dump_diag_regs()
    and post-SerDes init diagnostics.

    \b
    Examples:
      tt-mgmt erisc diag 3-1
      tt-mgmt erisc diag eth0 eth1
      tt-mgmt erisc diag --all
      tt-mgmt erisc diag --chip-id 2 3-1 13-1
      tt-mgmt erisc diag 13-1 -n 20
    """
    coords = _resolve_locations(locations, all_cores)
    device = _get_umd_device(chip_id)
    if device is None:
        raise click.ClickException("No device found")

    for idx, (noc_x, noc_y) in enumerate(coords):
        if idx > 0:
            click.echo()
        base = _BH_DIAG_SCRATCHPAD_BASE
        try:
            data = device.noc_read(noc_x, noc_y, base, count * 4)
        except Exception as err:
            if _is_harvested_err(err):
                click.echo(f"SerDes diagnostic scratchpad ({noc_x},{noc_y})  -> skipped (harvested / no NOC translation)")
            else:
                click.echo(f"SerDes diagnostic scratchpad ({noc_x},{noc_y})  -> ERROR: {err}")
            continue

        click.echo(f"SerDes diagnostic scratchpad ({noc_x},{noc_y})  [{count} entries from 0x{base:05X}]")
        click.echo("-" * 64)
        for i in range(count):
            val = struct.unpack_from("<I", data, i * 4)[0]
            label = _DIAG_LABELS.get(i, "")
            click.echo(f"  [{i:2d}] 0x{base + i*4:05X}: 0x{val:08X}  {label}")


# ---------------------------------------------------------------------------
# ERISC firmware loader
# ---------------------------------------------------------------------------

_SOFT_RESET_0_ADDR = 0xFFB121B0
_ERISC0_RESET_BIT = 11


def _jal_instruction(offset: int, rd: int = 0) -> int:
    """Encode a RISC-V JAL instruction to jump to `offset` from address 0."""
    offset &= 0x1FFFFF
    bit20 = (offset >> 20) & 0x1
    bits10_1 = (offset >> 1) & 0x3FF
    bit11 = (offset >> 11) & 0x1
    bits19_12 = (offset >> 12) & 0xFF
    imm = (bit20 << 31) | (bits19_12 << 12) | (bit11 << 20) | (bits10_1 << 21)
    return imm | (rd << 7) | 0x6F


def _load_erisc_fw(device, noc_x, noc_y, elf_path, verify=True, run=True):
    """Load an ERISC ELF firmware onto a core via NOC writes."""
    from elftools.elf.elffile import ELFFile

    sections_to_load = {".init", ".text", ".ldm_data"}

    # 1. Assert reset
    reset_reg = device.noc_read32(noc_x, noc_y, _SOFT_RESET_0_ADDR)
    reset_reg |= (1 << _ERISC0_RESET_BIT)
    device.noc_write32(noc_x, noc_y, _SOFT_RESET_0_ADDR, reset_reg)

    # 2. Parse ELF and write sections to L1
    init_addr = None
    with open(elf_path, 'rb') as f:
        elf = ELFFile(f)
        for section in elf.iter_sections():
            name = section.name
            if name not in sections_to_load:
                continue
            addr = section['sh_addr']
            data = section.data()
            if not data or addr is None:
                continue

            click.echo(f"  Writing {name} to 0x{addr:08X} ({len(data)} bytes)")
            device.noc_write(noc_x, noc_y, addr, data)

            if verify:
                readback = device.noc_read(noc_x, noc_y, addr, len(data))
                if readback != data:
                    click.echo(f"  WARNING: verify failed for {name} at 0x{addr:08X}")

            if name == ".init":
                init_addr = addr

    if init_addr is None:
        raise RuntimeError("No .init section found in ELF")

    # 3. Write JAL instruction at address 0 to jump to .init
    jal = _jal_instruction(init_addr)
    click.echo(f"  Writing JAL to 0x{init_addr:08X} at address 0x00000000")
    device.noc_write32(noc_x, noc_y, 0x0, jal)

    # 4. Deassert reset (start execution)
    if run:
        reset_reg = device.noc_read32(noc_x, noc_y, _SOFT_RESET_0_ADDR)
        reset_reg &= ~(1 << _ERISC0_RESET_BIT)
        device.noc_write32(noc_x, noc_y, _SOFT_RESET_0_ADDR, reset_reg)
        click.echo(f"  Started at ({noc_x},{noc_y})")
    else:
        click.echo(f"  Loaded at ({noc_x},{noc_y}) (not started, core in reset)")


@erisc.command("load")
@click.argument("elf_path", type=click.Path(exists=True))
@click.argument("locations", nargs=-1)
@click.option("--all", "-a", "all_cores", is_flag=True, help="Load onto all ETH cores.")
@click.option("--chip-id", "-c", type=int, default=None,
              help="Target BH/WH chip id (from `tt-mgmt erisc list`). Default: first local device.")
@click.option("--no-run", is_flag=True, help="Load but don't start (keep core in reset).")
@click.option("--no-verify", is_flag=True, help="Skip read-back verification.")
def load_cmd(elf_path, locations, all_cores, chip_id, no_run, no_verify):
    """Load ERISC firmware ELF onto ETH core(s).

    \b
    Examples:
      tt-mgmt erisc load fw.elf 4-1
      tt-mgmt erisc load fw.elf 3-1 13-1 10-1 11-1
      tt-mgmt erisc load fw.elf eth0 eth1
      tt-mgmt erisc load fw.elf --chip-id 2 3-1 13-1
      tt-mgmt erisc load fw.elf --all
    """
    device = _get_umd_device(chip_id)
    if device is None:
        raise click.ClickException("No device found")
    if chip_id is not None:
        click.echo(f"Targeting chip-id {chip_id} (remote={device.is_remote()})")

    if all_cores:
        # Resolve cores from the SAME umd device we load with. Do NOT use the
        # DeviceManager path (_resolve_locations/--all): a second provider on the
        # same PCIe device bus-faults Blackhole.
        coords = _get_eth_cores_umd(device)
        if not coords:
            raise click.ClickException("No ETH cores found on device.")
    else:
        coords = _resolve_locations(locations, all_cores=False)

    for noc_x, noc_y in coords:
        click.echo(f"Loading {elf_path} onto ({noc_x},{noc_y})...")
        _load_erisc_fw(device, noc_x, noc_y, elf_path,
                       verify=not no_verify, run=not no_run)

    if not no_run:
        click.echo(f"\nTo check status: tt-mgmt erisc peek --all")


# ---------------------------------------------------------------------------
# List command
# ---------------------------------------------------------------------------

def _format_list_simple(all_devices):
    """Simple coordinate-only list for all devices."""
    W = 50
    lines = []
    lines.append(f"+{'=' * W}+")
    lines.append(f"| {'tt-mgmt ERISC Cores':^{W}} |")
    lines.append(f"+{'=' * W}+")

    total = 0
    for dev in all_devices:
        arch_label = dev["arch_label"]
        label = dev["label"]
        cores = dev["cores"]
        total += len(cores)
        dev_title = f"Device {dev['display_id']} ({label}) - {arch_label}"
        lines.append(f"| {dev_title:<{W}} |")
        lines.append(f"+{'-' * 8}+{'-' * 9}+{'-' * (W - 19)}+")
        lines.append(f"| {'Core':<6} | {'NOC':^7} | {'Channel':<{W - 20}} |")
        lines.append(f"+{'-' * 8}+{'-' * 9}+{'-' * (W - 19)}+")
        for i, (x, y) in enumerate(cores):
            lines.append(f"| eth{i:<3} | ({x:>2},{y:<2}) | {'ch' + str(i):<{W - 20}} |")
        lines.append(f"+{'-' * 8}+{'-' * 9}+{'-' * (W - 19)}+")

    lines.append(f"| {f'{total} cores across {len(all_devices)} device(s)':^{W}} |")
    lines.append(f"+{'=' * W}+")
    return "\n".join(lines)


def _format_status_table(all_devices, all_statuses):
    """nvidia-smi style ASCII box table with status info for all devices."""
    import datetime
    ts = datetime.datetime.now().strftime("%a %b %d %H:%M:%S %Y")

    W = 72
    COL = f"+{'-' * 7}+{'-' * 9}+{'-' * 14}+{'-' * 12}+{'-' * 10}+{'-' * 8}+{'-' * 6}+"
    HDR = f"| {'Core':^5} | {'NOC':^7} | {'Link State':^12} | {'Heartbeat':^10} | {'FW Ver':^8} | {'Status':^6} | {'HB':^4} |"

    lines = []
    lines.append(f"+{'=' * W}+")
    lines.append(f"| {'tt-mgmt ERISC Status':^{W}} |")
    lines.append(f"| {ts:^{W}} |")
    lines.append(f"+{'=' * W}+")

    total_alive = 0
    total_cores = 0

    for dev, statuses in zip(all_devices, all_statuses):
        arch_label = dev["arch_label"]
        label = dev["label"]
        cores = dev["cores"]
        total_cores += len(cores)

        access_note = " (via ethernet)" if dev.get("is_remote") else ""
        dev_hdr = f"Device {dev['display_id']} ({label}{access_note}) | {arch_label} | {len(cores)} ETH cores"
        lines.append(f"| {dev_hdr:<{W}} |")
        lines.append(COL)
        lines.append(HDR)
        lines.append(COL)

        for i, (x, y) in enumerate(cores):
            st, err = statuses[i]
            if err:
                is_harvested = "no translation" in str(err) or "harvested" in str(err).lower()
                tag = "HARVESTED" if is_harvested else "ERROR"
                lines.append(f"| eth{i:<2} | ({x:>2},{y:<2}) | {tag:^12} | {'':^10} | {'':^8} | {tag[:6]:^6} | {'':^4} |")
            else:
                if st.is_alive:
                    total_alive += 1
                    status_str = "ALIVE"
                elif st.heartbeat[0] == 0:
                    status_str = "OFF"
                elif st.heartbeat[0] == 0xDEADBEEF:
                    status_str = "BOOT"
                else:
                    status_str = "???"
                # Show train/link status string if available, otherwise postcode hex
                if st.train_status_str and st.train_status_str not in ("?", ""):
                    link_str = st.train_status_str[:12]
                elif st.port_status_str and st.port_status_str not in ("?", ""):
                    link_str = st.port_status_str[:12]
                elif st.postcode != 0:
                    link_str = f"0x{st.postcode:08X}"
                else:
                    link_str = "---"
                hb_str = f"{st.heartbeat_counter}" if st.is_alive else f"0x{st.heartbeat[0]:08X}"
                lines.append(
                    f"| eth{i:<2} | ({x:>2},{y:<2}) "
                    f"| {link_str:>12} "
                    f"| {hb_str:>10} "
                    f"| {str(st.fw_version):>8} "
                    f"| {status_str:^6} "
                    f"| {st.heartbeat_counter:>4} |"
                )

        lines.append(COL)

    lines.append(f"| {f'{total_alive}/{total_cores} cores ALIVE across {len(all_devices)} device(s)':^{W}} |")
    lines.append(f"+{'=' * W}+")
    return "\n".join(lines)


@erisc.command("list")
@click.option("--status", "-s", is_flag=True, help="Also read heartbeat status from each core.")
@click.option("--watch", "-w", is_flag=False, flag_value=1.0, default=None, type=float,
              help="Watch mode: refresh every N seconds (default 1). Implies --status.")
def list_cmd(status, watch):
    """List ETH cores on all devices with NOC coordinates and optionally their status.

    \b
    Examples:
      tt-mgmt erisc list              # Show coordinates only
      tt-mgmt erisc list --status     # Also read heartbeat from each core
      tt-mgmt erisc list --watch      # Continuously refresh status
      tt-mgmt erisc list -w 0.5       # Refresh every 0.5s
    """
    if watch is not None:
        status = True

    all_devices = _get_all_devices_eth_cores()

    if not all_devices or all(len(d["cores"]) == 0 for d in all_devices):
        click.echo("No ETH cores found.")
        return

    if not status:
        click.echo(_format_list_simple(all_devices))
        return

    def read_all_status():
        all_statuses = []
        for dev in all_devices:
            dev_results = []
            for x, y in dev["cores"]:
                try:
                    dev_results.append((_read_boot_results(x, y, chip_id=dev["chip_id"], arch=dev["arch"]), None))
                except Exception as e:
                    dev_results.append((None, str(e)))
            all_statuses.append(dev_results)
        return all_statuses

    if watch is not None:
        import sys
        # Hide cursor, clear screen once
        sys.stdout.write("\033[?25l\033[2J")
        try:
            while True:
                # Move cursor to home position (no clear = no flicker)
                sys.stdout.write("\033[H")
                output = _format_status_table(all_devices, read_all_status())
                # Write output + clear any leftover lines below
                sys.stdout.write(output + "\033[J\n")
                sys.stdout.flush()
                time.sleep(watch)
        except KeyboardInterrupt:
            pass
        finally:
            # Show cursor again
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()
    else:
        click.echo(_format_status_table(all_devices, read_all_status()))
