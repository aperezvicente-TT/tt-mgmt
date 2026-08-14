// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#include "shm_memory.hpp"
#include "helpers.hpp"

#include <iostream>
#include <fstream>
#include <cstdlib>
#include <cstring>
#include <atomic>
#include <set>
#include <map>
#include <algorithm>
#include <unistd.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <dirent.h>

namespace tt_device_hal {

// Must match memory_stats_shm.hpp in tt-metal.
//
// v3 and v4 are both readable here. v4 appended init_state in what was trailing padding and
// changed how tt-metal maintains the region -- per-process slots are claimed at attach
// rather than on first allocation, aggregates and reference_count are derived from the live
// slots, and a process that dies without unwinding is reclaimed by the next attacher -- but
// every field read below keeps its offset and meaning, so both versions are handled by the
// same code. Anything outside that range is a layout we have not seen and is ignored rather
// than printed as garbage.
constexpr uint32_t SHM_VERSION_MIN = 3;
constexpr uint32_t SHM_VERSION_MAX = 4;
// v4+: written last by whichever process initializes the region. A freshly created region is
// zero-filled, and a zero chip_id is a real chip, so reading before READY can attribute
// memory to chip 0 that belongs to nobody.
constexpr uint32_t SHM_INIT_READY = 0x52454459u;  // 'REDY'

struct SHMDeviceMemoryRegion {
    uint32_t version;
    uint32_t num_active_processes;
    uint64_t last_update_timestamp;
    std::atomic<uint32_t> reference_count;

    uint64_t board_serial;
    uint64_t asic_id;
    int32_t device_id;

    std::atomic<uint64_t> total_dram_allocated;
    std::atomic<uint64_t> total_l1_allocated;
    std::atomic<uint64_t> total_l1_small_allocated;
    std::atomic<uint64_t> total_trace_allocated;
    std::atomic<uint64_t> total_cb_allocated;

    static constexpr size_t MAX_CHIPS_PER_DEVICE = 16;
    struct ChipStats {
        uint32_t chip_id;
        uint32_t is_remote;
        std::atomic<uint64_t> dram_allocated;
        std::atomic<uint64_t> l1_allocated;
        std::atomic<uint64_t> l1_small_allocated;
        std::atomic<uint64_t> trace_allocated;
        std::atomic<uint64_t> cb_allocated;
    } chip_stats[MAX_CHIPS_PER_DEVICE];

    static constexpr size_t MAX_PROCESSES = 64;
    struct ProcessStats {
        std::atomic<pid_t> pid;
        std::atomic<uint64_t> dram_allocated;
        std::atomic<uint64_t> l1_allocated;
        std::atomic<uint64_t> l1_small_allocated;
        std::atomic<uint64_t> trace_allocated;
        std::atomic<uint64_t> cb_allocated;
        std::atomic<uint64_t> last_update_timestamp;
        char process_name[64];
    } processes[MAX_PROCESSES];

