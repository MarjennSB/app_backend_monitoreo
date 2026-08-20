"""
storage/database.py
────────────────────────────────────────────────────────────────
Conexión y gestión del pool de conexiones a PostgreSQL.

Equivalente a config/database.php de Laravel pero en Python async.

Responsabilidades:
  - Leer credenciales desde .env (equivalente al .env de Laravel)
  - Gestionar el pool de conexiones (asyncpg.Pool)
  - Crear las tablas si no existen (equivalente a migrations)
  - Proveer health_check para verificar la BD desde la API

Uso:
  from modules.storage.database import db

  await db.connect()          # Al iniciar la app
  await db.create_tables()    # Crea tablas si no existen
  await db.disconnect()       # Al cerrar la app

  async with db.acquire() as conn:
      row = await conn.fetchrow("SELECT * FROM devices WHERE ip=$1", ip)

Librería: asyncpg (PostgreSQL async nativo para Python)
  pip install asyncpg python-dotenv
────────────────────────────────────────────────────────────────
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
from dotenv import load_dotenv

# Cargar .env (equivalente al bootstrap de Laravel)
load_dotenv()


# ──────────────────────────────────────────────
# CONFIGURACIÓN — Equivalente a config/database.php
# ──────────────────────────────────────────────

class DatabaseConfig:
    """
    Lee las credenciales de PostgreSQL desde el archivo .env.

    Equivalente a config/database.php en Laravel.
    Todos los valores tienen defaults seguros excepto la contraseña.
    """
    host:     str = os.getenv("DB_HOST",     "127.0.0.1")
    port:     int = int(os.getenv("DB_PORT", "5432"))
    database: str = os.getenv("DB_DATABASE", "mvp_monitoreo")
    user:     str = os.getenv("DB_USERNAME", "postgres")
    password: str = os.getenv("DB_PASSWORD", "")
    pool_min: int = int(os.getenv("DB_POOL_MIN", "2"))
    pool_max: int = int(os.getenv("DB_POOL_MAX", "10"))

    @classmethod
    def dsn(cls) -> str:
        """
        Genera el DSN (Data Source Name) para asyncpg.

        Equivalente al array de configuración que usa Laravel
        internamente para PDO.

        Returns:
            String DSN: postgresql://user:pass@host:port/database
        """
        return (
            f"postgresql://{cls.user}:{cls.password}"
            f"@{cls.host}:{cls.port}/{cls.database}"
        )


# ──────────────────────────────────────────────
# GESTOR DE CONEXIONES — Equivalente a DB facade de Laravel
# ──────────────────────────────────────────────

class Database:
    """
    Gestor del pool de conexiones a PostgreSQL.

    Usa asyncpg.Pool para mantener múltiples conexiones reutilizables,
    equivalente al connection pooling de Laravel con PDO.

    El pool está configurado con un mínimo de conexiones siempre
    activas (pool_min) y un máximo (pool_max) para no saturar la BD.

    Uso típico en la app:
        db = Database()
        await db.connect()

        async with db.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM devices")
    """

    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None

    @property
    def is_connected(self) -> bool:
        """True si el pool está activo."""
        return self._pool is not None

    async def connect(self) -> None:
        """
        Inicializa el pool de conexiones a PostgreSQL.

        Llamar una vez al arrancar la aplicación (en main.py).
        Equivalente a que Laravel establece la conexión en el
        primer request.

        Raises:
            asyncpg.PostgresConnectionError: Si no puede conectar.
            Exception: Si las credenciales son incorrectas.
        """
        config = DatabaseConfig()
        self._pool = await asyncpg.create_pool(
            dsn=config.dsn(),
            min_size=config.pool_min,
            max_size=config.pool_max,
            command_timeout=30,
        )
        logging.info(
            "PostgreSQL conectado → %s:%s/%s (pool %s-%s)",
            config.host, config.port, config.database,
            config.pool_min, config.pool_max,
        )

    async def disconnect(self) -> None:
        """
        Cierra el pool de conexiones limpiamente.

        Llamar al cerrar la aplicación (en main.py).
        """
        if self._pool:
            await self._pool.close()
            self._pool = None
            logging.info("PostgreSQL desconectado.")

    @asynccontextmanager
    async def acquire(self):
        """
        Obtiene una conexión del pool como context manager.

        Equivalente a DB::connection() de Laravel pero async.
        La conexión se devuelve al pool automáticamente al salir.

        Uso:
            async with db.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM devices WHERE ip=$1", ip)

        Yields:
            asyncpg.Connection lista para usar.

        Raises:
            RuntimeError: Si el pool no ha sido inicializado.
        """
        if not self._pool:
            raise RuntimeError(
                "El pool de BD no está inicializado. "
                "Llama a db.connect() al arrancar la app."
            )
        async with self._pool.acquire() as conn:
            yield conn

    async def execute(self, query: str, *args) -> str:
        """
        Ejecuta un query de escritura (INSERT, UPDATE, DELETE).

        Equivalente a DB::statement() de Laravel.

        Args:
            query: Query SQL con placeholders $1, $2...
            *args: Valores para los placeholders.

        Returns:
            Status string de PostgreSQL (ej: "INSERT 0 1").
        """
        async with self.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch_all(self, query: str, *args) -> list[asyncpg.Record]:
        """
        Ejecuta un SELECT y retorna todas las filas.

        Equivalente a DB::select() de Laravel.

        Args:
            query: Query SELECT con placeholders $1, $2...
            *args: Valores para los placeholders.

        Returns:
            Lista de Records (accesibles como dicts: row["campo"]).
        """
        async with self.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetch_one(self, query: str, *args) -> Optional[asyncpg.Record]:
        """
        Ejecuta un SELECT y retorna solo la primera fila.

        Equivalente a DB::selectOne() de Laravel.

        Args:
            query: Query SELECT con placeholders $1, $2...
            *args: Valores para los placeholders.

        Returns:
            Un Record o None si no hay resultados.
        """
        async with self.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetch_val(self, query: str, *args):
        """
        Retorna el valor de la primera columna de la primera fila.

        Útil para queries como COUNT(*), MAX(), EXISTS().

        Args:
            query: Query SELECT con placeholders $1, $2...
            *args: Valores para los placeholders.

        Returns:
            El valor escalar o None.
        """
        async with self.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def health_check(self) -> dict:
        """
        Verifica el estado de la conexión a PostgreSQL.

        Usado por routes/api.py para el endpoint de health
        que muestra el estado del sistema en el dashboard.

        Returns:
            Diccionario con el estado de la BD.
        """
        try:
            version = await self.fetch_val("SELECT version()")
            pool_size = self._pool.get_size() if self._pool else 0
            idle = self._pool.get_idle_size() if self._pool else 0
            return {
                "status":     "ok",
                "version":    version.split(",")[0] if version else "unknown",
                "pool_size":  pool_size,
                "pool_idle":  idle,
                "pool_busy":  pool_size - idle,
            }
        except Exception as exc:
            return {
                "status":  "error",
                "message": str(exc),
            }

    # ── Migraciones — Equivalente a php artisan migrate ──────────

    async def create_tables(self) -> None:
        """
        Crea todas las tablas del sistema si no existen.

        Equivalente a correr 'php artisan migrate' en Laravel.
        Se llama al arrancar la app — es idempotente (IF NOT EXISTS).

        Tablas creadas:
          networks        → Redes configuradas para monitoreo
          devices         → Dispositivos descubiertos
          scan_results    → Resultados históricos de scans
          port_checks     → Estado de puertos TCP por dispositivo
          device_stats    → Estadísticas de uptime/downtime
          device_inventory → Inventario de hardware (SNMP/WMI/SSH)
        """
        async with self.acquire() as conn:
            await conn.execute(_SQL_CREATE_TABLES)
            
            # Asegurar que se elimine la columna label de redes y se agregue FK
            try:
                await conn.execute("ALTER TABLE networks DROP COLUMN IF EXISTS label;")
                await conn.execute("ALTER TABLE networks ADD CONSTRAINT fk_vlan FOREIGN KEY (vlan_id) REFERENCES vlans(id);")
            except Exception:
                pass # Ignorar si la FK ya existe u otro error

            # Asegurar que la columna is_critical exista en devices
            try:
                await conn.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS is_critical BOOLEAN NOT NULL DEFAULT FALSE;")
            except Exception:
                pass

            from passlib.hash import bcrypt

            # Insert default role
            await conn.execute(
                """
                INSERT INTO roles (id, name)
                VALUES (1, 'Administrador'), (2, 'Usuario')
                ON CONFLICT (id) DO NOTHING
                """
            )

            # Hash default password if user doesn't exist
            user_exists = await conn.fetchval("SELECT id FROM users WHERE email = 'admin@admin.com'")
            if not user_exists:
                hashed = bcrypt.hash("admin123")
                await conn.execute(
                    """
                    INSERT INTO users (role_id, username, first_name, email, password)
                    VALUES (1, 'admin', 'Administrador', 'admin@admin.com', $1)
                    """, hashed
                )

            logging.info("Tablas y roles verificados/creados correctamente.")


# ──────────────────────────────────────────────
# DDL — Definición de tablas
# ──────────────────────────────────────────────
_SQL_CREATE_TABLES = """
-- ─────────────────────────────────────────────────────────────
-- TABLAS DE AUTH Y ROLES
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS roles (
    id SERIAL PRIMARY KEY,
    name VARCHAR(60) NOT NULL UNIQUE,
    status BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    role_id INT NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    username VARCHAR(100) NOT NULL UNIQUE,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) DEFAULT '',
    email VARCHAR(150) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    status BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

