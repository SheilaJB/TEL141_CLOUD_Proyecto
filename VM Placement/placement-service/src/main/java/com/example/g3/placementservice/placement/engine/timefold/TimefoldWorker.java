package com.example.g3.placementservice.placement.engine.timefold;

import com.example.g3.placementservice.placement.domain.ResourceAmount;

public class TimefoldWorker {

    private final Integer serverId;
    private final ResourceAmount capacity;
    private final ResourceAmount allocated;

    public TimefoldWorker(Integer serverId, ResourceAmount capacity, ResourceAmount allocated) {
        this.serverId = serverId;
        this.capacity = capacity;
        this.allocated = allocated;
    }

    public Integer getServerId() {
        return serverId;
    }

    public ResourceAmount getCapacity() {
        return capacity;
    }

    public ResourceAmount getAllocated() {
        return allocated;
    }
}