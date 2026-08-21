"""
discovery/sync_service.py
────────────────────────────────────────────────────────────────
Servicio puente entre el escáner (memoria) y la base de datos (PostgreSQL).

Recibe los datos crudos del escaneo y los persiste usando los
repositorios de storage/.
────────────────────────────────────────────────────────────────
"""

import logging
from datetime import datetime
from modules.discovery.scanner import RawScanData
from modules.storage.repository import (
    NetworkRepository,
    DeviceRepository,
    ScanResultRepository,
    PortCheckRepository,
    StatsRepository
)
from modules.services.analyzer import analyzer_registry
from modules.services.ping_icmp import PingResult
from modules.services.telegram_notifier import notify_in_background

log = logging.getLogger("mvp_monitoreo.sync")

# Instancias de repositorios
_net_repo = NetworkRepository()
_dev_repo = DeviceRepository()
_scan_repo = ScanResultRepository()
_port_repo = PortCheckRepository()
_stats_repo = StatsRepository()


async def sync_scan_result(scan_data: RawScanData) -> None:
    """
    Toma el resultado de un escaneo de red y lo persiste en PostgreSQL.

    1. Busca la red en la BD.
    2. Guarda el historial global del escaneo (ScanResult).
    3. Crea o actualiza (upsert) cada dispositivo encontrado.
    4. Guarda el estado de los puertos verificados.
    """
    # 1. Buscar la red
    network = await _net_repo.get_by_cidr(scan_data.network_cidr)
    if not network:
        log.warning("Intento de sincronizar red desconocida: %s", scan_data.network_cidr)
        return

    active_hosts = sum(1 for h in scan_data.hosts if h.is_alive)
    inactive_hosts = len(scan_data.hosts) - active_hosts

    # 2. Guardar el historial del escaneo
    await _scan_repo.save(
        network_id=network.id,
        started_at=scan_data.started_at,
        finished_at=scan_data.finished_at,
        duration_ms=int(scan_data.total_duration_seconds * 1000),
        total_hosts=len(scan_data.hosts),
        active_hosts=active_hosts,
        inactive_hosts=inactive_hosts,
    )

    from routes.ws import manager

    updated_devices = []

    # 3. Guardar dispositivos y puertos
    for host in scan_data.hosts:
        existing = await _dev_repo.get_by_ip(network.id, host.ip)

        # Solo guardamos o actualizamos dispositivos vivos, o aquellos que 
        # antes estaban vivos y ahora murieron (para no llenar la BD de IPs vacías).
        if not host.is_alive and not existing:
            continue  # Nunca estuvo vivo, lo ignoramos.

        # Upsert: Lo crea si es nuevo, o actualiza su estado (is_alive) si ya existe
        device = await _dev_repo.upsert(
            network_id=network.id,
            ip=host.ip,
            is_alive=host.is_alive,
            mac_address=host.mac_address,
        )

        # ¡Lógica de Tolerancia (Anti-Flapping) para VIPs!
        if device.is_critical:
            alert_type = None
            
            # 1. Regla de Caída: 3 fallos consecutivos
            if not device.is_alive and device.failed_pings_count == 3:
                alert_type = "CRITICAL_DEVICE_DOWN"
            
            # 2. Regla de Recuperación: Solo si estaba OFICIALMENTE caído (>= 3 fallos previos)
            elif device.is_alive and existing and not existing.is_alive and existing.failed_pings_count >= 3:
                alert_type = "CRITICAL_DEVICE_UP"

            if alert_type:
                alert_msg = {
                    "type": alert_type,
                    "device": {
                        "id": device.id,
                        "ip": str(device.ip),
                        "hostname": device.hostname
                    },
                    "timestamp": datetime.now().isoformat()
                }
                await manager.broadcast_global(alert_msg)
                
                # Enviar notificación a Telegram
                notify_in_background(alert_type, str(device.ip), device.hostname)

        updated_devices.append({
            "ip": str(device.ip),
            "hostname": device.hostname,
            "is_alive": device.is_alive,
            "mac_address": device.mac_address,
            "open_ports": host.open_ports,
            "os": host.os,
            "vendor": host.vendor,
            "is_local": host.is_local,
        })

        # 4. Guardar los puertos
        if host.is_alive:
            port_checks = []
            for p in host.open_ports:
                port_checks.append({"port": p, "state": "open", "rtt_ms": None})
            for p in host.closed_ports:
                port_checks.append({"port": p, "state": "closed", "rtt_ms": None})
            
            await _port_repo.save_batch(device.id, port_checks)
            
        # 5. Generar y guardar estadísticas (Analyzer)
        analyzer = analyzer_registry.get_or_create(scan_data.network_cidr, host.ip)
        
        # Simular PingResult basado en RawHostData
        rtt_val = float(host.scan_duration_ms)
        if rtt_val > 99999.0:
            rtt_val = 99999.0
            
        ping_res = PingResult(
            ip=host.ip,
            is_alive=host.is_alive,
            rtt_ms=rtt_val,
            hostname="unknown",
            hostname_method="unknown",
            hostname_changed=False,
            checked_at=scan_data.finished_at,
        )
        
        analyzer.process_ping(ping_res)
        await _stats_repo.upsert_from_analyzer(device.id, analyzer.stats)

    if updated_devices:
        await manager.broadcast_to_network(network.id, {
            "type": "scan_update",
            "devices": updated_devices
        })

    log.info("Sincronización completa: %s (%d hosts activos)", 
             scan_data.network_cidr, active_hosts)
