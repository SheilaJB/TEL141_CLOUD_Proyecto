package com.example.g3.placementservice.placement.domain;

import java.util.Objects;

public record HostCapacity(int serverId, ResourceVector capacity, ResourceVector occupied) {

    public HostCapacity {
        Objects.requireNonNull(capacity, "capacity");
        Objects.requireNonNull(occupied, "occupied");
        if (serverId <= 0) {
            throw new IllegalArgumentException("serverId must be positive");
        }
        if (capacity.vcpu() <= 0 || capacity.ramMb() <= 0 || capacity.diskMb() <= 0) {
            throw new IllegalArgumentException("Host capacities must be positive");
        }
    }

    public boolean isEmpty() {
        return occupied.isZero();
    }
}
