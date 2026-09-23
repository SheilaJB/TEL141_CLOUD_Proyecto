package com.example.g3.placementservice.placement.engine;

import com.example.g3.placementservice.placement.domain.PlacementPlan;
import com.example.g3.placementservice.placement.domain.PlacementProblem;

public interface PlacementEngine {
    PlacementPlan solve(PlacementProblem problem);
}