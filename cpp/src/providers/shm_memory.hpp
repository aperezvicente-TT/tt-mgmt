// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "tt_device_hal/providers.hpp"
#include "umd_discovery.hpp"

namespace tt_device_hal {

/// SHM-based memory and process tracking via /dev/shm/tt_device_*_memory.
class ShmMemoryProvider : public MemoryProvider {
public:
    /// @param discovery Optional UMD discovery provider for shm_asic_id and pci_ordinal lookup.
    ///                  If null, SHM is queried using the device's asic_id directly.
    explicit ShmMemoryProvider(UmdDiscoveryProvider* discovery = nullptr)
        : discovery_(discovery) {}

    std::string name() const override { return "shm"; }
    bool update(DeviceInfo& device) override;
    int cleanup_dead_processes() override;

private:
    UmdDiscoveryProvider* discovery_;
};

}  // namespace tt_device_hal
