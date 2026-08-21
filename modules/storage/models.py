"""
storage/models.py
────────────────────────────────────────────────────────────────
Modelos de datos Python que mapean a las tablas PostgreSQL.

Equivalente a los Models de Eloquent en Laravel, pero sin ORM:
son dataclasses simples que representan filas de la BD.

Relación con las tablas de database.py:
  NetworkModel      → tabla 'networks'
  DeviceModel       → tabla 'devices'
  ScanResultModel   → tabla 'scan_results'
  PortCheckModel    → tabla 'port_checks'
  DeviceStatsModel  → tabla 'device_stats'
  InventoryModel    → tabla 'device_inventory'
  HostnameChangeModel → tabla 'hostname_changes'

Estos modelos son el "contrato" entre la BD y el resto del sistema.
Repository los usa para construir y retornar datos tipados.
────────────────────────────────────────────────────────────────
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ──────────────────────────────────────────────
# MODELO — Vlan (tabla: vlans)
# ──────────────────────────────────────────────

@dataclass
class VlanModel:
    name: str
    description: str = ""
    is_active: bool = True
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_record(cls, row) -> "VlanModel":
        return cls(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            is_active=row["is_active"],
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


# ──────────────────────────────────────────────
# MODELO — Networks (tabla: networks)
# ──────────────────────────────────────────────

@dataclass
class NetworkModel:
    """
    Red configurada para monitoreo.

    Equivalente al Model Network de Laravel con:
      $fillable = ['cidr', 'vlan_id', 'scan_interval', 'is_active']

    Attributes:
        id:            ID autoincremental (SERIAL).
        cidr:          Red en notación CIDR (ej: "192.168.1.0/24").
        vlan_id:       ID de VLAN si aplica.
        scan_interval: Segundos entre ciclos de autoscan.
        is_active:     Si el autoscan está habilitado para esta red.
        created_at:    Timestamp de creación.
        updated_at:    Timestamp de última modificación.
    """
    cidr: str
    scan_interval: int                = 300
    is_active: bool                   = True
    vlan_id: Optional[int]            = None
    id: Optional[int]                 = None
    created_at: Optional[datetime]    = None
    updated_at: Optional[datetime]    = None

    # Opcional: Propiedad para tener datos relacionales (joined data)
    vlan_name: Optional[str]          = None

    @classmethod
    def from_record(cls, row) -> "NetworkModel":
        """
        Construye el modelo desde un Record de asyncpg.

        Equivalente al método fromArray() o al hydration de Eloquent.
        """
        model = cls(
            id=row["id"],
            cidr=row["cidr"],
            vlan_id=row["vlan_id"],
            scan_interval=row["scan_interval"],
            is_active=row["is_active"],
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )
        if "vlan_name" in row:
            model.vlan_name = row["vlan_name"]
        elif "label" in row:
            model.vlan_name = row["label"]
        return model


# ──────────────────────────────────────────────
# MODELO — Device (tabla: devices)
# ──────────────────────────────────────────────

@dataclass
class DeviceModel:
    """
    Dispositivo descubierto en la red.

    Equivalente al Model Device de Laravel con:
      $fillable = ['network_id', 'ip', 'hostname', 'hostname_method',
                   'mac_address', 'is_alive', 'last_seen_at']

    Attributes:
        network_id:      FK a networks.id.
        ip:              Dirección IP del dispositivo.
        hostname:        Nombre resuelto del dispositivo.
        hostname_method: Cómo se obtuvo el hostname (dns-ptr/netbios/snmp).
        is_alive:        Si respondió en el último scan.
        mac_address:     MAC si está disponible.
        last_seen_at:    Última vez que respondió al scan.
        first_seen_at:   Primera vez que fue descubierto.
    """
    network_id: int
    ip: str
    hostname: str                     = "unknown"
    hostname_method: str              = "unknown"
    is_alive: bool                    = False
    is_critical: bool                 = False
    failed_pings_count: int           = 0
    mac_address: Optional[str]        = None
    last_seen_at: Optional[datetime]  = None
    first_seen_at: Optional[datetime] = None
    id: Optional[int]                 = None
    created_at: Optional[datetime]    = None
    updated_at: Optional[datetime]    = None

    @classmethod
    def from_record(cls, row) -> "DeviceModel":
        return cls(
            id=row["id"],
            network_id=row["network_id"],
            ip=str(row["ip"]),
            hostname=row["hostname"],
            hostname_method=row["hostname_method"],
            mac_address=row.get("mac_address"),
            is_alive=row["is_alive"],
            is_critical=row.get("is_critical", False),
            failed_pings_count=row.get("failed_pings_count", 0),
            last_seen_at=row.get("last_seen_at"),
            first_seen_at=row.get("first_seen_at"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


# ──────────────────────────────────────────────
# MODELO — ScanResult (tabla: scan_results)
# ──────────────────────────────────────────────

@dataclass
class ScanResultModel:
    """
    Resultado de un ciclo de scan completo.

    Historial de cuándo se escaneó una red y qué se encontró.

    Attributes:
        network_id:     FK a networks.id.
        started_at:     Inicio del scan.
        finished_at:    Fin del scan.
        duration_ms:    Duración total en milisegundos.
        total_hosts:    Total de IPs escaneadas.
        active_hosts:   Hosts que respondieron.
        inactive_hosts: Hosts que no respondieron.
    """
    network_id: int
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    total_hosts: int     = 0
    active_hosts: int    = 0
    inactive_hosts: int  = 0
    id: Optional[int]    = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_record(cls, row) -> "ScanResultModel":
        return cls(
            id=row["id"],
            network_id=row["network_id"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            duration_ms=row["duration_ms"],
            total_hosts=row["total_hosts"],
            active_hosts=row["active_hosts"],
            inactive_hosts=row["inactive_hosts"],
            created_at=row.get("created_at"),
        )


# ──────────────────────────────────────────────
# MODELO — PortCheck (tabla: port_checks)
# ──────────────────────────────────────────────

@dataclass
class PortCheckModel:
    """
    Estado de un puerto TCP en un momento específico.

    Permite ver el historial de cuándo un puerto estuvo
    abierto, cerrado o filtrado.

    Attributes:
        device_id:  FK a devices.id.
        port:       Número de puerto TCP.
        state:      Estado: "open" | "closed" | "filtered" | "error".
        rtt_ms:     RTT en milisegundos (con decimales).
        checked_at: Timestamp de la verificación.
    """
    device_id: int
    port: int
    state: str
    rtt_ms: Optional[float]  = None
    checked_at: Optional[datetime] = None
    id: Optional[int]        = None

    @classmethod
    def from_record(cls, row) -> "PortCheckModel":
        return cls(
            id=row["id"],
            device_id=row["device_id"],
            port=row["port"],
            state=row["state"],
            rtt_ms=float(row["rtt_ms"]) if row.get("rtt_ms") else None,
            checked_at=row.get("checked_at"),
        )


# ──────────────────────────────────────────────
# MODELO — DeviceStats (tabla: device_stats)
# ──────────────────────────────────────────────

@dataclass
class DeviceStatsModel:
    """
    Estadísticas acumuladas de un dispositivo.

    Equivalente al ServiceStats del analyzer.py pero persistido
    en PostgreSQL. Se actualiza en cada ciclo de scan.

    Attributes:
        device_id:               FK a devices.id (UNIQUE).
        total_probes:            Total de verificaciones.
        successful_probes:       Verificaciones exitosas.
        failed_probes:           Verificaciones fallidas.
        ongoing_successful:      Racha actual de éxitos.
        ongoing_failed:          Racha actual de fallos.
        availability_percent:    % de disponibilidad histórica.
        rtt_min_ms:              RTT mínimo registrado.
        rtt_max_ms:              RTT máximo registrado.
        rtt_avg_ms:              RTT promedio acumulado.
        total_uptime_seconds:    Uptime total acumulado.
        total_downtime_seconds:  Downtime total acumulado.
        longest_uptime_seconds:  Mejor racha de uptime.
        longest_downtime_seconds: Peor incidente de downtime.
    """
    device_id: int
    total_probes: int             = 0
    successful_probes: int        = 0
    failed_probes: int            = 0
    ongoing_successful: int       = 0
    ongoing_failed: int           = 0
    availability_percent: float   = 0.0
    rtt_min_ms: Optional[float]   = None
    rtt_max_ms: Optional[float]   = None
    rtt_avg_ms: Optional[float]   = None
    total_uptime_seconds: int     = 0
    total_downtime_seconds: int   = 0
    longest_uptime_seconds: int   = 0
    longest_downtime_seconds: int = 0
    last_seen_at: Optional[datetime]  = None
    last_down_at: Optional[datetime]  = None
    monitoring_since: Optional[datetime] = None
    id: Optional[int]             = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_record(cls, row) -> "DeviceStatsModel":
        return cls(
            id=row["id"],
            device_id=row["device_id"],
            total_probes=row["total_probes"],
            successful_probes=row["successful_probes"],
            failed_probes=row["failed_probes"],
            ongoing_successful=row["ongoing_successful"],
            ongoing_failed=row["ongoing_failed"],
            availability_percent=float(row["availability_percent"]),
            rtt_min_ms=float(row["rtt_min_ms"]) if row.get("rtt_min_ms") else None,
            rtt_max_ms=float(row["rtt_max_ms"]) if row.get("rtt_max_ms") else None,
            rtt_avg_ms=float(row["rtt_avg_ms"]) if row.get("rtt_avg_ms") else None,
            total_uptime_seconds=row["total_uptime_seconds"],
            total_downtime_seconds=row["total_downtime_seconds"],
            longest_uptime_seconds=row["longest_uptime_seconds"],
            longest_downtime_seconds=row["longest_downtime_seconds"],
            last_seen_at=row.get("last_seen_at"),
            last_down_at=row.get("last_down_at"),
            monitoring_since=row.get("monitoring_since"),
            updated_at=row.get("updated_at"),
        )


# ──────────────────────────────────────────────
# MODELO — InventoryModel (tabla: device_inventory)
# ──────────────────────────────────────────────

@dataclass
class InventoryModel:
    """
    Inventario de hardware de un dispositivo.

    Persistencia del DeviceInfo de normalizer.py en PostgreSQL.
    Una fila por dispositivo, actualizada cuando SNMP/WMI/SSH
    obtiene nueva información.

    Attributes:
        device_id:    FK a devices.id (UNIQUE — 1 por dispositivo).
        device_type:  Tipo: switch | router | printer | server | workstation...
        manufacturer: Fabricante: Cisco | HP | Dell | Ubiquiti...
        model:        Modelo específico del hardware.
        description:  Descripción técnica completa.
        location:     Ubicación física (de SNMP sysLocation).
        contact:      Responsable del equipo.
        os_info:      SO o firmware (Windows 11, IOS 15.2, Ubuntu 22.04).
        cpu_model:    Modelo de CPU (si está disponible).
        ram_mb:       RAM en MB.
        disk_gb:      Disco en GB.
        interfaces:   Lista de interfaces de red.
        uptime_seconds: Tiempo encendido en segundos.
        read_method:  Método de lectura: snmp | wmi | ssh | none.
    """
    device_id: int
    device_type: str              = "unknown"
    manufacturer: str             = "unknown"
    model: str                    = "unknown"
    description: str              = ""
    location: str                 = ""
    contact: str                  = ""
    os_info: str                  = ""
    cpu_model: str                = ""
    ram_mb: Optional[int]         = None
    disk_gb: Optional[float]      = None
    interfaces: list[str]         = field(default_factory=list)
    uptime_seconds: Optional[int] = None
    read_method: str              = "none"
    id: Optional[int]             = None
    last_updated: Optional[datetime] = None

    @classmethod
    def from_record(cls, row) -> "InventoryModel":
        return cls(
            id=row["id"],
            device_id=row["device_id"],
            device_type=row["device_type"],
            manufacturer=row["manufacturer"],
            model=row["model"],
            description=row["description"],
            location=row["location"],
            contact=row["contact"],
            os_info=row["os_info"],
            cpu_model=row["cpu_model"],
            ram_mb=row.get("ram_mb"),
            disk_gb=float(row["disk_gb"]) if row.get("disk_gb") else None,
            interfaces=list(row["interfaces"]) if row.get("interfaces") else [],
            uptime_seconds=row.get("uptime_seconds"),
            read_method=row["read_method"],
            last_updated=row.get("last_updated"),
        )


# ──────────────────────────────────────────────
# MODELO — HostnameChange (tabla: hostname_changes)
# ──────────────────────────────────────────────

@dataclass
class HostnameChangeModel:
    """
    Registro de un cambio de hostname en un dispositivo.

    Permite al soporte saber que una IP fue reasignada a otro equipo
    o que un dispositivo fue renombrado.

    Attributes:
        device_id:    FK a devices.id.
        old_hostname: Nombre anterior.
        new_hostname: Nombre nuevo detectado.
        detected_at:  Cuándo se detectó el cambio.
    """
    device_id: int
    old_hostname: str
    new_hostname: str
    detected_at: Optional[datetime] = None
    id: Optional[int]               = None

    @classmethod
    def from_record(cls, row) -> "HostnameChangeModel":
        return cls(
            id=row["id"],
            device_id=row["device_id"],
            old_hostname=row["old_hostname"],
            new_hostname=row["new_hostname"],
            detected_at=row.get("detected_at"),
        )