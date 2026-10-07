-- =========================================================
-- init.sql — Orquestador de slices TEL141
-- DB cloud_g3: schemas "auth" y "slices"
-- =========================================================

CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS slices;

-- SCHEMA: auth
-- USE `auth`;

CREATE TABLE auth.rol (
    id                          SERIAL PRIMARY KEY,
    nombre                      TEXT NOT NULL UNIQUE,          -- p.ej. 'consumidor', 'operador', 'admin'
    descripcion                 TEXT
);

INSERT INTO auth.rol (nombre, descripcion) VALUES
    ('consumidor', 'Usuario que consume recursos de la nube'),
    ('operador', 'Usuario que opera plataforma y gestiona usuarios'),
    ('admin', 'Usuario que administra infraestructura y supervisa estado global');

CREATE TABLE auth.nivel (
    id                          SERIAL PRIMARY KEY,
    nombre                      TEXT NOT NULL UNIQUE,          -- p.ej. 'basico', 'avanzado'
    descripcion                 TEXT
);

INSERT INTO auth.nivel (nombre, descripcion) VALUES
    ('basico', 'Nivel de servicio básico'),
    ('avanzado', 'Nivel de servicio avanzado');

CREATE TABLE auth.usuario (
    id                  SERIAL PRIMARY KEY,
    codigo              TEXT NOT NULL UNIQUE,
    hash_password       TEXT NOT NULL,
    rol_id              INTEGER REFERENCES auth.rol(id),
    nivel_id            INTEGER REFERENCES auth.nivel(id),  -- NULL para operador/admin
    estado              TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (estado IN ('ACTIVE', 'INACTIVE', 'BLOCKED')),
    creado_por          INTEGER REFERENCES auth.usuario(id),          -- NULL solo para la primera cuenta admin
    fecha_creacion      TIMESTAMP NOT NULL DEFAULT now(),

    -- un consumidor debe tener nivel de servicio; operador/admin no
    CONSTRAINT chk_nivel_segun_rol CHECK (
        (rol_id = 1 AND nivel_id IS NOT NULL)
        OR (rol_id != 1 AND nivel_id IS NULL)
    )
);

INSERT INTO auth.usuario (codigo, hash_password, rol_id, nivel_id, creado_por) VALUES
    ('20260001', '$argon2id$v=19$m=16,t=2,p=1$R3pXUEhxWGR1WnhNQkUzTw$iwIAr8qyKxalgV/SWVkYug', 1, 1, NULL),  -- usuario consumidor basico 20260001-consumidor
    ('20260002', '$argon2id$v=19$m=16,t=2,p=1$MDZ2aHZsM0JHb3ZsaG5KRg$4jxDWd4T0xs1kTZxt3ttWg', 1, 2, NULL),  -- usuario consumidor avanzado 20260002-consumidor
    ('operador', '$argon2id$v=19$m=16,t=2,p=1$OThZM2RRaU00NEZxbFM4bw$zg1BBvSyLTDCxVxnpAI1Yg', 2, NULL, NULL),  -- usuario operador-operador
    ('admin', '$argon2id$v=19$m=16,t=2,p=1$OThZM2RRaU00NEZxbFM4bw$JROgUqUKhYFy7sPqed/VWg', 3, NULL, NULL);     -- usuario admin-admin

CREATE TABLE auth.refresh_token (
    id              UUID PRIMARY KEY,
    usuario_id      INTEGER NOT NULL REFERENCES auth.usuario(id) ON DELETE CASCADE,
    familia_id      UUID NOT NULL,                 -- cadena de rotación
    token_hash      TEXT NOT NULL UNIQUE,          -- sha256 del token; nunca el token
    creado_en       TIMESTAMP NOT NULL DEFAULT now(),
    expira_en       TIMESTAMP NOT NULL,
    revocado_en     TIMESTAMP,
    reemplazado_por UUID REFERENCES auth.refresh_token(id)
);
CREATE INDEX idx_refresh_usuario ON auth.refresh_token(usuario_id);

CREATE TABLE auth.cuota (
    id              SERIAL PRIMARY KEY,
    nivel_id        INTEGER NOT NULL REFERENCES auth.nivel(id) ON DELETE CASCADE,
    recurso         TEXT NOT NULL,          -- 'vcpu', 'ram_mb', 'disk_mb', etc.
    prometido       NUMERIC NOT NULL,
    UNIQUE (nivel_id, recurso)
);

