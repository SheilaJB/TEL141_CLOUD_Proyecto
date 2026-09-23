package com.example.g3.placementservice.placement.persistence;

import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "flavor", schema = "slices")
public class FlavorEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Integer id;

    private Integer vcpu;
    private Integer vramMb;
    private Integer vdiskMb;

    protected FlavorEntity() {
    }

    public Integer getId() {
        return id;
    }

    public Integer getVcpu() {
        return vcpu;
    }

    public Integer getVramMb() {
        return vramMb;
    }

    public Integer getVdiskMb() {
        return vdiskMb;
    }
}