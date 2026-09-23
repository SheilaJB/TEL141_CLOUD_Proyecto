package com.example.g3.placementservice.placement.domain;

import java.util.Objects;

public record VmRequest(Integer vmId, ResourceAmount resources) {

    public VmRequest {
        Objects.requireNonNull(vmId, "vmId");
        Objects.requireNonNull(resources, "resources");
    }
}