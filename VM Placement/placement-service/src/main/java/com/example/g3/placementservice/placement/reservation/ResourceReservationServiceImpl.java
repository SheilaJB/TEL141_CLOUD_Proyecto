package com.example.g3.placementservice.placement.reservation;

import com.example.g3.placementservice.placement.domain.PlacementAssignment;
import com.example.g3.placementservice.placement.domain.PlacementSolution;
import com.example.g3.placementservice.placement.domain.ResourceVector;
import com.example.g3.placementservice.placement.engine.PlacementEngine;
import com.example.g3.placementservice.placement.application.PlacementFailureException;
import com.example.g3.placementservice.placement.snapshot.PlacementSnapshot;
import com.example.g3.placementservice.placement.snapshot.PlacementSnapshot.NodeReservation;
import com.example.g3.placementservice.placement.snapshot.PlacementSnapshotService;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ResourceReservationServiceImpl implements ResourceReservationService {

    private static final String INSERT_RESERVATION = """
            INSERT INTO slices.reserva_nodo
                (servidor_id, nodo_id, createdby_deployment_id, recurso, cantidad_reservada, estado)
            VALUES (?, ?, ?, ?, ?, 'RESERVED')
            """;

    private static final String UPDATE_RESIZE_RESERVATION = """
            UPDATE slices.reserva_nodo
            SET cantidad_reservada = ?
            WHERE nodo_id = ? AND recurso = ? AND estado = 'RESERVED'
              AND cantidad_reservada = ?
            """;

    private static final String RELEASE_RESERVATIONS_FOR_DEPLOYMENT = """
            UPDATE slices.reserva_nodo
            SET estado = 'RELEASED', fecha_liberacion = now()
            WHERE createdby_deployment_id = ? AND estado = 'RESERVED'
            RETURNING servidor_id, recurso, cantidad_reservada
            """;

    private static final String RELEASE_NODE_RESERVATION = """
            UPDATE slices.reserva_nodo
            SET estado = 'RELEASED', fecha_liberacion = now()
            WHERE nodo_id = ? AND recurso = ? AND estado = 'RESERVED'
              AND cantidad_reservada = ?
            """;

    private static final String UPDATE_SERVER_COUNTERS = """
            UPDATE slices.servidor
            SET vcpu_reserved = vcpu_reserved + ?,
                vram_mb_reserved = vram_mb_reserved + ?,
                vdisk_mb_reserved = vdisk_mb_reserved + ?
            FROM slices.zona_disponibilidad zona
            WHERE servidor.id = ?
              AND servidor.zona_id = ?
              AND servidor.zona_id = zona.id
              AND servidor.estado = 'ACTIVE'
              AND servidor.vcpu_reserved + ? <= FLOOR(servidor.total_cpu_cores * zona.cpu_allocation_ratio_default)
              AND servidor.vram_mb_reserved + ? <= FLOOR(servidor.total_ram_mb * zona.ram_allocation_ratio_default)
              AND servidor.vdisk_mb_reserved + ? <= FLOOR(servidor.total_disk_mb * zona.disk_allocation_ratio_default)
            """;

    private static final String DECREMENT_SERVER_COUNTERS = """
            UPDATE slices.servidor
            SET vcpu_reserved = vcpu_reserved - ?,
                vram_mb_reserved = vram_mb_reserved - ?,
                vdisk_mb_reserved = vdisk_mb_reserved - ?
            WHERE id = ?
              AND vcpu_reserved >= ?
              AND vram_mb_reserved >= ?
              AND vdisk_mb_reserved >= ?
            """;

    private static final String ACTIVE_DEPLOYMENT_RESERVATIONS = """
            SELECT nodo_id, servidor_id, recurso, cantidad_reservada, estado
            FROM slices.reserva_nodo
            WHERE createdby_deployment_id = ?
            ORDER BY nodo_id, recurso
            FOR UPDATE
            """;

    private static final String LOCK_DEPLOYMENT = """
            SELECT d.id, d.slice_id
            FROM slices.deployment d
            JOIN slices.slice s ON s.id = d.slice_id
            WHERE d.id = ?
            FOR UPDATE OF d, s
            """;

    private static final String MARK_RESERVED = """
            UPDATE slices.deployment SET reserved_at = now() WHERE id = ?
            """;

    private static final String CLEAR_RESERVED = """
            UPDATE slices.deployment SET reserved_at = NULL WHERE id = ?
            """;

    private final JdbcTemplate jdbcTemplate;
    private final PlacementSnapshotService snapshotService;
    private final PlacementEngine placementEngine;

    public ResourceReservationServiceImpl(
            JdbcTemplate jdbcTemplate,
            PlacementSnapshotService snapshotService,
            PlacementEngine placementEngine) {
        this.jdbcTemplate = Objects.requireNonNull(jdbcTemplate, "jdbcTemplate");
        this.snapshotService = Objects.requireNonNull(snapshotService, "snapshotService");
        this.placementEngine = Objects.requireNonNull(placementEngine, "placementEngine");
    }

    @Override
    @Transactional
    public PlacementSolution reserve(long deploymentId) {
        PlacementSolution solution = reserveInternal(deploymentId);
        jdbcTemplate.update(MARK_RESERVED, deploymentId);
        return solution;
    }

    private PlacementSolution reserveInternal(long deploymentId) {
        PlacementSnapshot snapshot = snapshotService.load(deploymentId);
        Map<UUID, NodeReservation> existingByNode = PlacementSnapshot.byNode(snapshot.activeReservations());
        List<ExistingAllocation> committedAllocations = loadDeploymentReservations(deploymentId);
        if (!committedAllocations.isEmpty()) {
            if (committedAllocations.stream().anyMatch(row -> !"RESERVED".equals(row.status()))) {
                throw new ReservationStateException(deploymentId);
            }
            return replay(snapshot, committedAllocations, existingByNode);
        }
        if (!snapshot.allocations().isEmpty()
                && allocationsAlreadyReserved(snapshot, existingByNode)) {
            return replay(snapshot, committedAllocations, existingByNode);
        }
        validateExistingAllocationStates(snapshot, existingByNode);

        if (snapshot.allocations().isEmpty()) {
            validateResizeHost(snapshot, existingByNode);
            if (snapshot.resizes().stream().allMatch(resize ->
                    expectedAfterGrowth(resize).equals(existingByNode.get(resize.nodeId()).resources()))) {
                return new PlacementSolution(deploymentId, List.of());
            }
        }
        validateResizeBefore(snapshot, existingByNode);
        validateResizeHost(snapshot, existingByNode);
        if (snapshot.problem().hosts().isEmpty()
                && (!snapshot.problem().newVms().isEmpty() || !snapshot.problem().growingVms().isEmpty())) {
            throw new PlacementFailureException(
                    "CONFLICT", "no_active_servers", "Availability zone has no ACTIVE servers");
        }
        PlacementSolution solution = placementEngine.solve(snapshot.problem());
        validateSolution(snapshot, solution);

        Map<UUID, Integer> serverByNode = solution.assignments().stream()
                .collect(Collectors.toMap(PlacementAssignment::nodeId, PlacementAssignment::serverId));
        Map<Integer, ResourceVector> increments = new LinkedHashMap<>();
        Map<Integer, ResourceVector> failedReservationDecrements = new LinkedHashMap<>();
        Set<UUID> reusedAllocations = new HashSet<>();
        for (PlacementSnapshot.Allocation allocation : snapshot.allocations()) {
            int serverId = serverByNode.get(allocation.nodeId());
            NodeReservation existing = existingByNode.get(allocation.nodeId());
            if (existing != null) {
                if (!"FAILED".equals(existing.nodeStatus())) {
                    throw new ReservationStateException(deploymentId);
                }
                if ("ACTIVE".equals(existing.serverStatus())
                        && existing.serverId() == serverId
                        && existing.resources().equals(allocation.resources())) {
                    reusedAllocations.add(allocation.nodeId());
                    continue;
                }
                List<ReservationChange> released = releaseNode(allocation.nodeId());
                if (!reservationVector(released, existing.serverId()).equals(existing.resources())) {
                    throw new IllegalStateException(
                            "Failed node reservation changed during placement: " + allocation.nodeId());
                }
                failedReservationDecrements.merge(
                        existing.serverId(), existing.resources(), ResourceVector::add);
            }
            increments.merge(serverId, allocation.resources(), ResourceVector::add);
        }
        for (PlacementSnapshot.Resize resize : snapshot.resizes()) {
            ResourceVector delta = resize.before().positiveDeltaTo(resize.after());
            if (delta.isZero()) {
                continue;
            }
            int serverId = existingByNode.get(resize.nodeId()).serverId();
            increments.merge(serverId, delta, ResourceVector::add);
        }
        decrementCounters(failedReservationDecrements);
        for (Map.Entry<Integer, ResourceVector> entry : increments.entrySet()) {
            int updated = updateServerCounters(entry.getKey(), snapshot.problem().zonaId(), entry.getValue());
            if (updated != 1) {
                throw new ReservationConflictException(entry.getKey());
            }
        }
        persistAllocations(deploymentId, snapshot, serverByNode, reusedAllocations);
        persistResizeGrowth(snapshot);
        return solution;
    }

    @Override
    @Transactional
    public void rollbackReservation(long deploymentId) {
        jdbcTemplate.query(LOCK_DEPLOYMENT, (rs, row) -> rs.getInt("slice_id"), deploymentId)
                .stream().findFirst()
                .orElseThrow(() -> new IllegalArgumentException("Deployment not found: " + deploymentId));
        jdbcTemplate.update(CLEAR_RESERVED, deploymentId);
        List<ExistingAllocation> created = loadDeploymentReservations(deploymentId).stream()
                .filter(row -> "RESERVED".equals(row.status()))
                .toList();
        Map<Integer, ResourceVector> decrements = new LinkedHashMap<>();
        if (!created.isEmpty()) {
            List<ReservationChange> rows = jdbcTemplate.query(
                    RELEASE_RESERVATIONS_FOR_DEPLOYMENT,
                    (rs, row) -> new ReservationChange(
                            rs.getInt("servidor_id"),
                            rs.getString("recurso"),
                            rs.getLong("cantidad_reservada")),
                    deploymentId);
            for (ReservationChange row : rows) {
                decrements.merge(row.serverId(), ResourceVector.of(row.resource(), row.amount()), ResourceVector::add);
            }
        }

        PlacementSnapshot snapshot = snapshotService.load(deploymentId);
        Map<UUID, NodeReservation> current = PlacementSnapshot.byNode(snapshot.activeReservations());
        for (PlacementSnapshot.Resize resize : snapshot.resizes()) {
            NodeReservation state = current.get(resize.nodeId());
            if (state == null && !resize.after().isZero()) {
                throw new IllegalStateException("Resize reservation is missing during rollback: " + resize.nodeId());
            }
            ResourceVector delta = resize.before().positiveDeltaTo(resize.after());
            if (delta.isZero()) {
                continue;
            }
            ResourceVector restored = rollbackResizeRows(resize, state);
            if (!restored.isZero()) {
                decrements.merge(state.serverId(), restored, ResourceVector::add);
            }
        }
        decrementCounters(decrements);
    }

    @Override
    @Transactional
    public void finalizeReservation(long deploymentId) {
        PlacementSnapshot snapshot = snapshotService.load(deploymentId);
        Map<UUID, NodeReservation> current = PlacementSnapshot.byNode(snapshot.activeReservations());
        Map<Integer, ResourceVector> decrements = new LinkedHashMap<>();

        for (UUID nodeId : snapshot.releases()) {
            NodeReservation state = current.get(nodeId);
            if (state == null) {
                continue;
            }
            List<ReservationChange> rows = releaseNode(nodeId);
            for (ReservationChange row : rows) {
                decrements.merge(row.serverId(), ResourceVector.of(row.resource(), row.amount()), ResourceVector::add);
            }
        }

        for (PlacementSnapshot.Resize resize : snapshot.resizes()) {
            NodeReservation state = current.get(resize.nodeId());
            if (state == null && !resize.after().isZero()) {
                throw new IllegalStateException("Resize reservation is missing during finalize: " + resize.nodeId());
            }
            for (ResourceChange change : resourceChanges(resize.before(), resize.after())) {
                long currentAmount = state == null ? 0 : state.value(change.resource());
                if (change.after() > change.before() && currentAmount != change.after()) {
                    throw new IllegalStateException("Growing resize was not reserved before finalize for "
                            + resize.nodeId());
                }
                if (change.after() == change.before() && currentAmount != change.before()) {
                    throw new IllegalStateException("Resize reservation has an unexpected value for "
                            + resize.nodeId());
                }
                if (change.after() < change.before()
                        && currentAmount != change.before()
                        && currentAmount != change.after()) {
                    throw new IllegalStateException("Resize reservation has an unexpected value for "
                            + resize.nodeId());
                }
                if (change.after() >= change.before() || currentAmount == change.after()) {
                    continue;
                }
                if (change.after() > 0) {
                    int updated = jdbcTemplate.update(
                            UPDATE_RESIZE_RESERVATION,
                            change.after(),
                            resize.nodeId(),
                            change.resource(),
                            change.before());
                    if (updated == 0 && !isReservationValue(resize.nodeId(), change.resource(), change.after())) {
                        throw new IllegalStateException("Missing active resize reservation for " + resize.nodeId());
                    }
                } else {
                    int updated = jdbcTemplate.update(
                            RELEASE_NODE_RESERVATION,
                            resize.nodeId(),
                            change.resource(),
                            change.before());
                    if (updated == 0 && !isReleasedReservation(resize.nodeId(), change.resource())) {
                        throw new IllegalStateException("Missing active resize reservation for " + resize.nodeId());
                    }
                }
                decrements.merge(
                        state.serverId(),
                        ResourceVector.of(change.resource(), change.before() - change.after()),
                        ResourceVector::add);
            }
        }
        decrementCounters(decrements);
    }

    private PlacementSolution replay(
            PlacementSnapshot snapshot,
            List<ExistingAllocation> rows,
            Map<UUID, NodeReservation> activeByNode) {
        Map<UUID, List<ExistingAllocation>> rowsByNode = rows.stream()
                .collect(Collectors.groupingBy(ExistingAllocation::nodeId));
        Set<UUID> expectedNodes = snapshot.allocations().stream()
                .map(PlacementSnapshot.Allocation::nodeId)
                .collect(Collectors.toSet());
        if (!expectedNodes.containsAll(rowsByNode.keySet())) {
            throw new ReservationStateException(snapshot.problem().deploymentId());
        }
        List<PlacementAssignment> assignments = new ArrayList<>();
        for (PlacementSnapshot.Allocation allocation : snapshot.allocations()) {
            NodeReservation state = activeByNode.get(allocation.nodeId());
            if (state == null || !state.resources().equals(allocation.resources())) {
                throw new ReservationStateException(snapshot.problem().deploymentId());
            }
            List<ExistingAllocation> nodeRows = rowsByNode.getOrDefault(allocation.nodeId(), List.of());
            if (!nodeRows.isEmpty()) {
                validateReservationVector(
                        nodeRows, allocation.resources(), allocation.nodeId(), snapshot.problem().deploymentId());
                Set<Integer> serverIds = nodeRows.stream()
                        .map(ExistingAllocation::serverId)
                        .collect(Collectors.toSet());
                if (serverIds.size() != 1 ||                 !serverIds.contains(state.serverId())) {
                    throw new ReservationStateException(snapshot.problem().deploymentId());
                }
            }
                            assignments.add(new PlacementAssignment(allocation.nodeId(), state.serverId()));
        }
        for (PlacementSnapshot.Resize resize : snapshot.resizes()) {
                            NodeReservation state = activeByNode.get(resize.nodeId());
            if (state == null && !resize.after().isZero()) {
                throw new IllegalStateException("Resize reservation is missing during replay: " + resize.nodeId());
            }
            if (!expectedAfterGrowth(resize).equals(state == null ? new ResourceVector(0, 0, 0) : state.resources())) {
                throw new IllegalStateException("Resize reservation is not fully applied during replay: "
                        + resize.nodeId());
            }
        }
        return new PlacementSolution(snapshot.problem().deploymentId(), assignments);
    }

    private boolean allocationsAlreadyReserved(
            PlacementSnapshot snapshot, Map<UUID, NodeReservation> activeByNode) {
        for (PlacementSnapshot.Allocation allocation : snapshot.allocations()) {
            NodeReservation state = activeByNode.get(allocation.nodeId());
            if (state == null
                    || !state.resources().equals(allocation.resources())
                    || !"ACTIVE".equals(state.serverStatus())
                    || (state.createdByDeploymentId() != snapshot.problem().deploymentId()
                            && !"FAILED".equals(state.nodeStatus()))) {
                return false;
            }
        }
        for (PlacementSnapshot.Resize resize : snapshot.resizes()) {
            NodeReservation state = activeByNode.get(resize.nodeId());
            if (state == null || !expectedAfterGrowth(resize).equals(state.resources())) {
                return false;
            }
        }
        return true;
    }

    private void validateExistingAllocationStates(
            PlacementSnapshot snapshot, Map<UUID, NodeReservation> activeByNode) {
        for (PlacementSnapshot.Allocation allocation : snapshot.allocations()) {
            NodeReservation state = activeByNode.get(allocation.nodeId());
            if (state != null
                    && state.createdByDeploymentId() != snapshot.problem().deploymentId()
                    && !"FAILED".equals(state.nodeStatus())) {
                throw new ReservationStateException(snapshot.problem().deploymentId());
            }
        }
    }

    private void validateResizeBefore(
            PlacementSnapshot snapshot, Map<UUID, NodeReservation> existingByNode) {
        for (PlacementSnapshot.Resize resize : snapshot.resizes()) {
            NodeReservation current = existingByNode.get(resize.nodeId());
            if (current == null || !resize.before().equals(current.resources())) {
                throw new IllegalStateException("Active reservation does not match resize.before for "
                        + resize.nodeId());
            }
        }
    }

    private void validateResizeHost(
            PlacementSnapshot snapshot, Map<UUID, NodeReservation> existingByNode) {
        for (PlacementSnapshot.Resize resize : snapshot.resizes()) {
            NodeReservation current = existingByNode.get(resize.nodeId());
            if (current == null || !"ACTIVE".equals(current.serverStatus())) {
                throw new PlacementFailureException(
                        "CONFLICT", "resize_host_not_active", "Resize host is not ACTIVE");
            }
        }
    }

    private void validateSolution(PlacementSnapshot snapshot, PlacementSolution solution) {
        if (solution == null || solution.deploymentId() != snapshot.problem().deploymentId()) {
            throw new IllegalStateException("Placement solver returned a result for another deployment");
        }
        Set<UUID> expected = snapshot.allocations().stream()
                .map(PlacementSnapshot.Allocation::nodeId)
                .collect(Collectors.toSet());
        Set<UUID> actual = solution.assignments().stream()
                .map(PlacementAssignment::nodeId)
                .collect(Collectors.toSet());
        if (expected.size() != snapshot.allocations().size()
                || !expected.equals(actual)
                || actual.size() != solution.assignments().size()) {
            throw new IllegalStateException("Placement solver returned an incomplete or duplicate assignment set");
        }
    }

    private int updateServerCounters(int serverId, int zoneId, ResourceVector increment) {
        return jdbcTemplate.update(
                UPDATE_SERVER_COUNTERS,
                increment.vcpu(),
                increment.ramMb(),
                increment.diskMb(),
                serverId,
                zoneId,
                increment.vcpu(),
                increment.ramMb(),
                increment.diskMb());
    }

    private void persistAllocations(
            long deploymentId,
            PlacementSnapshot snapshot,
            Map<UUID, Integer> serverByNode,
            Set<UUID> reusedAllocations) {
        for (PlacementSnapshot.Allocation allocation : snapshot.allocations()) {
            if (reusedAllocations.contains(allocation.nodeId())) {
                continue;
            }
            int serverId = serverByNode.get(allocation.nodeId());
            insertIfPositive(deploymentId, allocation.nodeId(), serverId, "vcpu", allocation.resources().vcpu());
            insertIfPositive(deploymentId, allocation.nodeId(), serverId, "vram_mb", allocation.resources().ramMb());
            insertIfPositive(deploymentId, allocation.nodeId(), serverId, "vdisk_mb", allocation.resources().diskMb());
        }
    }

    private void persistResizeGrowth(PlacementSnapshot snapshot) {
        for (PlacementSnapshot.Resize resize : snapshot.resizes()) {
            for (ResourceChange change : resourceChanges(resize.before(), resize.after())) {
                if (change.after() <= change.before()) {
                    continue;
                }
                int updated = jdbcTemplate.update(
                        UPDATE_RESIZE_RESERVATION,
                        change.after(),
                        resize.nodeId(),
                        change.resource(),
                        change.before());
                if (updated != 1) {
                    throw new IllegalStateException("Could not update active resize reservation for "
                            + resize.nodeId());
                }
            }
        }
    }

    private void insertIfPositive(
            long deploymentId, UUID nodeId, int serverId, String resource, long amount) {
        if (amount > 0) {
            jdbcTemplate.update(INSERT_RESERVATION, serverId, nodeId, deploymentId, resource, amount);
        }
    }

    private ResourceVector rollbackResizeRows(
            PlacementSnapshot.Resize resize, NodeReservation state) {
        ResourceVector restored = new ResourceVector(0, 0, 0);
        for (ResourceChange change : resourceChanges(resize.before(), resize.after())) {
            if (change.after() <= change.before()) {
                continue;
            }
            long currentAmount = state == null ? 0 : state.value(change.resource());
            if (currentAmount == change.before()) {
                continue;
            }
            if (currentAmount != change.after()) {
                throw new IllegalStateException("Resize reservation has an unexpected value during rollback: "
                        + resize.nodeId());
            }
            if (change.before() > 0) {
                int updated = jdbcTemplate.update(
                        UPDATE_RESIZE_RESERVATION,
                        change.before(),
                        resize.nodeId(),
                        change.resource(),
                        change.after());
                if (updated != 1) {
                    throw new IllegalStateException("Could not restore resize reservation for " + resize.nodeId());
                }
            } else {
                int updated = jdbcTemplate.update(
                        RELEASE_NODE_RESERVATION,
                        resize.nodeId(),
                        change.resource(),
                        change.after());
                if (updated != 1) {
                    throw new IllegalStateException("Could not release resize reservation for " + resize.nodeId());
                }
            }
            restored = restored.add(ResourceVector.of(change.resource(), change.after() - change.before()));
        }
        return restored;
    }

    private List<ReservationChange> releaseNode(UUID nodeId) {
        return jdbcTemplate.query(
                """
                UPDATE slices.reserva_nodo
                SET estado = 'RELEASED', fecha_liberacion = now()
                WHERE nodo_id = ? AND estado = 'RESERVED'
                RETURNING servidor_id, recurso, cantidad_reservada
                """,
                (rs, row) -> new ReservationChange(
                        rs.getInt("servidor_id"), rs.getString("recurso"), rs.getLong("cantidad_reservada")),
                nodeId);
    }

    private ResourceVector reservationVector(List<ReservationChange> rows, int expectedServerId) {
        ResourceVector result = new ResourceVector(0, 0, 0);
        for (ReservationChange row : rows) {
            if (row.serverId() != expectedServerId) {
                throw new IllegalStateException("Reservation resources span multiple servers");
            }
            result = result.add(ResourceVector.of(row.resource(), row.amount()));
        }
        return result;
    }

    private boolean isReservationValue(UUID nodeId, String resource, long amount) {
        Integer count = jdbcTemplate.queryForObject(
                """
                SELECT count(*) FROM slices.reserva_nodo
                WHERE nodo_id = ? AND recurso = ? AND estado = 'RESERVED'
                  AND cantidad_reservada = ?
                """,
                Integer.class,
                nodeId,
                resource,
                amount);
        return count != null && count == 1;
    }

    private boolean isReleasedReservation(UUID nodeId, String resource) {
        Integer count = jdbcTemplate.queryForObject(
                """
                SELECT count(*) FROM slices.reserva_nodo
                WHERE nodo_id = ? AND recurso = ? AND estado = 'RESERVED'
                """,
                Integer.class,
                nodeId,
                resource);
        return count != null && count == 0;
    }

    private void decrementCounters(Map<Integer, ResourceVector> decrements) {
        for (Map.Entry<Integer, ResourceVector> entry : decrements.entrySet()) {
            ResourceVector amount = entry.getValue();
            if (amount.isZero()) {
                continue;
            }
            int updated = jdbcTemplate.update(
                    DECREMENT_SERVER_COUNTERS,
                    amount.vcpu(),
                    amount.ramMb(),
                    amount.diskMb(),
                    entry.getKey(),
                    amount.vcpu(),
                    amount.ramMb(),
                    amount.diskMb());
            if (updated != 1) {
                throw new IllegalStateException("Server reserved counters are inconsistent for server "
                        + entry.getKey());
            }
        }
    }

    private List<ExistingAllocation> loadDeploymentReservations(long deploymentId) {
        return jdbcTemplate.query(
                ACTIVE_DEPLOYMENT_RESERVATIONS,
                (rs, row) -> new ExistingAllocation(
                        rs.getObject("nodo_id", UUID.class),
                        rs.getInt("servidor_id"),
                        rs.getString("recurso"),
                        rs.getLong("cantidad_reservada"),
                        rs.getString("estado")),
                deploymentId);
    }

    private void validateReservationVector(
            List<ExistingAllocation> rows,
            ResourceVector expected,
            UUID nodeId,
            long deploymentId) {
        Map<String, Long> actual = rows.stream().collect(Collectors.toMap(
                ExistingAllocation::resource,
                ExistingAllocation::amount,
                (left, right) -> {
                    throw new IllegalStateException("Duplicate active reservation resource for node " + nodeId);
                }));
        Map<String, Long> expectedResources = new HashMap<>();
        if (expected.vcpu() > 0) {
            expectedResources.put("vcpu", expected.vcpu());
        }
        if (expected.ramMb() > 0) {
            expectedResources.put("vram_mb", expected.ramMb());
        }
        if (expected.diskMb() > 0) {
            expectedResources.put("vdisk_mb", expected.diskMb());
        }
        if (!expectedResources.equals(actual)) {
            throw new ReservationStateException(deploymentId);
        }
    }

    private List<ResourceChange> resourceChanges(ResourceVector before, ResourceVector after) {
        return List.of(
                new ResourceChange("vcpu", before.vcpu(), after.vcpu()),
                new ResourceChange("vram_mb", before.ramMb(), after.ramMb()),
                new ResourceChange("vdisk_mb", before.diskMb(), after.diskMb()));
    }

    private record ExistingAllocation(
            UUID nodeId, int serverId, String resource, long amount, String status) {
    }

    private record ReservationChange(int serverId, String resource, long amount) {
    }

    private record ResourceChange(String resource, long before, long after) {
    }

    private ResourceVector expectedAfterGrowth(PlacementSnapshot.Resize resize) {
        return new ResourceVector(
                resize.after().vcpu() > resize.before().vcpu()
                        ? resize.after().vcpu()
                        : resize.before().vcpu(),
                resize.after().ramMb() > resize.before().ramMb()
                        ? resize.after().ramMb()
                        : resize.before().ramMb(),
                resize.after().diskMb() > resize.before().diskMb()
                        ? resize.after().diskMb()
                        : resize.before().diskMb());
    }
}
