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
@Table(name = "servidor", schema = "slices")
public class ServerEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Integer id;

    private Integer totalCpuCores;
    private Integer totalRamMb;
    private Integer totalDiskMb;
    private Integer vcpuReserved;
    private Integer vramMbReserved;
    private Integer vdiskMbReserved;
    private String estado;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "zona_id")
    private AvailabilityZoneEntity zona;

    protected ServerEntity() {
    }

    public Integer getId() {
        return id;
    }

    public Integer getTotalCpuCores() {
        return totalCpuCores;
    }

    public Integer getTotalRamMb() {
        return totalRamMb;
    }

    public Integer getTotalDiskMb() {
        return totalDiskMb;
    }

    public Integer getVcpuReserved() {
        return vcpuReserved;
    }

    public Integer getVramMbReserved() {
        return vramMbReserved;
    }

    public Integer getVdiskMbReserved() {
        return vdiskMbReserved;
    }

    public String getEstado() {
        return estado;
    }

    public AvailabilityZoneEntity getZona() {
        return zona;
    }
}