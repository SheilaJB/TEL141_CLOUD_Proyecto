package com.example.g3.placementservice.placement.domain;

import java.util.Objects;
import java.util.UUID;


/**VM nueva: La demanda es una variable aleatoria:
  media = demand (flavor) y varianza por recurso (cpuVariance, ramVariance) y (El disco se mantiene deterministico)*/


public record NewVm(UUID nodeId, long sliceId, ResourceVector demand, double cpuVariance, double ramVariance) {

    public NewVm {
        Objects.requireNonNull(nodeId, "nodeId");
        Objects.requireNonNull(demand, "demand");
        if (sliceId <= 0) {
            throw new IllegalArgumentException("sliceId must be positive");
        }
        if (cpuVariance < 0 || ramVariance < 0 || Double.isNaN(cpuVariance) || Double.isNaN(ramVariance)) {
            throw new IllegalArgumentException("Demand variance cannot be negative");
        }
    }

    /**Demanda deterministica */
    public NewVm(UUID nodeId, long sliceId, ResourceVector demand) {
        this(nodeId, sliceId, demand, 0.0, 0.0);
    }
}
