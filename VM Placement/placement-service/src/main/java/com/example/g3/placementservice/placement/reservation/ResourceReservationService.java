package com.example.g3.placementservice.placement.reservation;

import com.example.g3.placementservice.placement.domain.PlacementPlan;
import com.example.g3.placementservice.placement.domain.PlacementResult;

public interface ResourceReservationService {
    PlacementResult reserve(Integer sliceVersionId, PlacementPlan plan);
}