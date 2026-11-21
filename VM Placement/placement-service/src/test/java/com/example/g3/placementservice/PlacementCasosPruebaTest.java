package com.example.g3.placementservice;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import ai.timefold.solver.core.api.solver.SolverFactory;
import ai.timefold.solver.core.config.solver.SolverConfig;
import com.example.g3.placementservice.placement.domain.HostCapacity;
import com.example.g3.placementservice.placement.domain.NewVm;
import com.example.g3.placementservice.placement.domain.PlacementProblem;
import com.example.g3.placementservice.placement.domain.PlacementWeights;
import com.example.g3.placementservice.placement.domain.ResourceVector;
import com.example.g3.placementservice.placement.engine.timefold.TimefoldPlacementConstraintProvider;
import com.example.g3.placementservice.placement.engine.timefold.TimefoldPlacementMapper;
import com.example.g3.placementservice.placement.engine.timefold.TimefoldSolution;
import com.example.g3.placementservice.placement.engine.timefold.TimefoldVm;
import java.math.BigDecimal;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

class PlacementCasosPruebaTest {

    private static final PlacementWeights WEIGHTS = new PlacementWeights(200, 1000, 20, 1, false);

    @Test
    @DisplayName("Caso 1: seleccion del host por memoria RAM")
    void seleccionaHostConRamSuficiente() {
        PlacementProblem problem = problem(201,
                List.of(host(5, 8, 4096, 1, 3584), host(6, 8, 4096, 1, 0)),
                List.of(vm(1, 1024)));

        TimefoldSolution solved = solve(problem);

        report("Caso 1", solved);
        assertTrue(solved.getScore().isFeasible());
        assertEquals(6, solved.getVms().get(0).getWorker().getServerId());
    }

    @Test
    @DisplayName("Caso 2: seleccion del host por CPU")
    void seleccionaHostConCpuSuficiente() {
        PlacementProblem problem = problem(202,
                List.of(host(5, 4, 4096, 3, 512), host(6, 4, 4096, 1, 512)),
                List.of(vm(2, 512)));

        TimefoldSolution solved = solve(problem);

        report("Caso 2", solved);
        assertTrue(solved.getScore().isFeasible());
        assertEquals(6, solved.getVms().get(0).getWorker().getServerId());
    }

    @Test
    @DisplayName("Caso 3: fallback a otro host por falta de recursos y rechazo si ninguno alcanza")
    void haceFallbackYRechazaSinCapacidad() {
        PlacementProblem fallback = problem(203,
                List.of(host(5, 8, 4096, 1, 3800), host(6, 8, 4096, 0, 0)),
                List.of(vm(1, 512)));

        TimefoldSolution moved = solve(fallback);

        report("Caso 3a", moved);
        assertTrue(moved.getScore().isFeasible());
        assertEquals(6, moved.getVms().get(0).getWorker().getServerId());

        PlacementProblem noCapacity = problem(204,
                List.of(host(5, 4, 4096, 4, 0), host(6, 4, 4096, 4, 0)),
                List.of(vm(1, 512)));

        TimefoldSolution rejected = solve(noCapacity);

        report("Caso 3b", rejected);
        assertFalse(rejected.getScore().isFeasible());
        assertTrue(rejected.getScore().hardScore().compareTo(BigDecimal.ZERO) < 0);
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

    private void report(String name, TimefoldSolution solution) {
        TimefoldVm vm = solution.getVms().get(0);
        System.out.printf("%s -> servidor=%d score=%s factible=%s%n",
                name,
                vm.getWorker().getServerId(),
                solution.getScore(),
                solution.getScore().isFeasible());
    }

    private PlacementProblem problem(long deploymentId, List<HostCapacity> hosts, List<NewVm> vms) {
        return new PlacementProblem(deploymentId, 1, hosts, vms, List.of(), List.of(), WEIGHTS);
    }

    private HostCapacity host(int serverId, long cpu, long ramMb, long cpuUsed, long ramUsed) {
        return new HostCapacity(
                serverId,
                new ResourceVector(cpu, ramMb, 100000),
                new ResourceVector(cpuUsed, ramUsed, 0));
    }

    private NewVm vm(long cpu, long ramMb) {
        return new NewVm(UUID.randomUUID(), 1, new ResourceVector(cpu, ramMb, 1000));
    }
}
