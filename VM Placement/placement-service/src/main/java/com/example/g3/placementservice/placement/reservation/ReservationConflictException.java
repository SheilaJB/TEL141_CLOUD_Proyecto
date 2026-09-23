package com.example.g3.placementservice.placement.reservation;

public class ReservationConflictException extends RuntimeException {

    public ReservationConflictException(Integer serverId) {
        super("Resources are no longer available on server: " + serverId);
    }
}