// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <cstdint>
#include <string>

namespace tt_device_hal {

/// Fan state as reported by the ARC firmware of a single ASIC.
struct FanState {
    /// False when the ASIC has no fan-control path we know how to drive.
    bool supported = false;
    /// True while a host-requested speed overrides the thermal curve.
    bool forced = false;
    /// Commanded PWM duty cycle, 0..255, as the ARC publishes it to the M3.
    uint32_t target_pwm = 0;
    /// target_pwm expressed as a percentage.
    uint32_t target_pct = 0;
    /// Measured RPM per fan; 0 means the fan (or its tach) is absent.
    uint32_t fan1_rpm = 0;
    uint32_t fan2_rpm = 0;
};

/// Interface for driving board fans.
///
/// Fans belong to a *board*, not an ASIC: on an n300 both ASICs publish a target
/// duty cycle to the M3, which runs the fan at max(local, remote). Setting a speed
/// therefore addresses a board_id and touches every ASIC on it — otherwise the
/// untouched ASIC's thermal curve silently holds the fan above the request.
class FanControlProvider {
public:
    virtual ~FanControlProvider() = default;
    virtual std::string name() const = 0;

    /// Force every ASIC on `board_id` to `pct` (0..100), or pass -1 to release
    /// control back to the firmware's thermal curve. Throws on failure.
    virtual void set_board_fan(uint64_t board_id, int pct) = 0;

    /// Read the fan state one ASIC currently sees.
    virtual FanState get_fan_state(uint64_t asic_id) = 0;
};

}  // namespace tt_device_hal
