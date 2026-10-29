package com.example.g3.placementservice.placement.application;

public interface PlacementService {
    PlacementReserveResponse reserve(long deploymentId);

    void rollbackReservation(long deploymentId);

    void finalizeReservation(long deploymentId);
}