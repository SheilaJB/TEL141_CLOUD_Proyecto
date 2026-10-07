#!/bin/bash
# Script de inicializacion de Postgres (después de 10-init.sql).
#  - APP_DB_USER:      usa la DB de la aplicación (esquemas auth y slices).
#  - TEMPORAL_DB_USER: puede crear bases (temporal-sql-tool crea "temporal" y "temporal_visibility");
#                      no tiene acceso a las tablas de la aplicación.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE ${APP_DB_USER} LOGIN PASSWORD '${APP_DB_PASSWORD}';
    CREATE ROLE ${TEMPORAL_DB_USER} LOGIN PASSWORD '${TEMPORAL_DB_PASSWORD}' CREATEDB;

    REVOKE CONNECT ON DATABASE ${POSTGRES_DB} FROM PUBLIC;
    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ${APP_DB_USER};

    GRANT USAGE ON SCHEMA auth, slices TO ${APP_DB_USER};
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA auth, slices TO ${APP_DB_USER};
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA auth, slices TO ${APP_DB_USER};
    ALTER DEFAULT PRIVILEGES IN SCHEMA auth, slices
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ${APP_DB_USER};
    ALTER DEFAULT PRIVILEGES IN SCHEMA auth, slices
        GRANT USAGE, SELECT ON SEQUENCES TO ${APP_DB_USER};
EOSQL