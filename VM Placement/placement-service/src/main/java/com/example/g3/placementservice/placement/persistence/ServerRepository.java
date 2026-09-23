package com.example.g3.placementservice.placement.persistence;

import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ServerRepository extends JpaRepository<ServerEntity, Integer> {
    List<ServerEntity> findByZonaIdAndEstado(Integer zonaId, String estado);
}