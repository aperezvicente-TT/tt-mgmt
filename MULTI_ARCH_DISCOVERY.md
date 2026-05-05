# Multi-Architecture Topology Discovery

## Problem

When Blackhole and Wormhole devices coexist in the same system, UMD's `TopologyDiscovery::discover()` fails because:

1. **ETH firmware version mismatches** - Different architectures have incompatible ETH firmware
2. **Cross-architecture topology validation fails** - UMD expects consistent board types and chip counts
3. **Global discovery** - UMD discovers all devices at once and tries to build a unified cluster topology

Example error:
```
warning: ETH FW version mismatch for device...
warning: Board 0x100014611903024 has 1 chips, but expected 2 chips for board type n300
```

## Current Solution: Single Discovery + TT_VISIBLE_DEVICES Filter

### Architecture

```
initialize_all_architectures()
    │
    └─► TopologyDiscovery::discover(no_remote_discovery=true)
        ├─► Success? Partition devices by architecture → DONE
        │
        └─► Failure (mixed arch board validation)?
            └─► Display error + workaround instructions
                └─► Use TT_VISIBLE_DEVICES to filter before discovery
```

### Current Limitation

**UMD TopologyDiscovery cannot be bypassed:**
- `BlackholeTTDevice` and `WormholeTTDevice` constructors are `protected`
- We cannot create `TTDevice` objects directly
- Must use `TopologyDiscovery::discover()`, which validates board topology
- Mixed architectures trigger validation errors that abort discovery

**The validation error:**
```
warning: Board 0x100014611903024 has 1 chips, but expected 2 chips for board type n300
```

This happens because UMD sees both Blackhole and Wormhole devices and tries to validate them as a unified topology, which fails.

### Key Features

1. **Phase 1: TopologyDiscovery (Preferred)**
   - Try `TopologyDiscovery::discover()` with:
     - `no_remote_discovery = true` → Skip ETH discovery
     - `no_eth_firmware_strictness = true` → Allow FW mismatches
     - `no_wait_for_eth_training = true` → Skip ETH training
   - If successful, partition devices by arch and use full topology

2. **Phase 2: Direct PCIe Enumeration (Fallback)**
   - If TopologyDiscovery throws (board validation errors), use direct PCIe access:
     - `PCIDevice::enumerate_devices_info()` → Get all `/dev/tenstorrent/N` devices
     - For each device: Create `PCIDevice`, then arch-specific `TTDevice`
     - Bypasses cluster topology validation entirely
   - Works for basic monitoring (telemetry, memory, processes)
   - No cluster topology or remote devices

3. **Architecture Partitioning**
   - Devices are stored in `g_arch_caches[arch]`
   - Each architecture has its own `std::map<chip_id, TTDevice>`
   - Telemetry/memory queries search all caches by chip_id

4. **Unified API**
   - `enumerate_devices()` merges all architecture caches
   - Python/CLI sees a flat list regardless of architecture
   - Works with single-arch or mixed-arch systems

### Current Workaround

**Use `TT_VISIBLE_DEVICES` to filter before discovery:**

```bash
# Option 1: Monitor Blackhole only
export TT_VISIBLE_DEVICES=0
tt-mgmt smi monitor

# Option 2: Monitor Wormhole only  
export TT_VISIBLE_DEVICES=1
tt-mgmt smi monitor

# Option 3: Specific device IDs (comma-separated)
export TT_VISIBLE_DEVICES=0,2,4
tt-mgmt device list
```

This filters devices **before** UMD discovery, so TopologyDiscovery only sees one architecture and succeeds.

### Configuration

```cpp
TopologyDiscoveryOptions options;
options.no_remote_discovery = true;          // Skip ETH discovery
options.no_eth_firmware_strictness = true;   // Allow FW mismatches  
options.no_wait_for_eth_training = true;     // Skip ETH training
```

Even with these settings, mixed architectures cause board validation errors.

### Limitations

| Limitation | Impact |
|------------|--------|
| **Cannot create TTDevice directly** | Protected constructors prevent PCIe fallback |
| **TopologyDiscovery validates boards** | Mixed architectures fail validation (n300 chip count mismatch) |
| **Global discovery only** | No per-PCI-device filtering in UMD API |
| **No remote devices** | `no_remote_discovery=true` disables ETH links |

**Current Status:**
- ✅ Works with single architecture (Blackhole only OR Wormhole only)
- ❌ Fails with mixed architectures (requires TT_VISIBLE_DEVICES filtering)
- ⚠️ Shows cosmetic ETH firmware warnings (doesn't affect functionality)

### Long-Term Solution (Requires UMD Changes)

1. **Add per-device discovery API** - `TopologyDiscovery::discover_device(pci_device_num)`
2. **Make TTDevice constructors public** - Allow external device creation
3. **Skip board validation for mixed archs** - Add `no_board_validation` option
4. **Architecture-aware discovery** - Filter PCI devices by arch before discovery

## Code Structure

```
tools/tt-mgmt/cpp/src/smi/
├── tt_smi_backend.hpp           # API definitions
└── tt_smi_backend.cpp           # Implementation
    ├── initialize_all_architectures()  # Single global discovery
    ├── g_arch_caches               # Per-arch device storage
    └── enumerate_devices()          # Merge all caches

tools/tt-mgmt/cpp/src/device_hal/
├── device_hal.hpp               # Generic HAL API
└── device_hal.cpp               # Wraps tt_smi backend
```

## Usage

```cpp
// Discovery happens automatically on first call
auto devices = tt_smi::enumerate_devices();

// Returns all devices (Blackhole + Wormhole + ...)
for (auto& dev : devices) {
    std::cout << dev.arch_name << " " << dev.display_id << std::endl;
}
```

## Testing

```bash
# With mixed Blackhole + Wormhole
tt-mgmt smi monitor
# Should show all devices without ETH warnings

# With TT_VISIBLE_DEVICES filtering
export TT_VISIBLE_DEVICES=0  # Show only first device
tt-mgmt device list
```
