package com.example.g3.placementservice.placement.snapshot;

import com.example.g3.placementservice.placement.domain.PlacementProblem;

public interface PlacementSnapshotService {
    PlacementProblem loadProblem(Integer sliceVersionId, Integer zonaId);
}