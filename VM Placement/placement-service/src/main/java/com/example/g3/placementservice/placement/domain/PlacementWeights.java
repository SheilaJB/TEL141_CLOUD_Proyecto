package com.example.g3.placementservice.placement.domain;

/**
 * Pesos del placement. zCpu y zRam son los cuantiles z(1 - epsilon) del placement
 * estocastico: P(carga > capacidad) <= epsilon  <=>  sum(mu) + z * sqrt(sum(sigma^2)) <= C.
 * uCpu y uRam son el uso medio: mu = u * flavor (u = 1 -> cada VM usa todo su flavor).
 * z = 0 y u = 1 equivalen al placement deterministico.
 */
public record PlacementWeights(
        long openHostCost,
        long convexCost,
        long sliceCost,
        long tieBreakCost,
        boolean sliceReward,
        double zCpu,
        double zRam,
        double uCpu,
        double uRam) {

    public PlacementWeights {
        if (openHostCost < 0 || convexCost < 0 || sliceCost < 0 || tieBreakCost < 0) {
            throw new IllegalArgumentException("Placement weights cannot be negative");
        }
        if (!(zCpu >= 0) || !(zRam >= 0) || Double.isInfinite(zCpu) || Double.isInfinite(zRam)) {
            throw new IllegalArgumentException("Stochastic z values must be finite and >= 0");
        }
        if (!(uCpu > 0 && uCpu <= 1) || !(uRam > 0 && uRam <= 1)) {
            throw new IllegalArgumentException("Mean usage u must be in (0, 1]");
        }
    }

    /** Pesos sin componente estocastica (z = 0, u = 1). */
    public PlacementWeights(
            long openHostCost,
            long convexCost,
            long sliceCost,
            long tieBreakCost,
            boolean sliceReward) {
        this(openHostCost, convexCost, sliceCost, tieBreakCost, sliceReward, 0.0, 0.0, 1.0, 1.0);
    }

    public PlacementWeights withStochastic(double zCpu, double zRam, double uCpu, double uRam) {
        return new PlacementWeights(
                openHostCost, convexCost, sliceCost, tieBreakCost, sliceReward, zCpu, zRam, uCpu, uRam);
    }
}
