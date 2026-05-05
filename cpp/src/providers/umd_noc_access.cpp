// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#include "umd_noc_access.hpp"
#include "umd/device/types/xy_pair.hpp"
#include "umd/device/soc_descriptor.hpp"
#include "umd/device/types/core_coordinates.hpp"

#include <algorithm>
#include <stdexcept>

using tt::CoreType;
using tt::CoordSystem;

namespace tt_device_hal {

UmdNocAccessProvider::UmdNocAccessProvider(UmdDiscoveryProvider* discovery)
    : discovery_(discovery) {}

// UMD's read_from_device/write_to_device expect TRANSLATED coordinates (the
// coordinate system after NOC address translation is applied by hardware).
// All callers of this provider supply NOC0 coordinates, so we must translate
// before forwarding to UMD.  This matches what UMD's own TopologyDiscovery
// does internally for every read/write.
tt_xy_pair UmdNocAccessProvider::translate_noc0_to_translated(
    tt::umd::TTDevice* device, uint32_t noc_x, uint32_t noc_y) {
    // Cache SocDescriptor keyed by device pointer — very cheap after first call.
    auto key = reinterpret_cast<uintptr_t>(device);
    auto it = soc_desc_cache_.find(key);
    if (it == soc_desc_cache_.end()) {
        it = soc_desc_cache_.emplace(
            key, tt::umd::SocDescriptor(device->get_arch(), device->get_chip_info())).first;
    }
    // Our API always accepts NOC0 coordinates.
    auto translated = it->second.translate_coord_to(
        tt_xy_pair(noc_x, noc_y), CoordSystem::NOC0, CoordSystem::TRANSLATED);

    // Safety: on Blackhole with NOC translation enabled, every valid core must
    // have a different translated coordinate.  An identity mapping means the
    // core is not in the SocDescriptor (e.g. harvested) and using it with the
    // TLB would target a wrong NOC node, causing PCIe faults / system resets.
    if (translated.x == noc_x && translated.y == noc_y &&
        device->get_arch() == tt::ARCH::BLACKHOLE) {
        throw std::runtime_error(
            "NOC coordinate (" + std::to_string(noc_x) + "," + std::to_string(noc_y) +
            ") has no translation — core is likely harvested or invalid");
    }
    return translated;
}

std::vector<uint8_t> UmdNocAccessProvider::read(
    int chip_id, uint32_t noc_x, uint32_t noc_y,
    uint64_t addr, uint32_t size) {
    auto* device = discovery_->get_tt_device_by_umd_chip_id(chip_id);
    if (!device) {
        throw std::runtime_error("No device found for chip_id " + std::to_string(chip_id));
    }
    tt_xy_pair translated = translate_noc0_to_translated(device, noc_x, noc_y);
    std::vector<uint8_t> data(size);
    device->read_from_device(data.data(), translated, addr, size);
    return data;
}

void UmdNocAccessProvider::write(
    int chip_id, uint32_t noc_x, uint32_t noc_y,
    uint64_t addr, const std::vector<uint8_t>& data) {
    auto* device = discovery_->get_tt_device_by_umd_chip_id(chip_id);
    if (!device) {
        throw std::runtime_error("No device found for chip_id " + std::to_string(chip_id));
    }
    tt_xy_pair translated = translate_noc0_to_translated(device, noc_x, noc_y);
    device->write_to_device(data.data(), translated, addr, data.size());
}

uint32_t UmdNocAccessProvider::read32(
    int chip_id, uint32_t noc_x, uint32_t noc_y,
    uint64_t addr) {
    auto* device = discovery_->get_tt_device_by_umd_chip_id(chip_id);
    if (!device) {
        throw std::runtime_error("No device found for chip_id " + std::to_string(chip_id));
    }
    tt_xy_pair translated = translate_noc0_to_translated(device, noc_x, noc_y);
    uint32_t val = 0;
    device->read_from_device(&val, translated, addr, sizeof(uint32_t));
    return val;
}

void UmdNocAccessProvider::write32(
    int chip_id, uint32_t noc_x, uint32_t noc_y,
    uint64_t addr, uint32_t value) {
    auto* device = discovery_->get_tt_device_by_umd_chip_id(chip_id);
    if (!device) {
        throw std::runtime_error("No device found for chip_id " + std::to_string(chip_id));
    }
    tt_xy_pair translated = translate_noc0_to_translated(device, noc_x, noc_y);
    device->write_to_device(&value, translated, addr, sizeof(uint32_t));
}

std::vector<std::pair<uint32_t, uint32_t>> UmdNocAccessProvider::get_eth_cores(int chip_id) {
    auto* device = discovery_->get_tt_device_by_umd_chip_id(chip_id);
    if (!device) {
        throw std::runtime_error("No device found for chip_id " + std::to_string(chip_id));
    }

    std::vector<std::pair<uint32_t, uint32_t>> result;

    // Only query ACTIVE_ETH and IDLE_ETH — CoreType::ETH includes harvested
    // cores that have no valid NOC translation and cannot be safely accessed.
    try {
        auto chip_info = device->get_chip_info();
        tt::umd::SocDescriptor soc(device->get_arch(), chip_info);
        for (auto ct : {CoreType::ACTIVE_ETH, CoreType::IDLE_ETH}) {
            try {
                auto cores = soc.get_cores(ct, CoordSystem::NOC0);
                for (const auto& c : cores) {
                    result.emplace_back(static_cast<uint32_t>(c.x), static_cast<uint32_t>(c.y));
                }
            } catch (...) {}
        }
    } catch (const std::exception& e) {
        std::cerr << "[tt_mgmt] SoC descriptor failed: " << e.what() << std::endl;
    }

    // Fallback: use known Blackhole ETH core layout (all 14 possible, y=1)
    if (result.empty() && device->get_arch() == tt::ARCH::BLACKHOLE) {
        // Blackhole has up to 14 ETH cores at y=1, x={2,3,4,5,6,7,10,11,12,13,14,15,16,17}
        for (uint32_t x : {2u, 3u, 4u, 5u, 6u, 7u, 10u, 11u, 12u, 13u, 14u, 15u, 16u, 17u}) {
            result.emplace_back(x, 1u);
        }
    }

    // Deduplicate and sort by x
    std::sort(result.begin(), result.end());
    result.erase(std::unique(result.begin(), result.end()), result.end());
    return result;
}

}  // namespace tt_device_hal
