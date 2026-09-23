package com.example.g3.placementservice.placement.reservation;

public class ReservationStateException extends RuntimeException {

    public ReservationStateException(Integer sliceVersionId) {
        super("Existing reservations do not match the requested placement for slice version: "
                + sliceVersionId);
    }
}