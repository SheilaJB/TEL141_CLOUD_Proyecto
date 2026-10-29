package com.example.g3.placementservice.placement.reservation;

import com.example.g3.placementservice.placement.domain.PlacementSolution;

public interface ResourceReservationService {
    PlacementSolution reserve(long deploymentId);

    void rollbackReservation(long deploymentId);

    void finalizeReservation(long deploymentId);
}