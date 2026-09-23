# Modulo VM Placement para el Orquestador de Slices

Documento tecnico del modulo `placement-service`. Separa las decisiones del dominio de lo que existe actualmente en este repositorio.

## 1. Objetivo y limites

El modulo coloca las VMs nuevas de una `slice_version` en servidores fisicos de una zona de disponibilidad elegida previamente por el usuario o por el orquestador. La operacion solo tiene exito cuando la asignacion propuesta por el solver queda confirmada mediante una reserva atomica en PostgreSQL.

El modulo:

- recibe `sliceVersionId` y `zonaId`;
- lee un snapshot consistente de la base de datos;
- calcula la capacidad efectiva de los servidores de la zona;
- obtiene una propuesta mediante `PlacementEngine`;
- actualiza atomicamente los contadores de recursos de los servidores;
- escribe el detalle de auditoria en `slices.reserva_nodo`;
- devuelve las asignaciones confirmadas.

El modulo no:

- valida autenticacion, autorizacion, cuotas, nivel de usuario o acceso a la zona;
- valida estados de workflow o aprobaciones de negocio;
- cambia `slice_version.estado_version` ni `slice_nodo.estado_elemento`;
- despliega, configura o verifica VMs;
- llama Terraform, Ansible, OpenStack, libvirt o SSH;
- implementa Temporal ni una cola propia;
- libera recursos en esta primera version.

Las validaciones de negocio pertenecen al orquestador o a capas superiores. Temporal rodea este modulo y coordina el flujo global:

```text
Validate -> Place VMs -> Provision VMs -> Configure -> Verify -> Finalize
```

## 2. Decisiones confirmadas

### 2.1 Arquitectura

```text
Temporal (Workflow/Activity)
        |
        v
PlacementService
        |
        +--> PlacementSnapshotService (lectura PostgreSQL)
        +--> PlacementEngine (Timefold, encapsulado)
        +--> ResourceReservationService (transaccion PostgreSQL)
```

- VM Placement es un modulo interno, no el workflow completo.
- El engine esta detras de la interfaz `PlacementEngine`.
- El dominio no conoce clases de Timefold ni de otro solver.
- PostgreSQL es la fuente de verdad de recursos, asignaciones y auditoria.
- Temporal es la fuente de verdad de la ejecucion, retries y compensaciones.
- No se usa `BlockingQueue` ni retry manual dentro del modulo.
- Se mantienen las task queues de Temporal `placement-linux` y `placement-openstack` en el sistema superior.

### 2.2 Zona y capacidad

- `zonaId` entra como parametro de placement.
- La zona ya fue elegida y autorizada por una capa superior.
- Este modulo solo coloca en servidores de esa zona.
- Se usa `allocation_ratio_default` para el placement normal.
- `allocation_ratio_limit` es un techo administrativo y queda fuera del MVP.
- La capacidad efectiva se calcula al vuelo:

```text
CPU efectiva   = total_cpu_cores * cpu_allocation_ratio_default
RAM efectiva   = total_ram_mb    * ram_allocation_ratio_default
Disco efectivo = total_disk_mb  * disk_allocation_ratio_default
```

Los contadores actuales del servidor representan los recursos ya alojados:

```text
vcpu_reservado
vram_mb_reservado
vdisk_mb_reservado
```

El solver y la reserva deben usar la misma capacidad efectiva.

### 2.3 Reserva e idempotencia

Los contadores de `slices.servidor` son el estado operativo rapido. Las filas de `slices.reserva_nodo` son el detalle auditable. Ambos se actualizan en una sola transaccion.

El orden de la reserva es:

```text
1. Agrupar recursos del plan por servidor.
2. Ejecutar UPDATE condicional por cada servidor.
3. Si algun UPDATE afecta cero filas, hacer rollback.
4. Insertar las reservas de vCPU, RAM y disco por nodo.
5. Commit.
```

La reserva condicional valida servidor activo, zona correcta y capacidad efectiva suficiente. PostgreSQL es el arbitro ante carreras concurrentes.

Para retries, el servicio busca reservas activas existentes para la `slice_version`:

