// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "tt_device_hal/fabric_provider.hpp"

#ifdef TT_MGMT_HAS_FABRIC_MANAGER
#include <fabric_manager/client.hpp>
#endif

#include <memory>
#include <string>

namespace tt_device_hal {

#ifdef TT_MGMT_HAS_FABRIC_MANAGER

/// FabricProvider backed by the tt-fabric-manager gRPC SDK.
class FabricManagerProvider : public FabricProvider {
public:
    explicit FabricManagerProvider(const std::string& endpoint);
    ~FabricManagerProvider() override = default;

    std::string name() const override { return "fabric-manager"; }
    bool connect() override;
    bool is_connected() const override;
    FabricClusterInfo get_cluster_topology() override;
    PlacementResult get_placements(
        const std::string& mgd_textproto,
        const std::vector<std::string>& host_ids = {}) override;

private:
    std::string endpoint_;
    std::unique_ptr<tt::fabricmanager::sdk::FabricManagerClient> client_;
    bool connected_ = false;
};

#else

/// Stub when fabric manager SDK is not available at build time.
class FabricManagerProvider : public FabricProvider {
public:
    explicit FabricManagerProvider(const std::string&) {}

    std::string name() const override { return "fabric-manager (unavailable)"; }
    bool connect() override { return false; }
    bool is_connected() const override { return false; }

    FabricClusterInfo get_cluster_topology() override {
        FabricClusterInfo info;
        info.error = "fabric-manager SDK not available at build time";
        return info;
    }

    PlacementResult get_placements(
        const std::string&, const std::vector<std::string>&) override {
        PlacementResult r;
        r.status = "ERROR";
        r.error_message = "fabric-manager SDK not available at build time";
        return r;
    }
};

#endif

}  // namespace tt_device_hal
