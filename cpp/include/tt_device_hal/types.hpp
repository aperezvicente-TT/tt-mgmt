// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

/**
 * @file types.hpp
 * @brief Canonical types for the tt_device_hal API.
 *
 * Every provider, binding, and consumer uses these types directly.
 */

#pragma once

#include <string>
#include <vector>
#include <cstdint>

namespace tt_device_hal {

/// Telemetry data for a device.
struct TelemetryData {
    float temperature = -1.0f;
    float power = -1.0f;
    uint32_t voltage_mv = 0;
    uint32_t current_ma = 0;
    uint32_t aiclk_mhz = 0;
    float vreg_temperature = -1.0f;
    float board_temperature = -1.0f;
    uint32_t axiclk_mhz = 0;
    uint32_t arcclk_mhz = 0;
    uint32_t ddr_speed_mhz = 0;
    uint32_t fan_speed_pct = 0;
    uint32_t fan_speed_rpm = 0;
    uint32_t tdp_limit_w = 0;
    uint32_t tdc_limit_a = 0;
    uint32_t aiclk_limit_mhz = 0;
    uint32_t input_power_w = 0;
    uint32_t max_gddr_temp = 0;
    uint32_t gddr01_temp = 0;
    uint32_t gddr23_temp = 0;
    uint32_t gddr45_temp = 0;
    uint32_t gddr67_temp = 0;
    std::string status = "Unknown";
    bool available = false;
};

/// Firmware version information.
struct FirmwareInfo {
    std::string fw_bundle_ver;
    std::string arc_fw_ver;
    std::string eth_fw_ver;
    std::string m3app_fw_ver;
    std::string ttflash_ver;
};

/// Per-process memory allocation.
struct ProcessMemory {
    int pid = 0;
    std::string name;
    std::string cmdline;               ///< first 256 bytes of /proc/<pid>/cmdline, NUL-bytes replaced by spaces
    bool registered_to_device = false;

    // TT device memory allocations (from SHM / KMD)
    uint64_t dram_allocated = 0;
    uint64_t l1_allocated = 0;
    uint64_t l1_small_allocated = 0;
    uint64_t trace_allocated = 0;
    uint64_t cb_allocated = 0;

    // CPU / host-side metrics (from /proc/<pid>/stat and /proc/<pid>/status)
    uint64_t runtime_seconds = 0;
    float cpu_percent = 0.0f;          ///< lifetime-average CPU% (utime+stime / runtime)
    uint64_t vm_rss_kb = 0;           ///< physical RAM resident set size (kB)
    uint64_t vm_virt_kb = 0;          ///< virtual address space size (kB)
    uint64_t vm_swap_kb = 0;          ///< bytes currently swapped out (kB)
    uint32_t num_threads = 0;         ///< total thread count for this process
};

/// A single ethernet link between two ASICs.
struct EthConnection {
    uint32_t local_channel = 0;
    uint64_t remote_chip_id = 0;   ///< maps to another DeviceInfo.chip_id
    uint32_t remote_channel = 0;
    bool is_exit_link = false;     ///< true = goes off-host (remote device not in cluster)
};

/// Ethernet routing coordinates assigned by firmware.
struct EthCoord {
    int cluster_id = -1;
    int x = -1;
    int y = -1;
    int rack = -1;
    int shelf = -1;
};

/// Full device information -- the single canonical device type.
struct DeviceInfo {
    // Identity
    uint64_t chip_id = 0;
    uint64_t asic_id = 0;
    uint64_t board_id = 0;
    int pci_ordinal = -1;
    int logical_id = -1;
    std::string pci_bdf;
    std::string arch_name;
    bool is_remote = false;

    // Board metadata (populated by sysfs or UMD)
    std::string serial;
    std::string card_type;

    // Display
    uint32_t tray_id = 0;
    uint32_t chip_in_tray = 0;
    uint8_t asic_location = 0;
    std::string display_id;

    // Telemetry
    TelemetryData telemetry;

    // Firmware
    FirmwareInfo firmware;

    // Memory
    uint64_t total_dram = 0;
    uint64_t used_dram = 0;
    uint64_t total_l1 = 0;
    uint64_t used_l1 = 0;
    uint64_t used_l1_small = 0;
    uint64_t used_trace = 0;
    uint64_t used_cb = 0;

    // Processes
    std::vector<ProcessMemory> processes;

    // SHM
    bool has_shm = false;

    // Topology (populated by UMD backend)
    std::vector<EthConnection> eth_connections;
    EthCoord eth_coord;
    uint32_t active_eth_channels = 0;
    uint32_t idle_eth_channels = 0;
    bool is_mmio_capable = false;
};

// ---- Fabric Manager cluster types (Phase 2) ----

/// Host summary from fabric manager.
struct FabricHost {
    std::string host_name;
    int asic_count = 0;
    std::string arch;
    std::vector<std::string> connected_hosts;
};

/// Aggregated cluster topology from fabric manager.
struct FabricClusterInfo {
    std::vector<FabricHost> hosts;
    int total_cross_host_links = 0;
    bool connected = false;
    std::string error;
};

/// A single host-to-ASIC assignment within a placement.
struct PlacementAssignment {
    std::string host_id;
    int rank = 0;
    std::vector<uint64_t> asic_ids;
};

/// Result of a placement query.
struct PlacementResult {
    bool success = false;
    std::string status;
    std::string error_message;
    std::vector<std::vector<PlacementAssignment>> placements;
};

}  // namespace tt_device_hal
