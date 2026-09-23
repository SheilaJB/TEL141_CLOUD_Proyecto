package com.example.g3.placementservice.placement.application;

import com.example.g3.placementservice.placement.domain.PlacementPlan;
import com.example.g3.placementservice.placement.domain.PlacementResult;
import com.example.g3.placementservice.placement.engine.PlacementEngine;
import com.example.g3.placementservice.placement.reservation.ResourceReservationService;
import com.example.g3.placementservice.placement.snapshot.PlacementSnapshotService;
import java.util.Objects;
import org.springframework.stereotype.Service;

@Service
public class PlacementServiceImpl implements PlacementService {

    private final PlacementSnapshotService snapshotService;
    private final PlacementEngine placementEngine;
    private final ResourceReservationService reservationService;

    public PlacementServiceImpl(
            PlacementSnapshotService snapshotService,
            PlacementEngine placementEngine,
            ResourceReservationService reservationService) {
        this.snapshotService = Objects.requireNonNull(snapshotService, "snapshotService");
        this.placementEngine = Objects.requireNonNull(placementEngine, "placementEngine");
        this.reservationService = Objects.requireNonNull(reservationService, "reservationService");
    }

    @Override
    public PlacementResult place(Integer sliceVersionId, Integer zonaId) {
        Objects.requireNonNull(sliceVersionId, "sliceVersionId");
        Objects.requireNonNull(zonaId, "zonaId");

        var problem = snapshotService.loadProblem(sliceVersionId, zonaId);
        var plan = placementEngine.solve(problem);
        validatePlanIdentity(sliceVersionId, zonaId, plan);

        return reservationService.reserve(sliceVersionId, plan);
    }

    private void validatePlanIdentity(Integer sliceVersionId, Integer zonaId, PlacementPlan plan) {
        if (plan == null) {
            throw new IllegalStateException("Placement engine returned no plan");
        }
        if (!sliceVersionId.equals(plan.sliceVersionId())) {
            throw new IllegalStateException("Placement plan belongs to another slice version");
        }
        if (!zonaId.equals(plan.zonaId())) {
            throw new IllegalStateException("Placement plan belongs to another availability zone");
        }
    }
}