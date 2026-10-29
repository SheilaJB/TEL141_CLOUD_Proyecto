package com.example.g3.placementservice.placement.application;

import com.example.g3.placementservice.placement.domain.PlacementAssignment;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;

public record PlacementReserveResponse(
        @JsonProperty("deployment_id") long deploymentId,
        String status,
        List<Allocation> allocations) {

    public PlacementReserveResponse {
        allocations = List.copyOf(allocations);
    }

    public static PlacementReserveResponse from(
            long deploymentId, List<PlacementAssignment> assignments) {
        return new PlacementReserveResponse(
                deploymentId,
                "RESERVED",
                assignments.stream()
                        .map(assignment -> new Allocation(assignment.nodeId(), assignment.serverId()))
                        .toList());
    }

    public record Allocation(
            @JsonProperty("node_id") java.util.UUID nodeId,
            @JsonProperty("server_id") int serverId) {
    }
}
