package com.example.g3.placementservice.placement.domain;

import java.util.List;
import java.util.Objects;

public record ClusterSnapshot(String cluster, List<WorkerSnapshot> workers) {

    public ClusterSnapshot {
        Objects.requireNonNull(cluster, "cluster");
        workers = List.copyOf(Objects.requireNonNull(workers, "workers"));
    }
}