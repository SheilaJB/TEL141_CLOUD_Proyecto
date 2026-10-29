package com.example.g3.placementservice;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import ai.timefold.solver.core.api.score.HardSoftBigDecimalScore;
import ai.timefold.solver.core.api.solver.SolverFactory;
import ai.timefold.solver.core.config.solver.SolverConfig;
import com.example.g3.placementservice.placement.domain.ExistingSliceVm;
import com.example.g3.placementservice.placement.domain.HostCapacity;
import com.example.g3.placementservice.placement.domain.NewVm;
import com.example.g3.placementservice.placement.domain.PlacementProblem;
import com.example.g3.placementservice.placement.domain.PlacementSolution;
import com.example.g3.placementservice.placement.domain.PlacementWeights;
import com.example.g3.placementservice.placement.domain.ResizedVm;
import com.example.g3.placementservice.placement.domain.ResourceVector;
import com.example.g3.placementservice.placement.engine.timefold.TimefoldPlacementConstraintProvider;
import com.example.g3.placementservice.placement.engine.timefold.TimefoldPlacementMapper;
import com.example.g3.placementservice.placement.engine.timefold.TimefoldSolution;
import com.example.g3.placementservice.placement.engine.timefold.TimefoldVm;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class PlacementAlgorithmTest {

    private static final PlacementWeights WEIGHTS =
            new PlacementWeights(200, 1000, 20, 1, false);

    @Test
    void consolidatesBelowTheThresholdAndOpensASecondHostAboveIt() {
        PlacementProblem belowThreshold = problem(
                101,
                List.of(host(1, 10, 4, 0), host(2, 10, 0, 0)),
                List.of(newVm(1, 2, 0, 0)),
                List.of(),
                List.of());
        TimefoldSolution belowThresholdResult = solve(belowThreshold);
        assertTrue(belowThresholdResult.getScore().isFeasible());
        assertEquals(1, belowThresholdResult.getVms().get(0).getWorker().getServerId());

        PlacementProblem aboveThreshold = problem(
                104,
                List.of(host(1, 10, 6, 0), host(2, 10, 0, 0)),
                List.of(newVm(1, 2, 0, 0)),
                List.of(),
                List.of());
        TimefoldSolution aboveThresholdResult = solve(aboveThreshold);
        assertTrue(aboveThresholdResult.getScore().isFeasible());
        assertEquals(2, aboveThresholdResult.getVms().get(0).getWorker().getServerId());
    }

    @Test
    void pinsResizeDeltaToCurrentHostWithoutDoubleCountingTheExistingSize() {
        UUID resizedNodeId = UUID.randomUUID();
        PlacementProblem problem = problem(
                102,
                List.of(
                        host(1, 8, 6, 0),
                        host(2, 8, 6, 0)),
                List.of(newVm(1, 2, 0, 0)),
                List.of(new ResizedVm(
                        resizedNodeId,
                        1,
                        1,
                        new ResourceVector(2, 0, 0))),
                List.of(new ExistingSliceVm(1, 1)));

        TimefoldSolution solved = solve(problem);
        assertTrue(solved.getScore().isFeasible());

        TimefoldVm resized = solved.getVms().stream()
                .filter(vm -> resizedNodeId.equals(vm.getNodeId()))
                .findFirst()
                .orElseThrow();
        TimefoldVm newlyAllocated = solved.getVms().stream()
                .filter(vm -> !resizedNodeId.equals(vm.getNodeId()))
                .findFirst()
                .orElseThrow();
        assertTrue(resized.isPinned());
        assertEquals(1, resized.getWorker().getServerId());
        assertEquals(2, newlyAllocated.getWorker().getServerId());
        assertEquals(2, resized.getResources().vcpu());
        assertEquals(8, resized.getWorker().getOccupied().vcpu() + resized.getResources().vcpu());
    }

    @Test
    void reportsAnInfeasibleScoreWhenNewDemandExceedsAllAvailableCapacity() {
        PlacementProblem problem = problem(
                103,
                List.of(host(1, 4, 4, 0), host(2, 4, 4, 0)),
                List.of(newVm(1, 5, 0, 0)),
                List.of(),
                List.of());

        TimefoldSolution solved = solve(problem);

        assertNotNull(solved.getScore());
        assertFalse(solved.getScore().isFeasible());
        assertTrue(solved.getScore().hardScore().compareTo(java.math.BigDecimal.ZERO) < 0);
    }

    private TimefoldSolution solve(PlacementProblem problem) {
        SolverConfig config = new SolverConfig()
                .withSolutionClass(TimefoldSolution.class)
                .withEntityClasses(TimefoldVm.class)
                .withConstraintProviderClass(TimefoldPlacementConstraintProvider.class)
                .withTerminationSpentLimit(Duration.ofSeconds(2));
        return SolverFactory.<TimefoldSolution>create(config)
                .buildSolver()
                .solve(new TimefoldPlacementMapper().toSolution(problem));
    }

    private PlacementProblem problem(
            long deploymentId,
            List<HostCapacity> hosts,
            List<NewVm> newVms,
            List<ResizedVm> growingVms,
            List<ExistingSliceVm> members) {
        return new PlacementProblem(
                deploymentId,
                1,
                hosts,
                newVms,
                growingVms,
                members,
                WEIGHTS);
    }

    private HostCapacity host(int serverId, long cpuCapacity, long cpuOccupied, long ramOccupied) {
        return new HostCapacity(
                serverId,
                new ResourceVector(cpuCapacity, 100, 100),
                new ResourceVector(cpuOccupied, ramOccupied, 0));
    }

    private NewVm newVm(long sliceId, long cpu, long ram, long disk) {
        return new NewVm(
                UUID.randomUUID(),
                sliceId,
                new ResourceVector(cpu, ram, disk));
    }
}
