package com.example.g3.placementservice.placement.engine.timefold;

import ai.timefold.solver.core.api.domain.solution.PlanningEntityCollectionProperty;
import ai.timefold.solver.core.api.domain.solution.PlanningScore;
import ai.timefold.solver.core.api.domain.solution.PlanningSolution;
import ai.timefold.solver.core.api.domain.solution.ProblemFactCollectionProperty;
import ai.timefold.solver.core.api.domain.valuerange.ValueRangeProvider;
import ai.timefold.solver.core.api.score.HardSoftBigDecimalScore;
import java.util.List;

@PlanningSolution
public class TimefoldSolution {

    @ValueRangeProvider(id = "workerRange")
    @ProblemFactCollectionProperty
    private List<TimefoldWorker> workers;

    @ProblemFactCollectionProperty
    private List<TimefoldSliceMember> sliceMembers;

    @PlanningEntityCollectionProperty
    private List<TimefoldVm> vms;

    @PlanningScore
    private HardSoftBigDecimalScore score;

    public TimefoldSolution() {
    }

    public TimefoldSolution(
            List<TimefoldWorker> workers,
            List<TimefoldSliceMember> sliceMembers,
            List<TimefoldVm> vms) {
        this.workers = workers;
        this.sliceMembers = sliceMembers;
        this.vms = vms;
    }

    public List<TimefoldWorker> getWorkers() {
        return workers;
    }

    public void setWorkers(List<TimefoldWorker> workers) {
        this.workers = workers;
    }

    public List<TimefoldSliceMember> getSliceMembers() {
        return sliceMembers;
    }

    public void setSliceMembers(List<TimefoldSliceMember> sliceMembers) {
        this.sliceMembers = sliceMembers;
    }

    public List<TimefoldVm> getVms() {
        return vms;
    }

    public void setVms(List<TimefoldVm> vms) {
        this.vms = vms;
    }

    public HardSoftBigDecimalScore getScore() {
        return score;
    }

    public void setScore(HardSoftBigDecimalScore score) {
        this.score = score;
    }
}