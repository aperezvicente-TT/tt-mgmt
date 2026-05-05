# UMD Patch for Mixed Architecture Support

## Problem Statement

UMD's `TopologyDiscovery` **throws an exception** when detecting mixed architectures (Blackhole + Wormhole), preventing any device discovery:

```cpp
// cluster_descriptor.cpp:1317
TT_THROW("Chips with differing architectures detected. This is unsupported.");
```

## Call Chain

```
TopologyDiscovery::discover()
    ↓
TopologyDiscovery::fill_cluster_descriptor_info()  (topology_discovery.cpp:350)
    ↓
ClusterDescriptor::verify_cluster_descriptor_info()  (cluster_descriptor.cpp:1394)
    ↓
ClusterDescriptor::verify_same_architecture()  (cluster_descriptor.cpp:1307)
    ↓
TT_THROW("Chips with differing architectures detected...")  (line 1317)
```

## Patch Options

### Option 1: Environment Variable Guard (Recommended)

**Files to patch:**
1. `tt_metal/third_party/umd/device/cluster_descriptor.cpp`

**Patch:**

```cpp
bool ClusterDescriptor::verify_same_architecture() {
    const std::unordered_set<ChipId> &chips = get_all_chips();
    if (!chips.empty()) {
        tt::ARCH arch = get_arch(*chips.begin());
        if (arch == tt::ARCH::Invalid) {
            TT_THROW("Chip {} has invalid architecture.", *chips.begin());
        }
        bool all_same_arch =
            std::all_of(chips.begin(), chips.end(), [&](ChipId chip_id) { return this->get_arch(chip_id) == arch; });
        if (!all_same_arch) {
            // ADD THIS CHECK:
            if (std::getenv("TT_ALLOW_MIXED_ARCH") != nullptr) {
                log_warning(LogUMD, "Mixed architectures detected but allowed via TT_ALLOW_MIXED_ARCH");
                return true;  // Skip validation
            }
            // END OF ADDITION
            
            TT_THROW("Chips with differing architectures detected. This is unsupported.");
        }
    }

    return true;
}
```

**Usage:**
```bash
export TT_ALLOW_MIXED_ARCH=1
tt-acm smi monitor  # Now discovers all devices
```

