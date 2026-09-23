package com.example.g3.placementservice.placement.engine.timefold;

import ai.timefold.solver.core.api.domain.common.PlanningId;
import ai.timefold.solver.core.api.domain.entity.PlanningEntity;
import ai.timefold.solver.core.api.domain.variable.PlanningVariable;
import com.example.g3.placementservice.placement.domain.ResourceAmount;

@PlanningEntity
public class TimefoldVm {

    @PlanningId
    private Integer vmId;

    private ResourceAmount resources;

    @PlanningVariable(valueRangeProviderRefs = "workerRange")
    private TimefoldWorker worker;

    public TimefoldVm() {
    }

    public TimefoldVm(Integer vmId, ResourceAmount resources) {
        this.vmId = vmId;
        this.resources = resources;
    }

    public Integer getVmId() {
        return vmId;
    }

    public ResourceAmount getResources() {
        return resources;
    }

    public TimefoldWorker getWorker() {
        return worker;
    }

    public void setWorker(TimefoldWorker worker) {
        this.worker = worker;
    }
}