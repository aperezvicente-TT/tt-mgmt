# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
# SPDX-License-Identifier: Apache-2.0

"""ERISC-aware API for reading boot_results and monitoring ETH core status.

Provides parsed, structured access to ERISC firmware status on Tenstorrent devices.
Supports both Blackhole and Wormhole architectures with their different memory maps.

Usage:
    from tt_mgmt.api.noc import NocAccess
    from tt_mgmt.api.erisc import EriscAccess
    from tt_mgmt.backend.smi.core import _get_manager

    dm = _get_manager()
    erisc = EriscAccess(NocAccess(dm), arch="blackhole")
    status = erisc.get_status(chip_id=0, noc_x=4, noc_y=1)
    print(status.postcode, status.is_alive, status.fw_version)
"""

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Blackhole memory layout (from bh-erisc eth_init.h)
# ---------------------------------------------------------------------------
BH_BOOT_RESULTS_ADDR = 0x7CC00
BH_BOOT_RESULTS_SIZE = 1024  # 256 DWORDs

BH_OFF_POSTCODE = 0
BH_OFF_PORT_STATUS = 1
BH_OFF_TRAIN_STATUS = 2
BH_OFF_TRAIN_SPEED = 3
BH_OFF_HEARTBEAT_0 = 28
BH_OFF_HEARTBEAT_1 = 29
BH_OFF_HEARTBEAT_2 = 30
BH_OFF_HEARTBEAT_3 = 31
BH_OFF_SERDES_FW_VER = 238
BH_OFF_ETH_FW_VER = 239

# ---------------------------------------------------------------------------
# Wormhole memory layout (from tt-umd wormhole_eth.hpp + t6py wormhole bringup)
# ---------------------------------------------------------------------------
WH_FW_VERSION_ADDR = 0x210             # ETH FW semantic version
WH_HEARTBEAT_ADDR = 0x1C              # fw >= 6.0.0 (new location)
WH_HEARTBEAT_ADDR_LEGACY = 0x1F80     # fw < 6.0.0 (test_results[48])
WH_TRAIN_STATUS_ADDR = 0x1104         # link training status (in node_info)
WH_LINK_ERR_STATUS_ADDR = 0x1440      # link error/disconnect status
WH_POSTCODE_ADDR = 0xFFB3010C         # ERISC firmware postcode (NOC register)
WH_BOOT_PARAMS_ADDR = 0x1000          # boot_params_t start
WH_RESULTS_BUF_ADDR = 0x1EC0          # test_results_t (fw >= 5.0.0)
WH_DEBUG_BUF_ADDR = 0x12C0            # debug_buffer_t (link_state at [0])

# Backwards-compat aliases (used by commands/erisc.py fallback path)
BOOT_RESULTS_ADDR = BH_BOOT_RESULTS_ADDR
BOOT_RESULTS_SIZE = BH_BOOT_RESULTS_SIZE
_OFF_POSTCODE = BH_OFF_POSTCODE
_OFF_PORT_STATUS = BH_OFF_PORT_STATUS
_OFF_TRAIN_STATUS = BH_OFF_TRAIN_STATUS
_OFF_TRAIN_SPEED = BH_OFF_TRAIN_SPEED
_OFF_HEARTBEAT_0 = BH_OFF_HEARTBEAT_0
_OFF_HEARTBEAT_1 = BH_OFF_HEARTBEAT_1
_OFF_HEARTBEAT_2 = BH_OFF_HEARTBEAT_2
_OFF_HEARTBEAT_3 = BH_OFF_HEARTBEAT_3
_OFF_SERDES_FW_VER = BH_OFF_SERDES_FW_VER
_OFF_ETH_FW_VER = BH_OFF_ETH_FW_VER

# Enum mappings from eth_defs.h (Blackhole)
PORT_STATUS = {0: "UNKNOWN", 1: "UP", 2: "DOWN", 3: "UNUSED"}