**Pros:**
- Minimal code change (3 lines)
- Opt-in behavior (doesn't affect normal usage)
- Clear signal that user accepts mixed-arch limitations

---

### Option 2: Add TopologyDiscoveryOptions Flag

**Files to patch:**
1. `tt_metal/third_party/umd/device/api/umd/device/topology/topology_discovery.hpp`
2. `tt_metal/third_party/umd/device/cluster_descriptor.cpp`
3. `tt_metal/third_party/umd/device/topology/topology_discovery.cpp`

**Step 1: Add option to TopologyDiscoveryOptions**

```cpp
// topology_discovery.hpp
struct TopologyDiscoveryOptions {
    // ... existing options ...
    
    // Allow mixed architectures (Blackhole + Wormhole, etc.)
    // Disables architecture validation in ClusterDescriptor.
    bool allow_mixed_architectures = false;  // ADD THIS
};
```

**Step 2: Pass option to ClusterDescriptor**

```cpp
// topology_discovery.cpp - in TopologyDiscovery constructor
TopologyDiscovery::TopologyDiscovery(const TopologyDiscoveryOptions& options)
    : options(options) {
    // ... existing code ...
}

// topology_discovery.cpp - when creating cluster_desc
cluster_desc->allow_mixed_architectures = options.allow_mixed_architectures;  // ADD THIS
```

**Step 3: Use flag in validation**

```cpp
// cluster_descriptor.cpp
bool ClusterDescriptor::verify_same_architecture() {
    // ADD THIS:
    if (allow_mixed_architectures) {
        log_info(LogUMD, "Skipping architecture validation (allow_mixed_architectures=true)");
        return true;
    }
    
    // ... rest of existing validation ...
}
```

**Usage in tt-acm:**
```cpp
TopologyDiscoveryOptions options;
options.no_remote_discovery = true;
options.allow_mixed_architectures = true;  // Enable mixed arch
auto [desc, devices] = TopologyDiscovery::discover(options);
```

**Pros:**
- Explicit API control
- Type-safe configuration
- Can be unit tested

**Cons:**
- More code changes
- Requires modifying multiple files

---

### Option 3: Convert THROW to Warning

**Files to patch:**
1. `tt_metal/third_party/umd/device/cluster_descriptor.cpp`

**Patch:**

```cpp
bool ClusterDescriptor::verify_same_architecture() {
    const std::unordered_set<ChipId> &chips = get_all_chips();
    if (!chips.empty()) {
        tt::ARCH arch = get_arch(*chips.begin());
        if (arch == tt::ARCH::Invalid) {
            TT_THROW("Chip {} has invalid architecture.", *chips.begin());
        }
        bool all_same_arch =
            std::all_of(chips.begin(), chips.end(), [&](ChipId chip_id) { return this->get_arch(chip_id) == arch; });
        if (!all_same_arch) {
            // CHANGE FROM:
            // TT_THROW("Chips with differing architectures detected. This is unsupported.");
            // TO:
            log_warning(LogUMD, "Chips with differing architectures detected. Cluster topology may be incorrect.");
            return false;  // Return false but don't throw
        }
    }

    return true;
}
```

**Pros:**
- Simplest change
- Always allows mixed architectures
- Discovery continues even with validation failures

**Cons:**
- No opt-in/opt-out mechanism
- Could mask real topology errors
- Affects all UMD users

---

### Option 4: Board Count Validation Guard

**Files to patch:**
1. `tt_metal/third_party/umd/device/cluster_descriptor.cpp`

**Patch:**

```cpp
bool ClusterDescriptor::verify_board_info_for_chips() {
    bool board_info_good = true;
    for (const ChipId chip : all_chips) {
        if (!chip_to_board_id.empty() && chip_to_board_id.find(chip) == chip_to_board_id.end()) {
            log_warning(LogUMD, "Chip {} does not have a board ID assigned.", chip);
            board_info_good = false;
        }
    }

    for (const auto &[board_id, chips] : board_to_chips) {
        const BoardType board_type = get_board_type_from_board_id(board_id);
        const uint32_t number_chips_from_board = get_number_of_chips_from_board_type(board_type);
        if (chips.size() != number_chips_from_board) {
            // ADD THIS CHECK:
            if (std::getenv("TT_ALLOW_MIXED_ARCH") != nullptr) {
                log_info(LogUMD, "Board {:#x} chip count mismatch ignored (TT_ALLOW_MIXED_ARCH set)", board_id);
                continue;  // Skip this validation
            }
            // END OF ADDITION
            
            log_warning(
                LogUMD,
                "Board {:#x} has {} chips, but expected {} chips for board type {}.",
                board_id,
                chips.size(),
                number_chips_from_board,
                board_type_to_string(board_type));
            board_info_good = false;
        }
    }

    return board_info_good;
}
```

This handles the n300 chip count error as well.

---

## Recommended Patch (Minimal + Safe)

**Patch both validations with TT_ALLOW_MIXED_ARCH guard:**

```diff
diff --git a/tt_metal/third_party/umd/device/cluster_descriptor.cpp b/tt_metal/third_party/umd/device/cluster_descriptor.cpp
index abc123..def456 100644
--- a/tt_metal/third_party/umd/device/cluster_descriptor.cpp
+++ b/tt_metal/third_party/umd/device/cluster_descriptor.cpp
@@ -1289,6 +1289,11 @@ bool ClusterDescriptor::verify_board_info_for_chips() {
     for (const auto &[board_id, chips] : board_to_chips) {
         const BoardType board_type = get_board_type_from_board_id(board_id);
         const uint32_t number_chips_from_board = get_number_of_chips_from_board_type(board_type);
         if (chips.size() != number_chips_from_board) {
+            if (std::getenv("TT_ALLOW_MIXED_ARCH") != nullptr) {
+                log_info(LogUMD, "Board {:#x} chip count mismatch ignored (TT_ALLOW_MIXED_ARCH)", board_id);
+                continue;
+            }
             log_warning(
                 LogUMD,
                 "Board {:#x} has {} chips, but expected {} chips for board type {}.",
@@ -1314,6 +1319,10 @@ bool ClusterDescriptor::verify_same_architecture() {
         bool all_same_arch =
             std::all_of(chips.begin(), chips.end(), [&](ChipId chip_id) { return this->get_arch(chip_id) == arch; });
         if (!all_same_arch) {
+            if (std::getenv("TT_ALLOW_MIXED_ARCH") != nullptr) {
+                log_warning(LogUMD, "Mixed architectures allowed via TT_ALLOW_MIXED_ARCH");
+                return true;
+            }
             TT_THROW("Chips with differing architectures detected. This is unsupported.");
         }
     }
```

## How to Apply

```bash
cd /home/alex/mpi-shfs/tenstorrent/tt-metal-smi
# Edit the file
nano tt_metal/third_party/umd/device/cluster_descriptor.cpp
# Apply changes at lines 1292 and 1316

# Rebuild TT-Metal
cd build_Release
cmake .. && ninja install

# Rebuild tt-acm
cd ../../tools/tt-acm
uv pip install -e . --no-build-isolation

# Test with mixed architectures
export TT_ALLOW_MIXED_ARCH=1
tt-acm smi monitor  # Should now work!
```

## Testing

```bash
# Before patch (fails):
tt-acm smi monitor
# Error: "Chips with differing architectures detected"

# After patch (works):
export TT_ALLOW_MIXED_ARCH=1
tt-acm smi monitor
# Shows all devices (Blackhole + Wormhole)
```

## Upstreaming

To get this accepted upstream, propose it as:
- Optional feature flag (opt-in behavior)
- Useful for monitoring/debugging mixed-arch systems
- Doesn't affect normal operation (off by default)
- Add warning logs when enabled

Submit as PR to TT-UMD with use case: *"Enable system monitoring tools to work with mixed architecture deployments"*
