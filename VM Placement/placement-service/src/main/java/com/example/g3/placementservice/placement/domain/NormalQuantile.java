package com.example.g3.placementservice.placement.domain;

/**
 * Cuantil de la normal estandar (inversa de la CDF), aproximacion de Acklam
 * (error relativo < 1.15e-9). Se usa para obtener z(1 - epsilon) del placement estocastico.
 * Ejemplos: epsilon = 0.05 -> z ~ 1.645 ; epsilon = 0.01 -> z ~ 2.326.
 */
public final class NormalQuantile {

    private static final double[] A = {
            -3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
            1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00};
    private static final double[] B = {
            -5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
            6.680131188771972e+01, -1.328068155288572e+01};
    private static final double[] C = {
            -7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
            -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00};
    private static final double[] D = {
            7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
            3.754408661907416e+00};
    private static final double P_LOW = 0.02425;

    private NormalQuantile() {
    }

    /** z tal que P(Z <= z) = p, para 0 < p < 1. */
    public static double inverseCdf(double p) {
        if (!(p > 0 && p < 1)) {
            throw new IllegalArgumentException("p must be in (0, 1)");
        }
        if (p < P_LOW) {
            double q = Math.sqrt(-2 * Math.log(p));
            return (((((C[0] * q + C[1]) * q + C[2]) * q + C[3]) * q + C[4]) * q + C[5])
                    / ((((D[0] * q + D[1]) * q + D[2]) * q + D[3]) * q + 1);
        }
        if (p > 1 - P_LOW) {
            return -inverseCdf(1 - p);
        }
        double q = p - 0.5;
        double r = q * q;
        return (((((A[0] * r + A[1]) * r + A[2]) * r + A[3]) * r + A[4]) * r + A[5]) * q
                / (((((B[0] * r + B[1]) * r + B[2]) * r + B[3]) * r + B[4]) * r + 1);
    }

    /** z(1 - epsilon): cuantos sigmas de margen para que P(overflow) <= epsilon. */
    public static double zForOverflow(double epsilon) {
        if (!(epsilon > 0 && epsilon < 0.5)) {
            throw new IllegalArgumentException("epsilon must be in (0, 0.5)");
        }
        return inverseCdf(1 - epsilon);
    }
}