# Wormhole link states (eth_link_state_t from eth_init.h)
WH_LINK_STATE = {
    0: "POWERUP", 1: "AN_RESTART", 2: "AN_CFG", 3: "AN_PG_RCV",
    4: "TRAINING", 5: "AN_COMPLETE", 6: "PCS_ON_WAIT", 7: "SYMERR_CHK",
    8: "RESTART_CHK", 9: "NO_AN_START", 10: "ACTIVE", 11: "PCS_RESET",
    12: "CRC_CHECK", 13: "TRAINING_FW", 14: "NOT_ACTIVE", 15: "TEST_MODE",
    16: "PKT_TEST", 17: "RESERVED",
}

# Wormhole link_train_status_e (from eth_init.h, read at ETH_TRAIN_STATUS_ADDR=0x1104)
WH_TRAIN_STATUS = {
    0: "TRAINING",    # LINK_TRAIN_TRAINING — still in progress
    1: "TRAINED",     # LINK_TRAIN_SUCCESS  — link up, connected
    2: "TIMEOUT",     # LINK_TRAIN_TIMEOUT  — no link partner
    3: "TEST_MODE",   # LINK_TRAIN_TEST_MODE
}

TRAIN_STATUS = {
    0: "TRAINING", 1: "SKIP", 2: "PASS",
    3: "INT_LB", 4: "EXT_LB",
    5: "TIMEOUT_MANUAL_EQ", 6: "TIMEOUT_ANLT",
    7: "TIMEOUT_CDR_LOCK", 8: "TIMEOUT_BIST_LOCK",
    9: "TIMEOUT_LINK_UP", 10: "TIMEOUT_CHIP_INFO", 11: "PRBS",
}


@dataclass
class FwVersion:
    major: int = 0
    minor: int = 0
    patch: int = 0

    def __str__(self):
        return f"{self.major}.{self.minor}.{self.patch}"

    @classmethod
    def from_raw(cls, raw: int) -> "FwVersion":
        return cls(
            patch=raw & 0xFF,
            minor=(raw >> 8) & 0xFF,
            major=(raw >> 16) & 0xFF,
        )


@dataclass
class EriscStatus:
    """Parsed boot_results_t from an ERISC core."""
    noc_x: int = 0
    noc_y: int = 0
    postcode: int = 0
    port_status: int = 0
    port_status_str: str = ""
    train_status: int = 0
    train_status_str: str = ""
    train_speed: int = 0
    heartbeat: list = field(default_factory=lambda: [0, 0, 0, 0])
    fw_version: FwVersion = field(default_factory=FwVersion)
    serdes_fw_version: FwVersion = field(default_factory=FwVersion)
    is_alive: bool = False
    heartbeat_counter: int = 0


