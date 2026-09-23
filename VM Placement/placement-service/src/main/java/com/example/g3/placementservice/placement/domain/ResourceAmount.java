package com.example.g3.placementservice.placement.domain;

import java.util.Objects;

public record ResourceAmount(long cpu, long ram, long disk) {

    public ResourceAmount {
        if (cpu < 0 || ram < 0 || disk < 0) {
            throw new IllegalArgumentException("Resource amounts cannot be negative");
        }
    }

    public static ResourceAmount zero() {
        return new ResourceAmount(0, 0, 0);
    }

    public ResourceAmount add(ResourceAmount other) {
        Objects.requireNonNull(other, "other");
        return new ResourceAmount(cpu + other.cpu, ram + other.ram, disk + other.disk);
    }
}