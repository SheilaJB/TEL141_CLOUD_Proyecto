package com.example.g3.placementservice.placement.snapshot;

import com.example.g3.placementservice.placement.application.PlacementFailureException;
import com.example.g3.placementservice.placement.domain.ExistingSliceVm;
import com.example.g3.placementservice.placement.domain.HostCapacity;
import com.example.g3.placementservice.placement.domain.NewVm;
import com.example.g3.placementservice.placement.domain.PlacementProblem;
import com.example.g3.placementservice.placement.domain.PlacementWeights;
import com.example.g3.placementservice.placement.domain.ResizedVm;
import com.example.g3.placementservice.placement.domain.ResourceVector;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import java.util.function.ToLongFunction;
import java.util.stream.Collectors;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

@Service
public class PlacementSnapshotServiceImpl implements PlacementSnapshotService {

    private static final String DEPLOYMENT_LOCK = """
            SELECT d.id, d.tipo, d.estado, d.target_version_id, s.id AS slice_id, s.zona_id
            FROM slices.deployment d
            JOIN slices.slice s ON s.id = d.slice_id
            WHERE d.id = ?
            FOR UPDATE OF d, s
            """;

    private static final String ZONE_LOCK = """
            SELECT id, cpu_allocation_ratio_default, ram_allocation_ratio_default,
                   disk_allocation_ratio_default
            FROM slices.zona_disponibilidad
            WHERE id = ?
            FOR UPDATE
            """;

    private static final String ALLOCATIONS = """
            SELECT (item->>'node_id')::uuid AS node_id,
                   (item->'resources'->>'vcpu')::bigint AS vcpu,
                   (item->'resources'->>'ram_mb')::bigint AS ram_mb,
                   (item->'resources'->>'disk_mb')::bigint AS disk_mb
            FROM slices.deployment d
            CROSS JOIN LATERAL jsonb_array_elements(
                COALESCE(d.plan #> '{reservation,allocate}', '[]'::jsonb)) item
            WHERE d.id = ?
            ORDER BY node_id
            """;

    private static final String RESIZES = """
            SELECT (item->>'node_id')::uuid AS node_id,
                   (item->'before'->>'vcpu')::bigint AS before_vcpu,
                   (item->'before'->>'ram_mb')::bigint AS before_ram_mb,
                   (item->'before'->>'disk_mb')::bigint AS before_disk_mb,
                   (item->'after'->>'vcpu')::bigint AS after_vcpu,
                   (item->'after'->>'ram_mb')::bigint AS after_ram_mb,
                   (item->'after'->>'disk_mb')::bigint AS after_disk_mb
            FROM slices.deployment d
            CROSS JOIN LATERAL jsonb_array_elements(
                COALESCE(d.plan #> '{reservation,resize}', '[]'::jsonb)) item
            WHERE d.id = ?
            ORDER BY node_id
            """;

    private static final String RELEASES = """
            SELECT (item->>'node_id')::uuid AS node_id
            FROM slices.deployment d
            CROSS JOIN LATERAL jsonb_array_elements(
                COALESCE(d.plan #> '{reservation,release}', '[]'::jsonb)) item
            WHERE d.id = ?
            ORDER BY node_id
            """;

    private static final String ACTIVE_HOSTS = """
            SELECT sv.id, sv.total_cpu_cores, sv.total_ram_mb, sv.total_disk_mb,
                   sv.vcpu_reserved, sv.vram_mb_reserved, sv.vdisk_mb_reserved,
                   z.cpu_allocation_ratio_default, z.ram_allocation_ratio_default,
                   z.disk_allocation_ratio_default
            FROM slices.servidor sv
            JOIN slices.zona_disponibilidad z ON z.id = sv.zona_id
            WHERE sv.zona_id = ? AND sv.estado = 'ACTIVE'
            ORDER BY sv.id
            FOR UPDATE OF sv
            """;

    private static final String ACTIVE_RESERVATIONS = """
            SELECT r.nodo_id, r.servidor_id, r.recurso, r.cantidad_reservada,
                   n.estado_nodo AS node_status,
                   r.createdby_deployment_id, sv.estado AS server_status, sv.zona_id
            FROM slices.reserva_nodo r
            JOIN slices.slice_nodo n ON n.id = r.nodo_id
            JOIN slices.servidor sv ON sv.id = r.servidor_id
            WHERE n.slice_id = ? AND r.estado = 'RESERVED'
            ORDER BY r.nodo_id, r.recurso
            FOR UPDATE OF r, n, sv
            """;