INSERT INTO auth.cuota (nivel_id, recurso, prometido) VALUES
    (1, 'vcpu', 8),
    (1, 'ram_mb', 4096),
    (1, 'disk_mb', 14000),
    (2, 'vcpu', 16),
    (2, 'ram_mb', 8192),
    (2, 'disk_mb', 28000);

CREATE TABLE auth.cuota_override (
    id              SERIAL PRIMARY KEY,
    usuario_id      INTEGER NOT NULL REFERENCES auth.usuario(id) ON DELETE CASCADE,
    recurso         TEXT NOT NULL,          -- 'vcpu', 'ram_mb', 'disk_mb', etc.
    prometido       NUMERIC NOT NULL,
    creado_por      INTEGER NOT NULL REFERENCES auth.usuario(id),
    fecha_creacion  TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (usuario_id, recurso)
);

CREATE INDEX idx_usuario_nivel ON auth.usuario(nivel_id);
CREATE INDEX idx_cuota_override_nivel ON auth.cuota_override(usuario_id);

-- Slices — infraestructura

CREATE TABLE slices.cluster (
    id                          SERIAL PRIMARY KEY,
    nombre                      TEXT NOT NULL UNIQUE
);

INSERT INTO slices.cluster (nombre) VALUES
    ('linux'),
    ('openstack');

CREATE TABLE slices.zona_disponibilidad (
    id                              SERIAL PRIMARY KEY,
    nombre                          TEXT NOT NULL UNIQUE,
    cluster_id                      INTEGER REFERENCES slices.cluster(id),
    estado                          TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (estado IN ('ACTIVE', 'MAINTENANCE', 'DOWN')),
    -- Allocattion ratio config. Quizas en otra tabla? aunque no es cambiante.
    cpu_allocation_ratio_default    NUMERIC(4,1) NOT NULL DEFAULT 1.0,
    cpu_allocation_ratio_limit      NUMERIC(4,1) NOT NULL DEFAULT 1.5,
    ram_allocation_ratio_default    NUMERIC(4,1) NOT NULL DEFAULT 1.0,
    ram_allocation_ratio_limit      NUMERIC(4,1) NOT NULL DEFAULT 1.5,
    disk_allocation_ratio_default   NUMERIC(4,1) NOT NULL DEFAULT 1.0,
    disk_allocation_ratio_limit     NUMERIC(4,1) NOT NULL DEFAULT 1.5,

    CONSTRAINT chk_ratio_cpu_valido  CHECK (cpu_allocation_ratio_default  <= cpu_allocation_ratio_limit),
    CONSTRAINT chk_ratio_ram_valido  CHECK (ram_allocation_ratio_default  <= ram_allocation_ratio_limit),
    CONSTRAINT chk_ratio_disk_valido CHECK (disk_allocation_ratio_default <= disk_allocation_ratio_limit)
);

INSERT INTO slices.zona_disponibilidad (nombre, cluster_id, cpu_allocation_ratio_default, cpu_allocation_ratio_limit, ram_allocation_ratio_default, ram_allocation_ratio_limit, disk_allocation_ratio_default, disk_allocation_ratio_limit) VALUES
    ('linx-1', 1, 7.0, 8.0, 1.5, 2.0, 1.0, 1.5),     -- Linux: nivel basico y avanzado, densidad alta 
    ('linx-2', 1, 4.0, 5.5, 2.0, 2.5, 1.0, 1.5),     -- Linux: nivel avanzado, densidad baja
    ('openst-1', 2, 6.0, 7.5, 1.0, 1.5, 1.0, 1.5);     -- OpenStack: nivel avanzado, densidad media

CREATE TABLE auth.nivel_accede_zona (
    nivel_id INTEGER NOT NULL,
    zona_id INTEGER NOT NULL,
    PRIMARY KEY (nivel_id, zona_id),
    
    FOREIGN KEY (nivel_id) REFERENCES auth.nivel(id) ON DELETE CASCADE,
    FOREIGN KEY (zona_id) REFERENCES slices.zona_disponibilidad(id) ON DELETE CASCADE
);

INSERT INTO auth.nivel_accede_zona (nivel_id, zona_id) VALUES
    (1, 1),  -- nivel basico accede a linx-1
    (2, 1),  -- nivel avanzado accede a linx-1
    (2, 2),  -- nivel avanzado accede a linx-2
    (2, 3);  -- nivel avanzado accede a openst-1

