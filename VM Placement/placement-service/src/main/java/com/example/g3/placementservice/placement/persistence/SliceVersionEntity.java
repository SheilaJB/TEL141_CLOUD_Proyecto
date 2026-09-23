package com.example.g3.placementservice.placement.persistence;

import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.OneToMany;
import jakarta.persistence.Table;
import java.util.List;

@Entity
@Table(name = "slice_version", schema = "slices")
public class SliceVersionEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Integer id;

    @OneToMany(mappedBy = "sliceVersion", fetch = FetchType.LAZY)
    private List<SliceNodeEntity> nodes;

    protected SliceVersionEntity() {
    }

    public Integer getId() {
        return id;
    }

    public List<SliceNodeEntity> getNodes() {
        return nodes;
    }
}