    private static final String ALLOCATION_NODES = """
            SELECT n.id
            FROM slices.slice_nodo n
            WHERE n.slice_id = ? AND n.defin_version_id = ?
              AND n.id IN (
                  SELECT (item->>'node_id')::uuid
                  FROM slices.deployment d
                  CROSS JOIN LATERAL jsonb_array_elements(
                      COALESCE(d.plan #> '{reservation,allocate}', '[]'::jsonb)) item
                  WHERE d.id = ?
              )
            """;

    private static final String PLAN_NODES = """
            SELECT n.id
            FROM slices.slice_nodo n
            WHERE n.slice_id = ?
              AND n.id IN (
                  SELECT (item->>'node_id')::uuid
                  FROM slices.deployment d
                  CROSS JOIN LATERAL jsonb_array_elements(
                      COALESCE(d.plan #> '{reservation,allocate}', '[]'::jsonb)) item
                  WHERE d.id = ?
                  UNION
                  SELECT (item->>'node_id')::uuid
                  FROM slices.deployment d
                  CROSS JOIN LATERAL jsonb_array_elements(
                      COALESCE(d.plan #> '{reservation,resize}', '[]'::jsonb)) item
                  WHERE d.id = ?
                  UNION
                  SELECT (item->>'node_id')::uuid
                  FROM slices.deployment d
                  CROSS JOIN LATERAL jsonb_array_elements(
                      COALESCE(d.plan #> '{reservation,release}', '[]'::jsonb)) item
                  WHERE d.id = ?
              )
            """;

    private static final String REFERENCE_FLAVOR = """
            SELECT vcpu, vram_mb, vdisk_mb, activo
            FROM slices.flavor
            WHERE name = ?
            """;

    private final JdbcTemplate jdbcTemplate;
    private final String referenceFlavor;
    private final BigDecimal consolidationThreshold;
    private final BigDecimal convexWeight;
    private final BigDecimal sliceWeightFraction;
    private final BigDecimal tieBreakWeight;
    private final boolean sliceReward;

    public PlacementSnapshotServiceImpl(
            JdbcTemplate jdbcTemplate,
            @Value("${placement.flavor-referencia:small}") String referenceFlavor,
            @Value("${placement.umbral-consolidacion-t:0.5}") BigDecimal consolidationThreshold,
            @Value("${placement.w2:1}") BigDecimal convexWeight,
            @Value("${placement.s4-fraccion-w1:0.1}") BigDecimal sliceWeightFraction,
            @Value("${placement.epsilon-desempate:0.001}") BigDecimal tieBreakWeight,
            @Value("${placement.s4-modo:PENALIZE}") String sliceMode) {
        this.jdbcTemplate = Objects.requireNonNull(jdbcTemplate, "jdbcTemplate");
        this.referenceFlavor = Objects.requireNonNull(referenceFlavor, "referenceFlavor");
        this.consolidationThreshold = consolidationThreshold;
        this.convexWeight = convexWeight;
        this.sliceWeightFraction = sliceWeightFraction;
        this.tieBreakWeight = tieBreakWeight;
        if (!"PENALIZE".equals(sliceMode) && !"REWARD".equals(sliceMode)) {
            throw new IllegalArgumentException("placement.s4-modo must be PENALIZE or REWARD");
        }
        this.sliceReward = "REWARD".equals(sliceMode);
        validateConfiguration();
    }

