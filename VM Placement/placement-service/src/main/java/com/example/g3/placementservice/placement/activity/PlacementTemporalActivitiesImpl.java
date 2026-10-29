package com.example.g3.placementservice.placement.activity;

import com.example.g3.placementservice.placement.application.PlacementReserveResponse;
import com.example.g3.placementservice.placement.application.PlacementFailureException;
import com.example.g3.placementservice.placement.application.PlacementService;
import com.example.g3.placementservice.placement.reservation.ReservationStateException;
import io.temporal.failure.ApplicationFailure;
import java.util.Objects;
import org.springframework.stereotype.Component;

@Component
public class PlacementTemporalActivitiesImpl implements PlacementTemporalActivities {

    private final PlacementService placementService;

    public PlacementTemporalActivitiesImpl(PlacementService placementService) {
        this.placementService = Objects.requireNonNull(placementService, "placementService");
    }

    @Override
    public PlacementReserveResponse reserve(long deploymentId) {
        try {
            return placementService.reserve(deploymentId);
        } catch (PlacementFailureException exception) {
            throw ApplicationFailure.newNonRetryableFailure(
                    exception.getMessage(), exception.code(), exception.reason());
        } catch (ReservationStateException | IllegalStateException | IllegalArgumentException exception) {
            //LOGGER.error("Placement reserve failed for deployment {}", deploymentId, exception);
            throw ApplicationFailure.newNonRetryableFailure(
                    exception.getMessage(), "PLACEMENT_INTERNAL_ERROR");
        }
    }

    @Override
    public void rollbackReservation(long deploymentId) {
        try {
            placementService.rollbackReservation(deploymentId);
        } catch (ReservationStateException | IllegalStateException | IllegalArgumentException exception) {
            //LOGGER.error("Placement rollback failed for deployment {}", deploymentId, exception);
            throw ApplicationFailure.newNonRetryableFailure(
                    exception.getMessage(), "PLACEMENT_INTERNAL_ERROR");
        }
    }

    @Override
    public void finalizeReservation(long deploymentId) {
        try {
            placementService.finalizeReservation(deploymentId);
        } catch (ReservationStateException | IllegalStateException | IllegalArgumentException exception) {
            //LOGGER.error("Placement finalize failed for deployment {}", deploymentId, exception);
            throw ApplicationFailure.newNonRetryableFailure(
                    exception.getMessage(), "PLACEMENT_INTERNAL_ERROR");
        }
    }
}
