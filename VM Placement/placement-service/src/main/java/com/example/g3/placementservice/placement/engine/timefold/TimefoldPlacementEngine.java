package com.example.g3.placementservice.placement.engine.timefold;

import ai.timefold.solver.core.api.solver.SolverJob;
import ai.timefold.solver.core.api.solver.SolverManager;
import com.example.g3.placementservice.placement.domain.PlacementProblem;
import com.example.g3.placementservice.placement.domain.PlacementSolution;
import com.example.g3.placementservice.placement.domain.HostCapacity;
import com.example.g3.placementservice.placement.domain.ResourceVector;
import com.example.g3.placementservice.placement.application.PlacementFailureException;
import com.example.g3.placementservice.placement.engine.PlacementEngine;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;
import java.util.UUID;
import java.util.concurrent.ExecutionException;
import org.springframework.stereotype.Component;

@Component
public class TimefoldPlacementEngine implements PlacementEngine {

    private final SolverManager<TimefoldSolution> solverManager;
    private final TimefoldPlacementMapper mapper;

    public TimefoldPlacementEngine(SolverManager<TimefoldSolution> solverManager,
                                   TimefoldPlacementMapper mapper) {
        this.solverManager = solverManager;
        this.mapper = mapper;
    }

    @Override
    public PlacementSolution solve(PlacementProblem problem) {
        if (problem.newVms().isEmpty() && problem.growingVms().isEmpty()) {
            return new PlacementSolution(problem.deploymentId(), java.util.List.of());
        }
        validatePinnedCapacity(problem);
        TimefoldSolution initialSolution = mapper.toSolution(problem);
        UUID problemId = UUID.randomUUID(); // el método acepta Object, no hay ProblemId_ genérico

        SolverJob<TimefoldSolution> job = solverManager.solve(problemId, initialSolution);
        TimefoldSolution solved;
        try {
            solved = job.getFinalBestSolution();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Placement solving interrupted", e);
        } catch (ExecutionException e) {
            throw new IllegalStateException("Placement solving failed", e);
        }

        if (solved.getScore() == null || !solved.getScore().isFeasible()) {
            throw new PlacementFailureException(
                    "CAPACITY_EXCEEDED",
                    "new_vm_no_capacity",
                    "No feasible placement exists for the planned new VMs");
        }
        return mapper.toSolution(problem, solved);
    }

    private void validatePinnedCapacity(PlacementProblem problem) {
        Map<Integer, HostCapacity> hosts = problem.hosts().stream()
                .collect(Collectors.toMap(HostCapacity::serverId, Function.identity()));
        for (var vm : problem.growingVms()) {
            HostCapacity host = hosts.get(vm.currentServerId());
            ResourceVector finalUsage = host.occupied().add(vm.delta());
            if (finalUsage.vcpu() > host.capacity().vcpu()
                    || finalUsage.ramMb() > host.capacity().ramMb()
                    || finalUsage.diskMb() > host.capacity().diskMb()) {
                throw new PlacementFailureException(
                        "CAPACITY_EXCEEDED",
                        "resize_on_fixed_host",
                        "Resize does not fit on its current ACTIVE host");
            }
        }
    }
}