package com.example.g3.placementservice.placement.snapshot;

import com.example.g3.placementservice.placement.domain.ClusterSnapshot;
import com.example.g3.placementservice.placement.domain.WorkerSnapshot;
import com.example.g3.placementservice.placement.domain.PlacementConstraints;
import com.example.g3.placementservice.placement.domain.PlacementProblem;
import com.example.g3.placementservice.placement.domain.ResourceAmount;
import com.example.g3.placementservice.placement.domain.VmRequest;
import com.example.g3.placementservice.placement.persistence.AvailabilityZoneEntity;
import com.example.g3.placementservice.placement.persistence.AvailabilityZoneRepository;
import com.example.g3.placementservice.placement.persistence.ServerEntity;
import com.example.g3.placementservice.placement.persistence.ServerRepository;
import com.example.g3.placementservice.placement.persistence.SliceNodeEntity;
import com.example.g3.placementservice.placement.persistence.SliceVersionEntity;
import com.example.g3.placementservice.placement.persistence.SliceVersionRepository;
import java.math.BigDecimal;
import java.util.List;
import java.util.Objects;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class PlacementSnapshotServiceImpl implements PlacementSnapshotService {

    private static final String ACTIVE = "ACTIVE";

    private final SliceVersionRepository sliceVersionRepository;
    private final AvailabilityZoneRepository zoneRepository;
    private final ServerRepository serverRepository;

    public PlacementSnapshotServiceImpl(
            SliceVersionRepository sliceVersionRepository,
            AvailabilityZoneRepository zoneRepository,
            ServerRepository serverRepository) {
        this.sliceVersionRepository = sliceVersionRepository;
        this.zoneRepository = zoneRepository;
        this.serverRepository = serverRepository;
    }

    @Override
    @Transactional(readOnly = true)
    public PlacementProblem loadProblem(Integer sliceVersionId, Integer zonaId) {
        Objects.requireNonNull(sliceVersionId, "sliceVersionId");
        Objects.requireNonNull(zonaId, "zonaId");

        SliceVersionEntity sliceVersion = sliceVersionRepository.findById(sliceVersionId)
                .orElseThrow(() -> new IllegalArgumentException("Slice version not found: " + sliceVersionId));
        AvailabilityZoneEntity zone = zoneRepository.findById(zonaId)
                .orElseThrow(() -> new IllegalArgumentException("Availability zone not found: " + zonaId));

        List<WorkerSnapshot> workers = serverRepository.findByZonaIdAndEstado(zonaId, ACTIVE)
                .stream()
                .map(server -> toWorkerSnapshot(server, zone))
                .toList();

        List<VmRequest> newVms = sliceVersion.getNodes()
                .stream()
                .map(this::toVmRequest)
                .toList();

        return new PlacementProblem(
                sliceVersionId,
                zonaId,
                new ClusterSnapshot(zone.getCluster().getNombre(), workers),
                newVms,
                PlacementConstraints.none());
    }

    private WorkerSnapshot toWorkerSnapshot(ServerEntity server, AvailabilityZoneEntity zone) {
        return new WorkerSnapshot(
                server.getId(),
                zone.getId(),
                zone.getCluster().getId(),
                new ResourceAmount(
                        effective(server.getTotalCpuCores(), zone.getCpuAllocationRatioDefault()),
                        effective(server.getTotalRamMb(), zone.getRamAllocationRatioDefault()),
                        effective(server.getTotalDiskMb(), zone.getDiskAllocationRatioDefault())),
                new ResourceAmount(
                        server.getVcpuReserved(),
                        server.getVramMbReserved(),
                        server.getVdiskMbReserved()),
                server.getEstado());
    }

    private VmRequest toVmRequest(SliceNodeEntity node) {
        return new VmRequest(
                node.getId(),
                new ResourceAmount(
                        node.getFlavor().getVcpu(),
                        node.getFlavor().getVramMb(),
                        node.getFlavor().getVdiskMb()));
    }

    private long effective(Integer physicalCapacity, BigDecimal allocationRatio) {
        return BigDecimal.valueOf(physicalCapacity)
                .multiply(allocationRatio)
                .longValue();
    }
}