- si la reserva completa coincide con el plan, devuelve el placement existente sin volver a incrementar contadores;
- si existe una reserva parcial o distinta, informa una inconsistencia;
- si no existe reserva, ejecuta la transaccion normal.

La liberacion no pertenece al modulo inicial. La compensacion posterior a un fallo de provisioning queda en el orquestador o en otro modulo.

### 2.4 Estados

La base de datos contempla estados como `RESERVED`, `PROVISIONING` y `RUNNING`, pero este modulo no los modifica. El orquestador coordina sus transiciones:

```text
slice_version: DRAFT -> APPROVAL_PENDING -> RESERVED -> PROVISIONING -> RUNNING
slice_nodo:    PENDING -> RESERVED -> CREATED -> RUNNING
```

Estas transiciones son contexto del sistema, no responsabilidad de `PlacementService`.

### 2.5 Engine

Se eligio **Timefold 2.6.0** como primer engine por su integracion Java/Spring y porque permite modelar directamente VMs como entidades de planificacion y servidores como hechos de planificacion. El adapter queda aislado en `engine/timefold`.

La interfaz publica del engine es:

```java
public interface PlacementEngine {
    PlacementPlan solve(PlacementProblem problem);
}
```

El engine devuelve una propuesta. No escribe en PostgreSQL y no confirma por si mismo el placement.

## 3. Contrato del modulo

Entrada interna:

```json
{
  "sliceVersionId": 1,
  "zonaId": 1
}
```

Interfaz Java:

```java
PlacementResult place(Integer sliceVersionId, Integer zonaId);
```

Resultado normal:

```json
{
  "sliceVersionId": 1,
  "successful": true,
  "assignments": [
    { "vmId": 10, "serverId": 1 },
    { "vmId": 11, "serverId": 2 }
  ]
}
```

Un resultado exitoso significa que los contadores y las filas de auditoria ya fueron confirmados por PostgreSQL. El modulo no devuelve estados de workflow.

## 4. Flujo completo

```text
sliceVersionId + zonaId
        |
        v
PlacementServiceImpl
        |
        +--> PlacementSnapshotService.loadProblem(...)
        |          |
        |          v
        |      PlacementProblem
        |
        +--> PlacementEngine.solve(problem)
        |          |
        |          v
        |      PlacementPlan
        |
        +--> ResourceReservationService.reserve(...)
                   |
                   +--> contadores servidor
                   +--> reserva_nodo
                   v
              PlacementResult confirmado
```

Errores principales:

- `NoPlacementSolutionException`: el solver no encuentra una solucion factible;
- `ReservationConflictException`: la capacidad cambio entre snapshot y reserva;
- `ReservationStateException`: ya existe una reserva parcial o diferente;
- errores transitorios de PostgreSQL: retryable para Temporal;
- version, zona o plan invalido: no retryable salvo que la capa superior lo corrija.

## 5. Estructura real del proyecto

```text
src/main/java/com/example/g3/placementservice/
├── PlacementServiceApplication.java
└── placement/
    ├── application/
    ├── domain/
    ├── engine/
    │   └── timefold/
    ├── persistence/
    ├── reservation/
    └── snapshot/
```

No existe una capa `api` ni un controller REST. Es intencional: el modulo esta pensado primero como servicio interno invocado por Temporal u otro orquestador.

## 6. Archivos importantes implementados

### 6.1 Arranque y configuracion

`PlacementServiceApplication.java`

- Punto de entrada Spring Boot.
- Activa el descubrimiento de componentes, repositorios JPA y servicios.

`pom.xml`

- Java 17.
- Spring Boot Data JPA.
- Spring Boot Web MVC y Validation.
- Driver PostgreSQL.
- Timefold Solver Spring Boot Starter `2.6.0`.

`application.properties`

- Nombre de la aplicacion.
- Conexion PostgreSQL.
- Configuracion JPA/Hibernate.

### 6.2 `application`

`PlacementService.java`

Contrato de entrada del modulo:

```java
PlacementResult place(Integer sliceVersionId, Integer zonaId);
```

`PlacementServiceImpl.java`

- Esta anotado con `@Service`.
- Valida solo referencias tecnicas no nulas.
- Carga el problema.
- Invoca el engine.
- Verifica `sliceVersionId` y `zonaId` del plan.
- Invoca la reserva.
- No conoce SQL, JPA ni clases de Timefold.

