// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "types.hpp"
#include "providers.hpp"
#include "fabric_provider.hpp"
#include <memory>
#include <string>
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

    std::vector<DeviceInfo> devices_;
    bool discovered_ = false;
};

/// Format a byte count with binary units (KiB, MiB, GiB).
std::string format_bytes(uint64_t bytes);

}  // namespace tt_device_hal
