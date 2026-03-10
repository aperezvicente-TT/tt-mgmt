# Build Instructions for Multi-Architecture Support

## Current Status

✅ **UMD Patches Applied:**
1. `cluster_descriptor.cpp:1292` - Board chip count validation guard
2. `cluster_descriptor.cpp:1316` - Architecture homogeneity validation guard
3. `multi_arch_topology_discovery.hpp/cpp` - New per-architecture discovery API
4. `CMakeLists.txt` - Added new source files

⚠️ **UMD NOT YET REBUILT** - You're still running the old UMD without patches!

## What You Need to Do

### Step 1: Rebuild UMD with Patches

```bash
cd /home/alex/mpi-shfs/tenstorrent/tt-metal-smi

# Option A: Full rebuild (clean)
rm -rf build_Release
./build_metal.sh

# Option B: Incremental rebuild (faster)
cd build_Release
ninja install
```

**Expected output:**
```
[...] Building CXX object tt_metal/third_party/umd/device/...multi_arch_topology_discovery.cpp.o
[...] Building CXX object tt_metal/third_party/umd/device/...cluster_descriptor.cpp.o
[...] Linking CXX shared library libtt-metalium.so
[...] Install the project...
```

### Step 2: Rebuild tt-mgmt

```bash
cd /home/alex/mpi-shfs/tenstorrent/tt-metal-smi/tools/tt-mgmt

# Activate python environment
source ../../python_env/bin/activate

# Rebuild with new UMD
uv pip install -e . --no-build-isolation
```

### Step 3: Test with Mixed Architectures

```bash
# Set the flag to enable mixed architecture support
export TT_ALLOW_MIXED_ARCH=1

# Test discovery
tt-mgmt smi monitor
```

**Expected output (with patches):**
```
2026-02-16 XX:XX:XX.XXX | info  | UMD | Established firmware bundle version: 19.3.1
2026-02-16 XX:XX:XX.XXX | info  | UMD | Board 0x100014611903024 chip count mismatch ignored (TT_ALLOW_MIXED_ARCH set)
2026-02-16 XX:XX:XX.XXX | warning | UMD | Mixed architectures detected but allowed via TT_ALLOW_MIXED_ARCH

╭──────────────┬──────────────┬───────┬───────┬────────────┬────────────────────┬────────────────────┬────────╮
│ ID           │ Arch         │ Temp  │ Power │ AICLK      │ DRAM Usage         │ L1 Usage           │ Status │
├──────────────┼──────────────┼───────┼───────┼────────────┼────────────────────┼────────────────────┼────────┤
│ c489755861c… │ Wormhole_B0  │ 43°C  │ 12W   │ 500 MHz    │ 0B / 12.0GiB       │ 0B / 91.5MiB       │ OK     │
│ d73643961ec… │ Blackhole    │ 29°C  │ 17W   │ 800 MHz    │ 0B / 32.0GiB       │ 0B / 210.0MiB      │ OK     │
│ 361903024R   │ Wormhole_B0  │ 31°C  │ 10W   │ 500 MHz    │ 0B / 12.0GiB       │ 0B / 91.5MiB       │ OK     │  ← REMOTE!
╰──────────────┴──────────────┴───────┴───────┴────────────┴────────────────────┴────────────────────┴────────╯
```

## Troubleshooting

### Issue: "Board has 1 chips, but expected 2" at line 1304

**Cause:** UMD not rebuilt. You're using old library without patches.

**Fix:** Rebuild UMD (Step 1 above)

### Issue: Still only 2 devices (missing remote)

**Cause:** ETH firmware version mismatches block remote discovery

**Options:**
1. **Immediate:** Use `TT_VISIBLE_DEVICES` to filter
   ```bash
   export TT_VISIBLE_DEVICES=1
   tt-mgmt smi monitor  # Shows Wormhole L + R
   ```

2. **Long-term:** Patch ETH firmware version check (see below)

### Issue: "No Tenstorrent devices found"

**Cause:** Both validations failing (board count + architecture)

**Fix:** 
```bash
export TT_ALLOW_MIXED_ARCH=1
tt-mgmt smi monitor
```

## Additional Patch Needed: ETH Firmware Version Check

Your system has ETH firmware mismatches blocking remote discovery. To fix:

### Patch topology_discovery.cpp

**File:** `tt_metal/third_party/umd/device/topology/topology_discovery.cpp`

**Find line ~165:**
```cpp
if (!verify_eth_core_fw_version(tt_device, eth_core)) {
    log_warning(
        LogUMD,
        "Skipping discovery from device {} ETH core {}",
        get_local_asic_id(tt_device, eth_core),
        eth_core.str());
    channel++;
    continue;
}
```

**Replace with:**
```cpp
if (!verify_eth_core_fw_version(tt_device, eth_core)) {
    // Allow ETH FW mismatch if TT_ALLOW_MIXED_ARCH is set
    if (std::getenv("TT_ALLOW_MIXED_ARCH") == nullptr) {
        log_warning(
            LogUMD,
            "Skipping discovery from device {} ETH core {}",
            get_local_asic_id(tt_device, eth_core),
            eth_core.str());
        channel++;
        continue;
    } else {
        log_debug(
            LogUMD,
            "ETH FW mismatch ignored for device {} ETH core {} (TT_ALLOW_MIXED_ARCH set)",
            get_local_asic_id(tt_device, eth_core),
            eth_core.str());
    }
}
```

After this patch, **all 3 devices** (Blackhole + Wormhole L + Wormhole R) will be discovered!

## Complete Build Sequence

```bash
# 1. Apply ETH firmware check patch (see above)
nano /home/alex/mpi-shfs/tenstorrent/tt-metal-smi/tt_metal/third_party/umd/device/topology/topology_discovery.cpp

# 2. Rebuild UMD
cd /home/alex/mpi-shfs/tenstorrent/tt-metal-smi/build_Release
ninja install

# 3. Rebuild tt-mgmt
cd /home/alex/mpi-shfs/tenstorrent/tt-metal-smi/tools/tt-mgmt
source ../../python_env/bin/activate
uv pip install -e . --no-build-isolation

# 4. Test
export TT_ALLOW_MIXED_ARCH=1
tt-mgmt smi monitor
```

## Summary of Required Patches

### Patch 1: Board Count Validation ✅ APPLIED
**File:** `cluster_descriptor.cpp:1292`

### Patch 2: Architecture Validation ✅ APPLIED  
**File:** `cluster_descriptor.cpp:1316`

### Patch 3: ETH Firmware Check ⚠️ PENDING
**File:** `topology_discovery.cpp:165`

### Patch 4: Multi-Arch API ✅ ADDED
**Files:** `multi_arch_topology_discovery.hpp/cpp`

**Status:** Patches applied to source, but **UMD NOT REBUILT YET**.

## Why Patches are Needed

| Issue | Without Patch | With Patch |
|-------|---------------|------------|
| **Board chip count** | ❌ Throws on n300 single chip | ✅ Allowed with flag |
| **Mixed architectures** | ❌ TT_THROW exception | ✅ Allowed with flag |
| **ETH FW mismatch** | ❌ Skips remote discovery | ✅ Continues with flag |
| **Remote devices** | ❌ Not discovered | ✅ Discovered per-arch |

All three patches are required for full mixed-architecture support with remote devices!
