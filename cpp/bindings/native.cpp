// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>
#include <nanobind/stl/unique_ptr.h>
#include "tt_device_hal/device_manager.hpp"
#include "tt_device_hal/types.hpp"

namespace nb = nanobind;
using namespace tt_device_hal;

NB_MODULE(native, m) {
    m.doc() = "tt-mgmt native backend: modular device management API";

    // ---- Types ----

    nb::class_<TelemetryData>(m, "TelemetryData")
        .def(nb::init<>())
        .def_rw("temperature", &TelemetryData::temperature)
        .def_rw("power", &TelemetryData::power)
        .def_rw("voltage_mv", &TelemetryData::voltage_mv)
        .def_rw("current_ma", &TelemetryData::current_ma)
        .def_rw("aiclk_mhz", &TelemetryData::aiclk_mhz)
        .def_rw("vreg_temperature", &TelemetryData::vreg_temperature)
        .def_rw("board_temperature", &TelemetryData::board_temperature)
        .def_rw("axiclk_mhz", &TelemetryData::axiclk_mhz)
        .def_rw("arcclk_mhz", &TelemetryData::arcclk_mhz)
        .def_rw("ddr_speed_mhz", &TelemetryData::ddr_speed_mhz)
        .def_rw("fan_speed_pct", &TelemetryData::fan_speed_pct)
        .def_rw("fan_speed_rpm", &TelemetryData::fan_speed_rpm)
        .def_rw("tdp_limit_w", &TelemetryData::tdp_limit_w)
        .def_rw("tdc_limit_a", &TelemetryData::tdc_limit_a)
        .def_rw("aiclk_limit_mhz", &TelemetryData::aiclk_limit_mhz)
        .def_rw("input_power_w", &TelemetryData::input_power_w)
        .def_rw("max_gddr_temp", &TelemetryData::max_gddr_temp)
        .def_rw("gddr01_temp", &TelemetryData::gddr01_temp)
        .def_rw("gddr23_temp", &TelemetryData::gddr23_temp)
        .def_rw("gddr45_temp", &TelemetryData::gddr45_temp)
        .def_rw("gddr67_temp", &TelemetryData::gddr67_temp)
        .def_rw("status", &TelemetryData::status)
        .def_rw("available", &TelemetryData::available);

    nb::class_<FirmwareInfo>(m, "FirmwareInfo")
        .def(nb::init<>())
        .def_rw("fw_bundle_ver", &FirmwareInfo::fw_bundle_ver)
        .def_rw("arc_fw_ver", &FirmwareInfo::arc_fw_ver)
        .def_rw("eth_fw_ver", &FirmwareInfo::eth_fw_ver)
        .def_rw("m3app_fw_ver", &FirmwareInfo::m3app_fw_ver)
        .def_rw("ttflash_ver", &FirmwareInfo::ttflash_ver);

    nb::class_<ProcessMemory>(m, "ProcessMemory")
        .def(nb::init<>())
        .def_rw("pid", &ProcessMemory::pid)
        .def_rw("name", &ProcessMemory::name)
        .def_rw("cmdline", &ProcessMemory::cmdline)
        .def_rw("registered_to_device", &ProcessMemory::registered_to_device)
        .def_rw("dram_allocated", &ProcessMemory::dram_allocated)
        .def_rw("l1_allocated", &ProcessMemory::l1_allocated)
        .def_rw("l1_small_allocated", &ProcessMemory::l1_small_allocated)
        .def_rw("trace_allocated", &ProcessMemory::trace_allocated)
        .def_rw("cb_allocated", &ProcessMemory::cb_allocated)
        .def_rw("runtime_seconds", &ProcessMemory::runtime_seconds)
        .def_rw("cpu_percent", &ProcessMemory::cpu_percent)
        .def_rw("vm_rss_kb", &ProcessMemory::vm_rss_kb)
        .def_rw("vm_virt_kb", &ProcessMemory::vm_virt_kb)
        .def_rw("vm_swap_kb", &ProcessMemory::vm_swap_kb)
        .def_rw("num_threads", &ProcessMemory::num_threads);

    nb::class_<EthConnection>(m, "EthConnection")
        .def(nb::init<>())
        .def_rw("local_channel", &EthConnection::local_channel)
        .def_rw("remote_chip_id", &EthConnection::remote_chip_id)
        .def_rw("remote_channel", &EthConnection::remote_channel)
        .def_rw("is_exit_link", &EthConnection::is_exit_link);

    nb::class_<EthCoord>(m, "EthCoord")
        .def(nb::init<>())
        .def_rw("cluster_id", &EthCoord::cluster_id)
        .def_rw("x", &EthCoord::x)
        .def_rw("y", &EthCoord::y)
        .def_rw("rack", &EthCoord::rack)
        .def_rw("shelf", &EthCoord::shelf);

    nb::class_<DeviceInfo>(m, "DeviceInfo")
        .def(nb::init<>())
        .def_rw("chip_id", &DeviceInfo::chip_id)
        .def_rw("asic_id", &DeviceInfo::asic_id)
        .def_rw("board_id", &DeviceInfo::board_id)
        .def_rw("pci_ordinal", &DeviceInfo::pci_ordinal)
        .def_rw("logical_id", &DeviceInfo::logical_id)
        .def_rw("pci_bdf", &DeviceInfo::pci_bdf)
        .def_rw("arch_name", &DeviceInfo::arch_name)
        .def_rw("is_remote", &DeviceInfo::is_remote)
        .def_rw("serial", &DeviceInfo::serial)
        .def_rw("card_type", &DeviceInfo::card_type)
        .def_rw("tray_id", &DeviceInfo::tray_id)
        .def_rw("chip_in_tray", &DeviceInfo::chip_in_tray)
        .def_rw("asic_location", &DeviceInfo::asic_location)
        .def_rw("display_id", &DeviceInfo::display_id)
        .def_rw("telemetry", &DeviceInfo::telemetry)
        .def_rw("firmware", &DeviceInfo::firmware)
        .def_rw("total_dram", &DeviceInfo::total_dram)
        .def_rw("used_dram", &DeviceInfo::used_dram)
        .def_rw("total_l1", &DeviceInfo::total_l1)
        .def_rw("used_l1", &DeviceInfo::used_l1)
        .def_rw("used_l1_small", &DeviceInfo::used_l1_small)
        .def_rw("used_trace", &DeviceInfo::used_trace)
        .def_rw("used_cb", &DeviceInfo::used_cb)
        .def_rw("processes", &DeviceInfo::processes)
        .def_rw("has_shm", &DeviceInfo::has_shm)
        .def_rw("eth_connections", &DeviceInfo::eth_connections)
        .def_rw("eth_coord", &DeviceInfo::eth_coord)
        .def_rw("active_eth_channels", &DeviceInfo::active_eth_channels)
        .def_rw("idle_eth_channels", &DeviceInfo::idle_eth_channels)
        .def_rw("is_mmio_capable", &DeviceInfo::is_mmio_capable);

    // ---- Fabric cluster types ----

    nb::class_<FabricHost>(m, "FabricHost")
        .def(nb::init<>())
        .def_rw("host_name", &FabricHost::host_name)
        .def_rw("asic_count", &FabricHost::asic_count)
        .def_rw("arch", &FabricHost::arch)
        .def_rw("connected_hosts", &FabricHost::connected_hosts);

    nb::class_<FabricClusterInfo>(m, "FabricClusterInfo")
        .def(nb::init<>())
        .def_rw("hosts", &FabricClusterInfo::hosts)
        .def_rw("total_cross_host_links", &FabricClusterInfo::total_cross_host_links)
        .def_rw("connected", &FabricClusterInfo::connected)
        .def_rw("error", &FabricClusterInfo::error);

    nb::class_<PlacementAssignment>(m, "PlacementAssignment")
        .def(nb::init<>())
        .def_rw("host_id", &PlacementAssignment::host_id)
        .def_rw("rank", &PlacementAssignment::rank)
        .def_rw("asic_ids", &PlacementAssignment::asic_ids);

    nb::class_<PlacementResult>(m, "PlacementResult")
        .def(nb::init<>())
        .def_rw("success", &PlacementResult::success)
        .def_rw("status", &PlacementResult::status)
        .def_rw("error_message", &PlacementResult::error_message)
        .def_rw("placements", &PlacementResult::placements);

    // ---- DeviceManager ----

    nb::class_<DeviceManager>(m, "DeviceManager")
        .def(nb::init<>())
        .def_static("create_default", &DeviceManager::create_default)
        .def_static("create_sysfs", &DeviceManager::create_sysfs)
        .def_static("create_auto", &DeviceManager::create_auto)
        .def_static("create_with_fabric", &DeviceManager::create_with_fabric)
        .def("discover", &DeviceManager::discover, nb::rv_policy::reference_internal)
        .def("update_telemetry", &DeviceManager::update_telemetry)
        .def("update_memory", &DeviceManager::update_memory)
        .def("cleanup_dead_processes", &DeviceManager::cleanup_dead_processes)
        .def("backend_name", &DeviceManager::backend_name)
        .def("has_fabric", &DeviceManager::has_fabric)
        .def("get_cluster_topology", &DeviceManager::get_cluster_topology)
        .def("get_placements", &DeviceManager::get_placements,
             nb::arg("mgd_textproto"), nb::arg("host_ids") = std::vector<std::string>{});

    // ---- Free functions ----

    m.def("format_bytes", &tt_device_hal::format_bytes);
}
