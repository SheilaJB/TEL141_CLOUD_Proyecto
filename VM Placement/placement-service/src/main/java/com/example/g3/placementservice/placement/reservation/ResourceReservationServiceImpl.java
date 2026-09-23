package com.example.g3.placementservice.placement.reservation;

import com.example.g3.placementservice.placement.domain.Assignment;
import com.example.g3.placementservice.placement.domain.PlacementPlan;
import com.example.g3.placementservice.placement.domain.PlacementResult;
import com.example.g3.placementservice.placement.domain.ResourceAmount;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ResourceReservationServiceImpl implements ResourceReservationService {

    private static final String UPDATE_SERVER_RESOURCES = """
            UPDATE slices.servidor servidor
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

    private static final String INSERT_RESERVATION = """
            INSERT INTO slices.reserva_nodo
                (servidor_id, nodo_id, slice_version_id, recurso, cantidad_reservada)
            VALUES (?, ?, ?, ?, ?)
            """;

        private static final String FIND_ACTIVE_RESERVATIONS = """
                        SELECT nodo_id,
                                     servidor_id,
                                     COALESCE(SUM(CASE WHEN recurso = 'vcpu' THEN cantidad_reservada ELSE 0 END), 0) AS cpu,
                                     COALESCE(SUM(CASE WHEN recurso = 'vram_mb' THEN cantidad_reservada ELSE 0 END), 0) AS ram,
                                     COALESCE(SUM(CASE WHEN recurso = 'vdisk_mb' THEN cantidad_reservada ELSE 0 END), 0) AS disk
                        FROM slices.reserva_nodo
                        WHERE slice_version_id = ?
                            AND estado = 'RESERVED'
                        GROUP BY nodo_id, servidor_id
                        ORDER BY nodo_id
                        """;

    private final JdbcTemplate jdbcTemplate;

    public ResourceReservationServiceImpl(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    @Override
    @Transactional
    public PlacementResult reserve(Integer sliceVersionId, PlacementPlan plan) {
        Objects.requireNonNull(sliceVersionId, "sliceVersionId");
        Objects.requireNonNull(plan, "plan");
        if (!sliceVersionId.equals(plan.sliceVersionId())) {
            throw new IllegalArgumentException("Plan belongs to another slice version");
        }

        List<Assignment> existingAssignments = findExistingAssignments(sliceVersionId);
        if (!existingAssignments.isEmpty()) {
            if (!sameAssignments(existingAssignments, plan.assignments())) {
                throw new ReservationStateException(sliceVersionId);
            }
            return PlacementResult.confirmed(sliceVersionId, existingAssignments);
        }

        Map<Integer, ResourceAmount> resourcesByServer = aggregateByServer(plan);
        for (Map.Entry<Integer, ResourceAmount> entry : resourcesByServer.entrySet()) {
            ResourceAmount resources = entry.getValue();
            int updated = jdbcTemplate.update(
                    UPDATE_SERVER_RESOURCES,
                    resources.cpu(),
                    resources.ram(),
                    resources.disk(),
                    entry.getKey(),
                    plan.zonaId(),
                    resources.cpu(),
                    resources.ram(),
                    resources.disk());
            if (updated != 1) {
                throw new ReservationConflictException(entry.getKey());
            }
        }

        for (Assignment assignment : plan.assignments()) {
            insertReservation(sliceVersionId, assignment, "vcpu", assignment.resources().cpu());
            insertReservation(sliceVersionId, assignment, "vram_mb", assignment.resources().ram());
            insertReservation(sliceVersionId, assignment, "vdisk_mb", assignment.resources().disk());
        }

        return PlacementResult.confirmed(sliceVersionId, plan.assignments());
    }

    private List<Assignment> findExistingAssignments(Integer sliceVersionId) {
        return jdbcTemplate.query(
                FIND_ACTIVE_RESERVATIONS,
                (resultSet, rowNumber) -> new Assignment(
                        resultSet.getInt("nodo_id"),
                        resultSet.getInt("servidor_id"),
                        new ResourceAmount(
                                resultSet.getLong("cpu"),
                                resultSet.getLong("ram"),
                                resultSet.getLong("disk"))),
                sliceVersionId);
    }

    private boolean sameAssignments(List<Assignment> existing, List<Assignment> requested) {
        if (existing.size() != requested.size()) {
            return false;
        }
        Map<Integer, Assignment> existingByVm = existing.stream()
                .collect(java.util.stream.Collectors.toMap(Assignment::vmId, assignment -> assignment));
        return requested.stream().allMatch(assignment ->
                assignment.equals(existingByVm.get(assignment.vmId())));
    }

    private Map<Integer, ResourceAmount> aggregateByServer(PlacementPlan plan) {
        Map<Integer, ResourceAmount> resourcesByServer = new LinkedHashMap<>();
        for (Assignment assignment : plan.assignments()) {
            resourcesByServer.merge(
                    assignment.serverId(),
                    assignment.resources(),
                    ResourceAmount::add);
        }
        return resourcesByServer;
    }

    private void insertReservation(
            Integer sliceVersionId,
            Assignment assignment,
            String resource,
            long amount) {
        if (amount > 0) {
            jdbcTemplate.update(
                    INSERT_RESERVATION,
                    assignment.serverId(),
                    assignment.vmId(),
                    sliceVersionId,
                    resource,
                    amount);
        }
    }
}