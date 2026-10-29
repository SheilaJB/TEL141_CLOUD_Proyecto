package com.example.g3.placementservice.placement.activity;

import com.example.g3.placementservice.placement.application.PlacementReserveResponse;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

@ActivityInterface
public interface PlacementTemporalActivities {

    @ActivityMethod(name = "reserve")
    PlacementReserveResponse reserve(long deploymentId);

    @ActivityMethod(name = "rollback_reservation")
    void rollbackReservation(long deploymentId);

    @ActivityMethod(name = "finalize_reservation")
    void finalizeReservation(long deploymentId);
}
