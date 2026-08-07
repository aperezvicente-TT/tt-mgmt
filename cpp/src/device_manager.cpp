// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#include "tt_device_hal/device_manager.hpp"
#include "providers/helpers.hpp"
#include "providers/umd_discovery.hpp"
#include "providers/umd_telemetry.hpp"
#include "providers/shm_memory.hpp"
#include "providers/sysfs_discovery.hpp"
#include "providers/sysfs_telemetry.hpp"
#include "providers/fabric_manager_provider.hpp"
#include "providers/umd_noc_access.hpp"
#include "providers/umd_fan_control.hpp"

#include "umd/device/pcie/pci_device.hpp"

#include <iostream>
#include <algorithm>
#include <map>
#include <vector>
#include <cstdlib>

namespace tt_device_hal {

void DeviceManager::set_discovery(std::unique_ptr<DeviceDiscoveryProvider> p) {
    discovery_ = std::move(p);
    discovered_ = false;
}

void DeviceManager::add_telemetry_provider(std::unique_ptr<TelemetryProvider> p) {
    telemetry_providers_.push_back(std::move(p));
}

void DeviceManager::set_memory_provider(std::unique_ptr<MemoryProvider> p) {
    memory_ = std::move(p);
}

void DeviceManager::set_fabric_provider(std::unique_ptr<FabricProvider> p) {
    fabric_ = std::move(p);
}

void DeviceManager::set_noc_access_provider(std::unique_ptr<NocAccessProvider> p) {
    noc_access_ = std::move(p);
}

void DeviceManager::set_fan_control_provider(std::unique_ptr<FanControlProvider> p) {
    fan_control_ = std::move(p);
}

std::vector<uint8_t> DeviceManager::noc_read(int chip_id, uint32_t noc_x, uint32_t noc_y,
                                              uint64_t addr, uint32_t size) {
    if (!noc_access_) {
        throw std::runtime_error("No NOC access provider configured");
    }
    return noc_access_->read(chip_id, noc_x, noc_y, addr, size);
}

void DeviceManager::noc_write(int chip_id, uint32_t noc_x, uint32_t noc_y,
                               uint64_t addr, const std::vector<uint8_t>& data) {
    if (!noc_access_) {
        throw std::runtime_error("No NOC access provider configured");
    }
    noc_access_->write(chip_id, noc_x, noc_y, addr, data);
}

uint32_t DeviceManager::noc_read32(int chip_id, uint32_t noc_x, uint32_t noc_y, uint64_t addr) {
    if (!noc_access_) {
        throw std::runtime_error("No NOC access provider configured");
    }
    return noc_access_->read32(chip_id, noc_x, noc_y, addr);
}

void DeviceManager::noc_write32(int chip_id, uint32_t noc_x, uint32_t noc_y,
                                 uint64_t addr, uint32_t value) {
    if (!noc_access_) {
        throw std::runtime_error("No NOC access provider configured");
    }
    noc_access_->write32(chip_id, noc_x, noc_y, addr, value);
}

std::vector<DeviceInfo>& DeviceManager::discover() {
    if (!discovered_ && discovery_) {
        devices_ = discovery_->discover();

        // Build a globally-stable kmd_id → logical_id map by BDF-sorting ALL
        // physical devices on the system, ignoring TT_VISIBLE_DEVICES.
        // This ensures that a device's logical ID is the same whether or not
        // other devices are filtered out.
        std::map<int, int> kmd_to_logical_id;
        try {
            // Temporarily unset TT_VISIBLE_DEVICES so enumerate_devices_info() returns
            // ALL physical devices, not just the filtered subset.  This gives us a
            // globally-stable BDF→kmd_id map that is independent of which devices the
            // caller asked to see.
            const char* saved_env = getenv("TT_VISIBLE_DEVICES");
            std::string saved_val = saved_env ? saved_env : "";
            if (saved_env) {
                unsetenv("TT_VISIBLE_DEVICES");
            }
            auto all_devs = tt::umd::PCIDevice::enumerate_devices_info();
            if (!saved_val.empty()) {
                setenv("TT_VISIBLE_DEVICES", saved_val.c_str(), 1);
            }
            // Sort entries by BDF string to get a stable global rank.
            std::vector<std::pair<std::string, int>> sorted_devs;
            sorted_devs.reserve(all_devs.size());
            for (const auto& kv : all_devs) {
                sorted_devs.emplace_back(kv.second.pci_bdf, kv.first);
            }
            std::sort(sorted_devs.begin(), sorted_devs.end(),
                      [](const auto& a, const auto& b) { return a.first < b.first; });
            for (int lid = 0; lid < static_cast<int>(sorted_devs.size()); ++lid) {
                kmd_to_logical_id[sorted_devs[lid].second] = lid;
            }
        } catch (...) {
            // If UMD is unavailable (e.g. sysfs backend), fall back to
            // BDF-sorting the visible devices only.
            std::vector<size_t> local_indices;
            for (size_t i = 0; i < devices_.size(); ++i) {
                if (!devices_[i].is_remote && !devices_[i].pci_bdf.empty()) {
                    local_indices.push_back(i);
                }
            }
            std::sort(local_indices.begin(), local_indices.end(),
                      [this](size_t a, size_t b) {
                          return devices_[a].pci_bdf < devices_[b].pci_bdf;
                      });
            for (int lid = 0; lid < static_cast<int>(local_indices.size()); ++lid) {
                devices_[local_indices[lid]].logical_id = lid;
            }
            discovered_ = true;
            return devices_;
        }

        // Assign each visible local device its globally-stable logical ID.
        for (auto& dev : devices_) {
            if (!dev.is_remote && dev.pci_ordinal >= 0) {
                auto it = kmd_to_logical_id.find(dev.pci_ordinal);
                if (it != kmd_to_logical_id.end()) {
                    dev.logical_id = it->second;
                }
            }
        }

        discovered_ = true;
    }
    return devices_;
}

bool DeviceManager::update_telemetry(DeviceInfo& dev) {
    for (auto& provider : telemetry_providers_) {
        if (provider->update(dev)) {
            return true;
        }
    }
    return false;
}

bool DeviceManager::update_memory(DeviceInfo& dev) {
    if (memory_) {
        return memory_->update(dev);
    }
    return false;
}

int DeviceManager::cleanup_dead_processes() {
    if (memory_) {
        return memory_->cleanup_dead_processes();
    }
    return 0;
}

std::string DeviceManager::backend_name() const {
    if (discovery_) {
        return discovery_->name();
    }
    return "none";
}

std::vector<std::pair<uint32_t, uint32_t>> DeviceManager::get_eth_cores(int chip_id) {
    if (!noc_access_) {
        throw std::runtime_error("No NOC access provider configured");
    }
    // Downcast to UmdNocAccessProvider to access get_eth_cores
    auto* umd_noc = dynamic_cast<UmdNocAccessProvider*>(noc_access_.get());
    if (!umd_noc) {
        throw std::runtime_error("get_eth_cores requires UMD backend");
    }
    return umd_noc->get_eth_cores(chip_id);
}

// ---- Fan control ----

bool DeviceManager::has_fan_control() const {
    return fan_control_ != nullptr;
}

void DeviceManager::set_board_fan(uint64_t board_id, int pct) {
    if (!fan_control_) {
        throw std::runtime_error(
            "No fan control provider configured (requires the UMD backend)");
    }
    fan_control_->set_board_fan(board_id, pct);
}

FanState DeviceManager::get_fan_state(uint64_t asic_id) {
    if (!fan_control_) {
        return FanState{};
    }
    return fan_control_->get_fan_state(asic_id);
}

// ---- Factory: UMD (default) ----

std::unique_ptr<DeviceManager> DeviceManager::create_default() {
    auto mgr = std::make_unique<DeviceManager>();
    auto discovery = std::make_unique<UmdDiscoveryProvider>();
    auto* disc_ptr = discovery.get();
    mgr->set_discovery(std::move(discovery));
    mgr->add_telemetry_provider(std::make_unique<UmdTelemetryProvider>(disc_ptr));
    mgr->set_memory_provider(std::make_unique<ShmMemoryProvider>(disc_ptr));
    mgr->set_noc_access_provider(std::make_unique<UmdNocAccessProvider>(disc_ptr));
    mgr->set_fan_control_provider(std::make_unique<UmdFanControlProvider>(disc_ptr));
    return mgr;
}

// ---- Factory: sysfs ----

std::unique_ptr<DeviceManager> DeviceManager::create_sysfs() {
    auto mgr = std::make_unique<DeviceManager>();
    auto discovery = std::make_unique<SysfsDiscoveryProvider>();
    auto* disc_ptr = discovery.get();
    mgr->set_discovery(std::move(discovery));
    mgr->add_telemetry_provider(std::make_unique<SysfsTelemetryProvider>(disc_ptr));
    mgr->set_memory_provider(std::make_unique<ShmMemoryProvider>());
    return mgr;
}

// ---- Factory: auto ----

std::unique_ptr<DeviceManager> DeviceManager::create_auto() {
    try {
        auto mgr = create_default();
        auto& devs = mgr->discover();
        if (!devs.empty()) {
            return mgr;
        }
    } catch (const std::exception& e) {
        std::cerr << "[tt_mgmt] UMD backend failed: " << e.what()
                  << "; trying sysfs." << std::endl;
    }
    return create_sysfs();
}

// ---- Fabric methods ----

bool DeviceManager::has_fabric() const {
    return fabric_ && fabric_->is_connected();
}

FabricClusterInfo DeviceManager::get_cluster_topology() {
    if (!fabric_) {
        FabricClusterInfo info;
        info.error = "No fabric provider configured";
        return info;
    }
    return fabric_->get_cluster_topology();
}

PlacementResult DeviceManager::get_placements(
    const std::string& mgd_textproto,
    const std::vector<std::string>& host_ids) {
    if (!fabric_) {
        PlacementResult r;
        r.status = "ERROR";
        r.error_message = "No fabric provider configured";
        return r;
    }
    return fabric_->get_placements(mgd_textproto, host_ids);
}

// ---- Factory: UMD + Fabric Manager ----

std::unique_ptr<DeviceManager> DeviceManager::create_with_fabric(const std::string& endpoint) {
    auto mgr = create_default();
    auto fabric = std::make_unique<FabricManagerProvider>(endpoint);
    fabric->connect();
    mgr->set_fabric_provider(std::move(fabric));
    return mgr;
}

}  // namespace tt_device_hal
