package com.example.g3.placementservice.placement.domain;

import java.util.Objects;

public record Assignment(Integer vmId, Integer serverId, ResourceAmount resources) {

    public Assignment {
        Objects.requireNonNull(vmId, "vmId");
        Objects.requireNonNull(serverId, "serverId");
        Objects.requireNonNull(resources, "resources");
    }
}