"""Device backend -- uses DeviceManager via SMI core."""

try:
    from tt_mgmt.backend.smi import get_devices, update_telemetry, update_memory
except ImportError:
    get_devices = None
    update_telemetry = None
    update_memory = None


def list_devices(include_remote=True):
    """List all available devices via DeviceManager."""
    if get_devices is None:
        return []
    devices = get_devices()
    result = []
    for idx, dev in enumerate(devices):
        if not include_remote and dev.is_remote:
            continue
        update_telemetry(dev)
        result.append({
            'id': idx,
            'display_id': dev.display_id or '',
            'arch': dev.arch_name,
            'board_id': dev.board_id,
            'card_type': dev.card_type or '',
            'status': dev.telemetry_status or 'Unknown',
            'temp': dev.temperature if dev.temperature >= 0 else None,
            'power': dev.power if dev.power >= 0 else None,
            'pci_ordinal': dev.pci_ordinal,
            'logical_id': dev.logical_id,
            'is_remote': dev.is_remote,
        })
    return result


def get_device_info(device_id, verbose=False):
    """Get detailed device information by index."""
    if get_devices is None:
        raise RuntimeError("Device HAL not available")
    devices = get_devices()
    if device_id < 0 or device_id >= len(devices):
        raise ValueError(f"Device {device_id} not found")
    dev = devices[device_id]
    update_telemetry(dev)
    update_memory(dev)
    return {
        'arch': dev.arch_name,
        'status': dev.telemetry_status or 'Active',
        'temp': dev.temperature if dev.temperature >= 0 else 0.0,
        'power': dev.power if dev.power >= 0 else 0.0,
        'aiclk': dev.aiclk_mhz or 0,
        'memory': {
            'dram': {'used': dev.used_dram // (1024 * 1024), 'total': dev.total_dram // (1024 * 1024)},
            'l1': {'used': (dev.used_l1 + dev.used_l1_small) // (1024 * 1024), 'total': dev.total_l1 // (1024 * 1024)},
        },
        'processes': [
            {'pid': p['pid'], 'name': p['name']}
            for p in dev.processes
        ] if verbose else []
    }
