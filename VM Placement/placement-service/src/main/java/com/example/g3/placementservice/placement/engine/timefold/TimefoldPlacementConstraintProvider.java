package com.example.g3.placementservice.placement.engine.timefold;

import ai.timefold.solver.core.api.score.HardSoftBigDecimalScore;
import ai.timefold.solver.core.api.score.stream.Constraint;
import ai.timefold.solver.core.api.score.stream.ConstraintCollectors;
import ai.timefold.solver.core.api.score.stream.ConstraintFactory;
import ai.timefold.solver.core.api.score.stream.ConstraintProvider;
import ai.timefold.solver.core.api.score.stream.Joiners;
import java.math.BigDecimal;
import java.math.MathContext;
import java.math.RoundingMode;
import org.springframework.stereotype.Component;

@Component
public class TimefoldPlacementConstraintProvider implements ConstraintProvider {

    @Override
    public Constraint[] defineConstraints(ConstraintFactory factory) {
        return new Constraint[] {
                vmSinWorker(factory),
                resizeCpuExcedida(factory),
                resizeRamExcedida(factory),
                resizeDiscoExcedido(factory),
                capacidadCpuExcedida(factory),
                capacidadRamExcedida(factory),
                capacidadDiscoExcedida(factory),
                techoCpuExcedido(factory),
                techoRamExcedido(factory),
                abrirHostVacio(factory),
                utilizacionConvexaCpu(factory),
                utilizacionConvexaRam(factory),
                utilizacionConvexaDisco(factory),
                desempateEstable(factory),
                distribucionSliceNuevas(factory),
                distribucionSliceExistentes(factory)
        };
    }