class EriscAccess:
    """High-level ERISC API. Parses boot_results_t into structured data."""

    def __init__(self, noc, arch="blackhole"):
        """Initialize with a NocAccess instance and architecture name."""
        self._noc = noc
        self._arch = arch.lower() if arch else "blackhole"

    def _is_wormhole(self):
        return "wormhole" in self._arch

    def get_status(self, chip_id: int, noc_x: int, noc_y: int) -> EriscStatus:
        """Read and parse full boot_results from an ERISC core."""
        if self._is_wormhole():
            return self._get_status_wormhole(chip_id, noc_x, noc_y)
        return self._get_status_blackhole(chip_id, noc_x, noc_y)

    def _get_status_blackhole(self, chip_id: int, noc_x: int, noc_y: int) -> EriscStatus:
        """Read Blackhole boot_results_t at 0x7CC00.

        Reads eth_status_t (DWORDs 0-31) and FW versions (DWORDs 238-239).
        Skips eth_live_status_t (DWORDs 128-191) which contains MAC/PCS
        snapshot registers that should not be polled from host.
        """
        # Read eth_status_t (first 32 DWORDs)
        words = self._noc.read_words(chip_id, noc_x, noc_y,
                                      BH_BOOT_RESULTS_ADDR, 32)

        # Read FW versions separately (DWORDs 238-239)
        fw_words = self._noc.read_words(chip_id, noc_x, noc_y,
                                         BH_BOOT_RESULTS_ADDR + BH_OFF_SERDES_FW_VER * 4, 2)

        hb0 = words[BH_OFF_HEARTBEAT_0]
        is_alive = (hb0 & 0xFFFF0000) == 0xABCD0000

        return EriscStatus(
            noc_x=noc_x,
            noc_y=noc_y,
            postcode=words[BH_OFF_POSTCODE],
            port_status=words[BH_OFF_PORT_STATUS],
            port_status_str=PORT_STATUS.get(words[BH_OFF_PORT_STATUS], "?"),
            train_status=words[BH_OFF_TRAIN_STATUS],
            train_status_str=TRAIN_STATUS.get(words[BH_OFF_TRAIN_STATUS], "?"),
            train_speed=words[BH_OFF_TRAIN_SPEED],
            heartbeat=[words[BH_OFF_HEARTBEAT_0], words[BH_OFF_HEARTBEAT_1],
                       words[BH_OFF_HEARTBEAT_2], words[BH_OFF_HEARTBEAT_3]],
            fw_version=FwVersion.from_raw(fw_words[1]),        # ETH FW at DWORD 239
            serdes_fw_version=FwVersion.from_raw(fw_words[0]), # SerDes FW at DWORD 238
            is_alive=is_alive,
            heartbeat_counter=hb0 & 0xFFFF if is_alive else 0,
        )

    def _get_status_wormhole(self, chip_id: int, noc_x: int, noc_y: int) -> EriscStatus:
        """Read Wormhole ERISC status from arch-specific addresses.

        Wormhole layout (fw >= 6.0.0):
          - Heartbeat at 0x1C
          - FW version at 0x210
          - Debug buffer at 0x12C0 (link_state at offset 0)
          - Boot params at 0x1000 (local_chip_coord at offset 0)
        """
        # Read heartbeat (try new address first, fall back to legacy)
        hb0 = self._noc.read32(chip_id, noc_x, noc_y, WH_HEARTBEAT_ADDR)
        is_alive = (hb0 & 0xFFFF0000) == 0xABCD0000

        # If heartbeat at new addr is zero, try legacy address
        if hb0 == 0:
            hb0_legacy = self._noc.read32(chip_id, noc_x, noc_y, WH_HEARTBEAT_ADDR_LEGACY)
            if hb0_legacy != 0:
                hb0 = hb0_legacy
                is_alive = (hb0 & 0xFFFF0000) == 0xABCD0000

        # Read FW version at 0x210
        fw_raw = self._noc.read32(chip_id, noc_x, noc_y, WH_FW_VERSION_ADDR)

        # Read training status at 0x1104 (from UMD wormhole_eth.hpp)
        train_status = self._noc.read32(chip_id, noc_x, noc_y, WH_TRAIN_STATUS_ADDR)

        # Read ERISC postcode from NOC register 0xFFB3010C
        postcode = self._noc.read32(chip_id, noc_x, noc_y, WH_POSTCODE_ADDR)

        return EriscStatus(
            noc_x=noc_x,
            noc_y=noc_y,
            postcode=postcode,
            train_status=train_status,
            train_status_str=WH_TRAIN_STATUS.get(train_status, f"0x{train_status:X}"),
            heartbeat=[hb0, 0, 0, 0],
            fw_version=FwVersion.from_raw(fw_raw),
            is_alive=is_alive,
            heartbeat_counter=hb0 & 0xFFFF if is_alive else 0,
        )

    def get_heartbeat(self, chip_id: int, noc_x: int, noc_y: int) -> int:
        """Read just heartbeat[0] (fast single-word read)."""
        if self._is_wormhole():
            return self._noc.read32(chip_id, noc_x, noc_y, WH_HEARTBEAT_ADDR)
        return self._noc.read32(chip_id, noc_x, noc_y,
                                 BH_BOOT_RESULTS_ADDR + BH_OFF_HEARTBEAT_0 * 4)

    def is_alive(self, chip_id: int, noc_x: int, noc_y: int) -> bool:
        """Check if ERISC core has an active runtime heartbeat."""
        hb = self.get_heartbeat(chip_id, noc_x, noc_y)
        return (hb & 0xFFFF0000) == 0xABCD0000
