// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "types.hpp"
#include "providers.hpp"
#include "fabric_provider.hpp"
#include "noc_access_provider.hpp"
#include "fan_control_provider.hpp"
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace tt_device_hal {

/// Central orchestrator: owns providers and manages device state.
class DeviceManager {
public:
    DeviceManager() = default;
    ~DeviceManager() = default;

    DeviceManager(const DeviceManager&) = delete;
    DeviceManager& operator=(const DeviceManager&) = delete;
    DeviceManager(DeviceManager&&) = default;
    DeviceManager& operator=(DeviceManager&&) = default;

    void set_discovery(std::unique_ptr<DeviceDiscoveryProvider> p);
    void add_telemetry_provider(std::unique_ptr<TelemetryProvider> p);
    void set_memory_provider(std::unique_ptr<MemoryProvider> p);
    void set_fabric_provider(std::unique_ptr<FabricProvider> p);
    void set_noc_access_provider(std::unique_ptr<NocAccessProvider> p);
    void set_fan_control_provider(std::unique_ptr<FanControlProvider> p);

    // ---- NOC memory access ----

    /// Read bytes from device NOC memory.
    std::vector<uint8_t> noc_read(int chip_id, uint32_t noc_x, uint32_t noc_y,
                                   uint64_t addr, uint32_t size);
    /// Write bytes to device NOC memory.
    void noc_write(int chip_id, uint32_t noc_x, uint32_t noc_y,
                   uint64_t addr, const std::vector<uint8_t>& data);
    /// Read a single 32-bit word from device NOC memory.
    uint32_t noc_read32(int chip_id, uint32_t noc_x, uint32_t noc_y, uint64_t addr);
    /// Write a single 32-bit word to device NOC memory.
    void noc_write32(int chip_id, uint32_t noc_x, uint32_t noc_y,
                     uint64_t addr, uint32_t value);

    /// Get ETH core NOC0 coordinates. Returns vector of (noc_x, noc_y).
    std::vector<std::pair<uint32_t, uint32_t>> get_eth_cores(int chip_id);

    // ---- Fan control ----

    /// Whether a fan control provider is configured.
    bool has_fan_control() const;

    /// Force every ASIC on `board_id` to `pct` (0..100), or -1 to release control
    /// back to the firmware thermal curve. Throws if unsupported or rejected.
    void set_board_fan(uint64_t board_id, int pct);

    /// Read the fan state one ASIC currently sees.
    FanState get_fan_state(uint64_t asic_id);

    /// Run discovery and return device list. Subsequent calls return cached results.
    std::vector<DeviceInfo>& discover();

    /// Update telemetry for a device (tries providers in order until one succeeds).
    bool update_telemetry(DeviceInfo& dev);

    /// Update memory/process info for a device.
    bool update_memory(DeviceInfo& dev);

    /// Clean up dead processes from SHM.
    int cleanup_dead_processes();

    /// Name of the active discovery backend.
    std::string backend_name() const;

    // ---- Fabric (cluster-level, optional) ----

    /// Whether a fabric provider is configured and connected.
    bool has_fabric() const;

    /// Query aggregated cluster topology from fabric manager.
    FabricClusterInfo get_cluster_topology();

    /// Query valid placements for a mesh graph descriptor.
    PlacementResult get_placements(
        const std::string& mgd_textproto,
        const std::vector<std::string>& host_ids = {});

    // ---- Factory methods ----

    /// UMD discovery + UMD telemetry + SHM memory (same behavior as the old monolith).
    static std::unique_ptr<DeviceManager> create_default();

    /// Sysfs discovery + sysfs telemetry + SHM memory (no UMD dependency at runtime).
    static std::unique_ptr<DeviceManager> create_sysfs();

    /// Try UMD first; if discovery fails or returns 0 devices, fall back to sysfs.
    static std::unique_ptr<DeviceManager> create_auto();

    /// UMD + SHM + FabricManagerProvider connected to the given endpoint.
    static std::unique_ptr<DeviceManager> create_with_fabric(const std::string& endpoint);

private:
    std::unique_ptr<DeviceDiscoveryProvider> discovery_;
    std::vector<std::unique_ptr<TelemetryProvider>> telemetry_providers_;
    std::unique_ptr<MemoryProvider> memory_;
    std::unique_ptr<FabricProvider> fabric_;
    std::unique_ptr<NocAccessProvider> noc_access_;
    std::unique_ptr<FanControlProvider> fan_control_;

    std::vector<DeviceInfo> devices_;
    bool discovered_ = false;
};

/// Format a byte count with binary units (KiB, MiB, GiB).
std::string format_bytes(uint64_t bytes);

}  // namespace tt_device_hal