-- ─────────────────────────────────────────────────────────────
-- VLANs configuradas
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vlans (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(100) NOT NULL UNIQUE,   -- ej: "VLAN RRHH Piso 2"
    description   TEXT         NOT NULL DEFAULT '',
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────
-- Redes configuradas para monitoreo
-- Equivalente a la tabla 'networks' en Laravel
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS networks (
    id            SERIAL PRIMARY KEY,
    cidr          VARCHAR(20)  NOT NULL UNIQUE,   -- ej: 192.168.1.0/24
    vlan_id       INTEGER      REFERENCES vlans(id) ON DELETE SET NULL, -- ID de VLAN
    scan_interval INTEGER      NOT NULL DEFAULT 300, -- segundos entre scans
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────
-- Dispositivos descubiertos en la red
-- Un dispositivo por IP — se actualiza en cada scan
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS devices (
    id            SERIAL PRIMARY KEY,
    network_id    INTEGER      NOT NULL REFERENCES networks(id) ON DELETE CASCADE,
    ip            INET         NOT NULL,
    hostname      VARCHAR(255) NOT NULL DEFAULT 'unknown',
    hostname_method VARCHAR(20) NOT NULL DEFAULT 'unknown', -- dns-ptr | netbios | snmp | unknown
    mac_address   VARCHAR(17),                              -- formato AA:BB:CC:DD:EE:FF
    is_alive      BOOLEAN      NOT NULL DEFAULT FALSE,
    is_critical   BOOLEAN      NOT NULL DEFAULT FALSE,
    last_seen_at  TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(network_id, ip)
);

CREATE INDEX IF NOT EXISTS idx_devices_network_id ON devices(network_id);
CREATE INDEX IF NOT EXISTS idx_devices_ip         ON devices(ip);
CREATE INDEX IF NOT EXISTS idx_devices_is_alive   ON devices(is_alive);

-- ─────────────────────────────────────────────────────────────
-- Resultados de cada ciclo de scan completo
-- Historial de cuándo se escaneó y qué se encontró
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scan_results (
    id              SERIAL PRIMARY KEY,
    network_id      INTEGER     NOT NULL REFERENCES networks(id) ON DELETE CASCADE,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ NOT NULL,
    duration_ms     INTEGER     NOT NULL,   -- duración en milisegundos
    total_hosts     INTEGER     NOT NULL DEFAULT 0,
    active_hosts    INTEGER     NOT NULL DEFAULT 0,
    inactive_hosts  INTEGER     NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scan_results_network_id  ON scan_results(network_id);
CREATE INDEX IF NOT EXISTS idx_scan_results_finished_at ON scan_results(finished_at DESC);

-- ─────────────────────────────────────────────────────────────
-- Estado de puertos TCP por dispositivo
-- Se actualiza en cada scan — historia de abierto/cerrado
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS port_checks (
    id          SERIAL PRIMARY KEY,
    device_id   INTEGER     NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    port        INTEGER     NOT NULL,
    state       VARCHAR(10) NOT NULL,       -- open | closed | filtered | error
    rtt_ms      NUMERIC(8,3),               -- RTT en ms con decimales
    checked_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(device_id, port, checked_at)
);

CREATE INDEX IF NOT EXISTS idx_port_checks_device_id  ON port_checks(device_id);
CREATE INDEX IF NOT EXISTS idx_port_checks_checked_at ON port_checks(checked_at DESC);

-- ─────────────────────────────────────────────────────────────
-- Estadísticas acumuladas de uptime/downtime por dispositivo
-- Una fila por dispositivo — se actualiza en cada ciclo
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS device_stats (
    id                      SERIAL PRIMARY KEY,
    device_id               INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE UNIQUE,
    total_probes            INTEGER NOT NULL DEFAULT 0,
    successful_probes       INTEGER NOT NULL DEFAULT 0,
    failed_probes           INTEGER NOT NULL DEFAULT 0,
    ongoing_successful      INTEGER NOT NULL DEFAULT 0,
    ongoing_failed          INTEGER NOT NULL DEFAULT 0,
    availability_percent    NUMERIC(5,2) NOT NULL DEFAULT 0.0,
    rtt_min_ms              NUMERIC(8,3),
    rtt_max_ms              NUMERIC(8,3),
    rtt_avg_ms              NUMERIC(8,3),
    total_uptime_seconds    INTEGER NOT NULL DEFAULT 0,
    total_downtime_seconds  INTEGER NOT NULL DEFAULT 0,
    longest_uptime_seconds  INTEGER NOT NULL DEFAULT 0,
    longest_downtime_seconds INTEGER NOT NULL DEFAULT 0,
    last_seen_at            TIMESTAMPTZ,
    last_down_at            TIMESTAMPTZ,
    monitoring_since        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────
-- Inventario de hardware por dispositivo (SNMP / WMI / SSH)
-- Una fila por dispositivo — se actualiza cuando el lector obtiene datos
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS device_inventory (
    id              SERIAL PRIMARY KEY,
    device_id       INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE UNIQUE,
    device_type     VARCHAR(30)  NOT NULL DEFAULT 'unknown', -- switch | router | printer | server...
    manufacturer    VARCHAR(100) NOT NULL DEFAULT 'unknown',
    model           VARCHAR(100) NOT NULL DEFAULT 'unknown',
    description     TEXT         NOT NULL DEFAULT '',
    location        VARCHAR(255) NOT NULL DEFAULT '',
    contact         VARCHAR(255) NOT NULL DEFAULT '',
    os_info         VARCHAR(255) NOT NULL DEFAULT '',
    cpu_model       VARCHAR(255) NOT NULL DEFAULT '',
    ram_mb          INTEGER,
    disk_gb         NUMERIC(8,2),
    interfaces      TEXT[]       NOT NULL DEFAULT '{}',     -- array de nombres
    uptime_seconds  INTEGER,
    read_method     VARCHAR(10)  NOT NULL DEFAULT 'none',   -- snmp | wmi | ssh | none
    last_updated    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────
-- Historial de cambios de hostname
-- Detecta cuando una IP fue reasignada a otro equipo
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hostname_changes (
    id           SERIAL PRIMARY KEY,
    device_id    INTEGER      NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    old_hostname VARCHAR(255) NOT NULL,
    new_hostname VARCHAR(255) NOT NULL,
    detected_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hostname_changes_device_id ON hostname_changes(device_id);
"""
db = Database()