### 6.3 `domain`

El dominio usa records y no depende de Spring, JPA ni Timefold.

`ResourceAmount.java`

Representa CPU/vCPU, RAM en MB y disco en MB. Incluye suma de recursos.

`VmRequest.java`

Representa un nodo/VM nuevo y los recursos de su flavor.

`WorkerSnapshot.java`

Snapshot de un servidor fisico, llamado worker para no confundirlo con un nodo del slice. Contiene `serverId`, `zonaId`, `clusterId`, capacidad efectiva, recursos ya asignados y estado del servidor.

`ClusterSnapshot.java`

Agrupa el cluster y la lista de `WorkerSnapshot` disponibles.

`PlacementProblem.java`

Entrada agnostica del engine: `sliceVersionId`, `zonaId`, `ClusterSnapshot`, lista de VMs nuevas y `PlacementConstraints`.

`PlacementConstraints.java`

Punto de extension para restricciones futuras. Actualmente no contiene reglas de negocio ni autorizacion.

`Assignment.java`

Una decision `vmId -> serverId` con los recursos de esa VM.

`PlacementPlan.java`

Propuesta del solver. Contiene `sliceVersionId`, `zonaId` y asignaciones. No representa una reserva confirmada hasta que termina la transaccion.

`PlacementResult.java`

Resultado confirmado, con `successful` y las asignaciones persistidas.

### 6.4 `engine`

`PlacementEngine.java`

Puerto agnostico del solver:

```java
PlacementPlan solve(PlacementProblem problem);
```

`NoPlacementSolutionException.java`

Error para una solucion no factible.

### 6.5 `engine/timefold`

Estas clases son internas del adapter y no deben aparecer en el contrato del modulo.

`TimefoldWorker.java`

Hecho de planificacion que representa un worker y sus recursos.

`TimefoldVm.java`

Entidad de planificacion. Timefold decide su `worker` mediante una variable de planificacion.

`TimefoldSolution.java`

Contiene hechos (`workers`), entidades (`vms`) y score.

`TimefoldPlacementConstraintProvider.java`

Define las Constraint Streams que calculan `HardSoftScore`:

- hard: todas las VMs deben asignarse;
- hard: no se puede superar CPU, RAM ni disco;
- soft: se intenta usar la menor cantidad de workers.

`TimefoldPlacementMapper.java`

Convierte `PlacementProblem` en `TimefoldSolution` y `TimefoldSolution` en `PlacementPlan`.

`TimefoldPlacementEngine.java`

- Construye el solver Timefold.
- Usa el `SolverManager` configurado por el starter de Spring y el
        `ConstraintProvider` descubierto como bean.
- Configura el limite temporal mediante
        `timefold.solver.termination.spent-limit=2s`.
- Ejecuta `solve`.
- Rechaza soluciones no factibles.
- Devuelve un `PlacementPlan` del dominio.

### 6.6 `snapshot`

`PlacementSnapshotService.java`

Puerto de lectura:

```java
PlacementProblem loadProblem(Integer sliceVersionId, Integer zonaId);
```

`PlacementSnapshotServiceImpl.java`

- Es un `@Service`.
- Usa una transaccion de solo lectura.
- Obtiene `slice_version` y la zona.
- Obtiene servidores `ACTIVE` de la zona.
- Calcula capacidad efectiva con los ratios `default`.
- Copia los contadores actuales como recursos asignados.
- Convierte nodos y flavors en `VmRequest`.
- No valida permisos ni cambia estados.

### 6.7 `reservation`

`ResourceReservationService.java`

Puerto de reserva:

```java
PlacementResult reserve(Integer sliceVersionId, PlacementPlan plan);
```

`ResourceReservationServiceImpl.java`

- Es un `@Service` y usa `@Transactional`.
- Usa `JdbcTemplate` para las actualizaciones condicionales.
- Agrupa recursos por servidor.
- Valida estado `ACTIVE`, zona y ratios de capacidad.
- Actualiza `vcpu_reservado`, `vram_mb_reservado` y `vdisk_mb_reservado`.
- Inserta filas `vcpu`, `vram_mb` y `vdisk_mb` en `reserva_nodo`.
- Devuelve el placement solo despues de completar la transaccion.
- Reconoce una reserva completa repetida y evita duplicar contadores.

