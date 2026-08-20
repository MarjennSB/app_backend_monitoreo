"""
storage/repository.py
────────────────────────────────────────────────────────────────
Acceso estructurado a datos — operaciones CRUD por entidad.

Equivalente a los Repositories o al Query Builder de Laravel.
Cada clase encapsula las queries de una tabla específica.

Responsabilidades:
  - SELECT, INSERT, UPDATE, DELETE tipados por entidad
  - Retornar modelos de models.py (no Records crudos)
  - Sin lógica de negocio — solo persistencia

Arquitectura:
  scanner.py / analyzer.py
      ↓ datos crudos
  repository.py   ← este archivo
      ↓ queries SQL
  database.py (pool asyncpg)
      ↓ TCP
  PostgreSQL 5432

Uso:
  from modules.storage.repository import (
      NetworkRepository, DeviceRepository,
      ScanResultRepository, StatsRepository
  )

  net_repo   = NetworkRepository()
  device_repo = DeviceRepository()
  await device_repo.upsert(network_id=1, ip="192.168.1.50", ...)
────────────────────────────────────────────────────────────────
"""

from datetime import datetime
from typing import Optional

from modules.storage.database import db
from modules.storage.models import (
    NetworkModel,
    VlanModel,
    DeviceModel,
    ScanResultModel,
    PortCheckModel,
    DeviceStatsModel,
    InventoryModel,
    HostnameChangeModel,
)
from modules.inventory.normalizer import DeviceInfo
from modules.services.analyzer import ServiceStats


# ──────────────────────────────────────────────
# REPOSITORY — Vlans
# ──────────────────────────────────────────────

