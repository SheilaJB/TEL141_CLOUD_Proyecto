package com.example.g3.placementservice.placement.domain;

import java.util.Objects;

/**
 * Host candidato. capacity es la capacidad fisica (CPU/RAM) usada por el modelo estocastico;
 * ceiling es el techo absoluto de reservas (fisico * allocation_ratio_limit), el mismo que valida
 * el SQL al reservar. Sin techo explicito, ceiling = capacity.
 */
public record HostCapacity(int serverId, ResourceVector capacity, ResourceVector occupied, ResourceVector ceiling) {

    public HostCapacity {
        Objects.requireNonNull(capacity, "capacity");
        Objects.requireNonNull(occupied, "occupied");
        Objects.requireNonNull(ceiling, "ceiling");
        if (serverId <= 0) {
            throw new IllegalArgumentException("serverId must be positive");
        }
        if (capacity.vcpu() <= 0 || capacity.ramMb() <= 0 || capacity.diskMb() <= 0) {
            throw new IllegalArgumentException("Host capacities must be positive");
        }
        if (ceiling.vcpu() <= 0 || ceiling.ramMb() <= 0 || ceiling.diskMb() <= 0) {
            throw new IllegalArgumentException("Host ceilings must be positive");
        }
    }

    public HostCapacity(int serverId, ResourceVector capacity, ResourceVector occupied) {
        this(serverId, capacity, occupied, capacity);
    }

    public boolean isEmpty() {
        return occupied.isZero();
    }
}
