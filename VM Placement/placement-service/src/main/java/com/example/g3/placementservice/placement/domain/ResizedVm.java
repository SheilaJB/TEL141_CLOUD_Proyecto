package com.example.g3.placementservice.placement.domain;

import java.util.Objects;
import java.util.UUID;

public record ResizedVm(UUID nodeId, long sliceId, int currentServerId, ResourceVector delta) {

    public ResizedVm {
        Objects.requireNonNull(nodeId, "nodeId");
        Objects.requireNonNull(delta, "delta");
        if (sliceId <= 0 || currentServerId <= 0) {
            throw new IllegalArgumentException("sliceId and currentServerId must be positive");
        }
    }
}
