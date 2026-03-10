// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#include "sysfs_discovery.hpp"

#include <iostream>
#include <fstream>
#include <filesystem>
#include <algorithm>
#include <cstring>

namespace fs = std::filesystem;

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

static std::string find_hwmon_dir(const fs::path& pci_device_dir) {
    auto hwmon_base = pci_device_dir / "hwmon";
    if (!fs::is_directory(hwmon_base)) {
        return {};
    }
    for (auto& entry : fs::directory_iterator(hwmon_base)) {
        if (entry.is_directory() && entry.path().filename().string().rfind("hwmon", 0) == 0) {
            return entry.path().string();
        }
    }
    return {};
}

const SysfsDevicePaths* SysfsDiscoveryProvider::get_paths(const std::string& pci_bdf) const {
    auto it = paths_.find(pci_bdf);
    if (it != paths_.end()) {
        return &it->second;
    }
    return nullptr;
}

std::vector<DeviceInfo> SysfsDiscoveryProvider::discover() {
    paths_.clear();
    std::vector<DeviceInfo> devices;

    const fs::path tt_class("/sys/class/tenstorrent");
    if (!fs::exists(tt_class)) {
        std::cerr << "[tt_mgmt] sysfs: /sys/class/tenstorrent not found (tt-kmd not loaded?)" << std::endl;
        return devices;
    }

    std::vector<fs::directory_entry> entries;
    for (auto& e : fs::directory_iterator(tt_class)) {
        if (e.is_directory() || e.is_symlink()) {
            auto fname = e.path().filename().string();
            if (fname.rfind("tenstorrent!", 0) == 0) {
                entries.push_back(e);
            }
        }
    }
    std::sort(entries.begin(), entries.end(), [](const auto& a, const auto& b) {
        return a.path().filename() < b.path().filename();
    });

    for (auto& entry : entries) {
        try {
            // Resolve the symlink to get the real sysfs path
            fs::path real = fs::canonical(entry.path());
            // real is: /sys/devices/.../DDDD:BB:DD.F/tenstorrent/tenstorrent!N
            fs::path pci_device_dir = real.parent_path().parent_path();
            std::string pci_bdf = pci_device_dir.filename().string();
            std::string hwmon_dir = find_hwmon_dir(pci_device_dir);

            // Parse ordinal from tenstorrent!N
            std::string fname = entry.path().filename().string();
            int ordinal = -1;
            auto bang = fname.find('!');
            if (bang != std::string::npos) {
                try { ordinal = std::stoi(fname.substr(bang + 1)); } catch (...) {}
            }

            SysfsDevicePaths sp;
            sp.tt_dir = real.string();
            sp.hwmon_dir = hwmon_dir;
            sp.pci_bdf = pci_bdf;
            sp.ordinal = ordinal;

            DeviceInfo dev;
            dev.pci_bdf = pci_bdf;
            dev.pci_ordinal = ordinal;
            dev.is_remote = false;

            // Read identity attributes
            dev.serial = read_sysfs_attr(sp.tt_dir + "/tt_serial");
            dev.card_type = read_sysfs_attr(sp.tt_dir + "/tt_card_type");

            std::string asic_id_str = read_sysfs_attr(sp.tt_dir + "/tt_asic_id");
            if (!asic_id_str.empty()) {
                try { dev.asic_id = std::stoull(asic_id_str, nullptr, 16); } catch (...) {}
            }

            // Chip name from hwmon (wormhole / blackhole)
            if (!hwmon_dir.empty()) {
                std::string chip_name = read_sysfs_attr(hwmon_dir + "/name");
                if (chip_name == "wormhole") {
                    dev.arch_name = "Wormhole_B0";
                } else if (chip_name == "blackhole") {
                    dev.arch_name = "Blackhole";
                } else if (!chip_name.empty()) {
                    dev.arch_name = chip_name;
                }
            }

            // Firmware versions
            dev.firmware.fw_bundle_ver = read_sysfs_attr(sp.tt_dir + "/tt_fw_bundle_ver");
            dev.firmware.arc_fw_ver = read_sysfs_attr(sp.tt_dir + "/tt_arc_fw_ver");
            dev.firmware.eth_fw_ver = read_sysfs_attr(sp.tt_dir + "/tt_eth_fw_ver");
            dev.firmware.m3app_fw_ver = read_sysfs_attr(sp.tt_dir + "/tt_m3app_fw_ver");
            dev.firmware.ttflash_ver = read_sysfs_attr(sp.tt_dir + "/tt_ttflash_ver");

            // Use the serial as board_id if available
            if (!dev.serial.empty()) {
                try { dev.board_id = std::stoull(dev.serial, nullptr, 16); } catch (...) {}
            }

            // Display ID
            dev.chip_id = (dev.asic_id != 0) ? dev.asic_id : static_cast<uint64_t>(ordinal);
            char display_buf[24];
            snprintf(display_buf, sizeof(display_buf), "%llx", (unsigned long long)dev.chip_id);
            dev.display_id = display_buf;

            paths_[pci_bdf] = std::move(sp);
            devices.push_back(std::move(dev));

            std::cerr << "[tt_mgmt] sysfs: found " << fname << " at " << pci_bdf
                      << " (" << devices.back().arch_name << ")" << std::endl;

        } catch (const std::exception& e) {
            std::cerr << "[tt_mgmt] sysfs: error processing " << entry.path() << ": " << e.what() << std::endl;
        }
    }

    std::cerr << "[tt_mgmt] sysfs discovery complete: " << devices.size() << " device(s)" << std::endl;
    return devices;
}

}  // namespace tt_device_hal
