# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
# SPDX-License-Identifier: Apache-2.0

"""Low-level NOC memory access API.

Provides a typed Python interface over the native DeviceManager NOC methods.
Can be used by any Python consumer -- CLI commands, test frameworks, scripts.

Usage:
    from tt_mgmt.api.noc import NocAccess
    from tt_mgmt.backend.smi.core import _get_manager

    dm = _get_manager()
    noc = NocAccess(dm)
    data = noc.read(chip_id=0, noc_x=4, noc_y=1, addr=0x7CC00, size=1024)
"""

import struct


class NocAccess:
    """Low-level NOC read/write interface wrapping DeviceManager native methods."""

    def __init__(self, device_manager):
        """Initialize with a DeviceManager instance (from native backend)."""
        self._dm = device_manager

    def read(self, chip_id: int, noc_x: int, noc_y: int, addr: int, size: int) -> bytes:
        """Read `size` bytes from device memory at (noc_x, noc_y, addr)."""
        return bytes(self._dm.noc_read(chip_id, noc_x, noc_y, addr, size))

    def read32(self, chip_id: int, noc_x: int, noc_y: int, addr: int) -> int:
        """Read a single 32-bit word from device memory."""
        return self._dm.noc_read32(chip_id, noc_x, noc_y, addr)

    def write(self, chip_id: int, noc_x: int, noc_y: int, addr: int, data: bytes) -> None:
        """Write bytes to device memory at (noc_x, noc_y, addr)."""
        self._dm.noc_write(chip_id, noc_x, noc_y, addr, data)

    def write32(self, chip_id: int, noc_x: int, noc_y: int, addr: int, value: int) -> None:
        """Write a single 32-bit word to device memory."""
        self._dm.noc_write32(chip_id, noc_x, noc_y, addr, value)

    def read_words(self, chip_id: int, noc_x: int, noc_y: int, addr: int, num_words: int) -> list:
        """Read `num_words` 32-bit words, returned as a list of ints."""
        data = self.read(chip_id, noc_x, noc_y, addr, num_words * 4)
        return list(struct.unpack(f"<{num_words}I", data))
