package com.example.g3.placementservice.placement.persistence;

import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;

@Entity
@Table(name = "slice_nodo", schema = "slices")
public class SliceNodeEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Integer id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "slice_version_id", nullable = false)
    private SliceVersionEntity sliceVersion;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "flavor_id", nullable = false)
    private FlavorEntity flavor;

    protected SliceNodeEntity() {
    }

    public Integer getId() {
        return id;
    }

    public FlavorEntity getFlavor() {
        return flavor;
    }
}