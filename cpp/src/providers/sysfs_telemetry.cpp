// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#include "sysfs_telemetry.hpp"

#include <fstream>
#include <iostream>
#include <string>

namespace tt_device_hal {

static std::string read_sysfs_attr(const std::string& path) {
    std::ifstream f(path);
    std::string val;
    if (std::getline(f, val)) {
        while (!val.empty() && (val.back() == '\n' || val.back() == '\r' || val.back() == ' '))
            val.pop_back();
    }
    return val;
}

static bool read_sysfs_long(const std::string& path, long& out) {
    std::string s = read_sysfs_attr(path);
    if (s.empty()) return false;
    try {
        out = std::stol(s);
        return true;
    } catch (...) {
        return false;
    }
}

bool SysfsTelemetryProvider::update(DeviceInfo& dev) {
    if (!discovery_) return false;

    const auto* paths = discovery_->get_paths(dev.pci_bdf);
    if (!paths) return false;

    bool any_read = false;

    // hwmon telemetry (unit conversion: millideg -> C, uW -> W, mV -> V, mA -> A)
    if (!paths->hwmon_dir.empty()) {
        long raw = 0;

        if (read_sysfs_long(paths->hwmon_dir + "/temp1_input", raw)) {
            dev.telemetry.temperature = static_cast<float>(raw) / 1000.0f;
            any_read = true;
        }

        if (read_sysfs_long(paths->hwmon_dir + "/power1_input", raw)) {
            dev.telemetry.power = static_cast<float>(raw) / 1000000.0f;
            any_read = true;
        }

        if (read_sysfs_long(paths->hwmon_dir + "/in0_input", raw)) {
            dev.telemetry.voltage_mv = static_cast<uint32_t>(raw);
            any_read = true;
        }

        if (read_sysfs_long(paths->hwmon_dir + "/curr1_input", raw)) {
            dev.telemetry.current_ma = static_cast<uint32_t>(raw);
            any_read = true;
        }
    }

    // Clock frequencies from tenstorrent driver attributes (already in MHz)
    {
        long clk = 0;
        if (read_sysfs_long(paths->tt_dir + "/tt_aiclk", clk)) {
            dev.telemetry.aiclk_mhz = static_cast<uint32_t>(clk);
            any_read = true;
        }
        if (read_sysfs_long(paths->tt_dir + "/tt_arcclk", clk)) {
            dev.telemetry.arcclk_mhz = static_cast<uint32_t>(clk);
            any_read = true;
        }
        if (read_sysfs_long(paths->tt_dir + "/tt_axiclk", clk)) {
            dev.telemetry.axiclk_mhz = static_cast<uint32_t>(clk);
            any_read = true;
        }
    }

    if (any_read) {
        dev.telemetry.available = true;
        dev.telemetry.status = "OK";
    } else {
        dev.telemetry.available = false;
        dev.telemetry.status = "Error";
    }

    return any_read;
}

}  // namespace tt_device_hal