CREATE TABLE slices.servidor (
    id                  SERIAL PRIMARY KEY,
    ip_serv             INET NOT NULL UNIQUE,
    mac_serv            MACADDR NOT NULL UNIQUE,
    total_cpu_cores     INTEGER NOT NULL,
    total_ram_mb        INTEGER NOT NULL,
    total_disk_mb       INTEGER NOT NULL,
    vcpu_reserved      INTEGER NOT NULL DEFAULT 0 CHECK (vcpu_reserved >= 0),
    vram_mb_reserved   INTEGER NOT NULL DEFAULT 0 CHECK (vram_mb_reserved >= 0),
    vdisk_mb_reserved  INTEGER NOT NULL DEFAULT 0 CHECK (vdisk_mb_reserved >= 0),
    zona_id             INTEGER REFERENCES slices.zona_disponibilidad(id),
    estado              TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (estado IN ('ACTIVE', 'MAINTENANCE', 'DOWN'))
);

INSERT INTO slices.servidor (ip_serv, mac_serv, total_cpu_cores, total_ram_mb, total_disk_mb, zona_id) VALUES
    ('10.0.10.1', '00:11:22:33:44:55', 8, 16384, 1000, 1),
    ('10.0.10.2', '00:11:22:33:44:56', 8, 16384, 1000, 1),
    ('10.0.10.3', '00:11:22:33:44:57', 16, 16384, 1000, 2),
    ('192.168.1.14', '00:11:22:33:44:59', 16, 16384, 102400, 3),
    ('192.168.1.15', '00:11:22:33:44:60', 8, 16384, 102400, 3);

CREATE TABLE slices.image (
    id                      SERIAL PRIMARY KEY,
    nombre                  TEXT NOT NULL,
    cluster_compatible      INTEGER REFERENCES slices.cluster(id),  -- puede ser NULL, cluster donde se puede desplegar la imagen
    ruta_referencia         TEXT NOT NULL,   -- ubicación/id real de la imagen
    contador_refs           INTEGER NOT NULL DEFAULT 0,
    estado                  TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (estado IN ('ACTIVE', 'DEPRECATED', 'DELETED'))
);

INSERT INTO slices.image (nombre, cluster_compatible, ruta_referencia) VALUES
    ('Cirros', 1, '/images/cirros.qcow2'),
    ('Linux Alpine', 1, '/images/alpine.qcow2');

CREATE TABLE slices.flavor (
    id                  SERIAL PRIMARY KEY,
    "name"              TEXT NOT NULL UNIQUE,
    descripcion         TEXT NOT NULL,
    vcpu                INTEGER NOT NULL,
    vram_mb             INTEGER NOT NULL,
    vdisk_mb            INTEGER NOT NULL,
    activo              BOOLEAN NOT NULL DEFAULT TRUE
);

INSERT INTO slices.flavor (name, descripcion, vcpu, vram_mb, vdisk_mb) VALUES
    ('micro-v1', 'Micro: 1 vCPU, 256 MB RAM, 1 GB disco', 1, 256, 1000),
    ('small-v1', 'Pequeño: 1 vCPU, 512 MB RAM, 1 GB disco', 1, 512, 1000),
    ('medium-v1', 'Mediano: 2 vCPU, 512 MB RAM, 600 MB disco', 1, 512, 2500),
    ('large-v1', 'Grande: 4 vCPU, 1 GB RAM, 1 GB disco', 2, 1024, 4000);


-- slices — plantillas y slices

CREATE TABLE slices.plantilla (
    id              SERIAL PRIMARY KEY,
    dueño_id        INTEGER REFERENCES auth.usuario(id),   -- NULL si es plantilla oficial
    nombre          TEXT NOT NULL,
    spec            JSONB NOT NULL -- ,
    -- visibilidad     TEXT NOT NULL DEFAULT 'PRIVATE' CHECK (visibilidad IN ('PRIVATE', 'SHARED', 'OFFICIAL'))
);

-- slices y slice_versiones se referencian mutuamente: FK circular, y se agrega después.
CREATE TABLE slices.slice (
    id                  SERIAL PRIMARY KEY,
    usuario_id          INTEGER NOT NULL REFERENCES auth.usuario(id),
    nombre              TEXT NOT NULL,
    estado              TEXT NOT NULL DEFAULT 'DRAFT' CHECK (
                            estado IN ('DRAFT', 'UPDATING', 'RUNNING', 'STOPPED', 'FAILED', 'ELIMINATED')
                        ),
    zona_id          INTEGER REFERENCES slices.zona_disponibilidad(id),
    version_activa_id   INTEGER,   -- FK tras crear slice_version, una por slice
    fecha_creacion      TIMESTAMP NOT NULL DEFAULT now(),
    fecha_modificacion  TIMESTAMP NOT NULL DEFAULT now()
);

