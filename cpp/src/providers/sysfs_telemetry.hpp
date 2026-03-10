// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "tt_device_hal/providers.hpp"
#include "sysfs_discovery.hpp"

namespace tt_device_hal {

/// Telemetry from sysfs hwmon + tenstorrent driver attributes.
/// No UMD dependency -- only reads files from /sys/.
class SysfsTelemetryProvider : public TelemetryProvider {
public:
    explicit SysfsTelemetryProvider(SysfsDiscoveryProvider* discovery)
        : discovery_(discovery) {}

    std::string name() const override { return "sysfs"; }
    bool update(DeviceInfo& device) override;

private:
    SysfsDiscoveryProvider* discovery_;
};

}  // namespace tt_device_hal
