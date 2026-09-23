package com.example.g3.placementservice.placement.persistence;

import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import java.math.BigDecimal;

@Entity
@Table(name = "zona_disponibilidad", schema = "slices")
public class AvailabilityZoneEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Integer id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "cluster_id")
    private ClusterEntity cluster;

    private String estado;
    private BigDecimal cpuAllocationRatioDefault;
    private BigDecimal ramAllocationRatioDefault;
    private BigDecimal diskAllocationRatioDefault;

    protected AvailabilityZoneEntity() {
    }

    public Integer getId() {
        return id;
    }

    public ClusterEntity getCluster() {
        return cluster;
    }

    public String getEstado() {
        return estado;
    }

    public BigDecimal getCpuAllocationRatioDefault() {
        return cpuAllocationRatioDefault;
    }

    public BigDecimal getRamAllocationRatioDefault() {
        return ramAllocationRatioDefault;
    }

    public BigDecimal getDiskAllocationRatioDefault() {
        return diskAllocationRatioDefault;
    }
}