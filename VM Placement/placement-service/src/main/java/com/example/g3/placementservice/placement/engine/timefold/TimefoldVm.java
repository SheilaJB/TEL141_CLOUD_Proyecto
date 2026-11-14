package com.example.g3.placementservice.placement.engine.timefold;

import ai.timefold.solver.core.api.domain.common.PlanningId;
import ai.timefold.solver.core.api.domain.entity.PlanningEntity;
import ai.timefold.solver.core.api.domain.entity.PlanningPin;
import ai.timefold.solver.core.api.domain.variable.PlanningVariable;
import com.example.g3.placementservice.placement.domain.PlacementWeights;
import com.example.g3.placementservice.placement.domain.ResourceVector;
import java.util.UUID;

@PlanningEntity
public class TimefoldVm {

    @PlanningId
    private UUID nodeId;

    private long sliceId;

    private ResourceVector resources;

    private PlacementWeights weights;

    // Placement estocastico: varianza de la demanda de CPU y RAM (sigma^2)
    private double cpuVariance;

    private double ramVariance;

    @PlanningPin
    private boolean pinned;

    @PlanningVariable(valueRangeProviderRefs = "workerRange")
    private TimefoldWorker worker;

    public TimefoldVm() {
    }

    public TimefoldVm(
            UUID nodeId,
            long sliceId,
            ResourceVector resources,
            PlacementWeights weights,
            boolean pinned) {
        this.nodeId = nodeId;
        this.sliceId = sliceId;
        this.resources = resources;
        this.weights = weights;
        this.pinned = pinned;
    }

    public TimefoldVm(
            UUID nodeId,
            long sliceId,
            ResourceVector resources,
            double cpuVariance,
            double ramVariance,
            PlacementWeights weights,
            boolean pinned) {
        this(nodeId, sliceId, resources, weights, pinned);
        this.cpuVariance = cpuVariance;
        this.ramVariance = ramVariance;
    }

    public double getCpuVariance() {
        return cpuVariance;
    }

    public double getRamVariance() {
        return ramVariance;
    }

    public UUID getNodeId() {
        return nodeId;
    }

    public long getSliceId() {
        return sliceId;
    }

    public ResourceVector getResources() {
        return resources;
    }

    public PlacementWeights getWeights() {
        return weights;
    }

    public boolean isPinned() {
        return pinned;
    }

    public TimefoldWorker getWorker() {
        return worker;
    }

    public void setWorker(TimefoldWorker worker) {
        this.worker = worker;
    }
}