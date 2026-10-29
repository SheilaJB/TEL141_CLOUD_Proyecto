package com.example.g3.placementservice.placement.domain;

public record PlacementWeights(
        long openHostCost,
        long convexCost,
        long sliceCost,
        long tieBreakCost,
        boolean sliceReward) {

    public PlacementWeights {
        if (openHostCost < 0 || convexCost < 0 || sliceCost < 0 || tieBreakCost < 0) {
            throw new IllegalArgumentException("Placement weights cannot be negative");
        }
    }
}
