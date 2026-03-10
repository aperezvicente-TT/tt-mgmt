// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "tt_device_hal/providers.hpp"
#include "umd_discovery.hpp"

namespace tt_device_hal {

/// UMD-based telemetry using ArcTelemetryReader / FirmwareInfoProvider.
class UmdTelemetryProvider : public TelemetryProvider {
public:
    explicit UmdTelemetryProvider(UmdDiscoveryProvider* discovery)
        : discovery_(discovery) {}

    std::string name() const override { return "umd"; }
    bool update(DeviceInfo& device) override;

private:
    UmdDiscoveryProvider* discovery_;
};

}  // namespace tt_device_hal
