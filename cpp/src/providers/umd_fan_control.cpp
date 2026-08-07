// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#include "umd_fan_control.hpp"

#include "umd/device/arch/wormhole_implementation.hpp"
#include "umd/device/tt_device/tt_device.hpp"
#include "umd/device/types/arch.hpp"
#include "umd/device/types/xy_pair.hpp"

#include <sstream>
#include <stdexcept>

namespace tt_device_hal {

namespace {

// ARC message: force the fan controller to a fixed PWM duty cycle.
// arg 0..100 sets the duty cycle, 0xFF releases back to the thermal curve.
constexpr uint32_t ARC_MSG_FORCE_FAN_SPEED = 0xAA94;
constexpr uint32_t FAN_RELEASE_ARG = 0xFF;

// The firmware reads the whole 32-bit scratch word as its argument, and UMD packs
// args as (arg0 | arg1 << 16) defaulting a missing arg1 to 0xFFFF. Passing arg1
// explicitly as 0 is required or the firmware sees e.g. 0xFFFF0050 and rejects it.
constexpr uint32_t ARC_ARG1_MUST_BE_ZERO = 0;

// ARC CSM, addressed through the ARC core's NOC window.
constexpr uint64_t CSM = tt::umd::wormhole::ARC_CSM_OFFSET_NOC;
constexpr uint64_t FAN_CTRL_PPM_TARG_FAN = CSM + 0x78364;  // commanded PWM, 0..0xFF
constexpr uint64_t FAN_CTRL_PPM_FORCE_EN = CSM + 0x78368;  // bits 7:0
constexpr uint64_t M3_CTRL_FAN_TACH = CSM + 0x78E74;       // (31:16) fan 1, (15:0) fan 2

// Matches fan_count_to_rpm() in the ARC firmware (arc_fw/lib/fan_ctrl.c).
constexpr uint32_t TACH_RPM_NUMERATOR = 5400000;
constexpr uint32_t FAN_TACH_ABSENT = 0xFFFF;

uint32_t tach_to_rpm(uint32_t count) {
    if (count == 0 || count == FAN_TACH_ABSENT) {
        return 0;
    }
    return TACH_RPM_NUMERATOR / count;
}

}  // namespace

UmdFanControlProvider::UmdFanControlProvider(UmdDiscoveryProvider* discovery)
    : discovery_(discovery) {}

tt::umd::ArcMessenger* UmdFanControlProvider::messenger_for(tt::umd::TTDevice* device) {
    auto key = reinterpret_cast<uintptr_t>(device);
    auto it = messengers_.find(key);
    if (it == messengers_.end()) {
        it = messengers_.emplace(key, tt::umd::ArcMessenger::create_arc_messenger(device)).first;
    }
    return it->second.get();
}

uint32_t UmdFanControlProvider::read_csm32(tt::umd::TTDevice* device, uint64_t noc_addr) {
    uint32_t value = 0;
    device->read_from_device(
        &value, tt::umd::wormhole::ARC_CORES_NOC0[0], noc_addr, sizeof(uint32_t));
    return value;
}

std::vector<uint64_t> UmdFanControlProvider::wormhole_asics_on_board(uint64_t board_id) {
    std::vector<uint64_t> asics;
    for (uint64_t asic_id : discovery_->get_asic_ids_on_board(board_id)) {
        auto* cache = discovery_->get_cache(asic_id);
        if (cache && cache->tt_device && cache->arch == tt::ARCH::WORMHOLE_B0) {
            asics.push_back(asic_id);
        }
    }
    return asics;
}

void UmdFanControlProvider::set_board_fan(uint64_t board_id, int pct) {
    if (pct > 100 || pct < -1) {
        throw std::runtime_error(
            "Fan speed must be 0..100 percent, or -1 to release control");
    }

    auto asics = wormhole_asics_on_board(board_id);
    if (asics.empty()) {
        std::ostringstream oss;
        oss << "No Wormhole ASIC found on board 0x" << std::hex << board_id
            << " — fan control is only implemented for Wormhole";
        throw std::runtime_error(oss.str());
    }

    const uint32_t arg = (pct < 0) ? FAN_RELEASE_ARG : static_cast<uint32_t>(pct);

    // Every ASIC on the board must be told: the M3 runs the fan at max() of the
    // per-ASIC targets, so leaving one on its thermal curve makes a request to
    // slow down a no-op.
    for (uint64_t asic_id : asics) {
        auto* device = discovery_->get_tt_device(asic_id);
        auto* messenger = messenger_for(device);
        uint32_t exit_code = messenger->send_message(
            ARC_MSG_FORCE_FAN_SPEED, {arg, ARC_ARG1_MUST_BE_ZERO});
        if (exit_code != 0) {
            std::ostringstream oss;
            oss << "FORCE_FAN_SPEED(" << arg << ") rejected by ARC on ASIC 0x"
                << std::hex << asic_id << " with exit code 0x" << exit_code;
            throw std::runtime_error(oss.str());
        }
    }
}

FanState UmdFanControlProvider::get_fan_state(uint64_t asic_id) {
    FanState state;

    auto* cache = discovery_->get_cache(asic_id);
    if (!cache || !cache->tt_device || cache->arch != tt::ARCH::WORMHOLE_B0) {
        return state;  // supported = false
    }

    auto* device = cache->tt_device.get();
    state.supported = true;
    state.forced = (read_csm32(device, FAN_CTRL_PPM_FORCE_EN) & 0xFF) != 0;
    state.target_pwm = read_csm32(device, FAN_CTRL_PPM_TARG_FAN) & 0xFF;
    state.target_pct = state.target_pwm * 100 / 0xFF;

    // Read the raw M3 tach rather than fan_ctrl_ppm.curr_fan: only newer ARC
    // firmware decodes curr_fan into RPM, older builds leave the packed tach
    // word there and never populate curr_fan2.
    uint32_t tach = read_csm32(device, M3_CTRL_FAN_TACH);
    state.fan1_rpm = tach_to_rpm(tach >> 16);
    state.fan2_rpm = tach_to_rpm(tach & 0xFFFF);

    return state;
}

}  // namespace tt_device_hal