INSERT INTO slices.slice (usuario_id, nombre, estado, zona_id, version_activa_id) VALUES
    (1, 'Slice de prueba 1', 'DRAFT', 1, NULL),
    (2, 'Slice de prueba 2', 'DRAFT', 2, NULL);

CREATE TABLE slices.slice_version (
    id                  SERIAL PRIMARY KEY,
    slice_id            INTEGER NOT NULL REFERENCES slices.slice(id) ON DELETE CASCADE,
    numero_version      INTEGER NOT NULL,
    spec                JSONB NOT NULL,
    custom              BOOLEAN NOT NULL DEFAULT FALSE,
    plantilla_origen_id INTEGER REFERENCES slices.plantilla(id),
    estado_version      TEXT NOT NULL DEFAULT 'DRAFT' CHECK (
                            estado_version IN ('DRAFT', 'APPROVAL_PENDING', 'DEPLOYING', 'ACTIVE', 'INCONSISTENT', 'ARCHIVED')
                        ),
    UNIQUE (slice_id, numero_version)
);

INSERT INTO slices.slice_version (slice_id, numero_version, spec, custom, plantilla_origen_id, estado_version) VALUES
    (1, 1, '{"nodos": [], "enlaces": []}', FALSE, NULL, 'DRAFT'),
    (2, 1, '{"nodos": [], "enlaces": []}', FALSE, NULL, 'DRAFT');

ALTER TABLE slices.slice
    ADD CONSTRAINT fk_slices_version_activa
    FOREIGN KEY (version_activa_id) REFERENCES slices.slice_version(id);


-- slices — topología (grafo de nodos y enlaces)

CREATE TABLE slices.slice_nodo (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slice_id            INTEGER NOT NULL REFERENCES slices.slice(id) ON DELETE CASCADE,
    "name"              TEXT NOT NULL,
    -- especificacion      JSONB NOT NULL,
    defin_version_id    INTEGER NOT NULL REFERENCES slices.slice_version(id),
    flavor_id           INTEGER NOT NULL REFERENCES slices.flavor(id),
    imagen_id           INTEGER NOT NULL REFERENCES slices.image(id),
    estado_nodo         TEXT NOT NULL DEFAULT 'PENDING' CHECK (
                            estado_nodo IN ('PENDING', 'RUNNING', 'STOPPED', 'FAILED', 'DELETED')
                        )
);

INSERT INTO slices.slice_nodo (slice_id, defin_version_id, name, flavor_id, imagen_id, estado_nodo) VALUES
    (1, 1, 'Nodo 1A', 1, 1, 'PENDING'),
    (1, 1, 'Nodo 1B', 2, 1, 'PENDING'),
    (1, 1, 'Nodo 1C', 3, 1, 'PENDING'),
    (2, 2, 'Nodo 2A', 3, 1, 'PENDING'),
    (2, 2, 'Nodo 2B', 2, 1, 'PENDING'),
    (2, 2, 'Nodo 2C', 1, 1, 'PENDING');

