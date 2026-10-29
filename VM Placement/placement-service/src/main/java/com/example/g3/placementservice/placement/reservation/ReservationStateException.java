package com.example.g3.placementservice.placement.reservation;

public class ReservationStateException extends RuntimeException {

    public ReservationStateException(long deploymentId) {
        super("Existing reservations do not match the requested placement for deployment: "
                + deploymentId);
    }
}