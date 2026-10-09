package com.example.g3.placementservice.placement.engine;

import com.example.g3.placementservice.placement.domain.Assignment;
import com.example.g3.placementservice.placement.domain.PlacementPlan;
import com.example.g3.placementservice.placement.domain.PlacementProblem;
import com.example.g3.placementservice.placement.domain.WorkerSnapshot;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

/**
 * Motor de asignación Round Robin creado específicamente para la DEMO del Examen (Ex1).
 * La anotación @Primary hace que Spring Boot ignore a Timefold y use este motor.
 * Cumple con el requisito: (VM1, VM4) -> W1, (VM2, VM5) -> W2, (VM3, VM6) -> W3.
 */
@Primary
@Component
public class RoundRobinPlacementEngine implements PlacementEngine {

    @Override
    public PlacementPlan solve(PlacementProblem problem) {
        List<WorkerSnapshot> workers = problem.cluster().workers();
        List<Assignment> assignments = new ArrayList<>();
        
        int workerIndex = 0;
        
        // Iteramos sobre las VMs solicitadas en orden (VM1 a VM6)
        for (var vm : problem.newVms()) {
            // Selecciona el worker usando módulo (0, 1, 2, 0, 1, 2...)
            WorkerSnapshot worker = workers.get(workerIndex % workers.size());
            
            assignments.add(new Assignment(vm.vmId(), worker.serverId(), vm.resources()));
            workerIndex++;
        }
        
        return new PlacementPlan(problem.sliceVersionId(), problem.zonaId(), assignments);
    }
}
