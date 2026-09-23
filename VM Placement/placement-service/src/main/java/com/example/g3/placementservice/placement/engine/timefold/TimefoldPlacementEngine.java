package com.example.g3.placementservice.placement.engine.timefold;

import ai.timefold.solver.core.api.solver.SolverJob;
import ai.timefold.solver.core.api.solver.SolverManager;
import com.example.g3.placementservice.placement.domain.PlacementPlan;
import com.example.g3.placementservice.placement.domain.PlacementProblem;
import com.example.g3.placementservice.placement.engine.NoPlacementSolutionException;
import com.example.g3.placementservice.placement.engine.PlacementEngine;
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
    public PlacementPlan solve(PlacementProblem problem) {
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
            throw new NoPlacementSolutionException("No feasible placement found");
        }
        return mapper.toPlan(problem, solved);
    }
}