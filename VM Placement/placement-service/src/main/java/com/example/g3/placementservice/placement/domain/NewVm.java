package com.example.g3.placementservice.placement.domain;

import java.util.Objects;
import java.util.UUID;

public record NewVm(UUID nodeId, long sliceId, ResourceVector demand) {

    public NewVm {
        Objects.requireNonNull(nodeId, "nodeId");
        Objects.requireNonNull(demand, "demand");
        if (sliceId <= 0) {
            throw new IllegalArgumentException("sliceId must be positive");
        }
    }
}
