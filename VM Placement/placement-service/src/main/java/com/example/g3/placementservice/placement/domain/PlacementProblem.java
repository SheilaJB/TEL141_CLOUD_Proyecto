package com.example.g3.placementservice.placement.domain;

import java.util.List;
import java.util.Objects;

public record PlacementProblem(
        Integer sliceVersionId,
        Integer zonaId,
        ClusterSnapshot cluster,
        List<VmRequest> newVms,
        PlacementConstraints constraints) {

    public PlacementProblem {
        Objects.requireNonNull(sliceVersionId, "sliceVersionId");
        Objects.requireNonNull(zonaId, "zonaId");
        Objects.requireNonNull(cluster, "cluster");
        newVms = List.copyOf(Objects.requireNonNull(newVms, "newVms"));
        Objects.requireNonNull(constraints, "constraints");
    }
}