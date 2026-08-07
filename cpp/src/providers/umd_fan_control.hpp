// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "tt_device_hal/fan_control_provider.hpp"
#include "umd_discovery.hpp"
#include "umd/device/arc/arc_messenger.hpp"

#include <cstdint>
#include <memory>
#include <unordered_map>
#include <vector>

namespace tt_device_hal {

/// UMD-based fan control. Drives the ARC FORCE_FAN_SPEED message through
/// UMD's ArcMessenger (which serializes on the same ARC_MSG mutex the telemetry
/// readers use) and reads state back out of the ARC CSM.
///
/// Wormhole only: the message code and CSM layout below are Wormhole-specific.
/// Blackhole boards report as unsupported rather than being sent a message that
/// means something else there.
class UmdFanControlProvider : public FanControlProvider {
public:
    explicit UmdFanControlProvider(UmdDiscoveryProvider* discovery);

    std::string name() const override { return "umd"; }

    void set_board_fan(uint64_t board_id, int pct) override;
    FanState get_fan_state(uint64_t asic_id) override;

private:
    UmdDiscoveryProvider* discovery_;

    /// ArcMessenger is stateful (holds named mutexes), so keep one per TTDevice.
    std::unordered_map<uintptr_t, std::unique_ptr<tt::umd::ArcMessenger>> messengers_;

    tt::umd::ArcMessenger* messenger_for(tt::umd::TTDevice* device);
    uint32_t read_csm32(tt::umd::TTDevice* device, uint64_t noc_addr);

    /// ASIC ids of every Wormhole device sharing `board_id`.
    std::vector<uint64_t> wormhole_asics_on_board(uint64_t board_id);
};

}  // namespace tt_device_hal
