"""
routes/api.py
────────────────────────────────────────────────────────────────
Endpoints REST de la API de MvpMonitoreo.

Equivalente a routes/api.php de Laravel pero en FastAPI.

Estructura de rutas:
  /health                  → Estado del sistema (BD, VPS)

  /networks                → CRUD de redes configuradas
  /networks/{id}/scan      → Trigger scan manual
  /networks/{id}/devices   → Dispositivos de una red
  /networks/{id}/stats     → Estadísticas de la red

  /devices/{id}            → Detalle de un dispositivo
  /devices/{id}/ports      → Puertos TCP del dispositivo
  /devices/{id}/stats      → Estadísticas uptime/RTT
  /devices/{id}/inventory  → Hardware del dispositivo
  /devices/{id}/history    → Cambios de hostname

  /scanner/status          → Estado del scanner por red
  /scanner/active/{cidr}   → Marcar red como activa (scan inmediato)

Autenticación: No implementada en MVP — agregar JWT en fase 2.
────────────────────────────────────────────────────────────────
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core.system_monitor import system_monitor
from modules.storage.database import db
from modules.storage.repository import (
    NetworkRepository,
    DeviceRepository,
    ScanResultRepository,
    PortCheckRepository,
    StatsRepository,
    InventoryRepository,
    HostnameChangeRepository,
    VlanRepository,
)

# Importamos el registry del scanner para triggers manuales
from modules.discovery.scanner import ScannerRegistry

router = APIRouter()

# Instancias de repositories (stateless — seguras para compartir)
_net_repo      = NetworkRepository()
_dev_repo      = DeviceRepository()
_scan_repo     = ScanResultRepository()
_port_repo     = PortCheckRepository()
_stats_repo    = StatsRepository()
_inv_repo      = InventoryRepository()
_hostname_repo = HostnameChangeRepository()
_vlan_repo     = VlanRepository()

# Registry del scanner (se inyecta desde main.py al arrancar)
_scanner_registry: Optional[ScannerRegistry] = None


def set_scanner_registry(registry: ScannerRegistry) -> None:
    """
    Inyecta el ScannerRegistry desde main.py.

    El registry vive en main.py para gestionar el ciclo de vida
    del event loop. La API lo usa solo para consultas y triggers.
    """
    global _scanner_registry
    _scanner_registry = registry

def get_scanner_registry() -> Optional[ScannerRegistry]:
    return _scanner_registry


# ──────────────────────────────────────────────
# SCHEMAS — Pydantic (equivalente a Form Requests de Laravel)
# ──────────────────────────────────────────────

class NetworkCreateRequest(BaseModel):
    """Payload para crear una red."""
    cidr: str
    vlan_id: Optional[int] = None
    scan_interval: int  = 300   # segundos

class VipStatusRequest(BaseModel):
    is_critical: bool


class NetworkUpdateRequest(BaseModel):
    """Payload para actualizar una red."""
    vlan_id: Optional[int]       = None
    scan_interval: Optional[int] = None
    is_active: Optional[bool]    = None


class VlanCreateRequest(BaseModel):
    name: str
    description: str = ""
    is_active: bool = True

class VlanUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


# ──────────────────────────────────────────────
# HEALTH — Estado del sistema
# ──────────────────────────────────────────────

@router.get("/health", tags=["Sistema"])
async def health_check():
    """
    Verifica el estado completo del sistema.

    Retorna:
      - Estado de PostgreSQL y del pool de conexiones
      - Estado del VPS (CPU, RAM, slots del scanner)
      - Timestamp del servidor
    """
    db_health     = await db.health_check()
    server_health = system_monitor.get_health_summary()

    return {
        "status":    "ok" if db_health["status"] == "ok" else "degraded",
        "timestamp": datetime.now().isoformat(),
        "database": db_health,
        "server": {
            "cpu_percent":    server_health.cpu_percent,
            "memory_percent": server_health.memory_percent,
            "cpu_health":     server_health.cpu_health,
            "memory_health":  server_health.memory_health,
            "overall_health": server_health.overall_health,
            "scanner_slots":  server_health.scanner_slots,
            "throttle_active": server_health.throttle_active,
            "is_stable":      server_health.is_stable,
            "uptime_seconds": server_health.uptime_seconds,
            "recommendations": server_health.recommendations,
        },
    }


# ──────────────────────────────────────────────
# VLANS — CRUD
# ──────────────────────────────────────────────

@router.get("/vlans", tags=["VLANs"])
async def list_vlans():
    vlans = await _vlan_repo.get_all()
    return {"data": [_serialize_vlan(v) for v in vlans]}

@router.post("/vlans", status_code=201, tags=["VLANs"])
async def create_vlan(body: VlanCreateRequest):
    vlan = await _vlan_repo.create(
        name=body.name,
        description=body.description,
        is_active=body.is_active
    )
    return _serialize_vlan(vlan)

@router.patch("/vlans/{vlan_id}", tags=["VLANs"])
async def update_vlan(vlan_id: int, body: VlanUpdateRequest):
    vlan = await _vlan_repo.update(
        vlan_id=vlan_id,
        name=body.name,
        description=body.description,
        is_active=body.is_active
    )
    if not vlan:
        raise HTTPException(status_code=404, detail="VLAN no encontrada.")
    return _serialize_vlan(vlan)

@router.delete("/vlans/{vlan_id}", status_code=204, tags=["VLANs"])
async def delete_vlan(vlan_id: int):
    deleted = await _vlan_repo.delete(vlan_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="VLAN no encontrada o no se pudo eliminar.")

# ──────────────────────────────────────────────
# NETWORKS — CRUD
# Equivalente a NetworkController de Laravel
# ──────────────────────────────────────────────

@router.get("/networks", tags=["Redes"])
async def list_networks(page: int = Query(1, ge=1), limit: int = Query(10, ge=1, le=100)):
    """
    Retorna redes paginadas con contadores de dispositivos.
    """
    result = await _net_repo.get_paginated_with_counts(page=page, limit=limit)
    
    # Serializar redes
    serialized_data = []
    for item in result["data"]:
        net_dict = _serialize_network(item["network"])
        net_dict["device_counts"] = item["counts"]
        serialized_data.append(net_dict)
        
    return {
        "data": serialized_data,
        "meta": {
            "total": result["total"],
            "page": result["page"],
            "limit": result["limit"],
            "total_pages": result["total_pages"]
        }
    }


@router.post("/networks", status_code=201, tags=["Redes"])
async def create_network(body: NetworkCreateRequest):
    """
    Agrega una nueva red al sistema y arranca su autoscan.

    Equivalente a: Network::create($request->validated()) en Laravel.
    """
    # Verificar que no exista ya esta red
    existing = await _net_repo.get_by_cidr(body.cidr)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"La red {body.cidr} ya está registrada (ID: {existing.id}).",
        )

    network = await _net_repo.create(
        cidr=body.cidr,
        vlan_id=body.vlan_id,
        scan_interval=body.scan_interval,
    )

    # Arrancar el autoscan para la nueva red
    if _scanner_registry:
        await _scanner_registry.add_network(
            network_cidr=network.cidr,
            network_id=network.id,
        )

    return _serialize_network(network)


@router.get("/networks/{network_id}", tags=["Redes"])
async def get_network(network_id: int):
    """
    Retorna el detalle de una red con estadísticas de sus dispositivos.

    Equivalente a: Network::findOrFail($id) en Laravel.
    """
    network = await _net_repo.get_by_id(network_id)
    if not network:
        raise HTTPException(status_code=404, detail="Red no encontrada.")

    counts = await _dev_repo.count_by_network(network_id)
    last_scan = await _scan_repo.get_last(network_id)

    return {
        **_serialize_network(network),
        "device_counts": counts,
        "last_scan": _serialize_scan_result(last_scan) if last_scan else None,
    }


@router.patch("/networks/{network_id}", tags=["Redes"])
async def update_network(network_id: int, body: NetworkUpdateRequest):
    """
    Actualiza propiedades de una red.

    Equivalente a: $network->update($request->validated()) en Laravel.
    """
    network = await _net_repo.update(
        network_id=network_id,
        vlan_id=body.vlan_id,
        scan_interval=body.scan_interval,
        is_active=body.is_active,
    )
    if not network:
        raise HTTPException(status_code=404, detail="Red no encontrada.")

    # Si se desactiva, pausar el scanner de esa red
    if body.is_active is False and _scanner_registry:
        await _scanner_registry.remove_network(network.cidr)

    return _serialize_network(network)


@router.delete("/networks/{network_id}", status_code=204, tags=["Redes"])
async def delete_network(network_id: int):
    """
    Elimina una red y todos sus dispositivos (CASCADE).

    Equivalente a: $network->delete() en Laravel.
    """
    network = await _net_repo.get_by_id(network_id)
    if not network:
        raise HTTPException(status_code=404, detail="Red no encontrada.")

    # Detener el autoscan antes de eliminar
    if _scanner_registry:
        await _scanner_registry.remove_network(network.cidr)

    deleted = await _net_repo.delete(network_id)
    if not deleted:
        raise HTTPException(status_code=500, detail="Error al eliminar la red.")


# ──────────────────────────────────────────────
# SCANNER — Triggers manuales
# ──────────────────────────────────────────────

@router.post("/networks/{network_id}/scan", tags=["Scanner"])
async def trigger_scan(network_id: int):
    """
    Dispara un scan inmediato de una red (fuera del ciclo automático).

    El scan corre en background — la respuesta es inmediata.
    El resultado se puede ver en GET /networks/{id}/scans.

    Equivalente a: dispatch(new ScanNetworkJob($network)) en Laravel.
    """
    network = await _net_repo.get_by_id(network_id)
    if not network:
        raise HTTPException(status_code=404, detail="Red no encontrada.")

    if not _scanner_registry:
        raise HTTPException(status_code=503, detail="Scanner no disponible.")

    scanner = _scanner_registry.get_scanner(network.cidr)
    if not scanner:
        raise HTTPException(
            status_code=404,
            detail=f"No hay scanner activo para {network.cidr}.",
        )

    # Trigger async sin bloquear el request
    import asyncio
    asyncio.create_task(scanner.scan_now())

    return {
        "message":    f"Scan iniciado para {network.cidr}",
        "network_id": network_id,
        "cidr":       network.cidr,
        "triggered_at": datetime.now().isoformat(),
    }


@router.post("/scanner/active/{network_id}", tags=["Scanner"])
async def set_active_network(network_id: int):
    """
    Marca una red como activa (modo de mayor frecuencia).

    Las demás redes pasan a modo background automáticamente.
    Úsalo cuando el usuario navega a una red específica en la UI.
    """
    network = await _net_repo.get_by_id(network_id)
    if not network:
        raise HTTPException(status_code=404, detail="Red no encontrada.")

    if _scanner_registry:
        await _scanner_registry.set_active_network(network.cidr)

    return {
        "message":    f"{network.cidr} marcada como red activa.",
        "cidr":       network.cidr,
        "network_id": network_id,
    }


@router.get("/scanner/status", tags=["Scanner"])
async def scanner_status():
    """
    Retorna el estado actual del scanner para todas las redes.

    Muestra cuándo fue el último scan, el modo y los slots usados.
    """
    if not _scanner_registry:
        return {"scanners": [], "total": 0}

    statuses = _scanner_registry.get_all_statuses()
    snapshot  = system_monitor.get_snapshot()

    return {
        "scanners":         statuses,
        "total":            len(statuses),
        "vps_slots_in_use": snapshot.recommended_slots,
        "vps_should_pause": snapshot.should_pause,
        "vps_throttle_s":   snapshot.throttle_delay_s,
    }


# ──────────────────────────────────────────────
# DEVICES — Consultas
# ──────────────────────────────────────────────

@router.get("/networks/{network_id}/devices", tags=["Dispositivos"])
async def list_devices(
    network_id: int,
    alive_only: bool = Query(False, description="Solo dispositivos activos"),
):
    """
    Retorna todos los dispositivos de una red.

    Equivalente a: $network->devices()->get() en Laravel.

    Args:
        alive_only: Si True, solo retorna los que están activos ahora.
    """
    network = await _net_repo.get_by_id(network_id)
    if not network:
        raise HTTPException(status_code=404, detail="Red no encontrada.")

    if alive_only:
        devices = await _dev_repo.get_alive_by_network(network_id)
    else:
        devices = await _dev_repo.get_by_network(network_id)

    # Fetch open ports for all devices in this network
    open_ports_map = await _port_repo.get_open_ports_by_network(network_id)

    serialized = []
    for d in devices:
        sd = _serialize_device(d)
        sd["open_ports"] = open_ports_map.get(d.id, [])
        serialized.append(sd)

    return {
        "network_id": network_id,
        "cidr":       network.cidr,
        "total":      len(devices),
        "devices":    serialized,
    }
@router.get("/devices", tags=["Dispositivos"])
async def list_all_devices(
    alive_only: bool = Query(False, description="Solo dispositivos activos"),
):
    """
    Retorna todos los dispositivos de todas las redes.
    """
    if alive_only:
        devices = await _dev_repo.get_all_alive()
    else:
        devices = await _dev_repo.get_all()

    return {
        "total":   len(devices),
        "devices": [_serialize_device(d) for d in devices],
    }


@router.get("/devices/{device_id}", tags=["Dispositivos"])
async def get_device(device_id: int):
    """
    Retorna el detalle completo de un dispositivo.

    Incluye inventario, estadísticas y último estado de puertos.
    """
    device = await _dev_repo.get_by_id(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado.")

    stats     = await _stats_repo.get_by_device(device_id)
    inventory = await _inv_repo.get_by_device(device_id)
    ports     = await _port_repo.get_latest_by_device(device_id)

    return {
        "device":    _serialize_device(device),
        "stats":     _serialize_stats(stats) if stats else None,
        "inventory": _serialize_inventory(inventory) if inventory else None,
        "ports":     [_serialize_port(p) for p in ports],
    }

@router.put("/devices/{device_id}/vip", tags=["Dispositivos"])
async def set_device_vip(device_id: int, body: VipStatusRequest):
    """Activa o desactiva el estado VIP (Crítico) de un dispositivo."""
    device = await _dev_repo.get_by_id(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado.")

    updated_device = await _dev_repo.update_critical_status(device_id, body.is_critical)
    if not updated_device:
        raise HTTPException(status_code=500, detail="Error al actualizar estado VIP.")

    return _serialize_device(updated_device)

@router.get("/devices/{device_id}/ports", tags=["Dispositivos"])
async def get_device_ports(device_id: int):
    """Estado actual de todos los puertos TCP del dispositivo."""
    device = await _dev_repo.get_by_id(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado.")

    ports = await _port_repo.get_latest_by_device(device_id)
    return {
        "device_id": device_id,
        "ip":        device.ip,
        "ports":     [_serialize_port(p) for p in ports],
    }


@router.get("/devices/{device_id}/stats", tags=["Dispositivos"])
async def get_device_stats(device_id: int):
    """
    Estadísticas acumuladas de uptime, downtime y RTT.

    Esto es lo que el soporte usa para saber la disponibilidad
    histórica del dispositivo.
    """
    device = await _dev_repo.get_by_id(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado.")

    stats = await _stats_repo.get_by_device(device_id)
    if not stats:
        raise HTTPException(
            status_code=404,
            detail="Aún no hay estadísticas. Esperando el primer ciclo de scan.",
        )

    return _serialize_stats(stats)


@router.get("/devices/{device_id}/inventory", tags=["Dispositivos"])
async def get_device_inventory(device_id: int):
    """
    Inventario de hardware del dispositivo.

    Requiere que SNMP, WMI o SSH estén activos para tener datos.
    """
    device = await _dev_repo.get_by_id(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado.")

    inventory = await _inv_repo.get_by_device(device_id)
    if not inventory:
        return {
            "device_id":  device_id,
            "ip":         device.ip,
            "message":    "Inventario no disponible. "
                          "SNMP/WMI/SSH pendiente de configuración.",
            "read_method": "none",
        }

    return _serialize_inventory(inventory)


@router.get("/devices/{device_id}/history", tags=["Dispositivos"])
async def get_device_hostname_history(device_id: int):
    """
    Historial de cambios de hostname del dispositivo.

    Útil para detectar si una IP fue reasignada a otro equipo.
    """
    device = await _dev_repo.get_by_id(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado.")

    changes = await _hostname_repo.get_by_device(device_id)
    return {
        "device_id": device_id,
        "ip":        device.ip,
        "changes":   [
            {
                "old_hostname": c.old_hostname,
                "new_hostname": c.new_hostname,
                "detected_at":  c.detected_at.isoformat() if c.detected_at else None,
            }
            for c in changes
        ],
    }


# ──────────────────────────────────────────────
# NETWORKS — Scans e informes
# ──────────────────────────────────────────────

@router.get("/networks/{network_id}/scans", tags=["Redes"])
async def list_scan_results(
    network_id: int,
    limit: int = Query(10, ge=1, le=100, description="Últimos N scans"),
):
    """Historial de scans de una red."""
    network = await _net_repo.get_by_id(network_id)
    if not network:
        raise HTTPException(status_code=404, detail="Red no encontrada.")

    results = await _scan_repo.get_latest(network_id, limit=limit)
    return {
        "network_id": network_id,
        "cidr":       network.cidr,
        "total":      len(results),
        "scans":      [_serialize_scan_result(r) for r in results],
    }


@router.get("/networks/{network_id}/worst", tags=["Redes"])
async def get_worst_devices(
    network_id: int,
    limit: int = Query(10, ge=1, le=50),
):
    """
    Dispositivos con peor disponibilidad histórica en la red.

    El soporte usa esto para priorizar problemas recurrentes.
    """
    network = await _net_repo.get_by_id(network_id)
    if not network:
        raise HTTPException(status_code=404, detail="Red no encontrada.")

    worst = await _stats_repo.get_worst_availability(network_id, limit=limit)
    return {
        "network_id": network_id,
        "cidr":       network.cidr,
        "total":      len(worst),
        "devices":    [_serialize_stats(s) for s in worst],
    }


# ──────────────────────────────────────────────
# SERIALIZADORES — Equivalente a API Resources de Laravel
# ──────────────────────────────────────────────
# Convierten los modelos de Python a dicts JSON-friendly.
# Equivalente a Resource::collection() / make() en Laravel.

def _serialize_vlan(v) -> dict:
    return {
        "id":          v.id,
        "name":        v.name,
        "description": v.description,
        "is_active":   v.is_active,
        "created_at":  v.created_at.isoformat() if v.created_at else None,
        "updated_at":  v.updated_at.isoformat() if v.updated_at else None,
    }

def _serialize_network(n) -> dict:
    return {
        "id":            n.id,
        "cidr":          n.cidr,
        "vlan_id":       n.vlan_id,
        "vlan_name":     getattr(n, 'vlan_name', None),
        "scan_interval": n.scan_interval,
        "is_active":     n.is_active,
        "created_at":    n.created_at.isoformat() if n.created_at else None,
        "updated_at":    n.updated_at.isoformat() if n.updated_at else None,
    }


def _serialize_device(d) -> dict:
    return {
        "id":              d.id,
        "network_id":      d.network_id,
        "ip":              d.ip,
        "hostname":        d.hostname,
        "hostname_method": d.hostname_method,
        "mac_address":     d.mac_address,
        "is_alive":        d.is_alive,
        "is_critical":     d.is_critical,
        "last_seen_at":    d.last_seen_at.isoformat() if d.last_seen_at else None,
        "first_seen_at":   d.first_seen_at.isoformat() if d.first_seen_at else None,
    }


def _serialize_scan_result(r) -> dict:
    return {
        "id":             r.id,
        "network_id":     r.network_id,
        "started_at":     r.started_at.isoformat() if r.started_at else None,
        "finished_at":    r.finished_at.isoformat() if r.finished_at else None,
        "duration_ms":    r.duration_ms,
        "total_hosts":    r.total_hosts,
        "active_hosts":   r.active_hosts,
        "inactive_hosts": r.inactive_hosts,
    }


def _serialize_port(p) -> dict:
    return {
        "port":       p.port,
        "state":      p.state,
        "rtt_ms":     p.rtt_ms,
        "checked_at": p.checked_at.isoformat() if p.checked_at else None,
    }


def _serialize_stats(s) -> dict:
    return {
        "device_id":               s.device_id,
        "total_probes":            s.total_probes,
        "successful_probes":       s.successful_probes,
        "failed_probes":           s.failed_probes,
        "ongoing_successful":      s.ongoing_successful,
        "ongoing_failed":          s.ongoing_failed,
        "availability_percent":    s.availability_percent,
        "rtt_min_ms":              s.rtt_min_ms,
        "rtt_max_ms":              s.rtt_max_ms,
        "rtt_avg_ms":              s.rtt_avg_ms,
        "total_uptime_seconds":    s.total_uptime_seconds,
        "total_downtime_seconds":  s.total_downtime_seconds,
        "longest_uptime_seconds":  s.longest_uptime_seconds,
        "longest_downtime_seconds": s.longest_downtime_seconds,
        "last_seen_at":            s.last_seen_at.isoformat() if s.last_seen_at else None,
        "last_down_at":            s.last_down_at.isoformat() if s.last_down_at else None,
        "monitoring_since":        s.monitoring_since.isoformat() if s.monitoring_since else None,
        "updated_at":              s.updated_at.isoformat() if s.updated_at else None,
    }


def _serialize_inventory(i) -> dict:
    return {
        "device_id":      i.device_id,
        "device_type":    i.device_type,
        "manufacturer":   i.manufacturer,
        "model":          i.model,
        "description":    i.description,
        "location":       i.location,
        "contact":        i.contact,
        "os_info":        i.os_info,
        "cpu_model":      i.cpu_model,
        "ram_mb":         i.ram_mb,
        "disk_gb":        i.disk_gb,
        "interfaces":     i.interfaces,
        "uptime_seconds": i.uptime_seconds,
        "read_method":    i.read_method,
        "last_updated":   i.last_updated.isoformat() if i.last_updated else None,
    }