    Constraint vmSinWorker(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> vm.getWorker() == null)
                .penalize(HardSoftBigDecimalScore.ONE_HARD)
                .asConstraint("VM_SIN_WORKER");
    }

    Constraint resizeCpuExcedida(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> vm.isPinned() && vm.getWorker() != null)
                .groupBy(TimefoldVm::getWorker,
                        ConstraintCollectors.sumBigDecimal(vm -> BigDecimal.valueOf(vm.getResources().vcpu())))
                .filter((worker, load) -> worker.getOccupied().vcpu() + load.longValueExact()
                        > worker.getCapacity().vcpu())
                .penalizeBigDecimal(HardSoftBigDecimalScore.ONE_HARD,
                        (worker, load) -> BigDecimal.valueOf(
                                worker.getOccupied().vcpu() + load.longValueExact() - worker.getCapacity().vcpu()))
                .asConstraint("RESIZE_SIN_CAPACIDAD_CPU");
    }

    Constraint resizeRamExcedida(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> vm.isPinned() && vm.getWorker() != null)
                .groupBy(TimefoldVm::getWorker,
                        ConstraintCollectors.sumBigDecimal(vm -> BigDecimal.valueOf(vm.getResources().ramMb())))
                .filter((worker, load) -> worker.getOccupied().ramMb() + load.longValueExact()
                        > worker.getCapacity().ramMb())
                .penalizeBigDecimal(HardSoftBigDecimalScore.ONE_HARD,
                        (worker, load) -> BigDecimal.valueOf(
                                worker.getOccupied().ramMb() + load.longValueExact() - worker.getCapacity().ramMb()))
                .asConstraint("RESIZE_SIN_CAPACIDAD_RAM");
    }

    Constraint resizeDiscoExcedido(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> vm.isPinned() && vm.getWorker() != null)
                .groupBy(TimefoldVm::getWorker,
                        ConstraintCollectors.sumBigDecimal(vm -> BigDecimal.valueOf(vm.getResources().diskMb())))
                .filter((worker, load) -> worker.getOccupied().diskMb() + load.longValueExact()
                        > worker.getCapacity().diskMb())
                .penalizeBigDecimal(HardSoftBigDecimalScore.ONE_HARD,
                        (worker, load) -> BigDecimal.valueOf(
                                worker.getOccupied().diskMb() + load.longValueExact() - worker.getCapacity().diskMb()))
                .asConstraint("RESIZE_SIN_CAPACIDAD_DISCO");
    }

    /**
     * CPU estocastica (chance constraint): P(carga > C) <= epsilon.
     * Por el TCL la carga del host ~ N(sum(mu), sum(sigma^2)), entonces la condicion es
     * sum(mu) + z * sqrt(sum(sigma^2)) <= C - ocupado.
     */
    Constraint capacidadCpuExcedida(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> vm.getWorker() != null)
                .groupBy(TimefoldVm::getWorker,
                        ConstraintCollectors.sumBigDecimal(vm -> BigDecimal.valueOf(vm.getResources().vcpu())),
                        ConstraintCollectors.sumBigDecimal(vm -> BigDecimal.valueOf(vm.getCpuVariance())))
                .filter((worker, flavor, variance) -> !fitsStochastic(
                        free(worker.getCapacity().vcpu(), worker.getOccupied().vcpu(), worker.getWeights().uCpu()),
                        expected(flavor, worker.getWeights().uCpu()), variance, worker.getWeights().zCpu()))
                .penalizeBigDecimal(HardSoftBigDecimalScore.ONE_HARD,
                        (worker, flavor, variance) -> stochasticExcess(
                                free(worker.getCapacity().vcpu(), worker.getOccupied().vcpu(), worker.getWeights().uCpu()),
                                expected(flavor, worker.getWeights().uCpu()), variance, worker.getWeights().zCpu()))
                .asConstraint("CAPACIDAD_EXCEDIDA_NUEVA_CPU");
    }

    /**
     * RAM estocastica: (misma chance constraint que CPU ) con su propio z.
     */
    Constraint capacidadRamExcedida(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> vm.getWorker() != null)
                .groupBy(TimefoldVm::getWorker,
                        ConstraintCollectors.sumBigDecimal(vm -> BigDecimal.valueOf(vm.getResources().ramMb())),
                        ConstraintCollectors.sumBigDecimal(vm -> BigDecimal.valueOf(vm.getRamVariance())))
                .filter((worker, flavor, variance) -> !fitsStochastic(
                        free(worker.getCapacity().ramMb(), worker.getOccupied().ramMb(), worker.getWeights().uRam()),
                        expected(flavor, worker.getWeights().uRam()), variance, worker.getWeights().zRam()))
                .penalizeBigDecimal(HardSoftBigDecimalScore.ONE_HARD,
                        (worker, flavor, variance) -> stochasticExcess(
                                free(worker.getCapacity().ramMb(), worker.getOccupied().ramMb(), worker.getWeights().uRam()),
                                expected(flavor, worker.getWeights().uRam()), variance, worker.getWeights().zRam()))
                .asConstraint("CAPACIDAD_EXCEDIDA_NUEVA_RAM");
    }

    /**
     * Techo de sobrecompromiso: lo reservado (flavors) no puede pasar de fisico * ratio_limit.
     * Es el mismo limite que valida el SQL al reservar, asi el solver no propone algo que la BD rechace.
     */
    Constraint techoCpuExcedido(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> vm.getWorker() != null)
                .groupBy(TimefoldVm::getWorker,
                        ConstraintCollectors.sumBigDecimal(vm -> BigDecimal.valueOf(vm.getResources().vcpu())))
                .filter((worker, load) -> worker.getOccupied().vcpu() + load.longValueExact()
                        > worker.getCeiling().vcpu())
                .penalizeBigDecimal(HardSoftBigDecimalScore.ONE_HARD,
                        (worker, load) -> BigDecimal.valueOf(
                                worker.getOccupied().vcpu() + load.longValueExact() - worker.getCeiling().vcpu()))
                .asConstraint("TECHO_SOBRECOMPROMISO_CPU");
    }

    Constraint techoRamExcedido(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> vm.getWorker() != null)
                .groupBy(TimefoldVm::getWorker,
                        ConstraintCollectors.sumBigDecimal(vm -> BigDecimal.valueOf(vm.getResources().ramMb())))
                .filter((worker, load) -> worker.getOccupied().ramMb() + load.longValueExact()
                        > worker.getCeiling().ramMb())
                .penalizeBigDecimal(HardSoftBigDecimalScore.ONE_HARD,
                        (worker, load) -> BigDecimal.valueOf(
                                worker.getOccupied().ramMb() + load.longValueExact() - worker.getCeiling().ramMb()))
                .asConstraint("TECHO_SOBRECOMPROMISO_RAM");
    }

    Constraint capacidadDiscoExcedida(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> vm.getWorker() != null)
                .groupBy(TimefoldVm::getWorker,
                        ConstraintCollectors.sumBigDecimal(vm -> BigDecimal.valueOf(vm.getResources().diskMb())))
                .filter((worker, load) -> worker.getOccupied().diskMb() + load.longValueExact()
                        > worker.getCapacity().diskMb())
                .penalizeBigDecimal(HardSoftBigDecimalScore.ONE_HARD,
                        (worker, load) -> BigDecimal.valueOf(
                                worker.getOccupied().diskMb() + load.longValueExact() - worker.getCapacity().diskMb()))
                .asConstraint("CAPACIDAD_EXCEDIDA_NUEVA_DISCO");
    }

    Constraint abrirHostVacio(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> !vm.isPinned() && vm.getWorker() != null)
                .groupBy(TimefoldVm::getWorker, ConstraintCollectors.count())
                .filter((worker, count) -> worker.isEmpty())
                .penalizeBigDecimal(HardSoftBigDecimalScore.ONE_SOFT,
                        (worker, count) -> BigDecimal.valueOf(worker.getWeights().openHostCost()))
                .asConstraint("ABRIR_HOST_VACIO");
    }

    Constraint utilizacionConvexaCpu(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> vm.getWorker() != null)
                .groupBy(TimefoldVm::getWorker,
                        ConstraintCollectors.sumBigDecimal(vm -> BigDecimal.valueOf(vm.getResources().vcpu())))
                .penalizeBigDecimal(HardSoftBigDecimalScore.ONE_SOFT,
                        (worker, load) -> BigDecimal.valueOf(
                                worker.convexCpuCost(load.longValueExact(), worker.getWeights().convexCost())))
                .asConstraint("UTILIZACION_CONVEXA_CPU");
    }

    Constraint utilizacionConvexaRam(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> vm.getWorker() != null)
                .groupBy(TimefoldVm::getWorker,
                        ConstraintCollectors.sumBigDecimal(vm -> BigDecimal.valueOf(vm.getResources().ramMb())))
                .penalizeBigDecimal(HardSoftBigDecimalScore.ONE_SOFT,
                        (worker, load) -> BigDecimal.valueOf(
                                worker.convexRamCost(load.longValueExact(), worker.getWeights().convexCost())))
                .asConstraint("UTILIZACION_CONVEXA_RAM");
    }

    Constraint utilizacionConvexaDisco(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> vm.getWorker() != null)
                .groupBy(TimefoldVm::getWorker,
                        ConstraintCollectors.sumBigDecimal(vm -> BigDecimal.valueOf(vm.getResources().diskMb())))
                .penalizeBigDecimal(HardSoftBigDecimalScore.ONE_SOFT,
                        (worker, load) -> BigDecimal.valueOf(
                                worker.convexDiskCost(load.longValueExact(), worker.getWeights().convexCost())))
                .asConstraint("UTILIZACION_CONVEXA_DISCO");
    }

    Constraint desempateEstable(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> !vm.isPinned() && vm.getWorker() != null)
                .penalizeBigDecimal(HardSoftBigDecimalScore.ONE_SOFT,
                        vm -> BigDecimal.valueOf(vm.getWeights().tieBreakCost())
                                .multiply(BigDecimal.valueOf(vm.getWorker().getServerId())))
                .asConstraint("DESEMPATE_ESTABLE");
    }

    Constraint distribucionSliceNuevas(ConstraintFactory factory) {
        return factory.forEachUniquePair(
                        TimefoldVm.class,
                        Joiners.equal(TimefoldVm::getSliceId),
                        Joiners.equal(TimefoldVm::getWorker))
                .filter((left, right) -> !left.isPinned()
                        && !right.isPinned()
                        && left.getWorker() != null)
                .impactBigDecimal(HardSoftBigDecimalScore.ONE_SOFT,
                        (left, right) -> sliceImpact(left))
                .asConstraint("DISTRIBUCION_SLICE_NUEVAS");
    }

    Constraint distribucionSliceExistentes(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> !vm.isPinned() && vm.getWorker() != null)
                .join(TimefoldSliceMember.class,
                        Joiners.equal(TimefoldVm::getSliceId, TimefoldSliceMember::sliceId),
                        Joiners.equal(vm -> vm.getWorker().getServerId(), TimefoldSliceMember::serverId))
                .impactBigDecimal(HardSoftBigDecimalScore.ONE_SOFT,
                        (vm, member) -> sliceImpact(vm))
                .asConstraint("DISTRIBUCION_SLICE_EXISTENTES");
    }

    /**
     * Condicion estocastica sin raiz cuadrada:
     * con H = libre - sum(mu), cabe si H >= 0 y H^2 >= z^2 * sum(sigma^2).
     */
    static boolean fitsStochastic(BigDecimal free, BigDecimal mean, BigDecimal variance, double z) {
        BigDecimal headroom = free.subtract(mean);
        if (headroom.signum() < 0) {
            return false;
        }
        BigDecimal zSquared = BigDecimal.valueOf(z).multiply(BigDecimal.valueOf(z));
        return headroom.multiply(headroom).compareTo(zSquared.multiply(variance)) >= 0;
    }

    /** sum(mu) + z * sqrt(sum(sigma^2)) - libre */
    static BigDecimal stochasticExcess(BigDecimal free, BigDecimal mean, BigDecimal variance, double z) {
        BigDecimal margin = BigDecimal.valueOf(z).multiply(variance.sqrt(MathContext.DECIMAL64));
        BigDecimal excess = mean.add(margin).subtract(free)
                .setScale(0, RoundingMode.CEILING);
        return excess.max(BigDecimal.ONE);
    }

    /** Capacidad libre esperada: C - u * ocupado (lo ocupado tambien se usa en promedio u). */
    static BigDecimal free(long capacity, long occupied, double u) {
        return BigDecimal.valueOf(capacity).subtract(expected(BigDecimal.valueOf(occupied), u));
    }

    /** Uso medio esperado: mu = u * flavor. */
    static BigDecimal expected(BigDecimal flavor, double u) {
        return flavor.multiply(BigDecimal.valueOf(u));
    }

    private BigDecimal sliceImpact(TimefoldVm vm) {
        BigDecimal cost = BigDecimal.valueOf(vm.getWeights().sliceCost());
        return vm.getWeights().sliceReward() ? cost : cost.negate();
    }
}
