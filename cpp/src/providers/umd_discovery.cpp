// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#include "umd_discovery.hpp"
#include "helpers.hpp"

#include <iostream>
#include <iomanip>
#include <sstream>
#include <cstdio>
#include <algorithm>
#include <set>
#include <unordered_map>

#include "umd/device/pcie/pci_device.hpp"
#include "umd/device/soc_descriptor.hpp"
#include "umd/device/types/arch.hpp"
#include "umd/device/arc/arc_telemetry_reader.hpp"
#include "umd/device/types/telemetry.hpp"
#include "umd/device/topology/topology_discovery.hpp"
#include "umd/device/types/cluster_descriptor_types.hpp"

using namespace tt::umd;

namespace tt_device_hal {

static std::string get_arch_name(tt::ARCH arch) {
    switch (arch) {
        case tt::ARCH::WORMHOLE_B0: return "Wormhole_B0";
        case tt::ARCH::BLACKHOLE: return "Blackhole";
        case tt::ARCH::QUASAR: return "Quasar";
        default: return "Unknown";
    }
}

static void set_memory_sizes(DeviceInfo& dev, TTDevice* tt_device) {
    if (tt_device) {
        try {
            SocDescriptor soc_desc(tt_device->get_arch(), tt_device->get_chip_info());
            size_t num_dram_channels = soc_desc.get_num_dram_channels();
            uint32_t l1_size_per_core = soc_desc.worker_l1_size;
            auto tensix_grid = soc_desc.get_grid_size(tt::CoreType::TENSIX);
            uint64_t dram_size_per_channel = soc_desc.dram_bank_size;

            dev.total_dram = (uint64_t)num_dram_channels * dram_size_per_channel;
            dev.total_l1 = (uint64_t)l1_size_per_core * tensix_grid.x * tensix_grid.y;
        } catch (...) {
            dev.total_dram = 0;
            dev.total_l1 = 0;
        }
    }
}

bool UmdDiscoveryProvider::invalidate_cache() {
    auto now = std::chrono::steady_clock::now();
    if (invalidate_time_valid_) {
        auto since_last = std::chrono::duration_cast<std::chrono::seconds>(now - invalidate_time_).count();
        if (since_last < INVALIDATION_COOLDOWN_SEC) {
            return false;
        }
    }
    device_cache_.clear();
    arch_clusters_.clear();
    initialized_ = false;
    invalidate_time_ = now;
    invalidate_time_valid_ = true;
    last_empty_discovery_valid_ = false;
    return true;
}

tt::umd::TTDevice* UmdDiscoveryProvider::get_tt_device(uint64_t asic_id) {
    auto it = device_cache_.find(asic_id);
    if (it != device_cache_.end()) {
        return it->second.tt_device.get();
    }
    return nullptr;
}

DeviceCache* UmdDiscoveryProvider::get_cache(uint64_t asic_id) {
    auto it = device_cache_.find(asic_id);
    if (it != device_cache_.end()) {
        return &it->second;
    }
    return nullptr;
}

void UmdDiscoveryProvider::initialize() {
    if (initialized_) {
        return;
    }

    auto now = std::chrono::steady_clock::now();
    if (invalidate_time_valid_) {
        auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(now - invalidate_time_).count();
        if (elapsed < RESCAN_DELAY_SEC) {
            return;
        }
        invalidate_time_valid_ = false;
    }
    if (last_empty_discovery_valid_) {
        auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(now - last_empty_discovery_time_).count();
        if (elapsed < RESCAN_INTERVAL_SEC) {
            return;
        }
    }

    std::cerr << "[tt_mgmt] Starting multi-architecture device discovery..." << std::endl;

    // Enumerate all present architectures from PCIe device list
    auto all_pci = PCIDevice::enumerate_devices_info();
    std::set<tt::ARCH> detected_archs;
    for (auto& [n, info] : all_pci) {
        if (info.get_arch() != tt::ARCH::Invalid) {
            detected_archs.insert(info.get_arch());
        }
    }

    for (tt::ARCH arch : detected_archs) {
        ArchCluster cluster;
        cluster.arch = arch;
        TopologyDiscoveryOptions options;
        options.preferred_arch = arch;
        options.discover_remote_devices = true;
        options.wait_on_ethernet_link_training = false;
        options.low_power = true;
        options.cmfw_mismatch_action = TopologyDiscoveryOptions::Action::IGNORE;
        options.eth_fw_mismatch_action = TopologyDiscoveryOptions::Action::IGNORE;
        options.eth_fw_heartbeat_failure = TopologyDiscoveryOptions::Action::IGNORE;

        try {
            auto [descriptor, devices] = TopologyDiscovery::discover(options);
            cluster.descriptor = std::move(descriptor);
            cluster.devices = std::move(devices);
            cluster.discovery_successful = true;
        } catch (const std::exception& e) {
            cluster.error_message = e.what();
            std::cerr << "[tt_mgmt] TopologyDiscovery failed for " << get_arch_name(arch)
                      << ": " << cluster.error_message << std::endl;
        }
        arch_clusters_[arch] = std::move(cluster);
    }

    std::cerr << "[tt_mgmt] Per-architecture discovery results:" << std::endl;
    for (const auto& [arch, cluster] : arch_clusters_) {
        std::cerr << "[tt_mgmt]   " << get_arch_name(arch) << ": ";
        if (cluster.discovery_successful) {
            std::cerr << cluster.devices.size() << " device(s)" << std::endl;
        } else {
            std::cerr << "FAILED (" << cluster.error_message << ")" << std::endl;
        }
    }

    auto pci_devices = PCIDevice::enumerate_devices_info();
    std::map<uint16_t, int> pci_bus_to_ordinal;
    for (auto& [ordinal, info] : pci_devices) {
        pci_bus_to_ordinal[info.pci_bus] = ordinal;
    }

    for (auto& [arch, cluster] : arch_clusters_) {
        if (!cluster.discovery_successful) {
            continue;
        }

        std::cerr << "[tt_mgmt] Processing " << get_arch_name(arch) << " cluster..." << std::endl;

        for (auto& [chip_id_key, tt_device] : cluster.devices) {
            try {
                uint64_t asic_id = static_cast<uint64_t>(chip_id_key);
                if (cluster.descriptor) {
                    const auto& unique_ids = cluster.descriptor->get_chip_unique_ids();
                    auto uid_it = unique_ids.find(chip_id_key);
                    if (uid_it != unique_ids.end()) {
                        asic_id = uid_it->second;
                    }
                }
                DeviceCache cache;
                cache.asic_id = asic_id;
                cache.chip_id = asic_id;
                cache.arch = tt_device->get_arch();
                cache.is_remote = tt_device->is_remote();
                cache.telemetry_initialized = false;

                if (!cache.is_remote && tt_device->get_pci_device()) {
                    uint16_t bus = tt_device->get_pci_device()->get_device_info().pci_bus;
                    auto bus_it = pci_bus_to_ordinal.find(bus);
                    if (bus_it != pci_bus_to_ordinal.end()) {
                        cache.pci_ordinal = bus_it->second;
                        cache.pci_bdf = pci_devices[cache.pci_ordinal].pci_bdf;
                    } else {
                        cache.pci_ordinal = -1;
                        cache.pci_bdf = "unknown";
                    }
                } else {
                    cache.pci_ordinal = -1;
                    cache.pci_bdf = cache.is_remote ? "remote" : "unknown";
                }

                try {
                    auto* telem_reader = tt_device->get_arc_telemetry_reader();
                    if (telem_reader) {
                        uint32_t board_id_low = telem_reader->read_entry(TelemetryTag::BOARD_ID_LOW);
                        uint32_t board_id_high = telem_reader->read_entry(TelemetryTag::BOARD_ID_HIGH);
                        cache.board_id = (static_cast<uint64_t>(board_id_high) << 32) | board_id_low;
                        cache.telemetry_initialized = true;

                        std::cerr << "[tt_mgmt]   Device 0x" << std::hex << asic_id << std::dec
                                  << ": " << get_arch_name(cache.arch)
                                  << (cache.is_remote ? " (remote)" : " (local)")
                                  << ", board_id=0x" << std::hex << cache.board_id << std::dec
                                  << std::endl;
                    }
                } catch (const std::exception& e) {
                    std::cerr << "[tt_mgmt]     WARNING: Telemetry unavailable: " << e.what() << std::endl;
                    cache.board_id = 0;
                }

                // Compute Metal-compatible shm_asic_id for SHM lookup
                try {
                    uint64_t shm_board_id = tt_device->get_board_id();
                    tt::BoardType board_type = tt_device->get_board_type();
                    uint32_t asic_location_composite = 0;

                    if (board_type == tt::BoardType::UBB && tt_device->get_pci_device()) {
                        uint16_t pci_bus = tt_device->get_pci_device()->get_device_info().pci_bus;
                        static const std::vector<uint16_t> tray_bus_ids = {0xC0, 0x80, 0x00, 0x40};
                        uint16_t bus_upper = pci_bus & 0xF0;
                        auto tray_it = std::find(tray_bus_ids.begin(), tray_bus_ids.end(), bus_upper);
                        if (tray_it != tray_bus_ids.end()) {
                            uint32_t tray_id = static_cast<uint32_t>(tray_it - tray_bus_ids.begin()) + 1;
                            uint32_t ubb_asic_id = pci_bus & 0x0F;
                            asic_location_composite = (tray_id << 4) | ubb_asic_id;
                        } else {
                            asic_location_composite = tt_device->get_chip_info().asic_location;
                        }
                    } else {
                        asic_location_composite = tt_device->get_chip_info().asic_location;
                    }
                    cache.shm_asic_id = (shm_board_id << 8) | asic_location_composite;
                } catch (const std::exception& e) {
                    cache.shm_asic_id = asic_id;
                }

                cache.tt_device = std::move(tt_device);
                device_cache_[asic_id] = std::move(cache);

            } catch (const std::exception& e) {
                std::cerr << "[tt_mgmt]     ERROR processing device: " << e.what() << std::endl;
            }
        }
    }

    // --- Extract topology from each ClusterDescriptor ---
    for (auto& [arch, cluster] : arch_clusters_) {
        if (!cluster.discovery_successful || !cluster.descriptor) {
            continue;
        }
        auto* desc = cluster.descriptor.get();

        // Build ChipId -> asic_id reverse map so EthConnection.remote_chip_id
        // matches the asic_id used as key in device_cache_.
        const auto& unique_ids = desc->get_chip_unique_ids();
        std::unordered_map<int, uint64_t> chip_to_asic;
        for (const auto& [cid, uid] : unique_ids) {
            chip_to_asic[cid] = uid;
        }
        auto resolve = [&](int cid) -> uint64_t {
            auto it = chip_to_asic.find(cid);
            return it != chip_to_asic.end() ? it->second : static_cast<uint64_t>(cid);
        };

        // In-cluster ethernet connections: (ChipId, Channel) -> (ChipId, Channel)
        try {
            const auto& eth_conns = desc->get_ethernet_connections();
            for (const auto& [src_chip, chan_map] : eth_conns) {
                uint64_t src_asic = resolve(src_chip);
                auto cache_it = device_cache_.find(src_asic);
                if (cache_it == device_cache_.end()) continue;
                for (const auto& [local_ch, remote_tuple] : chan_map) {
                    auto [remote_chip, remote_ch] = remote_tuple;
                    EthConnection conn;
                    conn.local_channel = static_cast<uint32_t>(local_ch);
                    conn.remote_chip_id = resolve(remote_chip);
                    conn.remote_channel = static_cast<uint32_t>(remote_ch);
                    conn.is_exit_link = false;
                    cache_it->second.eth_connections.push_back(conn);
                }
            }
        } catch (...) {}

        // Exit links: connections to devices not in this cluster
        try {
            const auto& exit_conns = desc->get_ethernet_connections_to_remote_devices();
            for (const auto& [src_chip, chan_map] : exit_conns) {
                uint64_t src_asic = resolve(src_chip);
                auto cache_it = device_cache_.find(src_asic);
                if (cache_it == device_cache_.end()) continue;
                for (const auto& [local_ch, remote_tuple] : chan_map) {
                    auto [remote_uid, remote_ch] = remote_tuple;
                    EthConnection conn;
                    conn.local_channel = static_cast<uint32_t>(local_ch);
                    conn.remote_chip_id = remote_uid;
                    conn.remote_channel = static_cast<uint32_t>(remote_ch);
                    conn.is_exit_link = true;
                    cache_it->second.eth_connections.push_back(conn);
                }
            }
        } catch (...) {}

        // Chip locations (EthCoord)
        try {
            const auto& locations = desc->get_chip_locations();
            for (const auto& [cid, coord] : locations) {
                uint64_t asic = resolve(cid);
                auto cache_it = device_cache_.find(asic);
                if (cache_it == device_cache_.end()) continue;
                cache_it->second.eth_coord.cluster_id = coord.cluster_id;
                cache_it->second.eth_coord.x = coord.x;
                cache_it->second.eth_coord.y = coord.y;
                cache_it->second.eth_coord.rack = coord.rack;
                cache_it->second.eth_coord.shelf = coord.shelf;
            }
        } catch (...) {}

        // Active/idle ETH channel counts + MMIO capability
        for (const auto& [cid, uid] : unique_ids) {
            uint64_t asic = uid;
            auto cache_it = device_cache_.find(asic);
            if (cache_it == device_cache_.end()) continue;
            try {
                auto active = desc->get_active_eth_channels(cid);
                cache_it->second.active_eth_channels = static_cast<uint32_t>(active.size());
            } catch (...) {}
            try {
                auto idle = desc->get_idle_eth_channels(cid);
                cache_it->second.idle_eth_channels = static_cast<uint32_t>(idle.size());
            } catch (...) {}
            try {
                cache_it->second.is_mmio_capable = desc->is_chip_mmio_capable(cid);
            } catch (...) {}
        }
    }

    std::cerr << "[tt_mgmt] Multi-architecture discovery complete: " << device_cache_.size()
              << " total device(s)" << std::endl;

    if (!device_cache_.empty()) {
        initialized_ = true;
        last_empty_discovery_valid_ = false;
    } else {
        last_empty_discovery_time_ = std::chrono::steady_clock::now();
        last_empty_discovery_valid_ = true;
    }
}

std::vector<DeviceInfo> UmdDiscoveryProvider::discover() {
    initialize();

    std::vector<DeviceInfo> devices;

    for (auto& [asic_id, cache] : device_cache_) {
        DeviceInfo dev;
        dev.chip_id = cache.chip_id;
        dev.asic_id = cache.asic_id;
        dev.board_id = cache.board_id;
        dev.pci_ordinal = cache.pci_ordinal;
        dev.pci_bdf = cache.pci_bdf;
        dev.arch_name = get_arch_name(cache.arch);
        dev.is_remote = cache.is_remote;

        char display_buf[24];
        snprintf(display_buf, sizeof(display_buf), "%llx%s", (unsigned long long)cache.chip_id,
                 cache.is_remote ? "R" : "");
        dev.display_id = display_buf;

        set_memory_sizes(dev, cache.tt_device.get());

        dev.eth_connections = cache.eth_connections;
        dev.eth_coord = cache.eth_coord;
        dev.active_eth_channels = cache.active_eth_channels;
        dev.idle_eth_channels = cache.idle_eth_channels;
        dev.is_mmio_capable = cache.is_mmio_capable;

        devices.push_back(dev);
    }

    return devices;
}

}  // namespace tt_device_hal