    @Override
    public PlacementSnapshot load(long deploymentId) {
        if (deploymentId <= 0) {
            throw new IllegalArgumentException("deploymentId must be positive");
        }
        var deployment = jdbcTemplate.query(
                DEPLOYMENT_LOCK,
                (rs, row) -> new DeploymentData(
                        rs.getInt("id"),
                        rs.getString("tipo"),
                        rs.getString("estado"),
                        rs.getInt("target_version_id"),
                        rs.getInt("slice_id"),
                        rs.getInt("zona_id")),
                deploymentId).stream().findFirst()
                .orElseThrow(() -> new IllegalArgumentException("Deployment not found: " + deploymentId));
        if (!"IN_PROGRESS".equals(deployment.status())) {
            throw new IllegalStateException("Placement requires an IN_PROGRESS deployment");
        }

        var zone = jdbcTemplate.query(
                ZONE_LOCK,
                (rs, row) -> new ZoneData(
                        rs.getInt("id"),
                        rs.getBigDecimal("cpu_allocation_ratio_default"),
                        rs.getBigDecimal("ram_allocation_ratio_default"),
                        rs.getBigDecimal("disk_allocation_ratio_default")),
                deployment.zoneId()).stream().findFirst()
                .orElseThrow(() -> new IllegalStateException("Availability zone not found: " + deployment.zoneId()));

        List<PlacementSnapshot.Allocation> allocations = jdbcTemplate.query(
                ALLOCATIONS,
                (rs, row) -> new PlacementSnapshot.Allocation(
                        rs.getObject("node_id", UUID.class),
                        new ResourceVector(
                                rs.getLong("vcpu"), rs.getLong("ram_mb"), rs.getLong("disk_mb"))),
                deploymentId);
        List<PlacementSnapshot.Resize> resizes = jdbcTemplate.query(
                RESIZES,
                (rs, row) -> new PlacementSnapshot.Resize(
                        rs.getObject("node_id", UUID.class),
                        new ResourceVector(
                                rs.getLong("before_vcpu"),
                                rs.getLong("before_ram_mb"),
                                rs.getLong("before_disk_mb")),
                        new ResourceVector(
                                rs.getLong("after_vcpu"),
                                rs.getLong("after_ram_mb"),
                                rs.getLong("after_disk_mb"))),
                deploymentId);
        List<UUID> releases = jdbcTemplate.query(
                RELEASES,
                (rs, row) -> rs.getObject("node_id", UUID.class),
                deploymentId);
        validatePlan(deployment, allocations, resizes, releases);

        List<HostCapacity> hosts = jdbcTemplate.query(
                ACTIVE_HOSTS,
                (rs, row) -> toHost(rs, zone),
                deployment.zoneId());
        List<PlacementSnapshot.ReservationRow> activeReservations = jdbcTemplate.query(
                ACTIVE_RESERVATIONS,
                (rs, row) -> new PlacementSnapshot.ReservationRow(
                        rs.getObject("nodo_id", UUID.class),
                        rs.getInt("servidor_id"),
                        rs.getString("recurso"),
                        rs.getLong("cantidad_reservada"),
                        rs.getInt("createdby_deployment_id"),
                        rs.getString("node_status"),
                        rs.getString("server_status"),
                        rs.getInt("zona_id")),
                deployment.sliceId());
        Set<UUID> allocationNodes = allocations.stream()
                .map(PlacementSnapshot.Allocation::nodeId)
                .collect(Collectors.toSet());
        Map<Integer, ResourceVector> failedNodeReservations = new HashMap<>();
        for (PlacementSnapshot.ReservationRow reservation : activeReservations) {
            if (allocationNodes.contains(reservation.nodeId())
                    && "FAILED".equals(reservation.nodeStatus())) {
                failedNodeReservations.merge(
                        reservation.serverId(),
                        ResourceVector.of(reservation.resource(), reservation.amount()),
                        ResourceVector::add);
            }
        }
        hosts = hosts.stream()
                .map(host -> new HostCapacity(
                        host.serverId(),
                        host.capacity(),
                        host.occupied().subtract(failedNodeReservations.getOrDefault(
                                host.serverId(), new ResourceVector(0, 0, 0)))))
                .toList();
        validatePlanNodes(deployment, deploymentId, allocations, resizes, releases);

        Map<UUID, PlacementSnapshot.NodeReservation> currentByNode =
                PlacementSnapshot.byNode(activeReservations);
        Set<UUID> releaseSet = Set.copyOf(releases);
        Set<UUID> allocationSet = allocations.stream()
                .map(PlacementSnapshot.Allocation::nodeId)
                .collect(Collectors.toUnmodifiableSet());
        List<ExistingSliceVm> members = currentByNode.entrySet().stream()
                .filter(entry -> !releaseSet.contains(entry.getKey()) && !allocationSet.contains(entry.getKey()))
                .map(entry -> new ExistingSliceVm(deployment.sliceId(), entry.getValue().serverId()))
                .distinct()
                .toList();

        List<NewVm> newVms = allocations.stream()
                .map(allocation -> new NewVm(
                        allocation.nodeId(), deployment.sliceId(), allocation.resources()))
                .toList();
        List<ResizedVm> growingVms = new ArrayList<>();
        for (PlacementSnapshot.Resize resize : resizes) {
            ResourceVector delta = resize.before().positiveDeltaTo(resize.after());
            if (!delta.isZero()) {
                PlacementSnapshot.NodeReservation current = currentByNode.get(resize.nodeId());
                if (current == null) {
                    throw new IllegalStateException("Resize has no active reservation: " + resize.nodeId());
                }
                growingVms.add(new ResizedVm(
                        resize.nodeId(), deployment.sliceId(), current.serverId(), delta));
            }
        }

        for (PlacementSnapshot.Resize resize : resizes) {
            PlacementSnapshot.NodeReservation current = currentByNode.get(resize.nodeId());
            if (current == null) {
                throw new IllegalStateException("Resize has no active reservation: " + resize.nodeId());
            }
            if (!"ACTIVE".equals(current.serverStatus())) {
                throw new PlacementFailureException(
                        "CONFLICT", "resize_host_not_active", "Resize host is not ACTIVE");
            }
        }
        PlacementWeights weights = newVms.isEmpty() && growingVms.isEmpty()
                ? new PlacementWeights(0, 0, 0, 0, sliceReward)
                : placementWeights(hosts);
        if (hosts.isEmpty() && (!newVms.isEmpty() || !growingVms.isEmpty())) {
            throw new PlacementFailureException(
                    "CONFLICT", "no_active_servers", "Availability zone has no ACTIVE servers");
        }
        PlacementProblem problem = new PlacementProblem(
                deploymentId,
                deployment.zoneId(),
                hosts,
                newVms,
                growingVms,
                members,
                weights);
        return new PlacementSnapshot(
                deployment.sliceId(),
                deployment.targetVersionId(),
                deployment.type(),
                problem,
                allocations,
                resizes,
                releases,
                activeReservations);
    }

