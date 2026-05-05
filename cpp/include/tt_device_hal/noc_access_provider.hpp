// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace tt_device_hal {

/// Interface for low-level NOC memory access on device cores.
class NocAccessProvider {
public:
    virtual ~NocAccessProvider() = default;
    virtual std::string name() const = 0;

    /// Read `size` bytes from device memory at (noc_x, noc_y, addr).
    virtual std::vector<uint8_t> read(
        int chip_id, uint32_t noc_x, uint32_t noc_y,
        uint64_t addr, uint32_t size) = 0;

    /// Write data to device memory at (noc_x, noc_y, addr).
    virtual void write(
        int chip_id, uint32_t noc_x, uint32_t noc_y,
        uint64_t addr, const std::vector<uint8_t>& data) = 0;

    /// Read a single 32-bit word from device memory.
    virtual uint32_t read32(
        int chip_id, uint32_t noc_x, uint32_t noc_y,
        uint64_t addr) = 0;

    /// Write a single 32-bit word to device memory.
    virtual void write32(
        int chip_id, uint32_t noc_x, uint32_t noc_y,
        uint64_t addr, uint32_t value) = 0;
};

}  // namespace tt_device_hal
