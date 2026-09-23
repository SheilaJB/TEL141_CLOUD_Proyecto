package com.example.g3.placementservice.placement.engine.timefold;

import com.example.g3.placementservice.placement.domain.Assignment;
import com.example.g3.placementservice.placement.domain.PlacementPlan;
import com.example.g3.placementservice.placement.domain.PlacementProblem;
import com.example.g3.placementservice.placement.domain.VmRequest;
import com.example.g3.placementservice.placement.domain.WorkerSnapshot;
import com.example.g3.placementservice.placement.engine.NoPlacementSolutionException;
import java.util.List;
import org.springframework.stereotype.Component;

@Component
public class TimefoldPlacementMapper {

    public TimefoldSolution toSolution(PlacementProblem problem) {
        List<TimefoldWorker> workers = problem.cluster().workers().stream()
                .map(this::toWorker)
                .toList();
        List<TimefoldVm> vms = problem.newVms().stream()
                .map(this::toVm)
                .toList();
        return new TimefoldSolution(workers, vms);
    }

    public PlacementPlan toPlan(PlacementProblem problem, TimefoldSolution solution) {
        List<Assignment> assignments = solution.getVms().stream()
                .map(this::toAssignment)
                .toList();

        if (assignments.size() != problem.newVms().size()) {
            throw new NoPlacementSolutionException(
                    "Se esperaban %d asignaciones y se obtuvieron %d"
                            .formatted(problem.newVms().size(), assignments.size()));
        }
        return new PlacementPlan(problem.sliceVersionId(), problem.zonaId(), assignments);
    }

    private Assignment toAssignment(TimefoldVm vm) {
        TimefoldWorker worker = vm.getWorker();
        if (worker == null) {
            // No debería pasar si el score ya es feasible; validación defensiva.
            throw new NoPlacementSolutionException("VM " + vm.getVmId() + " quedó sin worker asignado");
        }
        return new Assignment(vm.getVmId(), worker.getServerId(), vm.getResources());
    }

    private TimefoldWorker toWorker(WorkerSnapshot worker) {
        return new TimefoldWorker(worker.serverId(), worker.capacity(), worker.allocated());
    }

    private TimefoldVm toVm(VmRequest vm) {
        return new TimefoldVm(vm.vmId(), vm.resources());
    }
}