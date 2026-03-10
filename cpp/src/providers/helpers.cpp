// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#include "helpers.hpp"
#include <iostream>
#include <iomanip>
#include <sstream>
#include <fstream>
#include <vector>
#include <string>
#include <cstring>
#include <unistd.h>
#include <signal.h>

namespace tt_device_hal {

std::string format_bytes(uint64_t bytes) {
    if (bytes == 0) {
        return "0B";
    }
    const char* units[] = {"B", "KiB", "MiB", "GiB", "TiB"};
    int unit_idx = 0;
    double size = static_cast<double>(bytes);

    while (size >= 1024.0 && unit_idx < 4) {
        size /= 1024.0;
        unit_idx++;
    }

    std::ostringstream oss;
    oss << std::fixed << std::setprecision(1) << size << units[unit_idx];
    return oss.str();
}

uint64_t get_system_uptime() {
    std::ifstream uptime_file("/proc/uptime");
    double uptime = 0.0;
    if (uptime_file >> uptime) {
        return static_cast<uint64_t>(uptime);
    }
    return 0;
}

bool is_process_alive(pid_t pid) {
    if (kill(pid, 0) != 0) return false;
    std::string stat_path = "/proc/" + std::to_string(pid) + "/stat";
    std::ifstream f(stat_path);
    std::string line;
    if (!std::getline(f, line) || line.empty()) return false;
    size_t rparen = line.rfind(')');
    if (rparen == std::string::npos || rparen + 2 >= line.size()) return false;
    char state = line[rparen + 2];
    return state != 'Z' && state != 'X';
}

std::string get_process_name(pid_t pid) {
    std::string comm_path = "/proc/" + std::to_string(pid) + "/comm";
    std::ifstream comm_file(comm_path);
    std::string name;
    if (std::getline(comm_file, name)) {
        if (!name.empty() && name.back() == '\n') {
            name.pop_back();
        }
        return name;
    }
    return "unknown";
}

std::string get_process_cmdline(pid_t pid) {
    std::string cmdline_path = "/proc/" + std::to_string(pid) + "/cmdline";
    std::ifstream f(cmdline_path, std::ios::binary);
    if (!f.is_open()) return "";

    std::string raw(256, '\0');
    f.read(raw.data(), 256);
    std::streamsize n = f.gcount();
    raw.resize(static_cast<size_t>(n));

    // NUL bytes separate argv elements; replace with spaces for readability
    for (char& c : raw) {
        if (c == '\0') c = ' ';
    }
    while (!raw.empty() && raw.back() == ' ') raw.pop_back();
    return raw;
}

void get_process_stats(pid_t pid, uint64_t& runtime_seconds, float& cpu_percent) {
    runtime_seconds = 0;
    cpu_percent = 0.0f;

    std::string stat_path = "/proc/" + std::to_string(pid) + "/stat";
    std::ifstream stat_file(stat_path);
    if (!stat_file.is_open()) {
        return;
    }

    std::string line;
    if (!std::getline(stat_file, line)) {
        return;
    }

    try {
        size_t start = line.find('(');
        size_t end = line.rfind(')');
        if (start == std::string::npos || end == std::string::npos) {
            return;
        }

        std::string after_comm = line.substr(end + 2);
        std::istringstream iss(after_comm);
        std::vector<std::string> fields;
        std::string field;
        while (iss >> field) {
            fields.push_back(field);
        }

        if (fields.size() < 20) {
            return;
        }

        uint64_t utime = std::stoull(fields[11]);
        uint64_t stime = std::stoull(fields[12]);
        uint64_t starttime = std::stoull(fields[19]);

        long clock_ticks = sysconf(_SC_CLK_TCK);
        if (clock_ticks <= 0) clock_ticks = 100;

        uint64_t system_uptime = get_system_uptime();
        uint64_t process_start_seconds = starttime / clock_ticks;
        runtime_seconds = system_uptime > process_start_seconds ?
                         system_uptime - process_start_seconds : 0;

        uint64_t total_time_ticks = utime + stime;
        double total_time_seconds = static_cast<double>(total_time_ticks) / clock_ticks;

        if (runtime_seconds > 0) {
            cpu_percent = static_cast<float>((total_time_seconds / runtime_seconds) * 100.0);
            if (cpu_percent > 9999.0f) cpu_percent = 9999.0f;
        }
    } catch (...) {
    }
}

void get_process_host_stats(pid_t pid, uint64_t& vm_rss_kb, uint64_t& vm_virt_kb,
                            uint64_t& vm_swap_kb, uint32_t& num_threads) {
    vm_rss_kb = 0;
    vm_virt_kb = 0;
    vm_swap_kb = 0;
    num_threads = 0;

    std::string status_path = "/proc/" + std::to_string(pid) + "/status";
    std::ifstream f(status_path);
    if (!f.is_open()) return;

    std::string line;
    while (std::getline(f, line)) {
        // Each relevant line has the form "FieldName:\t<value> kB" or "Threads:\t<value>"
        auto colon = line.find(':');
        if (colon == std::string::npos) continue;
        std::string key = line.substr(0, colon);
        std::string val_str = line.substr(colon + 1);
        // Trim leading whitespace
        size_t start = val_str.find_first_not_of(" \t");
        if (start == std::string::npos) continue;
        val_str = val_str.substr(start);

        try {
            if (key == "VmRSS") {
                vm_rss_kb = std::stoull(val_str);
            } else if (key == "VmSize") {
                vm_virt_kb = std::stoull(val_str);
            } else if (key == "VmSwap") {
                vm_swap_kb = std::stoull(val_str);
            } else if (key == "Threads") {
                num_threads = static_cast<uint32_t>(std::stoull(val_str));
            }
        } catch (...) {}
    }
}

}  // namespace tt_device_hal
