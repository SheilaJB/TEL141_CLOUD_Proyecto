package com.example.g3.placementservice;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.example.g3.placementservice.placement.application.PlacementService;
import com.example.g3.placementservice.placement.domain.PlacementResult;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * Prueba manual de integracion contra PostgreSQL con la data dummy preparada.
 * No representa todavia una prueba aislada o portable para CI.
 */
@SpringBootTest
class PlacementIntegrationTest {

    // IDs de prueba; se pueden cambiar sin editar el test:
    // -Dplacement.test.slice-version-id=... -Dplacement.test.zone-id=...
    @Value("${placement.test.slice-version-id:1}")
    private Integer sliceVersionId;

    @Value("${placement.test.zone-id:1}")
    private Integer zoneId;

    @Autowired
    private PlacementService placementService;

    @Test
    void placesDummySliceVersionAndIsIdempotentOnRetry() {
        // Disparador de prueba: ejecuta snapshot -> Timefold -> reserva sobre la DB dummy.
        PlacementResult firstResult = placementService.place(sliceVersionId, zoneId);

        assertTrue(firstResult.successful());
        assertEquals(sliceVersionId, firstResult.sliceVersionId());
        assertFalse(firstResult.assignments().isEmpty());

        // Segundo disparo de prueba: simula un retry despues del commit.
        PlacementResult retryResult = placementService.place(sliceVersionId, zoneId);

        assertTrue(retryResult.successful());
        assertEquals(firstResult.assignments(), retryResult.assignments());
    }
}