`ReservationConflictException.java`

Indica que un servidor ya no tiene recursos suficientes o no cumple las condiciones de reserva.

`ReservationStateException.java`

Indica que existen reservas activas parciales o diferentes para la misma version.

### 6.8 `persistence`

Las entidades JPA representan solo la infraestructura necesaria para construir el snapshot:

| Archivo | Tabla |
|---|---|
| `ClusterEntity` | `slices.cluster` |
| `AvailabilityZoneEntity` | `slices.zona_disponibilidad` |
| `ServerEntity` | `slices.servidor` |
| `FlavorEntity` | `slices.flavor` |
| `SliceVersionEntity` | `slices.slice_version` |
| `SliceNodeEntity` | `slices.slice_nodo` |

Repositorios actuales:

- `ClusterRepository`;
- `AvailabilityZoneRepository`;
- `ServerRepository`;
- `SliceVersionRepository`.

`ServerRepository` obtiene servidores por `zonaId` y estado.

No hay entidades de autorizacion, usuario, cuota ni aprobacion porque esas decisiones pertenecen a capas superiores para este modulo.

## 7. Correspondencia con la base de datos

El placement usa estas tablas:

- `slices.slice_version`: identifica la version;
- `slices.slice_nodo`: identifica cada VM/nodo y su flavor;
- `slices.flavor`: define vCPU, RAM y disco solicitados;
- `slices.zona_disponibilidad`: define zona, cluster y ratios;
- `slices.cluster`: identifica el cluster;
- `slices.servidor`: capacidad fisica y contadores rapidos;
- `slices.reserva_nodo`: detalle auditable de cada recurso reservado.

El esquema ya contempla `RESERVED` y `PROVISIONING` en los estados de `slice_version`, pero el modulo no los modifica.

## 8. Estado actual y pendientes

### Implementado

- Contrato de dominio independiente del solver.
- Renombrado de `NodeSnapshot` a `WorkerSnapshot`.
- Snapshot de servidores activos por zona.
- Capacidad efectiva con `allocation_ratio_default`.
- Adapter interno inicial para Timefold 2.6.0.
- Reserva transaccional de contadores y auditoria.
- Verificacion tecnica de zona en el plan y la reserva.
- Idempotencia para una reserva completa repetida.
- `PlacementServiceImpl` registrado como bean Spring.

### Pendiente inmediato

1. Confirmar mediante compilacion la API exacta de Timefold 2.6.0. En particular, revisar imports de `HardSoftScore`, la comprobacion de factibilidad y la configuracion `SolverConfig`, porque la version concreta puede diferir de ejemplos de otras versiones.
2. Revisar y completar el mapeo JPA, especialmente relaciones lazy y nombres de columnas, contra el esquema ejecutado.
3. Validar que el plan tenga exactamente un assignment por cada nodo solicitado antes de escribir reservas.
4. Evitar que una reserva parcial concurrente sea interpretada como placement completo sin una comprobacion adicional de cardinalidad y recursos.
5. Clasificar formalmente excepciones retryable y no retryable para Temporal.
6. Añadir pruebas de score, capacidad, rollback, carrera, zona e idempotencia.

### Fuera del MVP

- usar `allocation_ratio_limit` para flexibilizar placement;
- migracion o reequilibrio de VMs existentes;
- liberacion de recursos;
- despliegue y provisioning;
- API REST publica;
- integracion de Temporal dentro de este repositorio;
- placement multi-cluster para una misma version;
- historial adicional de reservations.

## 9. Criterios de exito

| Escenario | Resultado |
|---|---|
| Placement valido | Plan factible, contadores y auditoria confirmados en un commit |
| Sin solucion | No se modifican contadores ni auditoria |
| Carrera de recursos | Una transaccion gana; la otra recibe conflicto y puede reintentarse con snapshot fresco |
| Retry despues de commit | Se devuelve la reserva existente sin duplicar contadores |
| Plan fuera de zona | Se rechaza antes de confirmar la reserva |
| Estado de slice/nodo | No lo modifica este modulo |
