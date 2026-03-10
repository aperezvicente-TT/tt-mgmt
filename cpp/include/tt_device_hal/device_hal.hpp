// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

/**
 * @file device_hal.hpp
 * @brief Public header -- re-exports the full tt_device_hal API.
 *
 * Consumers should include this single header for the complete API.
 */

#pragma once

#include "types.hpp"
#include "providers.hpp"
#include "fabric_provider.hpp"
#include "device_manager.hpp"
