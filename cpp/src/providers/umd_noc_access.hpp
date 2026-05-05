// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "tt_device_hal/noc_access_provider.hpp"
#include "umd_discovery.hpp"
#include "umd/device/soc_descriptor.hpp"
#include "umd/device/types/xy_pair.hpp"
#include <cstdint>
#include <unordered_map>
#include <utility>

namespace tt_device_hal {

/// UMD-based NOC access provider. Delegates to TTDevice::read_from_device/write_to_device.
/// Translates NOC0 coordinates to TRANSLATED coordinates before forwarding to UMD,
/// which is required on Blackhole (and harmless on Wormhole).
class UmdNocAccessProvider : public NocAccessProvider {
public:
    explicit UmdNocAccessProvider(UmdDiscoveryProvider* discovery);

    std::string name() const override { return "umd"; }

    std::vector<uint8_t> read(
        int chip_id, uint32_t noc_x, uint32_t noc_y,
        uint64_t addr, uint32_t size) override;

    void write(
        int chip_id, uint32_t noc_x, uint32_t noc_y,
        uint64_t addr, const std::vector<uint8_t>& data) override;

    uint32_t read32(
        int chip_id, uint32_t noc_x, uint32_t noc_y,
        uint64_t addr) override;

    void write32(
        int chip_id, uint32_t noc_x, uint32_t noc_y,
        uint64_t addr, uint32_t value) override;

    /// Get ETH core NOC0 coordinates for a device. Returns vector of (noc_x, noc_y).
    std::vector<std::pair<uint32_t, uint32_t>> get_eth_cores(int chip_id);

private:
    UmdDiscoveryProvider* discovery_;

    /// Cached SocDescriptors keyed by TTDevice pointer (for coordinate translation).
    std::unordered_map<uintptr_t, tt::umd::SocDescriptor> soc_desc_cache_;

    /// Translate (noc_x, noc_y) from NOC0 to TRANSLATED coordinate system.
    tt_xy_pair translate_noc0_to_translated(
        tt::umd::TTDevice* device, uint32_t noc_x, uint32_t noc_y);
};

}  // namespace tt_device_hal
