// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "types.hpp"
#include <memory>
#include <string>
#include <vector>

namespace tt_device_hal {

/// Abstract interface for cluster-level fabric data sources.
class FabricProvider {
public:
    virtual ~FabricProvider() = default;
    virtual std::string name() const = 0;
    virtual bool connect() = 0;
    virtual bool is_connected() const = 0;
    virtual FabricClusterInfo get_cluster_topology() = 0;
    virtual PlacementResult get_placements(
        const std::string& mgd_textproto,
        const std::vector<std::string>& host_ids = {}) = 0;
};

}  // namespace tt_device_hal
