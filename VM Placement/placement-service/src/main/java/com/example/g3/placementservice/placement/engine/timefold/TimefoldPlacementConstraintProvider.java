package com.example.g3.placementservice.placement.engine.timefold;

import ai.timefold.solver.core.api.score.HardSoftScore;
import ai.timefold.solver.core.api.score.stream.Constraint;
import ai.timefold.solver.core.api.score.stream.ConstraintCollectors;
import ai.timefold.solver.core.api.score.stream.ConstraintFactory;
import ai.timefold.solver.core.api.score.stream.ConstraintProvider;
import org.springframework.stereotype.Component;

@Component
public class TimefoldPlacementConstraintProvider implements ConstraintProvider {

    @Override
    public Constraint[] defineConstraints(ConstraintFactory factory) {
        // TODO: cuando PlacementConstraints tenga campos reales (pesos, afinidad, etc.),
        // leerlos acá vía problem/solution para parametrizar pesos o activar/desactivar constraints.
        return new Constraint[] {
                vmSinAsignar(factory),
                capacidadCpuExcedida(factory),
                capacidadRamExcedida(factory),
                capacidadDiscoExcedida(factory),
                minimizarWorkersUsados(factory)
        };
    }

    // Hard: toda VM debe quedar asignada a un worker.
    Constraint vmSinAsignar(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> vm.getWorker() == null)
                .penalize(HardSoftScore.ONE_HARD)
                .asConstraint("VM sin worker asignado");
    }

    // Hard: no exceder la CPU efectiva del worker.
    Constraint capacidadCpuExcedida(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> vm.getWorker() != null)
                .groupBy(TimefoldVm::getWorker, ConstraintCollectors.sum(vm -> vm.getResources().cpu()))
                .filter((worker, cpuSolicitada) ->
                        worker.getAllocated().cpu() + cpuSolicitada > worker.getCapacity().cpu())
                .penalize(HardSoftScore.ONE_HARD,
                        (worker, cpuSolicitada) -> Math.toIntExact(
                                worker.getAllocated().cpu() + cpuSolicitada - worker.getCapacity().cpu()))
                .asConstraint("Capacidad de CPU excedida");
    }

    // Hard: no exceder la RAM efectiva del worker.
    Constraint capacidadRamExcedida(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> vm.getWorker() != null)
                .groupBy(TimefoldVm::getWorker, ConstraintCollectors.sum(vm -> vm.getResources().ram()))
                .filter((worker, ramSolicitada) ->
                        worker.getAllocated().ram() + ramSolicitada > worker.getCapacity().ram())
                .penalize(HardSoftScore.ONE_HARD,
                        (worker, ramSolicitada) -> Math.toIntExact(
                                worker.getAllocated().ram() + ramSolicitada - worker.getCapacity().ram()))
                .asConstraint("Capacidad de RAM excedida");
    }

    // Hard: no exceder el disco efectivo del worker.
    Constraint capacidadDiscoExcedida(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> vm.getWorker() != null)
                .groupBy(TimefoldVm::getWorker, ConstraintCollectors.sum(vm -> vm.getResources().disk()))
                .filter((worker, discoSolicitado) ->
                        worker.getAllocated().disk() + discoSolicitado > worker.getCapacity().disk())
                .penalize(HardSoftScore.ONE_HARD,
                        (worker, discoSolicitado) -> Math.toIntExact(
                                worker.getAllocated().disk() + discoSolicitado - worker.getCapacity().disk()))
                .asConstraint("Capacidad de disco excedida");
    }

    // Soft: preferir usar la menor cantidad de workers distintos (consolidación).
    Constraint minimizarWorkersUsados(ConstraintFactory factory) {
        return factory.forEach(TimefoldVm.class)
                .filter(vm -> vm.getWorker() != null)
                .groupBy(TimefoldVm::getWorker)
                .penalize(HardSoftScore.ONE_SOFT)
                .asConstraint("Minimizar workers usados");
    }
}