    private HostCapacity toHost(java.sql.ResultSet rs, ZoneData zone) throws java.sql.SQLException {
        return new HostCapacity(
                rs.getInt("id"),
                new ResourceVector(
                        effective(rs.getInt("total_cpu_cores"), zone.cpuRatio()),
                        effective(rs.getInt("total_ram_mb"), zone.ramRatio()),
                        effective(rs.getInt("total_disk_mb"), zone.diskRatio())),
                new ResourceVector(
                        rs.getLong("vcpu_reserved"),
                        rs.getLong("vram_mb_reserved"),
                        rs.getLong("vdisk_mb_reserved")));
    }

    private long effective(long physical, BigDecimal ratio) {
        if (ratio == null || ratio.signum() <= 0) {
            throw new IllegalStateException("Availability-zone allocation ratios must be positive");
        }
        return BigDecimal.valueOf(physical)
                .multiply(ratio)
                .setScale(0, RoundingMode.FLOOR)
                .longValueExact();
    }

    private void validatePlan(DeploymentData deployment,
            List<PlacementSnapshot.Allocation> allocations,
            List<PlacementSnapshot.Resize> resizes,
            List<UUID> releases) {
        Set<UUID> ids = new HashSet<>();
        for (PlacementSnapshot.Allocation allocation : allocations) {
            if (!ids.add(allocation.nodeId())) {
                throw new IllegalStateException("Duplicate allocation for node " + allocation.nodeId());
            }
        }
        for (PlacementSnapshot.Resize resize : resizes) {
            if (!ids.add(resize.nodeId())) {
                throw new IllegalStateException("Node appears more than once in reservation plan: " + resize.nodeId());
            }
        }
        for (UUID nodeId : releases) {
            if (!ids.add(nodeId)) {
                throw new IllegalStateException("Node appears more than once in reservation plan: " + nodeId);
            }
        }
    }

    private void validatePlanNodes(
            DeploymentData deployment,
            long deploymentId,
            List<PlacementSnapshot.Allocation> allocations,
            List<PlacementSnapshot.Resize> resizes,
            List<UUID> releases) {
        Set<UUID> requested = new HashSet<>();
        allocations.forEach(item -> requested.add(item.nodeId()));
        resizes.forEach(item -> requested.add(item.nodeId()));
        requested.addAll(releases);
        Set<UUID> found = new HashSet<>(jdbcTemplate.query(
                PLAN_NODES,
                (rs, row) -> rs.getObject("id", UUID.class),
                deployment.sliceId(),
                deploymentId,
                deploymentId,
                deploymentId));
        if (!found.equals(requested)) {
            throw new IllegalStateException("Reservation plan nodes do not match slice inventory");
        }
        Set<UUID> targetNodes = new HashSet<>(jdbcTemplate.query(
                ALLOCATION_NODES,
                (rs, row) -> rs.getObject("id", UUID.class),
                deployment.sliceId(),
                deployment.targetVersionId(),
                deploymentId));
        if (!targetNodes.equals(allocations.stream()
                .map(PlacementSnapshot.Allocation::nodeId)
                .collect(Collectors.toSet()))) {
            throw new IllegalStateException("New allocation nodes do not match target version inventory");
        }
    }