    // v4+, in what v3 left as padding. Reading it on a v3 region yields whatever the padding
    // holds, so it is only consulted when the version says it is there.
    std::atomic<uint32_t> init_state;
    uint8_t padding[60];
};

static_assert(offsetof(SHMDeviceMemoryRegion, total_dram_allocated) == 48, "tt-metal SHM layout drifted");
static_assert(offsetof(SHMDeviceMemoryRegion, chip_stats) == 88, "tt-metal SHM layout drifted");
static_assert(offsetof(SHMDeviceMemoryRegion, processes) == 856, "tt-metal SHM layout drifted");
static_assert(offsetof(SHMDeviceMemoryRegion, init_state) == 8536, "tt-metal SHM layout drifted");

// True when the region is safe to read: a known layout, and published if it says so.
inline bool shm_region_readable(const SHMDeviceMemoryRegion* region) {
    const uint32_t version = region->version;
    if (version < SHM_VERSION_MIN || version > SHM_VERSION_MAX) {
        return false;
    }
    return version < 4 || region->init_state.load(std::memory_order_acquire) == SHM_INIT_READY;
}

static std::vector<ProcessMemory> query_registered_processes(int pci_ordinal) {
    std::vector<ProcessMemory> processes;

    if (pci_ordinal < 0) {
        return processes;
    }

    std::string pids_file = "/proc/driver/tenstorrent/" + std::to_string(pci_ordinal) + "/pids";
    std::ifstream pids_stream(pids_file);
    if (!pids_stream.is_open()) {
        return processes;
    }

    std::set<pid_t> unique_pids;
    std::string line;
    while (std::getline(pids_stream, line)) {
        try {
            pid_t pid = std::stoi(line);
            if (pid > 0 && is_process_alive(pid)) {
                unique_pids.insert(pid);
            }
        } catch (...) {
            continue;
        }
    }

    for (pid_t pid : unique_pids) {
        ProcessMemory proc;
        proc.pid = pid;
        proc.name = get_process_name(pid);
        proc.cmdline = get_process_cmdline(pid);
        proc.registered_to_device = true;
        get_process_stats(pid, proc.runtime_seconds, proc.cpu_percent);
        get_process_host_stats(pid, proc.vm_rss_kb, proc.vm_virt_kb, proc.vm_swap_kb, proc.num_threads);
        processes.push_back(proc);
    }

    return processes;
}

bool ShmMemoryProvider::update(DeviceInfo& dev) {
    // Determine pci_ordinal for registered process query
    int ordinal = dev.pci_ordinal;
    if (discovery_) {
        auto* cache = discovery_->get_cache(dev.asic_id);
        if (cache) {
            ordinal = cache->pci_ordinal;
        }
    }

    dev.processes = query_registered_processes(ordinal);

    dev.processes.reserve(dev.processes.size() + SHMDeviceMemoryRegion::MAX_PROCESSES);

    std::map<pid_t, ProcessMemory*> process_map;
    for (auto& proc : dev.processes) {
        process_map[proc.pid] = &proc;
    }

    // Determine legacy shm_asic_id for backward-compatible SHM lookup
    // tt-metal v2 uses chip_unique_id (= dev.asic_id) directly as the SHM filename key.
    // tt-metal v1 used composite (board_id << 8 | asic_location) stored in cache->shm_asic_id.
    uint64_t shm_asic_id_legacy = 0;
    if (discovery_) {
        auto* cache = discovery_->get_cache(dev.asic_id);
        if (cache) {
            shm_asic_id_legacy = cache->shm_asic_id;
        }
    }

    bool shm_writable = false;
    auto try_shm_open = [&shm_writable](uint64_t id) {
        std::string name = "/tt_device_" + std::to_string(id) + "_memory";
        int fd = shm_open(name.c_str(), O_RDWR, 0666);
        if (fd >= 0) { shm_writable = true; return fd; }
        shm_writable = false;
        return shm_open(name.c_str(), O_RDONLY, 0666);
    };

    // v2: try chip_unique_id first (new tt-metal uses this directly)
    int fd = try_shm_open(dev.asic_id);
    // Fallback: try legacy composite ID (old tt-metal used board_id << 8 | asic_location)
    if (fd < 0 && shm_asic_id_legacy != 0 && shm_asic_id_legacy != dev.asic_id) {
        fd = try_shm_open(shm_asic_id_legacy);
    }
    if (fd < 0) {
        if (std::getenv("TT_SMI_DEBUG")) {
            std::cerr << "[tt_mgmt] SHM not found for device " << dev.display_id
                      << " (tried asic_id=" << dev.asic_id
                      << ", legacy_shm_id=" << shm_asic_id_legacy << ")" << std::endl;
        }
        return !dev.processes.empty();
    }

    int prot = shm_writable ? (PROT_READ | PROT_WRITE) : PROT_READ;
    auto* region =
        static_cast<SHMDeviceMemoryRegion*>(mmap(nullptr, sizeof(SHMDeviceMemoryRegion), prot, MAP_SHARED, fd, 0));

    if (region == MAP_FAILED) {
        close(fd);
        return !dev.processes.empty();
    }

    if (!shm_region_readable(region)) {
        munmap(region, sizeof(SHMDeviceMemoryRegion));
        close(fd);
        return !dev.processes.empty();
    }

    dev.used_dram = region->total_dram_allocated.load(std::memory_order_relaxed);
    dev.used_l1 = region->total_l1_allocated.load(std::memory_order_relaxed);
    dev.used_l1_small = region->total_l1_small_allocated.load(std::memory_order_relaxed);
    dev.used_trace = region->total_trace_allocated.load(std::memory_order_relaxed);
    dev.used_cb = region->total_cb_allocated.load(std::memory_order_relaxed);

    auto saturating_sub = [](std::atomic<uint64_t>& counter, uint64_t val) {
        uint64_t cur = counter.load(std::memory_order_relaxed);
        while (cur > 0 && val > 0) {
            uint64_t next = (val > cur) ? 0 : cur - val;
            if (counter.compare_exchange_weak(cur, next, std::memory_order_relaxed)) break;
        }
    };

    for (uint32_t i = 0; i < SHMDeviceMemoryRegion::MAX_PROCESSES; i++) {
        auto& shm_entry = region->processes[i];
        pid_t pid = shm_entry.pid.load(std::memory_order_relaxed);
        if (pid <= 0) continue;

        if (!is_process_alive(pid)) {
            // Dead PID — zero out its SHM entry immediately if we have write access
            if (shm_writable) {
                uint64_t dram     = shm_entry.dram_allocated.exchange(0, std::memory_order_relaxed);
                uint64_t l1       = shm_entry.l1_allocated.exchange(0, std::memory_order_relaxed);
                uint64_t l1_small = shm_entry.l1_small_allocated.exchange(0, std::memory_order_relaxed);
                uint64_t trace    = shm_entry.trace_allocated.exchange(0, std::memory_order_relaxed);
                uint64_t cb       = shm_entry.cb_allocated.exchange(0, std::memory_order_relaxed);
                saturating_sub(region->total_dram_allocated,      dram);
                saturating_sub(region->total_l1_allocated,        l1);
                saturating_sub(region->total_l1_small_allocated,  l1_small);
                saturating_sub(region->total_trace_allocated,     trace);
                saturating_sub(region->total_cb_allocated,        cb);
                shm_entry.pid.store(0, std::memory_order_release);
            }
            continue;
        }

        auto it = process_map.find(pid);
        if (it != process_map.end()) {
            auto& proc = *it->second;
            proc.dram_allocated = shm_entry.dram_allocated.load(std::memory_order_relaxed);
            proc.l1_allocated = shm_entry.l1_allocated.load(std::memory_order_relaxed);
            proc.l1_small_allocated = shm_entry.l1_small_allocated.load(std::memory_order_relaxed);
            proc.trace_allocated = shm_entry.trace_allocated.load(std::memory_order_relaxed);
            proc.cb_allocated = shm_entry.cb_allocated.load(std::memory_order_relaxed);
            get_process_stats(proc.pid, proc.runtime_seconds, proc.cpu_percent);
            get_process_host_stats(proc.pid, proc.vm_rss_kb, proc.vm_virt_kb,
                                   proc.vm_swap_kb, proc.num_threads);
        } else {
            ProcessMemory proc;
            proc.pid = pid;
            proc.name = get_process_name(pid);
            proc.cmdline = get_process_cmdline(pid);
            proc.registered_to_device = false;
            proc.dram_allocated = shm_entry.dram_allocated.load(std::memory_order_relaxed);
            proc.l1_allocated = shm_entry.l1_allocated.load(std::memory_order_relaxed);
            proc.l1_small_allocated = shm_entry.l1_small_allocated.load(std::memory_order_relaxed);
            proc.trace_allocated = shm_entry.trace_allocated.load(std::memory_order_relaxed);
            proc.cb_allocated = shm_entry.cb_allocated.load(std::memory_order_relaxed);
            get_process_stats(pid, proc.runtime_seconds, proc.cpu_percent);
            get_process_host_stats(pid, proc.vm_rss_kb, proc.vm_virt_kb,
                                   proc.vm_swap_kb, proc.num_threads);
            dev.processes.push_back(proc);
            process_map[pid] = &dev.processes.back();
        }
    }

    // Re-read totals after inline cleanup may have decremented them
    if (shm_writable) {
        dev.used_dram = region->total_dram_allocated.load(std::memory_order_relaxed);
        dev.used_l1 = region->total_l1_allocated.load(std::memory_order_relaxed);
        dev.used_l1_small = region->total_l1_small_allocated.load(std::memory_order_relaxed);
        dev.used_trace = region->total_trace_allocated.load(std::memory_order_relaxed);
        dev.used_cb = region->total_cb_allocated.load(std::memory_order_relaxed);
    }

    munmap(region, sizeof(SHMDeviceMemoryRegion));
    close(fd);
    dev.has_shm = true;
    return true;
}

int ShmMemoryProvider::cleanup_dead_processes() {
    int cleaned = 0;

    DIR* shm_dir = opendir("/dev/shm");
    if (!shm_dir) {
        return 0;
    }

    struct dirent* entry;
    while ((entry = readdir(shm_dir)) != nullptr) {
        std::string name(entry->d_name);
        if (name.rfind("tt_device_", 0) != 0) continue;
        if (name.find("_memory") == std::string::npos) continue;

        std::string shm_path = "/" + name;
        int fd = shm_open(shm_path.c_str(), O_RDWR, 0666);
        if (fd < 0) continue;

        auto* region = static_cast<SHMDeviceMemoryRegion*>(
            mmap(nullptr, sizeof(SHMDeviceMemoryRegion), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0));
        close(fd);

        if (region == MAP_FAILED) continue;

        // Never write into a region whose layout we do not know, and never into one that is
        // still being initialized.
        if (!shm_region_readable(region)) {
            munmap(region, sizeof(SHMDeviceMemoryRegion));
            continue;
        }

        const bool derives_aggregates = region->version >= 4;

        for (uint32_t i = 0; i < SHMDeviceMemoryRegion::MAX_PROCESSES; i++) {
            pid_t pid = region->processes[i].pid.load(std::memory_order_acquire);
            if (pid <= 0) continue;
            if (is_process_alive(pid)) continue;

            uint64_t dram     = region->processes[i].dram_allocated.exchange(0, std::memory_order_relaxed);
            uint64_t l1       = region->processes[i].l1_allocated.exchange(0, std::memory_order_relaxed);
            uint64_t l1_small = region->processes[i].l1_small_allocated.exchange(0, std::memory_order_relaxed);
            uint64_t trace    = region->processes[i].trace_allocated.exchange(0, std::memory_order_relaxed);
            uint64_t cb       = region->processes[i].cb_allocated.exchange(0, std::memory_order_relaxed);

            auto saturating_sub = [](std::atomic<uint64_t>& counter, uint64_t val) {
                uint64_t cur = counter.load(std::memory_order_relaxed);
                while (cur > 0 && val > 0) {
                    uint64_t next = (val > cur) ? 0 : cur - val;
                    if (counter.compare_exchange_weak(cur, next, std::memory_order_relaxed)) break;
                }
            };
            saturating_sub(region->total_dram_allocated,      dram);
            saturating_sub(region->total_l1_allocated,        l1);
            saturating_sub(region->total_l1_small_allocated,  l1_small);
            saturating_sub(region->total_trace_allocated,     trace);
            saturating_sub(region->total_cb_allocated,        cb);

            region->processes[i].pid.store(0, std::memory_order_release);

            cleaned++;
        }

        // From v4 the counts of attached/active processes are derived from the live slots,
        // so having dropped some, re-derive them here. Otherwise they stay stale until the
        // next tt-metal process attaches -- which is exactly the situation this cleanup runs
        // in, i.e. none is running. (tt-metal now reaps dead slots on attach itself, so this
        // whole pass is a backstop for v4 rather than the only way out of it, as it was for
        // v3.)
        if (derives_aggregates) {
            uint32_t live = 0;
            for (uint32_t i = 0; i < SHMDeviceMemoryRegion::MAX_PROCESSES; i++) {
                if (region->processes[i].pid.load(std::memory_order_relaxed) != 0) {
                    live++;
                }
            }
            region->num_active_processes = live;
            region->reference_count.store(live, std::memory_order_release);
        }

        munmap(region, sizeof(SHMDeviceMemoryRegion));
    }

    closedir(shm_dir);
    return cleaned;
}

}  // namespace tt_device_hal
