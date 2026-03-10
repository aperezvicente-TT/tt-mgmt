// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "tt_device_hal/providers.hpp"
#include <string>
#include <map>

namespace tt_device_hal {

/// Cached paths for one sysfs device.
struct SysfsDevicePaths {
    std::string tt_dir;     // /sys/devices/.../tenstorrent/tenstorrent!N
    std::string hwmon_dir;  // /sys/devices/.../hwmon/hwmonX (may be empty)
    std::string pci_bdf;    // DDDD:BB:DD.F
    int ordinal = -1;       // N from tenstorrent!N
};

/// Device discovery via /sys/class/tenstorrent/ + /sys/class/hwmon/.
/// Requires only tt-kmd; no UMD dependency.
class SysfsDiscoveryProvider : public DeviceDiscoveryProvider {
public:
    std::string name() const override { return "sysfs"; }
    std::vector<DeviceInfo> discover() override;

    /// Look up cached sysfs paths by PCI BDF (for use by SysfsTelemetryProvider).
    const SysfsDevicePaths* get_paths(const std::string& pci_bdf) const;

    /// All discovered device paths.
    const std::map<std::string, SysfsDevicePaths>& all_paths() const { return paths_; }

private:
    std::map<std::string, SysfsDevicePaths> paths_;  // key: pci_bdf
};

}  // namespace tt_device_hal
