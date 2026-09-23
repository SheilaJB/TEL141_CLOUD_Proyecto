package com.example.g3.placementservice.placement.domain;

import java.util.List;
import java.util.Objects;

public record PlacementResult(
        Integer sliceVersionId,
        boolean successful,
        List<Assignment> assignments) {

    public PlacementResult {
        Objects.requireNonNull(sliceVersionId, "sliceVersionId");
        assignments = List.copyOf(Objects.requireNonNull(assignments, "assignments"));
    }

    public static PlacementResult confirmed(Integer sliceVersionId, List<Assignment> assignments) {
        return new PlacementResult(sliceVersionId, true, assignments);
    }
}