package com.example.g3.placementservice.placement.domain;

import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;

public record PlacementProblem(
        long deploymentId,
        int zonaId,
        List<HostCapacity> hosts,
        List<NewVm> newVms,
        List<ResizedVm> growingVms,
        List<ExistingSliceVm> sliceMembers,
        PlacementWeights weights) {

    public PlacementProblem {
        if (deploymentId <= 0 || zonaId <= 0) {
            throw new IllegalArgumentException("deploymentId and zonaId must be positive");
        }
        hosts = List.copyOf(Objects.requireNonNull(hosts, "hosts"));
        newVms = List.copyOf(Objects.requireNonNull(newVms, "newVms"));
        growingVms = List.copyOf(Objects.requireNonNull(growingVms, "growingVms"));
        sliceMembers = List.copyOf(Objects.requireNonNull(sliceMembers, "sliceMembers"));
        Objects.requireNonNull(weights, "weights");

        Set<Integer> hostIds = hosts.stream().map(HostCapacity::serverId).collect(Collectors.toSet());
        Set<UUID> nodeIds = new HashSet<>();
        for (NewVm vm : newVms) {
            if (!nodeIds.add(vm.nodeId())) {
                throw new IllegalArgumentException("Duplicate placement node: " + vm.nodeId());
            }
        }
        for (ResizedVm vm : growingVms) {
            if (!nodeIds.add(vm.nodeId())) {
                throw new IllegalArgumentException("Duplicate placement node: " + vm.nodeId());
            }
            if (!hostIds.contains(vm.currentServerId())) {
                throw new IllegalArgumentException("Resize host is not an active candidate: " + vm.currentServerId());
            }
        }
        for (ExistingSliceVm member : sliceMembers) {
            if (!hostIds.contains(member.serverId())) {
                throw new IllegalArgumentException("Slice member host is not an active candidate: " + member.serverId());
            }
        }
    }
}