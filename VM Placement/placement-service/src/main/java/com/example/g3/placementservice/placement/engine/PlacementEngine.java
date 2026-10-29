package com.example.g3.placementservice.placement.engine;

import com.example.g3.placementservice.placement.domain.PlacementProblem;
import com.example.g3.placementservice.placement.domain.PlacementSolution;

public interface PlacementEngine {
    PlacementSolution solve(PlacementProblem problem);
}