package com.example.g3.placementservice.placement.application;

import com.example.g3.placementservice.placement.reservation.ResourceReservationService;
import java.util.Objects;
import org.springframework.stereotype.Service;

@Service
public class PlacementServiceImpl implements PlacementService {

    private final ResourceReservationService reservationService;

    public PlacementServiceImpl(ResourceReservationService reservationService) {
        this.reservationService = Objects.requireNonNull(reservationService, "reservationService");
    }

    @Override
    public PlacementReserveResponse reserve(long deploymentId) {
        return PlacementReserveResponse.from(
                deploymentId,
                reservationService.reserve(deploymentId).assignments());
    }

    @Override
    public void rollbackReservation(long deploymentId) {
        reservationService.rollbackReservation(deploymentId);
    }

    @Override
    public void finalizeReservation(long deploymentId) {
        reservationService.finalizeReservation(deploymentId);
    }
}
