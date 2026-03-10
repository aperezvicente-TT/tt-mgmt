// SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC.
// SPDX-License-Identifier: Apache-2.0

#include "fabric_manager_provider.hpp"
#include <iostream>

namespace tt_device_hal {

#ifdef TT_MGMT_HAS_FABRIC_MANAGER

namespace sdk = tt::fabricmanager::sdk;

FabricManagerProvider::FabricManagerProvider(const std::string& endpoint)
    : endpoint_(endpoint) {}

bool FabricManagerProvider::connect() {
    try {
        client_ = std::make_unique<sdk::FabricManagerClient>(endpoint_);
        connected_ = client_->IsConnected();
        if (connected_) {
            std::cerr << "[tt_mgmt] Connected to fabric-manager at " << endpoint_ << std::endl;
        }
        return connected_;
    } catch (const std::exception& e) {
        std::cerr << "[tt_mgmt] Failed to connect to fabric-manager: " << e.what() << std::endl;
        connected_ = false;
        return false;
    }
}

bool FabricManagerProvider::is_connected() const {
    if (!client_) return false;
    return client_->IsConnected();
}

FabricClusterInfo FabricManagerProvider::get_cluster_topology() {
    FabricClusterInfo info;
    if (!client_) {
        info.error = "Not connected to fabric-manager";
        return info;
    }
    try {
        auto response = client_->QueryTopologySummary();
        info.connected = true;
        info.total_cross_host_links = response.total_cross_host_links;
        for (const auto& h : response.hosts) {
            FabricHost host;
            host.host_name = h.host_name;
            host.asic_count = h.asic_count;
            host.arch = h.arch;
            host.connected_hosts = h.connected_hosts;
            info.hosts.push_back(std::move(host));
        }
    } catch (const std::exception& e) {
        info.error = e.what();
    }
    return info;
}

PlacementResult FabricManagerProvider::get_placements(
    const std::string& mgd_textproto,
    const std::vector<std::string>& host_ids) {
    PlacementResult result;
    if (!client_) {
        result.status = "ERROR";
        result.error_message = "Not connected to fabric-manager";
        return result;
    }
    try {
        auto response = client_->GetValidPlacementsMGD(mgd_textproto, host_ids);
        switch (response.status) {
            case sdk::PlacementStatusMGD::Success:
                result.success = true;
                result.status = "SUCCESS";
                break;
            case sdk::PlacementStatusMGD::ErrorImpossible:
                result.status = "ERROR_IMPOSSIBLE";
                break;
            case sdk::PlacementStatusMGD::ErrorInsufficient:
                result.status = "ERROR_INSUFFICIENT";
                break;
            case sdk::PlacementStatusMGD::ErrorInvalidMGD:
                result.status = "ERROR_INVALID_MGD";
                break;
            default:
                result.status = "ERROR_UNKNOWN";
                break;
        }
        result.error_message = response.error_message;
        for (const auto& p : response.placements) {
            std::vector<PlacementAssignment> assignments;
            for (const auto& ha : p.host_assignments) {
                PlacementAssignment a;
                a.host_id = ha.host_id;
                a.rank = ha.rank;
                a.asic_ids = ha.asic_ids;
                assignments.push_back(std::move(a));
            }
            result.placements.push_back(std::move(assignments));
        }
    } catch (const std::exception& e) {
        result.status = "ERROR";
        result.error_message = e.what();
    }
    return result;
}

#endif  // TT_MGMT_HAS_FABRIC_MANAGER

}  // namespace tt_device_hal
