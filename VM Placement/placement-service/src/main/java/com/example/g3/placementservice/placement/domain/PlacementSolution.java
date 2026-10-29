package com.example.g3.placementservice.placement.domain;

import java.util.List;
import java.util.Objects;

public record PlacementSolution(long deploymentId, List<PlacementAssignment> assignments) {

    public PlacementSolution {
        if (deploymentId <= 0) {
            throw new IllegalArgumentException("deploymentId must be positive");
        }
        assignments = List.copyOf(Objects.requireNonNull(assignments, "assignments"));
    }
}
