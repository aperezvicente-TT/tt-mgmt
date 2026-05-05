// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "tt_device_hal/providers.hpp"
#include "umd/device/tt_device/tt_device.hpp"
#include "umd/device/cluster_descriptor.hpp"
#include "umd/device/types/arch.hpp"

#include <map>
#include <memory>
#include <chrono>

namespace tt_device_hal {

/// Internal cache entry for a discovered device.
struct DeviceCache {
    std::unique_ptr<tt::umd::TTDevice> tt_device;
    int pci_ordinal = -1;
    tt::ARCH arch = tt::ARCH::Invalid;
    uint64_t board_id = 0;
    uint64_t chip_id = 0;
    uint64_t asic_id = 0;
    uint64_t shm_asic_id = 0;
    std::string pci_bdf;
    bool is_remote = false;
    bool telemetry_initialized = false;

    // Topology (populated from ClusterDescriptor)
    std::vector<EthConnection> eth_connections;
    EthCoord eth_coord;
    uint32_t active_eth_channels = 0;
    uint32_t idle_eth_channels = 0;
    bool is_mmio_capable = false;
};

/// UMD-based device discovery using TopologyDiscovery.
class UmdDiscoveryProvider : public DeviceDiscoveryProvider {
public:
    std::string name() const override { return "umd"; }
    std::vector<DeviceInfo> discover() override;

    /// Access to a cached TTDevice handle (needed by UmdTelemetryProvider).
    tt::umd::TTDevice* get_tt_device(uint64_t asic_id);
    DeviceCache* get_cache(uint64_t asic_id);
    bool invalidate_cache();

    /// Get TTDevice by UMD chip ID (used by NocAccessProvider).
    tt::umd::TTDevice* get_tt_device_by_umd_chip_id(int chip_id);

    /// Return the PCI ordinal of a local MMIO chip that has an ETH link to
    /// the given remote chip, or -1 if none is known. Used by telemetry to
    /// route CHIP_IN_USE checks for remotes to their MMIO parent.
    int find_mmio_parent_ordinal(uint64_t remote_chip_id);

private:
    struct ArchCluster {
        tt::ARCH arch = tt::ARCH::Invalid;
        std::unique_ptr<tt::umd::ClusterDescriptor> descriptor;
        std::map<tt::ChipId, std::unique_ptr<tt::umd::TTDevice>> devices;
        bool discovery_successful = false;
        std::string error_message;
    };

    std::map<tt::ARCH, ArchCluster> arch_clusters_;
    std::map<uint64_t, DeviceCache> device_cache_;
    bool initialized_ = false;

    static constexpr int RESCAN_DELAY_SEC          = 3;
    static constexpr int RESCAN_INTERVAL_SEC       = 2;
    static constexpr int INVALIDATION_COOLDOWN_SEC = 15;

    std::chrono::steady_clock::time_point invalidate_time_;
    bool invalidate_time_valid_ = false;
    std::chrono::steady_clock::time_point last_empty_discovery_time_;
    bool last_empty_discovery_valid_ = false;

    void initialize();
};

}  // namespace tt_device_hal
