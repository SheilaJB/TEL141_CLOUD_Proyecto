package com.example.g3.placementservice.placement.domain;

public record ExistingSliceVm(long sliceId, int serverId) {

    public ExistingSliceVm {
        if (sliceId <= 0 || serverId <= 0) {
            throw new IllegalArgumentException("sliceId and serverId must be positive");
        }
    }
}
