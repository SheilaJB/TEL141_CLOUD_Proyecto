package com.example.g3.placementservice.placement.engine.timefold;

import com.example.g3.placementservice.placement.domain.HostCapacity;
import com.example.g3.placementservice.placement.domain.PlacementWeights;
import com.example.g3.placementservice.placement.domain.ResourceVector;
import java.math.BigDecimal;
import java.math.RoundingMode;

public class TimefoldWorker {

    private final int serverId;
    private final ResourceVector capacity;
    private final ResourceVector occupied;
    private final PlacementWeights weights;

    public TimefoldWorker(HostCapacity host, PlacementWeights weights) {
        this.serverId = host.serverId();
        this.capacity = host.capacity();
        this.occupied = host.occupied();
        this.weights = weights;
    }

    public int getServerId() {
        return serverId;
    }

    public ResourceVector getCapacity() {
        return capacity;
    }

    public ResourceVector getOccupied() {
        return occupied;
    }

    public PlacementWeights getWeights() {
        return weights;
    }

    public boolean isEmpty() {
        return occupied.isZero();
    }

    public long convexCpuCost(long load, long weight) {
        return convexCost(occupied.vcpu(), load, capacity.vcpu(), weight);
    }

    public long convexRamCost(long load, long weight) {
        return convexCost(occupied.ramMb(), load, capacity.ramMb(), weight);
    }

    public long convexDiskCost(long load, long weight) {
        return convexCost(occupied.diskMb(), load, capacity.diskMb(), weight);
    }

    private long convexCost(long occupiedAmount, long additionalLoad, long capacityAmount, long weight) {
        BigDecimal utilization = BigDecimal.valueOf(occupiedAmount)
                .add(BigDecimal.valueOf(additionalLoad))
                .divide(BigDecimal.valueOf(capacityAmount), 18, RoundingMode.HALF_UP);
        BigDecimal baseUtilization = BigDecimal.valueOf(occupiedAmount)
                .divide(BigDecimal.valueOf(capacityAmount), 18, RoundingMode.HALF_UP);
        return utilization.multiply(utilization)
                .subtract(baseUtilization.multiply(baseUtilization))
                .multiply(BigDecimal.valueOf(weight))
                .setScale(0, RoundingMode.HALF_UP)
                .longValueExact();
    }
}