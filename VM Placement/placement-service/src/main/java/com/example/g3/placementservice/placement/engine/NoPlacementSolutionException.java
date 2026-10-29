package com.example.g3.placementservice.placement.engine;

public class NoPlacementSolutionException extends IllegalStateException {

    public NoPlacementSolutionException(String message) {
        super(message);
    }

    public NoPlacementSolutionException(String message, Throwable cause) {
        super(message, cause);
    }
}