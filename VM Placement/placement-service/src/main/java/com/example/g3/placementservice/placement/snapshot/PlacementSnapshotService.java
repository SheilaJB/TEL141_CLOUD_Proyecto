package com.example.g3.placementservice.placement.snapshot;

public interface PlacementSnapshotService {
    PlacementSnapshot load(long deploymentId);
}