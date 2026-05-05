# UMD Multi-Architecture Rearchitecture

## Problem Statement

**Current UMD design assumes architecture homogeneity**, enforcing validation that breaks heterogeneous systems:

```cpp
// cluster_descriptor.cpp:1317
if (!all_same_arch) {
    TT_THROW("Chips with differing architectures detected. This is unsupported.");
}
```

This design is **incompatible with production environments**:
- ❌ Cannot monitor mixed Blackhole + Wormhole systems
- ❌ Requires environment variable hacks (`TT_ALLOW_MIXED_ARCH=1`)
- ❌ Single global discovery validates all devices together
- ❌ Remote device discovery blocked by architecture conflicts

## Solution: Per-Architecture Cluster Isolation

```
┌──────────────────────────────────────────────────────────────┐
│  OLD: Single Global Cluster (BREAKS on mixed arch)           │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  TopologyDiscovery::discover()                                │
│    ↓                                                          │
│  Discover ALL devices (BH + WH)                               │
│    ↓                                                          │
│  Create ONE ClusterDescriptor                                 │
│    ↓                                                          │
│  ❌ verify_same_architecture() → TT_THROW                     │
│                                                               │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│  NEW: Per-Architecture Clusters (WORKS on mixed arch)        │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  MultiArchTopologyDiscovery::discover_by_architecture()       │
│    ↓                                                          │
│  Detect architectures: {Blackhole, Wormhole}                 │
│    ↓                                                          │
│  ┌─────────────────────┐   ┌─────────────────────┐          │
│  │ Blackhole Cluster   │   │ Wormhole Cluster    │          │
│  ├─────────────────────┤   ├─────────────────────┤          │
│  │ - Descriptor        │   │ - Descriptor        │          │
│  │ - Devices (BH only) │   │ - Devices (WH only) │          │
│  │ - PCI ordinals: [0] │   │ - PCI ordinals: [1] │          │
│  │ ✓ Validates BH only │   │ ✓ Validates WH only │          │
│  │ ✓ Isolated          │   │ ✓ Isolated          │          │
│  └─────────────────────┘   └─────────────────────┘          │
│                                                               │
│  Both clusters available simultaneously!                     │
│  Runtime chooses which cluster to use for workload           │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

## Architecture Overview

### New API: `MultiArchTopologyDiscovery`

**Header:** `tt_metal/third_party/umd/device/api/umd/device/topology/multi_arch_topology_discovery.hpp`

```cpp
class MultiArchTopologyDiscovery {
public:
    struct ArchCluster {
        tt::ARCH arch;
        std::unique_ptr<ClusterDescriptor> descriptor;
        std::map<uint64_t, std::unique_ptr<TTDevice>> devices;
        std::unordered_set<int> pci_ordinals;
        bool discovery_successful;
        std::string error_message;
    };
    
    // Discover all architectures
    static std::map<tt::ARCH, ArchCluster> discover_by_architecture(
        const TopologyDiscoveryOptions& options = TopologyDiscoveryOptions()
    );
    
    // Discover specific architecture
    static ArchCluster discover_single_architecture(
        tt::ARCH target_arch,
        const TopologyDiscoveryOptions& options = TopologyDiscoveryOptions()
    );
    
    // Query available architectures
    static std::unordered_set<tt::ARCH> get_available_architectures();
};
```

## Algorithm

### 1. Detect Architectures

```cpp
auto pci_devices = PCIDevice::enumerate_devices_info();

// Group by architecture:
// Blackhole: [0]
// Wormhole:  [1]
```

### 2. Per-Architecture Discovery (Isolated)

For each architecture:

```cpp
// Filter TT_VISIBLE_DEVICES to this architecture's devices only
setenv("TT_VISIBLE_DEVICES", "0", 1);  // Blackhole

// Run isolated discovery
auto [desc, devices] = TopologyDiscovery::discover(options);

// Store in ArchCluster
cluster[BLACKHOLE] = {desc, devices, ...};

// Restore environment
unsetenv("TT_VISIBLE_DEVICES");
```

**Key insight:** By filtering `TT_VISIBLE_DEVICES` per-architecture, we force `TopologyDiscovery` to only see one architecture at a time, so validation passes!

### 3. Aggregate Results

```cpp
map<tt::ARCH, ArchCluster> all_clusters;
// Blackhole cluster: 1 device
// Wormhole cluster:  2 devices (L + R remote)
```

## Usage Examples

### Monitoring Tool (tt-acm)

```cpp
#include "umd/device/topology/multi_arch_topology_discovery.hpp"

// Discover all architectures
auto clusters = MultiArchTopologyDiscovery::discover_by_architecture();

// Iterate all devices across all architectures
for (auto& [arch, cluster] : clusters) {
    if (!cluster.discovery_successful) continue;
    
    for (auto& [asic_id, tt_device] : cluster.devices) {
        // Read telemetry (works during execution!)
        auto* telem = tt_device->get_arc_telemetry_reader();
        float temp = telem->read_entry(TelemetryTag::ASIC_TEMPERATURE);
        
        std::cout << "Device " << asic_id << " (" << arch << "): " 
                  << temp << "°C" << std::endl;
    }
}
```

### Runtime Workload Selection

```cpp
// Application chooses target architecture at runtime
tt::ARCH target_arch = user_selects_architecture();  // Wormhole or Blackhole

// Get cluster for target architecture
auto& cluster = clusters[target_arch];

