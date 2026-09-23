package com.example.g3.placementservice.placement.engine.timefold;

import ai.timefold.solver.core.api.domain.solution.PlanningEntityCollectionProperty;
import ai.timefold.solver.core.api.domain.solution.PlanningScore;
import ai.timefold.solver.core.api.domain.solution.PlanningSolution;
import ai.timefold.solver.core.api.domain.solution.ProblemFactCollectionProperty;
import ai.timefold.solver.core.api.domain.valuerange.ValueRangeProvider;
import ai.timefold.solver.core.api.score.HardSoftScore;
import java.util.List;

@PlanningSolution
public class TimefoldSolution {

    @ValueRangeProvider(id = "workerRange")
    @ProblemFactCollectionProperty
    private List<TimefoldWorker> workers;

    @PlanningEntityCollectionProperty
    private List<TimefoldVm> vms;

    @PlanningScore
    private HardSoftScore score;

    public TimefoldSolution() {
    }

    public TimefoldSolution(List<TimefoldWorker> workers, List<TimefoldVm> vms) {
        this.workers = workers;
        this.vms = vms;
    }

    public List<TimefoldWorker> getWorkers() {
        return workers;
    }

    public void setWorkers(List<TimefoldWorker> workers) {
        this.workers = workers;
    }

    public List<TimefoldVm> getVms() {
        return vms;
    }

    public void setVms(List<TimefoldVm> vms) {
        this.vms = vms;
    }

    public HardSoftScore getScore() {
        return score;
    }

    public void setScore(HardSoftScore score) {
        this.score = score;
    }
}