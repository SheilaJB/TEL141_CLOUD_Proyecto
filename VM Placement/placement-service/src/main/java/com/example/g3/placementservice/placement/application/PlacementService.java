package com.example.g3.placementservice.placement.application;

import com.example.g3.placementservice.placement.domain.PlacementResult;

public interface PlacementService {
    PlacementResult place(Integer sliceVersionId, Integer zonaId);
}