-- CREATE TABLE slices.slice_enlace (
--     id                  SERIAL PRIMARY KEY,
--     slice_id            INTEGER NOT NULL REFERENCES slices.slice(id) ON DELETE CASCADE,
--     name                TEXT NOT NULL,
--     defin_version_id    INTEGER NOT NULL REFERENCES slices.slice_version(id) ON DELETE CASCADE,
--     nodo_origen_id      INTEGER NOT NULL REFERENCES slices.slice_nodo(id),
--     nodo_destino_id     INTEGER NOT NULL REFERENCES slices.slice_nodo(id),
--     estado              TEXT NOT NULL DEFAULT 'PENDING' CHECK (
--                             estado IN ('PENDING', 'CREATED', 'FAILED', 'DELETED')
--                         ),
--     CHECK (nodo_origen_id != nodo_destino_id)
-- );
CREATE TABLE slices.slice_enlace (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slice_id            INTEGER NOT NULL REFERENCES slices.slice(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    defin_version_id    INTEGER NOT NULL REFERENCES slices.slice_version(id)
);
CREATE TABLE slices.slice_enlace_puerto (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    enlace_id           UUID NOT NULL REFERENCES slices.slice_enlace(id) ON DELETE CASCADE,
    nodo_id             UUID NOT NULL REFERENCES slices.slice_nodo(id),
    name                TEXT,
    public              BOOLEAN NOT NULL DEFAULT FALSE,
    -- facts que escribe el adaptador:
    mac                 MACADDR,
    ip                  INET,
    vlan_tag            INTEGER,
    ns                  TEXT,
    UNIQUE (enlace_id, nodo_id)
);


CREATE INDEX idx_slice_nodos_version ON slices.slice_nodo(defin_version_id);
CREATE INDEX idx_slice_enlaces_version ON slices.slice_enlace(defin_version_id);
CREATE UNIQUE INDEX uq_nodo_name_vivo ON slices.slice_nodo (slice_id, name) WHERE estado_nodo <> 'DELETED';
CREATE UNIQUE INDEX uq_enlace_name_vivo ON slices.slice_enlace (slice_id, name);

-- slices — despliegue (workflows de cambios de slice)
CREATE TABLE slices.deployment (             -- una fila por intento. ejem. actualizar versión A a la B
    id                  SERIAL PRIMARY KEY,
    slice_id            INTEGER NOT NULL REFERENCES slices.slice(id),
    origin_version_id   INTEGER REFERENCES slices.slice_version(id),
    target_version_id   INTEGER REFERENCES slices.slice_version(id),
    tipo                TEXT NOT NULL CHECK (tipo IN ('DEPLOY', 'UPDATE', 'START', 'STOP', 'DESTROY')),
    estado              TEXT NOT NULL DEFAULT 'PENDING' CHECK (estado IN ('PENDING', 'IN_PROGRESS', 'REJECTED', 'COMPENSATED', 'COMPLETED', 'FAILED')),
    workflow_id         TEXT UNIQUE,                     -- id del workflow temporal
    plan                JSONB,                                 -- changeset congelado
    error               JSONB,                                 -- errores del despliegue
    solicitado_por      INTEGER NOT NULL REFERENCES auth.usuario(id),
    created_at          TIMESTAMP NOT NULL DEFAULT now(),
    finished_at         TIMESTAMP
);

CREATE UNIQUE INDEX uq_deployment_slice_activo ON slices.deployment (slice_id, workflow_id);
CREATE UNIQUE INDEX uq_deployment_slice_version ON slices.deployment (slice_id) WHERE estado IN ('PENDING', 'IN_PROGRESS');


-- slices — reservas (libro de VM Placement)
CREATE TABLE slices.reserva_nodo (
    id                  SERIAL PRIMARY KEY,
    servidor_id         INTEGER NOT NULL REFERENCES slices.servidor(id),
    nodo_id             UUID NOT NULL REFERENCES slices.slice_nodo(id),
    createdby_deployment_id    INTEGER NOT NULL REFERENCES slices.deployment(id),
    recurso             TEXT NOT NULL,      -- 'vcpu', 'vram_mb', 'vdisk_mb'.
    cantidad_reservada  INTEGER NOT NULL CHECK (cantidad_reservada > 0),
    estado              TEXT NOT NULL DEFAULT 'RESERVED' CHECK (estado IN ('RESERVED', 'RELEASED')),
    fecha_reserva       TIMESTAMP NOT NULL DEFAULT now(),
    fecha_liberacion    TIMESTAMP
);

CREATE INDEX idx_reservas_servidor ON slices.reserva_nodo(servidor_id);
CREATE UNIQUE INDEX uq_reserva_nodo_activa ON slices.reserva_nodo (nodo_id, recurso) WHERE estado = 'RESERVED';
CREATE INDEX idx_reservas_nodo ON slices.reserva_nodo(nodo_id);

-- slices — aprobaciones
CREATE TABLE slices.solicitud_aprobacion (
    id                  SERIAL PRIMARY KEY,
    deployment_id       INTEGER NOT NULL REFERENCES slices.deployment(id) ON DELETE CASCADE,
    usuario_id          INTEGER NOT NULL REFERENCES auth.usuario(id),
    estado              TEXT NOT NULL DEFAULT 'VALIDATING' CHECK (estado IN ('VALIDATING', 'PENDING', 'APPROVED', 'REJECTED_BY_SYSTEM', 'REJECTED_BY_OPERATOR')),
    operador_id         INTEGER REFERENCES auth.usuario(id),
    fecha_sol           TIMESTAMP NOT NULL DEFAULT now(),
    motivo              TEXT
);