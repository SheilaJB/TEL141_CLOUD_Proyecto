package com.example.g3.placementservice.placement.domain;

import java.util.Objects;

public record WorkerSnapshot(
        Integer serverId,
        Integer zonaId,
        Integer clusterId,
        ResourceAmount capacity,
        ResourceAmount allocated,
        String status) {

    public WorkerSnapshot {
        Objects.requireNonNull(serverId, "serverId");
        Objects.requireNonNull(zonaId, "zonaId");
        Objects.requireNonNull(clusterId, "clusterId");
        Objects.requireNonNull(capacity, "capacity");
        Objects.requireNonNull(allocated, "allocated");
        Objects.requireNonNull(status, "status");
    }

    public ResourceAmount available() {
        return new ResourceAmount(
                capacity.cpu() - allocated.cpu(),
                capacity.ram() - allocated.ram(),
                capacity.disk() - allocated.disk());
    }
}