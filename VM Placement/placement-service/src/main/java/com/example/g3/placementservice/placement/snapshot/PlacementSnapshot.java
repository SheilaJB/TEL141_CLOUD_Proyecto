package com.example.g3.placementservice.placement.snapshot;

import com.example.g3.placementservice.placement.domain.PlacementProblem;
import com.example.g3.placementservice.placement.domain.ResourceVector;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public record PlacementSnapshot(
        int sliceId,
        int targetVersionId,
        String deploymentType,
        PlacementProblem problem,
        List<Allocation> allocations,
        List<Resize> resizes,
        List<UUID> releases,
        List<ReservationRow> activeReservations) {

    public PlacementSnapshot {
        allocations = List.copyOf(allocations);
        resizes = List.copyOf(resizes);
        releases = List.copyOf(releases);
        activeReservations = List.copyOf(activeReservations);
    }

    public record Allocation(UUID nodeId, ResourceVector resources) {
    }

    public record Resize(UUID nodeId, ResourceVector before, ResourceVector after) {
    }

    public record ReservationRow(
            UUID nodeId,
            int serverId,
            String resource,
            long amount,
            int createdByDeploymentId,
            String nodeStatus,
            String serverStatus,
            int zonaId) {
    }

    public record NodeReservation(
            int serverId,
            String serverStatus,
            String nodeStatus,
            int createdByDeploymentId,
            ResourceVector resources) {
        public long value(String resource) {
            return resources.get(resource);
        }
    }

    public static Map<UUID, NodeReservation> byNode(List<ReservationRow> rows) {
        Map<UUID, NodeReservation> result = new HashMap<>();
        for (ReservationRow row : rows) {
            ResourceVector amount = ResourceVector.of(row.resource(), row.amount());
            NodeReservation previous = result.get(row.nodeId());
            if (previous == null) {
                result.put(row.nodeId(), new NodeReservation(
                        row.serverId(), row.serverStatus(), row.nodeStatus(),
                        row.createdByDeploymentId(), amount));
                continue;
            }
            if (previous.serverId() != row.serverId()) {
                throw new IllegalStateException("A node's reservation resources span multiple hosts: " + row.nodeId());
            }
            if (!previous.serverStatus().equals(row.serverStatus())
                    || !previous.nodeStatus().equals(row.nodeStatus())
                    || previous.createdByDeploymentId() != row.createdByDeploymentId()) {
                throw new IllegalStateException("Inconsistent reservation state for node " + row.nodeId());
            }
            result.put(row.nodeId(), new NodeReservation(
                    previous.serverId(), previous.serverStatus(), previous.nodeStatus(),
                    previous.createdByDeploymentId(), previous.resources().add(amount)));
        }
        return result;
    }
}