// Launch workload on this cluster's devices
for (auto& [asic_id, tt_device] : cluster.devices) {
    program.run_on_device(tt_device.get());
}
```

### Mixed Architecture Node

```cpp
Node Configuration:
- 2x Blackhole (PCI 0, 2)
- 4x Wormhole  (PCI 1, 3, 4, 5)

Discovery Result:
clusters[BLACKHOLE].devices = {device0, device2}
clusters[WORMHOLE].devices  = {device1, device3, device4, device5}

Each cluster validates independently!
```

## Files Modified/Added

### New Files

1. **`tt_metal/third_party/umd/device/api/umd/device/topology/multi_arch_topology_discovery.hpp`**
   - Public API header

2. **`tt_metal/third_party/umd/device/topology/multi_arch_topology_discovery.cpp`**
   - Implementation

### Modified Files

1. **`tt_metal/third_party/umd/device/CMakeLists.txt`**
   - Add new source file and header to build

2. **`tt_metal/third_party/umd/device/cluster_descriptor.cpp`** (Optional but recommended)
   - Add environment variable guards for validation (already applied):
     - Line ~1295: Board chip count validation
     - Line ~1324: Architecture homogeneity validation

## Build Instructions

```bash
cd /home/alex/mpi-shfs/tenstorrent/tt-metal-smi

# Rebuild UMD with new multi-arch API
cd build_Release
cmake .. && ninja install

# Rebuild tt-acm with multi-arch support
cd ../../tools/tt-acm
uv pip install -e . --no-build-isolation

# Test with mixed architectures
tt-acm smi monitor  # Should discover ALL devices (BH + WH + remote)
```

## Testing

### Test 1: Mixed Architecture Discovery

```bash
# System: 1x Blackhole + 1x Wormhole n300 (2 chips)
tt-acm smi monitor

# Expected output:
# [tt_smi] Multi-architecture discovery results:
# [tt_smi]   Blackhole: 1 device(s) ✓
# [tt_smi]   Wormhole_B0: 2 device(s) ✓  # Local + Remote
# [tt_smi] Multi-architecture discovery complete: 3 total device(s)
```

### Test 2: Single Architecture (Graceful Degradation)

```bash
# System: Only Wormhole devices
tt-acm smi monitor

# Expected output:
# [tt_smi]   Wormhole_B0: 2 device(s) ✓
# [tt_smi] Multi-architecture discovery complete: 2 total device(s)
```

### Test 3: Architecture-Specific Discovery

```cpp
// Discover only Wormhole devices
auto wh_cluster = MultiArchTopologyDiscovery::discover_single_architecture(
    tt::ARCH::WORMHOLE_B0
);

if (wh_cluster.discovery_successful) {
    std::cout << "Wormhole devices: " << wh_cluster.devices.size() << std::endl;
}
```

## Benefits Over Patched Single-Cluster Approach

| Feature | Old (with TT_ALLOW_MIXED_ARCH) | New (Per-Arch Clusters) |
|---------|--------------------------------|-------------------------|
| **Validation** | Bypassed globally | Per-cluster (proper) |
| **Isolation** | ❌ All devices in one cluster | ✅ Separate clusters |
| **Remote Devices** | ⚠️ May work with hacks | ✅ Works per-architecture |
| **Runtime Selection** | ❌ No clear separation | ✅ Pick cluster by arch |
| **Error Handling** | ❌ One failure breaks all | ✅ Independent failures |
| **Production Ready** | ❌ Requires env var patches | ✅ Clean API design |

## Integration with tt_metal

For workload execution, `tt_metal` can use architecture-specific clusters:

```cpp
// In metal_context.cpp or device initialization
auto clusters = MultiArchTopologyDiscovery::discover_by_architecture();

// User specifies target architecture for workload
tt::ARCH target = config.get_target_architecture();

// Get devices for target architecture
auto& devices = clusters[target].devices;

// Initialize only this architecture's devices for execution
for (auto& [id, device] : devices) {
    initialize_device_for_execution(device.get());
}
```

## Migration Path

### Phase 1: Add Multi-Arch API (This patch)
- ✅ New `MultiArchTopologyDiscovery` class
- ✅ Per-architecture cluster isolation
- ✅ Backward compatible (existing code continues to work)

### Phase 2: Adopt in tt_metal
- Update `metal_context.cpp` to use multi-arch discovery
- Add architecture selection to workload configuration
- Keep single-arch fast path for homogeneous systems

### Phase 3: Deprecate Global Validation
- Make `verify_same_architecture()` optional/warning
- Remove architecture homogeneity assumption
- Clean up `TT_ALLOW_MIXED_ARCH` hacks

## Upstream Proposal

Submit to TT-UMD repository with rationale:

**Title:** "Add multi-architecture topology discovery for heterogeneous systems"

**Description:**
> Modern heterogeneous compute nodes run multiple Tenstorrent architectures simultaneously (e.g., Wormhole + Blackhole). Current UMD design enforces single-architecture homogeneity, preventing monitoring tools and mixed-architecture workloads from functioning.
>
> This PR introduces `MultiArchTopologyDiscovery`, which creates isolated per-architecture clusters. Each cluster is independently discovered and validated, enabling:
> - System monitoring across all devices
> - Runtime architecture selection for workloads  
> - Graceful handling of per-architecture failures
> - Proper support for remote devices (n300, etc.) in mixed systems
>
> The API is additive and backward compatible. Existing single-architecture code paths are unaffected.

## Summary

This rearchitecture transforms UMD from a **single-cluster, homogeneous-only** design to a **multi-cluster, heterogeneous-capable** architecture that matches real-world production requirements.

**No more hacks. No more patches. Just clean, isolated clusters per architecture.**
