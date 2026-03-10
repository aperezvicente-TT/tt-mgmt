// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#include "umd_telemetry.hpp"

#include <iostream>
#include <iomanip>
#include <string>
#include <cstring>
#include <pthread.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <fstream>

#include "umd/device/arc/arc_telemetry_reader.hpp"
#include "umd/device/types/telemetry.hpp"
#include "umd/device/firmware/firmware_info_provider.hpp"
#include "umd/device/types/arch.hpp"

using namespace tt::umd;

namespace tt_device_hal {

namespace {

// Mirrors tt::umd::RobustMutex::pthread_mutex_wrapper layout for read-only inspection.
struct UmdMutexWrapper {
    pthread_mutex_t mutex;
    uint64_t initialized;
    pid_t owner_tid;
    pid_t owner_pid;
};

static bool process_alive(pid_t pid) {
    return std::ifstream("/proc/" + std::to_string(pid) + "/stat").good();
}

// Check if the CHIP_IN_USE lock for a given KMD device ID is currently held
// by a live process. Returns true if the device is actively in use.
static bool check_chip_in_use(int kmd_id) {
    std::string shm_name = "TT_UMD_LOCK.CHIP_IN_USE_" + std::to_string(kmd_id) + "_PCIe";

    int fd = shm_open(shm_name.c_str(), O_RDONLY, 0);
    if (fd < 0) {
        return false;
    }

    void* ptr = mmap(nullptr, sizeof(UmdMutexWrapper), PROT_READ, MAP_SHARED, fd, 0);
    close(fd);
    if (ptr == MAP_FAILED) {
        return false;
    }

    auto* wrapper = static_cast<const UmdMutexWrapper*>(ptr);
    pid_t owner = wrapper->owner_pid;
    munmap(ptr, sizeof(UmdMutexWrapper));

    return owner != 0 && process_alive(owner);
}

}  // anonymous namespace

bool UmdTelemetryProvider::update(DeviceInfo& dev) {
    if (!discovery_) {
        return false;
    }

    auto* cache = discovery_->get_cache(dev.asic_id);
    if (!cache || !cache->tt_device) {
        return false;
    }

    try {
        auto* fw_info = cache->tt_device->get_firmware_info_provider();
        auto* telem_reader = cache->tt_device->get_arc_telemetry_reader();
        if (!telem_reader) {
            return false;
        }

        if (fw_info) {
            double temp = fw_info->get_asic_temperature();
            if (temp >= -50.0 && temp <= 150.0) {
                dev.telemetry.temperature = static_cast<float>(temp);
            }
        }

        if (telem_reader->is_entry_available(TelemetryTag::TDP)) {
            dev.telemetry.power = static_cast<float>(telem_reader->read_entry(TelemetryTag::TDP));
        }

        if (telem_reader->is_entry_available(TelemetryTag::AICLK)) {
            dev.telemetry.aiclk_mhz = telem_reader->read_entry(TelemetryTag::AICLK);
        }

        if (telem_reader->is_entry_available(TelemetryTag::VCORE)) {
            dev.telemetry.voltage_mv = telem_reader->read_entry(TelemetryTag::VCORE);
        }

        if (telem_reader->is_entry_available(TelemetryTag::TDC)) {
            dev.telemetry.current_ma = telem_reader->read_entry(TelemetryTag::TDC);
        }

        if (telem_reader->is_entry_available(TelemetryTag::VREG_TEMPERATURE)) {
            uint32_t raw = telem_reader->read_entry(TelemetryTag::VREG_TEMPERATURE);
            if (raw > 0) {
                dev.telemetry.vreg_temperature = static_cast<float>(raw);
            }
        }

        if (fw_info) {
            auto brd_temp = fw_info->get_board_temperature();
            if (brd_temp.has_value() && *brd_temp > 0.0) {
                dev.telemetry.board_temperature = static_cast<float>(*brd_temp);
            }
        }

        if (telem_reader->is_entry_available(TelemetryTag::AXICLK)) {
            dev.telemetry.axiclk_mhz = telem_reader->read_entry(TelemetryTag::AXICLK);
        }

        if (telem_reader->is_entry_available(TelemetryTag::ARCCLK)) {
            dev.telemetry.arcclk_mhz = telem_reader->read_entry(TelemetryTag::ARCCLK);
        }

        if (telem_reader->is_entry_available(TelemetryTag::DDR_SPEED)) {
            dev.telemetry.ddr_speed_mhz = telem_reader->read_entry(TelemetryTag::DDR_SPEED);
        }

        // FAN_RPM (tag 41): BH firmware. WH uses FAN_SPEED (tag 31) with tach period.
        if (telem_reader->is_entry_available(TelemetryTag::FAN_RPM)) {
            uint32_t raw = telem_reader->read_entry(TelemetryTag::FAN_RPM);
            if (raw > 0xFFFF) {
                dev.telemetry.fan_speed_rpm = cache->is_remote
                    ? (raw & 0xFFFF) : ((raw >> 16) & 0xFFFF);
            } else {
                dev.telemetry.fan_speed_rpm = raw;
            }
        } else if (cache->arch == tt::ARCH::WORMHOLE_B0 &&
                   telem_reader->is_entry_available(TelemetryTag::FAN_SPEED)) {
            uint32_t raw = telem_reader->read_entry(TelemetryTag::FAN_SPEED);
            uint32_t tach = (raw > 0xFFFF)
                ? (cache->is_remote ? (raw & 0xFFFF) : ((raw >> 16) & 0xFFFF))
                : raw;
            dev.telemetry.fan_speed_rpm = (tach > 0) ? (3000000u / tach) : 0;
        }

        if (telem_reader->is_entry_available(TelemetryTag::TDP_LIMIT_MAX)) {
            dev.telemetry.tdp_limit_w = telem_reader->read_entry(TelemetryTag::TDP_LIMIT_MAX);
        }

        if (telem_reader->is_entry_available(TelemetryTag::TDC_LIMIT_MAX)) {
            dev.telemetry.tdc_limit_a = telem_reader->read_entry(TelemetryTag::TDC_LIMIT_MAX);
        }

        if (telem_reader->is_entry_available(TelemetryTag::AICLK_LIMIT_MAX)) {
            dev.telemetry.aiclk_limit_mhz = telem_reader->read_entry(TelemetryTag::AICLK_LIMIT_MAX);
        }

        if (cache->arch == tt::ARCH::BLACKHOLE) {
            if (telem_reader->is_entry_available(TelemetryTag::INPUT_POWER)) {
                dev.telemetry.input_power_w = telem_reader->read_entry(TelemetryTag::INPUT_POWER);
            }
            if (telem_reader->is_entry_available(TelemetryTag::MAX_GDDR_TEMP)) {
                dev.telemetry.max_gddr_temp = telem_reader->read_entry(TelemetryTag::MAX_GDDR_TEMP);
            }
            if (telem_reader->is_entry_available(TelemetryTag::GDDR01_TEMP)) {
                dev.telemetry.gddr01_temp = telem_reader->read_entry(TelemetryTag::GDDR01_TEMP);
            }
            if (telem_reader->is_entry_available(TelemetryTag::GDDR23_TEMP)) {
                dev.telemetry.gddr23_temp = telem_reader->read_entry(TelemetryTag::GDDR23_TEMP);
            }
            if (telem_reader->is_entry_available(TelemetryTag::GDDR45_TEMP)) {
                dev.telemetry.gddr45_temp = telem_reader->read_entry(TelemetryTag::GDDR45_TEMP);
            }
            if (telem_reader->is_entry_available(TelemetryTag::GDDR67_TEMP)) {
                dev.telemetry.gddr67_temp = telem_reader->read_entry(TelemetryTag::GDDR67_TEMP);
            }
        }

        dev.telemetry.available = true;

        if (dev.pci_ordinal >= 0 && check_chip_in_use(dev.pci_ordinal)) {
            dev.telemetry.status = "Active";
        } else {
            dev.telemetry.status = "Idle";
        }

        return true;
    } catch (const std::exception& e) {
        dev.telemetry.available = false;
        dev.telemetry.status = "Error";
        std::string msg(e.what());
        size_t first_nl = msg.find('\n');
        std::string short_msg = (first_nl != std::string::npos) ? msg.substr(0, first_nl) : msg;
        std::cerr << "[tt_mgmt] Telemetry read failed for device asic_id=0x" << std::hex
                  << dev.asic_id << std::dec << ": " << short_msg;
        bool is_tlb_enodev = (msg.find("tt_tlb_map failed") != std::string::npos && msg.find("-19") != std::string::npos);
        if (is_tlb_enodev) {
            if (discovery_->invalidate_cache()) {
                std::cerr << " (ENODEV; cache invalidated, will re-discover)";
            } else {
                std::cerr << " (ENODEV; cooldown active, retaining cached handles)";
            }
        }
        std::cerr << std::endl;
        return false;
    }
}

}  // namespace tt_device_hal
