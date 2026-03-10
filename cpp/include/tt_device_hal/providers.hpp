// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "types.hpp"
#include <memory>
#include <string>
#include <vector>

namespace tt_device_hal {

/// Interface for device discovery backends.
class DeviceDiscoveryProvider {
public:
    virtual ~DeviceDiscoveryProvider() = default;
    virtual std::string name() const = 0;
    virtual std::vector<DeviceInfo> discover() = 0;
};

/// Interface for telemetry data collection backends.
class TelemetryProvider {
public:
    virtual ~TelemetryProvider() = default;
    virtual std::string name() const = 0;
    virtual bool update(DeviceInfo& device) = 0;
};

/// Interface for memory/process tracking backends.
class MemoryProvider {
public:
    virtual ~MemoryProvider() = default;
    virtual std::string name() const = 0;
    virtual bool update(DeviceInfo& device) = 0;
    virtual int cleanup_dead_processes() { return 0; }
};

}  // namespace tt_device_hal
