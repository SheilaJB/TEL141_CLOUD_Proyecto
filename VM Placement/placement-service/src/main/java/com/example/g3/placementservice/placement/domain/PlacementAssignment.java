package com.example.g3.placementservice.placement.domain;

import java.util.Objects;
import java.util.UUID;

public record PlacementAssignment(UUID nodeId, int serverId) {

    public PlacementAssignment {
        Objects.requireNonNull(nodeId, "nodeId");
        if (serverId <= 0) {
            throw new IllegalArgumentException("serverId must be positive");
        }
    }
}
