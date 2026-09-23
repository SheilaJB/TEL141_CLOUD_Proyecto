package com.example.g3.placementservice.placement.domain;

public record PlacementConstraints() {
    public static PlacementConstraints none() {
        return new PlacementConstraints();
    }
}