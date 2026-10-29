package com.example.g3.placementservice.placement.engine.timefold;

import com.example.g3.placementservice.placement.domain.HostCapacity;
import com.example.g3.placementservice.placement.domain.NewVm;
import com.example.g3.placementservice.placement.domain.PlacementProblem;
import com.example.g3.placementservice.placement.domain.PlacementAssignment;
import com.example.g3.placementservice.placement.domain.PlacementSolution;
import com.example.g3.placementservice.placement.domain.ResizedVm;
import com.example.g3.placementservice.placement.engine.NoPlacementSolutionException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Function;
import java.util.stream.Collectors;
import org.springframework.stereotype.Component;

@Component
public class TimefoldPlacementMapper {

    public TimefoldSolution toSolution(PlacementProblem problem) {
        List<TimefoldWorker> workers = problem.hosts().stream()
                .map(host -> toWorker(host, problem))
                .toList();
        Map<Integer, TimefoldWorker> workerById = workers.stream()
                .collect(Collectors.toMap(TimefoldWorker::getServerId, Function.identity()));
        List<TimefoldVm> vms = new ArrayList<>();
        problem.newVms().stream().map(vm -> toVm(vm, problem)).forEach(vms::add);
        problem.growingVms().stream()
                .map(vm -> toVm(vm, problem, workerById.get(vm.currentServerId())))
                .forEach(vms::add);
        List<TimefoldSliceMember> members = problem.sliceMembers().stream()
                .map(member -> new TimefoldSliceMember(member.sliceId(), member.serverId()))
                .toList();
        return new TimefoldSolution(workers, members, vms);
    }

    public PlacementSolution toSolution(PlacementProblem problem, TimefoldSolution solution) {
        Map<UUID, Integer> pinnedById = problem.growingVms().stream()
                .collect(Collectors.toMap(ResizedVm::nodeId, ResizedVm::currentServerId));
        List<PlacementAssignment> assignments = solution.getVms().stream()
                .filter(vm -> !pinnedById.containsKey(vm.getNodeId()))
                .map(this::toAssignment)
                .toList();

        if (assignments.size() != problem.newVms().size()) {
            throw new NoPlacementSolutionException(
                    "Expected %d assignments but got %d"
                            .formatted(problem.newVms().size(), assignments.size()));
        }
        for (TimefoldVm vm : solution.getVms()) {
            if (vm.isPinned()
                    && (vm.getWorker() == null
                            || !Integer.valueOf(vm.getWorker().getServerId()).equals(pinnedById.get(vm.getNodeId())))) {
                throw new IllegalStateException("Pinned resize changed host for node " + vm.getNodeId());
            }
        }
        return new PlacementSolution(problem.deploymentId(), assignments);
    }

    private PlacementAssignment toAssignment(TimefoldVm vm) {
        TimefoldWorker worker = vm.getWorker();
        if (worker == null) {
            throw new NoPlacementSolutionException("VM " + vm.getNodeId() + " has no assigned host");
        }
        return new PlacementAssignment(vm.getNodeId(), worker.getServerId());
    }

    private TimefoldWorker toWorker(HostCapacity host, PlacementProblem problem) {
        return new TimefoldWorker(host, problem.weights());
    }

    private TimefoldVm toVm(NewVm vm, PlacementProblem problem) {
        return new TimefoldVm(vm.nodeId(), vm.sliceId(), vm.demand(), problem.weights(), false);
    }

    private TimefoldVm toVm(ResizedVm vm, PlacementProblem problem, TimefoldWorker worker) {
        if (worker == null) {
            throw new NoPlacementSolutionException("Resize host is unavailable for node " + vm.nodeId());
        }
        TimefoldVm entity = new TimefoldVm(vm.nodeId(), vm.sliceId(), vm.delta(), problem.weights(), true);
        entity.setWorker(worker);
        return entity;
    }
}