package com.example.g3.placementservice.placement.domain;

import java.util.List;
import java.util.Objects;

public record PlacementPlan(Integer sliceVersionId, Integer zonaId, List<Assignment> assignments) {

    public PlacementPlan {
        Objects.requireNonNull(sliceVersionId, "sliceVersionId");
        Objects.requireNonNull(zonaId, "zonaId");
        assignments = List.copyOf(Objects.requireNonNull(assignments, "assignments"));
    }
}