class VlanRepository:
    """CRUD para la tabla 'vlans'."""

    async def get_all(self) -> list[VlanModel]:
        rows = await db.fetch_all("SELECT * FROM vlans ORDER BY created_at")
        return [VlanModel.from_record(r) for r in rows]

    async def get_by_id(self, vlan_id: int) -> Optional[VlanModel]:
        row = await db.fetch_one("SELECT * FROM vlans WHERE id = $1", vlan_id)
        return VlanModel.from_record(row) if row else None

    async def create(self, name: str, description: str = "", is_active: bool = True) -> VlanModel:
        row = await db.fetch_one(
            """
            INSERT INTO vlans (name, description, is_active)
            VALUES ($1, $2, $3)
            RETURNING *
            """,
            name, description, is_active,
        )
        return VlanModel.from_record(row)

    async def update(
        self,
        vlan_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[VlanModel]:
        row = await db.fetch_one(
            """
            UPDATE vlans SET
                name        = COALESCE($2, name),
                description = COALESCE($3, description),
                is_active   = COALESCE($4, is_active),
                updated_at  = NOW()
            WHERE id = $1
            RETURNING *
            """,
            vlan_id, name, description, is_active,
        )
        return VlanModel.from_record(row) if row else None

    async def delete(self, vlan_id: int) -> bool:
        status = await db.execute("DELETE FROM vlans WHERE id = $1", vlan_id)
        return status == "DELETE 1"

# ──────────────────────────────────────────────
# REPOSITORY — Networks
# ──────────────────────────────────────────────

class NetworkRepository:
    """
    CRUD para la tabla 'networks'.

    Equivalente al NetworkController/Repository de Laravel.
    """

    async def get_all(self) -> list[NetworkModel]:
        """Retorna todas las redes configuradas."""
        rows = await db.fetch_all(
            """
            SELECT n.*, v.name as vlan_name 
            FROM networks n
            LEFT JOIN vlans v ON n.vlan_id = v.id
            ORDER BY n.created_at
            """
        )
        return [NetworkModel.from_record(r) for r in rows]

    async def get_paginated_with_counts(self, page: int = 1, limit: int = 10) -> dict:
        """
        Retorna redes paginadas con contadores de dispositivos (vivo/muerto).
        Evita el problema N+1 usando un LEFT JOIN y COUNT en PostgreSQL.
        """
        offset = (page - 1) * limit
        
        # 1. Total de redes para la paginación
        total_count = await db.fetch_val("SELECT COUNT(*) FROM networks")
        
        # 2. Query con JOIN para estadísticas
        query = """
            SELECT 
                n.*,
                v.name as vlan_name,
                COUNT(d.id) AS total_devices,
                COUNT(d.id) FILTER (WHERE d.is_alive = true) AS alive_devices,
                COUNT(d.id) FILTER (WHERE d.is_alive = false) AS dead_devices
            FROM networks n
            LEFT JOIN vlans v ON n.vlan_id = v.id
            LEFT JOIN devices d ON n.id = d.network_id
            GROUP BY n.id, v.id
            ORDER BY n.id ASC
            LIMIT $1 OFFSET $2
        """
        rows = await db.fetch_all(query, limit, offset)
        
        networks = []
        for r in rows:
            net_dict = dict(r)
            # Extraer contadores
            counts = {
                "total": net_dict.pop("total_devices", 0),
                "alive": net_dict.pop("alive_devices", 0),
                "dead": net_dict.pop("dead_devices", 0),
            }
            net_model = NetworkModel.from_record(net_dict)
            networks.append({
                "network": net_model,
                "counts": counts
            })
            
        return {
            "data": networks,
            "total": total_count,
            "page": page,
            "limit": limit,
            "total_pages": (total_count + limit - 1) // limit if total_count > 0 else 1
        }

    async def get_by_id(self, network_id: int) -> Optional[NetworkModel]:
        """Busca una red por ID."""
        row = await db.fetch_one(
            """
            SELECT n.*, v.name as vlan_name 
            FROM networks n
            LEFT JOIN vlans v ON n.vlan_id = v.id
            WHERE n.id = $1
            """, network_id
        )
        return NetworkModel.from_record(row) if row else None

    async def get_by_cidr(self, cidr: str) -> Optional[NetworkModel]:
        """Busca una red por su CIDR."""
        row = await db.fetch_one(
            """
            SELECT n.*, v.name as vlan_name 
            FROM networks n
            LEFT JOIN vlans v ON n.vlan_id = v.id
            WHERE n.cidr = $1
            """, cidr
        )
        return NetworkModel.from_record(row) if row else None

    async def create(
        self,
        cidr: str,
        vlan_id: Optional[int] = None,
        scan_interval: int = 300,
    ) -> NetworkModel:
        """
        Crea una nueva red.

        Equivalente a Network::create([...]) en Laravel.
        """
        row = await db.fetch_one(
            """
            INSERT INTO networks (cidr, vlan_id, scan_interval)
            VALUES ($1, $2, $3)
            RETURNING *
            """,
            cidr, vlan_id, scan_interval,
        )
        return NetworkModel.from_record(row)

    async def update(
        self,
        network_id: int,
        cidr: Optional[str] = None,
        vlan_id: Optional[int] = None,
        scan_interval: Optional[int] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[NetworkModel]:
        """
        Actualiza campos de una red.

        Equivalente a $network->update([...]) en Laravel.
        """
        row = await db.fetch_one(
            """
            UPDATE networks SET
                cidr          = COALESCE($2, cidr),
                vlan_id       = COALESCE($3, vlan_id),
                scan_interval = COALESCE($4, scan_interval),
                is_active     = COALESCE($5, is_active),
                updated_at    = NOW()
            WHERE id = $1
            RETURNING *
            """,
            network_id, cidr, vlan_id, scan_interval, is_active,
        )
        return NetworkModel.from_record(row) if row else None

    async def delete(self, network_id: int) -> bool:
        """
        Elimina una red y todos sus dispositivos (CASCADE).

        Equivalente a $network->delete() en Laravel.
        """
        status = await db.execute(
            "DELETE FROM networks WHERE id = $1", network_id
        )
        return status == "DELETE 1"

    async def get_active(self) -> list[NetworkModel]:
        """Retorna solo las redes con is_active=True."""
        rows = await db.fetch_all(
            "SELECT * FROM networks WHERE is_active = TRUE ORDER BY cidr"
        )
        return [NetworkModel.from_record(r) for r in rows]


# ──────────────────────────────────────────────
# REPOSITORY — Devices
# ──────────────────────────────────────────────

class DeviceRepository:
    """
    CRUD para la tabla 'devices'.

    El método principal es upsert(): crea el dispositivo si no existe
    o actualiza sus campos si ya estaba en la BD.

    Equivalente al patrón updateOrCreate() de Eloquent:
      Device::updateOrCreate(['ip' => $ip], $data)
    """

    async def get_all(self) -> list[DeviceModel]:
        """Retorna todos los dispositivos globalmente."""
        rows = await db.fetch_all(
            """
            SELECT * FROM devices
            ORDER BY ip
            """
        )
        return [DeviceModel.from_record(r) for r in rows]

    async def get_all_alive(self) -> list[DeviceModel]:
        """Retorna todos los dispositivos activos globalmente."""
        rows = await db.fetch_all(
            """
            SELECT * FROM devices
            WHERE is_alive = TRUE
            ORDER BY ip
            """
        )
        return [DeviceModel.from_record(r) for r in rows]

    async def get_all(self) -> list[DeviceModel]:
        """Retorna todos los dispositivos de todas las redes."""
        rows = await db.fetch_all(
            """
            SELECT * FROM devices
            ORDER BY ip
            """
        )
        return [DeviceModel.from_record(r) for r in rows]

    async def get_all_alive(self) -> list[DeviceModel]:
        """Retorna todos los dispositivos activos de todas las redes."""
        rows = await db.fetch_all(
            """
            SELECT * FROM devices
            WHERE is_alive = TRUE
            ORDER BY ip
            """
        )
        return [DeviceModel.from_record(r) for r in rows]

    async def get_by_network(self, network_id: int) -> list[DeviceModel]:
        """Retorna todos los dispositivos de una red."""
        rows = await db.fetch_all(
            """
            SELECT * FROM devices
            WHERE network_id = $1
            ORDER BY ip
            """,
            network_id,
        )
        return [DeviceModel.from_record(r) for r in rows]

    async def get_alive_by_network(self, network_id: int) -> list[DeviceModel]:
        """Retorna solo los dispositivos activos de una red."""
        rows = await db.fetch_all(
            """
            SELECT * FROM devices
            WHERE network_id = $1 AND is_alive = TRUE
            ORDER BY ip
            """,
            network_id,
        )
        return [DeviceModel.from_record(r) for r in rows]

    async def get_by_ip(self, network_id: int, ip: str) -> Optional[DeviceModel]:
        """Busca un dispositivo por IP dentro de una red."""
        row = await db.fetch_one(
            "SELECT * FROM devices WHERE network_id=$1 AND ip=$2::inet",
            network_id, ip,
        )
        return DeviceModel.from_record(row) if row else None

    async def get_by_id(self, device_id: int) -> Optional[DeviceModel]:
        """Busca un dispositivo por su ID."""
        row = await db.fetch_one(
            "SELECT * FROM devices WHERE id = $1", device_id
        )
        return DeviceModel.from_record(row) if row else None

    async def upsert(
        self,
        network_id: int,
        ip: str,
        hostname: str = "unknown",
        hostname_method: str = "unknown",
        is_alive: bool = False,
        mac_address: Optional[str] = None,
    ) -> DeviceModel:
        """
        Crea o actualiza un dispositivo.

        Equivalente a Device::updateOrCreate() en Laravel.
        Si el dispositivo ya existe (por network_id + ip),
        actualiza sus campos. Si no, lo crea.

        Args:
            network_id:      ID de la red.
            ip:              Dirección IP.
            hostname:        Nombre resuelto.
            hostname_method: Método de resolución.
            is_alive:        Si respondió en el scan.
            mac_address:     MAC address si está disponible.

        Returns:
            DeviceModel actualizado o creado.
        """
        row = await db.fetch_one(
            """
            INSERT INTO devices
                (network_id, ip, hostname, hostname_method,
                 is_alive, mac_address, last_seen_at, first_seen_at)
            VALUES
                ($1, $2::inet, $3, $4, $5, $6,
                 CASE WHEN $5 THEN NOW() ELSE NULL END,
                 NOW())
            ON CONFLICT (network_id, ip)
            DO UPDATE SET
                hostname        = CASE 
                                    WHEN EXCLUDED.hostname != 'unknown' THEN EXCLUDED.hostname 
                                    ELSE devices.hostname 
                                  END,
                hostname_method = CASE 
                                    WHEN EXCLUDED.hostname_method != 'unknown' THEN EXCLUDED.hostname_method 
                                    ELSE devices.hostname_method 
                                  END,
                is_alive        = EXCLUDED.is_alive,
                mac_address     = COALESCE(EXCLUDED.mac_address, devices.mac_address),
                last_seen_at    = CASE WHEN EXCLUDED.is_alive THEN NOW()
                                       ELSE devices.last_seen_at END,
                updated_at      = NOW()
            RETURNING *
            """,
            network_id, ip, hostname, hostname_method,
            is_alive, mac_address,
        )
        return DeviceModel.from_record(row)

    async def count_by_network(self, network_id: int) -> dict:
        """
        Cuenta dispositivos activos e inactivos en una red.

        Usado para el resumen en el dashboard.
        """
        row = await db.fetch_one(
            """
            SELECT
                COUNT(*)                          AS total,
                COUNT(*) FILTER (WHERE is_alive)  AS alive,
                COUNT(*) FILTER (WHERE NOT is_alive) AS down
            FROM devices
            WHERE network_id = $1
            """,
            network_id,
        )
        return {
            "total": row["total"] or 0,
            "alive": row["alive"] or 0,
            "down":  row["down"] or 0,
        }

    async def get_all_critical_devices(self) -> list[DeviceModel]:
        """Retorna todos los dispositivos marcados como críticos (VIP)."""
        rows = await db.fetch_all(
            """
            SELECT * FROM devices
            WHERE is_critical = TRUE
            ORDER BY ip
            """
        )
        return [DeviceModel.from_record(r) for r in rows]

    async def update_critical_status(self, device_id: int, is_critical: bool) -> Optional[DeviceModel]:
        """Actualiza el estado VIP de un dispositivo."""
        row = await db.fetch_one(
            """
            UPDATE devices SET
                is_critical = $2,
                updated_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            device_id, is_critical
        )
        return DeviceModel.from_record(row) if row else None


# ──────────────────────────────────────────────
# REPOSITORY — ScanResults
# ──────────────────────────────────────────────

class ScanResultRepository:
    """
    CRUD para la tabla 'scan_results'.

    Historial de cada ciclo de scan completo.
    """

    async def save(
        self,
        network_id: int,
        started_at: datetime,
        finished_at: datetime,
        duration_ms: int,
        total_hosts: int,
        active_hosts: int,
        inactive_hosts: int,
    ) -> ScanResultModel:
        """
        Guarda el resultado de un scan completo.

        Llamado por el scanner al finalizar cada ciclo.
        """
        row = await db.fetch_one(
            """
            INSERT INTO scan_results
                (network_id, started_at, finished_at, duration_ms,
                 total_hosts, active_hosts, inactive_hosts)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            network_id, started_at, finished_at, duration_ms,
            total_hosts, active_hosts, inactive_hosts,
        )
        return ScanResultModel.from_record(row)

    async def get_latest(
        self, network_id: int, limit: int = 10
    ) -> list[ScanResultModel]:
        """Retorna los últimos N resultados de una red."""
        rows = await db.fetch_all(
            """
            SELECT * FROM scan_results
            WHERE network_id = $1
            ORDER BY finished_at DESC
            LIMIT $2
            """,
            network_id, limit,
        )
        return [ScanResultModel.from_record(r) for r in rows]

    async def get_last(self, network_id: int) -> Optional[ScanResultModel]:
        """Retorna el resultado del último scan de una red."""
        results = await self.get_latest(network_id, limit=1)
        return results[0] if results else None


# ──────────────────────────────────────────────
# REPOSITORY — PortChecks
# ──────────────────────────────────────────────

class PortCheckRepository:
    """
    CRUD para la tabla 'port_checks'.

    Historial del estado de puertos TCP por dispositivo.
    """

    async def save_batch(
        self,
        device_id: int,
        checks: list[dict],
    ) -> None:
        """
        Guarda múltiples verificaciones de puertos de un dispositivo.

        Args:
            device_id: ID del dispositivo.
            checks:    Lista de dicts con 'port', 'state', 'rtt_ms'.
        """
        if not checks:
            return

        async with db.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO port_checks (device_id, port, state, rtt_ms, checked_at)
                VALUES ($1, $2, $3, $4, NOW())
                """,
                [(device_id, c["port"], c["state"], c.get("rtt_ms")) for c in checks],
            )

    async def get_latest_by_device(self, device_id: int) -> list[PortCheckModel]:
        """
        Retorna el estado más reciente de cada puerto de un dispositivo.

        Útil para mostrar en el detalle del dispositivo.
        """
        rows = await db.fetch_all(
            """
            SELECT DISTINCT ON (port) *
            FROM port_checks
            WHERE device_id = $1
            ORDER BY port, checked_at DESC
            """,
            device_id,
        )
        return [PortCheckModel.from_record(r) for r in rows]

    async def get_open_ports_by_network(self, network_id: int) -> dict[int, list[int]]:
        """
        Retorna un diccionario mapeando device_id -> lista de puertos abiertos.
        Solo incluye los puertos cuyo estado más reciente sea 'open'.
        """
        rows = await db.fetch_all(
            """
            WITH LatestPorts AS (
                SELECT DISTINCT ON (pc.device_id, pc.port)
                       pc.device_id, pc.port, pc.state
                FROM port_checks pc
                JOIN devices d ON d.id = pc.device_id
                WHERE d.network_id = $1
                ORDER BY pc.device_id, pc.port, pc.checked_at DESC
            )
            SELECT device_id, port
            FROM LatestPorts
            WHERE state = 'open'
            """,
            network_id,
        )
        
        result = {}
        for r in rows:
            did = r["device_id"]
            if did not in result:
                result[did] = []
            result[did].append(r["port"])
            
        return result


# ──────────────────────────────────────────────
# REPOSITORY — DeviceStats
# ──────────────────────────────────────────────

class StatsRepository:
    """
    CRUD para la tabla 'device_stats'.

    Persiste las estadísticas acumuladas del analyzer.py.
    """

    async def upsert_from_analyzer(
        self,
        device_id: int,
        stats: ServiceStats,
    ) -> None:
        """
        Crea o actualiza las estadísticas de un dispositivo.

        Equivalente a DeviceStat::updateOrCreate() en Laravel.
        Convierte las duraciones de timedelta a segundos enteros.

        Args:
            device_id: ID del dispositivo en la BD.
            stats:     ServiceStats del analyzer.py.
        """
        await db.execute(
            """
            INSERT INTO device_stats (
                device_id, total_probes, successful_probes, failed_probes,
                ongoing_successful, ongoing_failed, availability_percent,
                rtt_min_ms, rtt_max_ms, rtt_avg_ms,
                total_uptime_seconds, total_downtime_seconds,
                longest_uptime_seconds, longest_downtime_seconds,
                last_seen_at, last_down_at, monitoring_since, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15, $16, $17, NOW()
            )
            ON CONFLICT (device_id)
            DO UPDATE SET
                total_probes             = EXCLUDED.total_probes,
                successful_probes        = EXCLUDED.successful_probes,
                failed_probes            = EXCLUDED.failed_probes,
                ongoing_successful       = EXCLUDED.ongoing_successful,
                ongoing_failed           = EXCLUDED.ongoing_failed,
                availability_percent     = EXCLUDED.availability_percent,
                rtt_min_ms               = EXCLUDED.rtt_min_ms,
                rtt_max_ms               = EXCLUDED.rtt_max_ms,
                rtt_avg_ms               = EXCLUDED.rtt_avg_ms,
                total_uptime_seconds     = EXCLUDED.total_uptime_seconds,
                total_downtime_seconds   = EXCLUDED.total_downtime_seconds,
                longest_uptime_seconds   = EXCLUDED.longest_uptime_seconds,
                longest_downtime_seconds = EXCLUDED.longest_downtime_seconds,
                last_seen_at             = EXCLUDED.last_seen_at,
                last_down_at             = EXCLUDED.last_down_at,
                updated_at               = NOW()
            """,
            device_id,
            stats.total_probes,
            stats.successful_probes,
            stats.failed_probes,
            stats.ongoing_successful,
            stats.ongoing_failed,
            stats.availability_percent,
            stats.rtt.min_ms if stats.rtt.has_results else None,
            stats.rtt.max_ms if stats.rtt.has_results else None,
            stats.rtt.avg_ms if stats.rtt.has_results else None,
            int(stats.total_uptime.total_seconds()),
            int(stats.total_downtime.total_seconds()),
            int(stats.longest_uptime.duration.total_seconds()),
            int(stats.longest_downtime.duration.total_seconds()),
            stats.last_successful_probe,
            stats.last_failed_probe,
            stats.monitoring_since,
        )

    async def get_by_device(self, device_id: int) -> Optional[DeviceStatsModel]:
        """Retorna las estadísticas de un dispositivo."""
        row = await db.fetch_one(
            "SELECT * FROM device_stats WHERE device_id = $1", device_id
        )
        return DeviceStatsModel.from_record(row) if row else None

    async def get_worst_availability(
        self, network_id: int, limit: int = 10
    ) -> list[DeviceStatsModel]:
        """
        Retorna los dispositivos con peor disponibilidad en una red.

        Útil para el dashboard de soporte: "¿Quién falla más?"
        """
        rows = await db.fetch_all(
            """
            SELECT ds.*
            FROM device_stats ds
            JOIN devices d ON d.id = ds.device_id
            WHERE d.network_id = $1
            ORDER BY ds.availability_percent ASC
            LIMIT $2
            """,
            network_id, limit,
        )
        return [DeviceStatsModel.from_record(r) for r in rows]


# ──────────────────────────────────────────────
# REPOSITORY — Inventory
# ──────────────────────────────────────────────

class InventoryRepository:
    """
    CRUD para la tabla 'device_inventory'.

    Persiste el DeviceInfo del normalizer.py.
    """

    async def upsert_from_device_info(
        self,
        device_id: int,
        info: DeviceInfo,
    ) -> None:
        """
        Crea o actualiza el inventario de un dispositivo.

        Args:
            device_id: ID del dispositivo en la BD.
            info:      DeviceInfo del normalizer.py.
        """
        await db.execute(
            """
            INSERT INTO device_inventory (
                device_id, device_type, manufacturer, model, description,
                location, contact, os_info, cpu_model, ram_mb, disk_gb,
                interfaces, uptime_seconds, read_method, last_updated
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12::text[], $13, $14, NOW()
            )
            ON CONFLICT (device_id)
            DO UPDATE SET
                device_type    = EXCLUDED.device_type,
                manufacturer   = EXCLUDED.manufacturer,
                model          = EXCLUDED.model,
                description    = EXCLUDED.description,
                location       = EXCLUDED.location,
                contact        = EXCLUDED.contact,
                os_info        = EXCLUDED.os_info,
                cpu_model      = EXCLUDED.cpu_model,
                ram_mb         = EXCLUDED.ram_mb,
                disk_gb        = EXCLUDED.disk_gb,
                interfaces     = EXCLUDED.interfaces,
                uptime_seconds = EXCLUDED.uptime_seconds,
                read_method    = EXCLUDED.read_method,
                last_updated   = NOW()
            """,
            device_id,
            info.device_type,
            info.manufacturer,
            info.model,
            info.description,
            info.location,
            info.contact,
            info.os_info,
            info.cpu_model,
            info.ram_mb,
            info.disk_gb,
            info.interfaces,
            info.uptime_seconds,
            info.read_method,
        )

    async def get_by_device(self, device_id: int) -> Optional[InventoryModel]:
        """Retorna el inventario de un dispositivo."""
        row = await db.fetch_one(
            "SELECT * FROM device_inventory WHERE device_id = $1", device_id
        )
        return InventoryModel.from_record(row) if row else None

    async def get_all(self) -> list[InventoryModel]:
        """Retorna el inventario de todos los dispositivos registrados."""
        rows = await db.fetch_all("SELECT * FROM device_inventory")
        return [InventoryModel.from_record(r) for r in rows]

    async def get_by_network(self, network_id: int) -> list[InventoryModel]:
        """Retorna el inventario de todos los dispositivos de una red."""
        rows = await db.fetch_all(
            """
            SELECT di.*
            FROM device_inventory di
            JOIN devices d ON d.id = di.device_id
            WHERE d.network_id = $1
            ORDER BY d.ip
            """,
            network_id,
        )
        return [InventoryModel.from_record(r) for r in rows]

    async def get_by_type(
        self,
        network_id: int,
        device_type: str,
    ) -> list[InventoryModel]:
        """
        Filtra inventario por tipo de dispositivo en una red.

        Ej: get_by_type(1, "switch") → todos los switches de la red 1.
        """
        rows = await db.fetch_all(
            """
            SELECT di.*
            FROM device_inventory di
            JOIN devices d ON d.id = di.device_id
            WHERE d.network_id = $1 AND di.device_type = $2
            ORDER BY d.ip
            """,
            network_id, device_type,
        )
        return [InventoryModel.from_record(r) for r in rows]

    async def bulk_upsert_inventory_and_hostnames(self, processed_data: list[dict]) -> None:
        """
        Inserta o actualiza masivamente el inventario proveniente de sheets_sync.py.
        También actualiza el hostname en 'devices' y registra el cambio en 'hostname_changes'.
        """
        for item in processed_data:
            # 1. Obtener device_id usando la IP (solo se actualizan los que ya existen en devices)
            device_row = await db.fetch_one("SELECT id, hostname FROM devices WHERE ip = $1", item["ip"])
            if not device_row:
                continue
            
            device_id = device_row["id"]
            old_hostname = device_row["hostname"]
            new_hostname = item["hostname"]

            # 2. Actualizar inventario (El campo 'graph' lo mapeamos temporalmente a 'description' de la BD)
            await db.execute(
                """
                INSERT INTO device_inventory (
                    device_id, device_type, manufacturer, model, description,
                    location, contact, os_info, cpu_model, ram_mb, disk_gb,
                    read_method, last_updated
                ) VALUES (
                    $1, $2, 'unknown', $3, $4, $5, $6, $7, $8, $9, $10, 'excel', NOW()
                )
                ON CONFLICT (device_id)
                DO UPDATE SET
                    device_type    = COALESCE(NULLIF(EXCLUDED.device_type, 'unknown'), device_inventory.device_type),
                    model          = COALESCE(NULLIF(EXCLUDED.model, 'unknown'), device_inventory.model),
                    description    = COALESCE(NULLIF(EXCLUDED.description, ''), device_inventory.description, ''),
                    location       = COALESCE(NULLIF(EXCLUDED.location, ''), device_inventory.location, ''),
                    contact        = COALESCE(NULLIF(EXCLUDED.contact, ''), device_inventory.contact, ''),
                    os_info        = COALESCE(NULLIF(EXCLUDED.os_info, ''), device_inventory.os_info, ''),
                    cpu_model      = COALESCE(NULLIF(EXCLUDED.cpu_model, ''), device_inventory.cpu_model, ''),
                    ram_mb         = COALESCE(EXCLUDED.ram_mb, device_inventory.ram_mb),
                    disk_gb        = COALESCE(EXCLUDED.disk_gb, device_inventory.disk_gb),
                    read_method    = 'excel',
                    last_updated   = NOW()
                """,
                device_id,
                item["device_type"],
                item["model"],
                item["graph"],  # El diccionario Python trae 'graph', y lo insertamos en el campo description de la BD
                item["location"],
                item["contact"],
                item["os_info"],
                item["cpu_model"],
                item["ram_mb"],
                item["disk_gb"]
            )

            # 3. Lógica de Hostname Change
            # Si el Excel trae un hostname válido y es diferente al que tenemos:
            if new_hostname and new_hostname != "unknown" and old_hostname != new_hostname:
                # Actualizamos la tabla principal de dispositivos
                await db.execute(
                    "UPDATE devices SET hostname = $1, updated_at = NOW() WHERE id = $2",
                    new_hostname, device_id
                )
                
                # Registramos en el historial SOLO si el viejo no era 'unknown'
                # (pasar de unknown a un nombre no es un "cambio" propiamente, es un descubrimiento)
                if old_hostname != "unknown":
                    await db.execute(
                        """
                        INSERT INTO hostname_changes (device_id, old_hostname, new_hostname)
                        VALUES ($1, $2, $3)
                        """,
                        device_id, old_hostname, new_hostname
                    )


# ──────────────────────────────────────────────
# REPOSITORY — HostnameChanges
# ──────────────────────────────────────────────

class HostnameChangeRepository:
    """CRUD para la tabla 'hostname_changes'."""

    async def save(
        self,
        device_id: int,
        old_hostname: str,
        new_hostname: str,
    ) -> HostnameChangeModel:
        """Registra un cambio de hostname detectado."""
        row = await db.fetch_one(
            """
            INSERT INTO hostname_changes (device_id, old_hostname, new_hostname)
            VALUES ($1, $2, $3)
            RETURNING *
            """,
            device_id, old_hostname, new_hostname,
        )
        return HostnameChangeModel.from_record(row)

    async def get_by_device(
        self, device_id: int, limit: int = 20
    ) -> list[HostnameChangeModel]:
        """Historial de cambios de nombre de un dispositivo."""
        rows = await db.fetch_all(
            """
            SELECT * FROM hostname_changes
            WHERE device_id = $1
            ORDER BY detected_at DESC
            LIMIT $2
            """,
            device_id, limit,
        )
        return [HostnameChangeModel.from_record(r) for r in rows]