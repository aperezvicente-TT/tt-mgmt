// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <string>
#include <cstdint>
#include <sys/types.h>

namespace tt_device_hal {

std::string format_bytes(uint64_t bytes);
uint64_t get_system_uptime();
bool is_process_alive(pid_t pid);
std::string get_process_name(pid_t pid);
std::string get_process_cmdline(pid_t pid);
void get_process_stats(pid_t pid, uint64_t& runtime_seconds, float& cpu_percent);
void get_process_host_stats(pid_t pid, uint64_t& vm_rss_kb, uint64_t& vm_virt_kb,
                            uint64_t& vm_swap_kb, uint32_t& num_threads);

}  // namespace tt_device_hal