    private PlacementWeights placementWeights(List<HostCapacity> hosts) {
        var reference = jdbcTemplate.query(
                REFERENCE_FLAVOR,
                (rs, row) -> new ReferenceFlavor(
                        rs.getLong("vcpu"),
                        rs.getLong("vram_mb"),
                        rs.getLong("vdisk_mb"),
                        rs.getBoolean("activo")),
                referenceFlavor).stream().findFirst()
                .orElseThrow(() -> new IllegalStateException(
                        "Reference flavor is missing: " + referenceFlavor));
        if (!reference.active()) {
            throw new IllegalStateException("Reference flavor is inactive: " + referenceFlavor);
        }
        if (hosts.isEmpty()) {
            return new PlacementWeights(0, scaled(convexWeight), 0, scaled(tieBreakWeight), sliceReward);
        }

        BigDecimal meanCpu = meanCapacity(hosts, ResourceVector::vcpu);
        BigDecimal meanRam = meanCapacity(hosts, ResourceVector::ramMb);
        BigDecimal meanDisk = meanCapacity(hosts, ResourceVector::diskMb);
        BigDecimal s = BigDecimal.valueOf(reference.vcpu()).divide(meanCpu, 18, RoundingMode.HALF_UP)
                .add(BigDecimal.valueOf(reference.ramMb()).divide(meanRam, 18, RoundingMode.HALF_UP))
                .add(BigDecimal.valueOf(reference.diskMb()).divide(meanDisk, 18, RoundingMode.HALF_UP));
        long openHostCost = scaled(
                consolidationThreshold.multiply(convexWeight).multiply(BigDecimal.valueOf(2)).multiply(s));
        long sliceCost = scaled(sliceWeightFraction.multiply(BigDecimal.valueOf(openHostCost)
                .divide(BigDecimal.valueOf(1000), 18, RoundingMode.HALF_UP)));
        return new PlacementWeights(
                openHostCost,
                scaled(convexWeight),
                sliceCost,
                scaled(tieBreakWeight),
                sliceReward);
    }

    private BigDecimal meanCapacity(List<HostCapacity> hosts, ToLongFunction<ResourceVector> resource) {
        long total = 0;
        for (HostCapacity host : hosts) {
            total = Math.addExact(total, resource.applyAsLong(host.capacity()));
        }
        if (total <= 0) {
            throw new IllegalStateException("Effective host capacity must be positive");
        }
        return BigDecimal.valueOf(total).divide(BigDecimal.valueOf(hosts.size()), 18, RoundingMode.HALF_UP);
    }

    private long scaled(BigDecimal weight) {
        return weight.multiply(BigDecimal.valueOf(1000))
                .setScale(0, RoundingMode.HALF_UP)
                .longValueExact();
    }

    private void validateConfiguration() {
        if (consolidationThreshold == null || consolidationThreshold.signum() <= 0
                || consolidationThreshold.compareTo(BigDecimal.ONE) > 0) {
            throw new IllegalArgumentException("placement.umbral-consolidacion-t must be in (0, 1]");
        }
        if (convexWeight == null || convexWeight.signum() <= 0) {
            throw new IllegalArgumentException("placement.w2 must be positive");
        }
        if (sliceWeightFraction == null || sliceWeightFraction.signum() < 0
                || sliceWeightFraction.compareTo(BigDecimal.ONE) >= 0) {
            throw new IllegalArgumentException("placement.s4-fraccion-w1 must be in [0, 1)");
        }
        if (tieBreakWeight == null || tieBreakWeight.signum() < 0) {
            throw new IllegalArgumentException("placement.epsilon-desempate cannot be negative");
        }
    }

    private record DeploymentData(
            int deploymentId,
            String type,
            String status,
            int targetVersionId,
            int sliceId,
            int zoneId) {
    }

    private record ZoneData(int id, BigDecimal cpuRatio, BigDecimal ramRatio, BigDecimal diskRatio) {
    }

    private record ReferenceFlavor(long vcpu, long ramMb, long diskMb, boolean active) {
    }
}
