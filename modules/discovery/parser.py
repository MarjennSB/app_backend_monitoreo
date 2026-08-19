"""
discovery/parser.py
────────────────────────────────────────────────────────────────
Módulo de procesamiento de resultados de escaneo.

Responsabilidades:
  - Transformar RawScanData (salida de scanner.py) en estructuras
    limpias y tipadas listas para consumir desde la UI o storage.
  - Filtrar, ordenar y resumir resultados.
  - NO realiza ninguna conexión de red ni I/O de disco.

Contrato con scanner.py:
  Entrada:  RawScanData  (viene de scanner.py)
  Salida:   ScanResult   (va hacia storage/ o routes/api.py)
────────────────────────────────────────────────────────────────
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

# Importamos los tipos raw que produce scanner.py
from modules.discovery.scanner import RawScanData, RawHostData, ScanMode


# ──────────────────────────────────────────────
# MODELOS DE SALIDA — Estructuras limpias
# ──────────────────────────────────────────────

@dataclass
class PortResult:
    """
    Resultado de un puerto individual.

    Attributes:
        number:   Número de puerto TCP.
        is_open:  True si el puerto está abierto.
        service:  Nombre del servicio conocido (ej: "ssh", "http").
    """
    number: int
    is_open: bool
    service: str = ""

    def __post_init__(self) -> None:
        """Asigna el nombre de servicio conocido al crear la instancia."""
        if not self.service:
            self.service = _KNOWN_SERVICES.get(self.number, "unknown")


@dataclass
class HostResult:
    """
    Resultado procesado de un host escaneado.

    Attributes:
        ip:             Dirección IP del host.
        is_alive:       True si responde a ping ICMP.
        ports:          Lista completa de puertos verificados.
        open_ports:     Solo los puertos abiertos (propiedad calculada).
        scanned_at:     Timestamp del escaneo de este host.
        scan_duration_ms: Duración del escaneo en milisegundos.
    """
    ip: str
    is_alive: bool
    ports: List[PortResult]
    scanned_at: datetime
    scan_duration_ms: float

    @property
    def open_ports(self) -> List[PortResult]:
        """Retorna solo los puertos abiertos."""
        return [p for p in self.ports if p.is_open]

    @property
    def open_port_numbers(self) -> List[int]:
        """Retorna los números de puertos abiertos."""
        return [p.number for p in self.ports if p.is_open]

    @property
    def has_open_ports(self) -> bool:
        """True si el host tiene al menos un puerto abierto."""
        return any(p.is_open for p in self.ports)


@dataclass
class ScanSummary:
    """
    Resumen estadístico de un ciclo de escaneo.

    Attributes:
        total_hosts:    Total de hosts en el rango escaneado.
        active_hosts:   Hosts que respondieron a ping.
        inactive_hosts: Hosts que no respondieron.
        hosts_with_open_ports: Hosts con al menos un puerto abierto.
        total_open_ports: Total de puertos abiertos encontrados.
        duration_seconds: Duración total del escaneo.
    """
    total_hosts: int
    active_hosts: int
    inactive_hosts: int
    hosts_with_open_ports: int
    total_open_ports: int
    duration_seconds: float

    @property
    def availability_percent(self) -> float:
        """Porcentaje de hosts activos sobre el total."""
        if self.total_hosts == 0:
            return 0.0
        return round((self.active_hosts / self.total_hosts) * 100, 2)


@dataclass
class ScanResult:
    """
    Resultado final procesado de un ciclo de escaneo completo.

    Este es el objeto que consume la capa de almacenamiento (storage/)
    y la capa de presentación (routes/api.py).

    Attributes:
        network_cidr:  Red escaneada en notación CIDR.
        hosts:         Lista de todos los hosts procesados.
        active_hosts:  Solo hosts que están activos.
        summary:       Estadísticas del ciclo.
        started_at:    Inicio del escaneo.
        finished_at:   Fin del escaneo.
        scan_mode:     Modo en que se ejecutó (active/background).
    """
    network_cidr: str
    hosts: List[HostResult]
    active_hosts: List[HostResult]
    summary: ScanSummary
    started_at: datetime
    finished_at: datetime
    scan_mode: str

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


# ──────────────────────────────────────────────
# SERVICIOS CONOCIDOS — Mapeo puerto → nombre
# ──────────────────────────────────────────────

_KNOWN_SERVICES: dict[int, str] = {
    21:   "ftp",
    22:   "ssh",
    23:   "telnet",
    25:   "smtp",
    53:   "dns",
    80:   "http",
    110:  "pop3",
    135:  "msrpc",
    139:  "netbios",
    143:  "imap",
    443:  "https",
    445:  "smb",
    3306: "mysql",
    3389: "rdp",
    5432: "postgresql",
    5900: "vnc",
    6379: "redis",
    8080: "http-alt",
    8443: "https-alt",
    27017: "mongodb",
}


# ──────────────────────────────────────────────
# FUNCIONES DE TRANSFORMACIÓN
# ──────────────────────────────────────────────

def _parse_host(raw: RawHostData) -> HostResult:
    """
    Convierte un RawHostData en un HostResult limpio y tipado.

    Combina open_ports y closed_ports del raw en una lista
    unificada de PortResult con su estado correcto.

    Args:
        raw: Dato crudo del host producido por scanner.py.

    Returns:
        HostResult con toda la información procesada.
    """
    open_set = set(raw.open_ports)
    all_ports = raw.open_ports + raw.closed_ports

    ports = [
        PortResult(number=port, is_open=(port in open_set))
        for port in sorted(set(all_ports))
    ]

    return HostResult(
        ip=raw.ip,
        is_alive=raw.is_alive,
        ports=ports,
        scanned_at=raw.scanned_at,
        scan_duration_ms=raw.scan_duration_ms,
    )


def _build_summary(
    hosts: List[HostResult],
    duration_seconds: float,
) -> ScanSummary:
    """
    Calcula las estadísticas de un ciclo de escaneo.

    Args:
        hosts:            Lista de hosts procesados.
        duration_seconds: Duración total del scan.

    Returns:
        ScanSummary con las métricas del ciclo.
    """
    total         = len(hosts)
    active        = sum(1 for h in hosts if h.is_alive)
    with_ports    = sum(1 for h in hosts if h.has_open_ports)
    total_open    = sum(len(h.open_ports) for h in hosts)

    return ScanSummary(
        total_hosts=total,
        active_hosts=active,
        inactive_hosts=total - active,
        hosts_with_open_ports=with_ports,
        total_open_ports=total_open,
        duration_seconds=round(duration_seconds, 2),
    )


# ──────────────────────────────────────────────
# FUNCIÓN PÚBLICA PRINCIPAL
# ──────────────────────────────────────────────

def parse_scan_result(raw: RawScanData) -> ScanResult:
    """
    Transforma un RawScanData (de scanner.py) en un ScanResult limpio.

    Es la función principal de este módulo. Debe llamarse cada vez
    que scanner.py completa un ciclo de escaneo.

    Args:
        raw: Objeto RawScanData producido por scan_network().

    Returns:
        ScanResult procesado, listo para storage o API.

    Example:
        raw    = await scan_network("192.168.1.0/24", ports, semaphore)
        result = parse_scan_result(raw)
        print(result.summary.active_hosts)
    """
    hosts        = [_parse_host(h) for h in raw.hosts]
    active_hosts = [h for h in hosts if h.is_alive]
    summary      = _build_summary(hosts, raw.total_duration_seconds)

    return ScanResult(
        network_cidr=raw.network_cidr,
        hosts=hosts,
        active_hosts=active_hosts,
        summary=summary,
        started_at=raw.started_at,
        finished_at=raw.finished_at,
        scan_mode=raw.mode.value,
    )


# ──────────────────────────────────────────────
# FUNCIONES DE CONSULTA — Filtros y utilidades
# ──────────────────────────────────────────────

def filter_active_hosts(result: ScanResult) -> List[HostResult]:
    """
    Retorna solo los hosts activos de un ScanResult.

    Args:
        result: ScanResult procesado.

    Returns:
        Lista de HostResult con is_alive=True.
    """
    return result.active_hosts


def filter_hosts_by_port(
    result: ScanResult,
    port: int,
) -> List[HostResult]:
    """
    Retorna hosts que tienen un puerto específico abierto.

    Útil para encontrar todos los hosts con SSH, RDP, HTTP, etc.

    Args:
        result: ScanResult procesado.
        port:   Número de puerto a buscar.

    Returns:
        Lista de HostResult con ese puerto abierto.
    """
    return [
        host for host in result.active_hosts
        if port in host.open_port_numbers
    ]


def get_host(result: ScanResult, ip: str) -> Optional[HostResult]:
    """
    Busca un host específico dentro de un ScanResult por IP.

    Args:
        result: ScanResult procesado.
        ip:     Dirección IP a buscar.

    Returns:
        HostResult si se encuentra, None si no existe.
    """
    for host in result.hosts:
        if host.ip == ip:
            return host
    return None


def format_summary(result: ScanResult) -> str:
    """
    Genera un resumen legible del ciclo de escaneo para logs o consola.

    Args:
        result: ScanResult procesado.

    Returns:
        String con el resumen formateado.
    """
    s = result.summary
    return (
        f"Red: {result.network_cidr} | "
        f"Modo: {result.scan_mode} | "
        f"Activos: {s.active_hosts}/{s.total_hosts} "
        f"({s.availability_percent}%) | "
        f"Con puertos abiertos: {s.hosts_with_open_ports} | "
        f"Puertos totales: {s.total_open_ports} | "
        f"Duración: {s.duration_seconds}s"
    )