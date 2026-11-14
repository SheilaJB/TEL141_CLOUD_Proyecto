package com.example.g3.placementservice.placement.domain;

/**
 * Pesos del placement. zCpu y zRam son los cuantiles z(1 - epsilon) del placement
 * estocastico: P(carga > capacidad) <= epsilon  <=>  sum(mu) + z * sqrt(sum(sigma^2)) <= C.
 * z = 0 equivale al placement deterministico.
 */
public record PlacementWeights(
        long openHostCost,
        long convexCost,
        long sliceCost,
        long tieBreakCost,
        boolean sliceReward,
        double zCpu,
        double zRam) {

    public PlacementWeights {
        if (openHostCost < 0 || convexCost < 0 || sliceCost < 0 || tieBreakCost < 0) {
            throw new IllegalArgumentException("Placement weights cannot be negative");
        }
        if (!(zCpu >= 0) || !(zRam >= 0) || Double.isInfinite(zCpu) || Double.isInfinite(zRam)) {
            throw new IllegalArgumentException("Stochastic z values must be finite and >= 0");
        }
    }

    /** Pesos sin componente estocastica (z = 0). */
    public PlacementWeights(
            long openHostCost,
            long convexCost,
            long sliceCost,
            long tieBreakCost,
            boolean sliceReward) {
        this(openHostCost, convexCost, sliceCost, tieBreakCost, sliceReward, 0.0, 0.0);
    }

    public PlacementWeights withStochastic(double zCpu, double zRam) {
        return new PlacementWeights(openHostCost, convexCost, sliceCost, tieBreakCost, sliceReward, zCpu, zRam);
    }
}
