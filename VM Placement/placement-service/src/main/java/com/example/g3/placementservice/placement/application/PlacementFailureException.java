package com.example.g3.placementservice.placement.application;

public class PlacementFailureException extends RuntimeException {

    private final String code;
    private final String reason;

    public PlacementFailureException(String code, String reason, String message) {
        super(message);
        this.code = code;
        this.reason = reason;
    }

    public String code() {
        return code;
    }

    public String reason() {
        return reason;
    }
}
