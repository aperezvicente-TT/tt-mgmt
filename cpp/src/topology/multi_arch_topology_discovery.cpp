// SPDX-FileCopyrightText: © 2025 Tenstorrent Inc.
//
// SPDX-License-Identifier: Apache-2.0

#include "umd/device/topology/multi_arch_topology_discovery.hpp"

#include <algorithm>
#include <iostream>
#include <string>

#include "fmt/format.h"
#include "umd/device/pcie/pci_device.hpp"
#include "umd/device/tt_device/tt_device.hpp"

namespace tt::umd {

std::unordered_set<tt::ARCH> MultiArchTopologyDiscovery::get_available_architectures() {
    std::unordered_set<tt::ARCH> architectures;

    try {
        auto pci_devices = PCIDevice::enumerate_devices_info();
        for (const auto& [ordinal, info] : pci_devices) {
            tt::ARCH arch = info.get_arch();
            if (arch != tt::ARCH::Invalid) {
                architectures.insert(arch);
            }
        }
    } catch (const std::exception& e) {
        std::cerr << "[tt_umd] Failed to enumerate PCI devices: " << e.what() << std::endl;
    }

    return architectures;
}

std::string MultiArchTopologyDiscovery::set_visible_devices_filter(const std::unordered_set<int>& ordinals) {
    return "";
}

void MultiArchTopologyDiscovery::restore_visible_devices(const std::string& previous_value) {
}

MultiArchTopologyDiscovery::ArchCluster MultiArchTopologyDiscovery::discover_single_architecture(
    tt::ARCH target_arch,
    const TopologyDiscoveryOptions& options
) {
    ArchCluster cluster(target_arch);

    try {
        // Populate pci_ordinals for informational purposes
        auto pci_devices = PCIDevice::enumerate_devices_info();
        for (const auto& [ordinal, info] : pci_devices) {
            if (info.get_arch() == target_arch) {
                cluster.pci_ordinals.insert(ordinal);
            }
        }

        // Use the UMD branch's options.architecture to filter devices internally.
        // TopologyDiscovery::discover() will enumerate all PCI devices but only
        // initialize and include those matching the target architecture.
        TopologyDiscoveryOptions arch_options = options;
        arch_options.architecture = target_arch;
        auto [descriptor, devices] = TopologyDiscovery::discover(arch_options);

        cluster.descriptor = std::move(descriptor);
        cluster.devices = std::move(devices);
        cluster.discovery_successful = true;

    } catch (const std::exception& e) {
        cluster.error_message = fmt::format("TopologyDiscovery failed for architecture {}: {}",
                                            static_cast<int>(target_arch), e.what());
        std::cerr << "[tt_umd] " << cluster.error_message << std::endl;
    }

    return cluster;
}

std::map<tt::ARCH, MultiArchTopologyDiscovery::ArchCluster>
MultiArchTopologyDiscovery::discover_by_architecture(const TopologyDiscoveryOptions& base_options) {
    std::map<tt::ARCH, ArchCluster> clusters;

    auto architectures = get_available_architectures();

    if (architectures.empty()) {
        return clusters;
    }

    for (tt::ARCH arch : architectures) {
        auto cluster = discover_single_architecture(arch, base_options);
        clusters[arch] = std::move(cluster);
    }

    return clusters;
}

}  // namespace tt::umd
