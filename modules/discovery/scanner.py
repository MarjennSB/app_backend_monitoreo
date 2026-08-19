"""
discovery/scanner.py
────────────────────────────────────────────────────────────────
Módulo de escaneo de red para el sistema de monitoreo.

Responsabilidades:
  - Verificar hosts activos via ICMP (ping)
  - Verificar puertos TCP via socket connect
  - Gestionar el ciclo de autoscan por red (activo / background)
  - Controlar la carga de red mediante semáforo global compartido

Modelo de dos velocidades:
  ACTIVO     → red que el usuario está viendo ahora
               Semáforo: 25 slots | Intervalo: 5 minutos
  BACKGROUND → resto de redes registradas
               Semáforo: 5 slots  | Intervalo: 30 minutos

NO depende de nmap. Usa únicamente stdlib de Python.
────────────────────────────────────────────────────────────────
"""

import asyncio
import ipaddress
import socket
import subprocess
import logging
import time
import platform
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

# Monitor de recursos del VPS — ajusta los slots del semáforo dinámicamente
from core.system_monitor import system_monitor

# ──────────────────────────────────────────────
# INTERFAZ VISUAL — Consola con colores ANSI
# ──────────────────────────────────────────────
RED     = "\033[0;31m"
GREEN   = "\033[0;32m"
GRAY    = "\033[0;90m"
WHITE   = "\033[0;37m"
B_RED   = "\033[1;31m"
B_GREEN = "\033[1;32m"
B_WHITE = "\033[1;37m"
NC      = "\033[0m"

ICON_SCAN  = "[v]"
ICON_CHECK = "[+]"
ICON_CROSS = "[-]"
ICON_WAIT  = "[~]"
ICON_INFO  = "[*]"


def _print_banner() -> None:
    """Imprime el banner visual del módulo discovery."""
    print(f"\n{B_RED}  ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓{NC}")
    print(f"{B_RED}  ┃{NC}  {B_WHITE}MVP Monitoreo{NC} {GRAY}:::{NC} {B_RED}Discovery Module{NC}              {B_RED}┃{NC}")
    print(f"{B_RED}  ┃{NC}  {GRAY}Escaneo TCP + ICMP | Semáforo controlado{NC}      {B_RED}┃{NC}")
    print(f"{B_RED}  ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛{NC}\n")


def _log(icon: str, color: str, message: str) -> None:
    """Salida de consola formateada con timestamp."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"  {GRAY}[{ts}]{NC} {color}{icon}{NC} {message}")


# ──────────────────────────────────────────────
# CONFIGURACIÓN GLOBAL
# ──────────────────────────────────────────────

# Puertos TCP a verificar por defecto
DEFAULT_PORTS: List[int] = [22, 80, 443, 3389, 8080, 8443, 445, 21, 25]

# Timeout de conexión TCP en segundos
SOCKET_TIMEOUT: float = 1.5

# Slots del semáforo que cada modo puede usar simultáneamente
ACTIVE_SLOTS: int = 25
BACKGROUND_SLOTS: int = 5

# Intervalos de autoscan
ACTIVE_INTERVAL_SECONDS: int = 300      # 5 minutos
BACKGROUND_INTERVAL_SECONDS: int = 1800  # 30 minutos


# ──────────────────────────────────────────────
# ENUMERACIONES Y TIPOS
# ──────────────────────────────────────────────

class ScanMode(Enum):
    """Modo de operación del scanner."""
    ACTIVE     = "active"      # Usuario mirando la red → scan rápido
    BACKGROUND = "background"  # Segundo plano → scan conservador


@dataclass
class RawHostData:
    """
    Resultado bruto de un host escaneado.
    Consumido por parser.py para generar HostResult.
    """
    ip: str
    is_alive: bool
    open_ports: List[int]
    closed_ports: List[int]
    scanned_at: datetime
    scan_duration_ms: float
    mac_address: Optional[str] = None


@dataclass
class RawScanData:
    """
    Resultado bruto de un ciclo de escaneo completo sobre una red.
    Consumido por parser.py para generar ScanResult.
    """
    network_cidr: str
    hosts: List[RawHostData]
    started_at: datetime
    finished_at: datetime
    total_duration_seconds: float
    mode: ScanMode


# ──────────────────────────────────────────────
# FUNCIONES DE BAJO NIVEL — Ping + TCP
# ──────────────────────────────────────────────

def _is_linux() -> bool:
    """Determina si el sistema es Linux para el comando ping."""
    return platform.system().lower() == "linux"


async def ping_host(ip: str) -> bool:
    """
    Verifica si un host responde a ICMP (ping).

    Usa subprocess no bloqueante para compatibilidad multiplataforma.

    Args:
        ip: Dirección IP a verificar.

    Returns:
        True si el host responde, False en caso contrario.
    """
    if _is_linux():
        cmd = ["ping", "-c", "1", "-W", "1", ip]
    else:
        cmd = ["ping", "-n", "1", "-w", "1000", ip]

    try:
        import subprocess
        # Utilizamos asyncio.to_thread para evitar NotImplementedError en Windows 
        # cuando FastAPI/Uvicorn usa el SelectorEventLoop que no soporta subprocesos asíncronos.
        def run_ping():
            return subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode == 0

        import asyncio
        return await asyncio.to_thread(run_ping)
    except Exception as e:
        import logging
        logging.error(f"Error en ping_host para {ip}: {repr(e)}")
        return False


async def check_tcp_port(
    ip: str,
    port: int,
    semaphore: asyncio.Semaphore,
    timeout: float = SOCKET_TIMEOUT,
) -> Tuple[int, bool]:
    """
    Verifica si un puerto TCP está abierto via socket connect.

    Args:
        ip:        Dirección IP del host.
        port:      Puerto TCP a verificar.
        semaphore: Semáforo global compartido.
        timeout:   Tiempo máximo de espera en segundos.

    Returns:
        Tupla (puerto, está_abierto).
    """
    async with semaphore:
        loop = asyncio.get_event_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            await asyncio.wait_for(
                loop.sock_connect(sock, (ip, port)),
                timeout=timeout,
            )
            return (port, True)
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            return (port, False)
        finally:
            sock.close()


async def check_host_ports(
    ip: str,
    ports: List[int],
    semaphore: asyncio.Semaphore,
    timeout: float = SOCKET_TIMEOUT,
) -> Tuple[List[int], List[int]]:
    """
    Verifica todos los puertos de un host en paralelo.

    Args:
        ip:        Dirección IP del host.
        ports:     Lista de puertos TCP a verificar.
        semaphore: Semáforo global compartido.
        timeout:   Timeout por socket en segundos.

    Returns:
        Tupla (puertos_abiertos, puertos_cerrados).
    """
    tasks = [
        check_tcp_port(ip, port, semaphore, timeout)
        for port in ports
    ]
    results = await asyncio.gather(*tasks)

    open_ports   = [port for port, is_open in results if is_open]
    closed_ports = [port for port, is_open in results if not is_open]

    return open_ports, closed_ports


async def get_arp_macs() -> dict[str, str]:
    """
    Obtiene la tabla ARP actual del sistema operativo y extrae las direcciones MAC.
    Retorna un diccionario { '192.168.1.50': 'AA:BB:CC:DD:EE:FF' }.
    """
    try:
        import subprocess
        def run_arp():
            return subprocess.run(
                ["arp", "-a"],
                capture_output=True,
                text=True,
                check=False
            ).stdout

        output = await asyncio.to_thread(run_arp)
        macs = {}
        import re
        # Busca lineas con formato IP y MAC, ej: "  192.168.1.1       00-11-22-33-44-55     dinámico"
        # Regex captura IP en grupo 1 y MAC en grupo 2
        pattern = re.compile(r"^\s*([\d\.]+)\s+([0-9a-fA-F\-:]+)\s+", re.MULTILINE)
        for match in pattern.finditer(output):
            ip = match.group(1)
            mac = match.group(2).replace("-", ":").upper()
            macs[ip] = mac
        return macs
    except Exception as e:
        import logging
        logging.error(f"Error extrayendo tabla ARP: {repr(e)}")
        return {}


# ──────────────────────────────────────────────
# FUNCIÓN PRINCIPAL DE ESCANEO
# ──────────────────────────────────────────────

async def scan_network(
    network_cidr: str,
    ports: List[int],
    semaphore: asyncio.Semaphore,
    mode: ScanMode = ScanMode.ACTIVE,
    timeout: float = SOCKET_TIMEOUT,
) -> RawScanData:
    """
    Escanea todos los hosts de una red CIDR.

    Flujo por host:
      1. Ping ICMP → si responde, verificar puertos TCP
      2. Si no responde ICMP → marcar inactivo (skip TCP)

    Args:
        network_cidr: Red en notación CIDR (ej: "192.168.1.0/24").
        ports:        Lista de puertos TCP a verificar.
        semaphore:    Semáforo global compartido.
        mode:         Modo activo o background.
        timeout:      Timeout de socket en segundos.

    Returns:
        RawScanData con todos los resultados crudos del ciclo.
    """
    started_at = datetime.now()
    network    = ipaddress.IPv4Network(network_cidr, strict=False)
    hosts_iter = list(network.hosts())

    _log(ICON_SCAN, B_RED,
         f"Iniciando scan {B_WHITE}{network_cidr}{NC} "
         f"{GRAY}| {len(hosts_iter)} hosts | modo {mode.value}{NC}")

    ping_semaphore = asyncio.Semaphore(50)  # Limitar concurrencia de subprocesos (ping) en Windows
    
    async def _scan_single_host(ip_obj) -> RawHostData:
        ip      = str(ip_obj)
        t_start = time.monotonic()

        async with ping_semaphore:
            alive = await ping_host(ip)

        if alive:
            open_p, closed_p = await check_host_ports(
                ip, ports, semaphore, timeout
            )
        else:
            open_p, closed_p = [], ports[:]

        duration_ms = (time.monotonic() - t_start) * 1000

        if alive:
            ports_label = (
                f"{B_GREEN}{open_p}{NC}" if open_p
                else f"{GRAY}sin puertos abiertos{NC}"
            )
            _log(ICON_CHECK, B_GREEN,
                 f"{B_GREEN}ACTIVO{NC} {WHITE}{ip}{NC} → {ports_label}")

        return RawHostData(
            ip=ip,
            is_alive=alive,
            open_ports=open_p,
            closed_ports=closed_p,
            scanned_at=datetime.now(),
            scan_duration_ms=duration_ms,
        )

    tasks     = [_scan_single_host(ip) for ip in hosts_iter]
    raw_hosts = await asyncio.gather(*tasks)

    # 3. Extraer tabla ARP para obtener MACs
    arp_macs = await get_arp_macs()
    for host in raw_hosts:
        if host.is_alive and host.ip in arp_macs:
            host.mac_address = arp_macs[host.ip]

    finished_at    = datetime.now()
    total_duration = (finished_at - started_at).total_seconds()
    active_count   = sum(1 for h in raw_hosts if h.is_alive)

    _log(ICON_CHECK, B_GREEN,
         f"Completado {B_WHITE}{network_cidr}{NC} "
         f"| {B_GREEN}{active_count}{NC}{GRAY}/{len(raw_hosts)} activos{NC} "
         f"| {GRAY}{total_duration:.1f}s{NC}")

    return RawScanData(
        network_cidr=network_cidr,
        hosts=list(raw_hosts),
        started_at=started_at,
        finished_at=finished_at,
        total_duration_seconds=total_duration,
        mode=mode,
    )


# ──────────────────────────────────────────────
# CLASE: NetworkScanner — Autoscan por red
# ──────────────────────────────────────────────

class NetworkScanner:
    """
    Scanner autónomo para una red individual.

    Cada instancia gestiona su propio ciclo de autoscan.
    Comparte slots del semáforo con todas las instancias activas.

    Modos:
      ACTIVE     → interval=5 min, 25 slots del semáforo
      BACKGROUND → interval=30 min, 5 slots del semáforo

    Uso:
        scanner = NetworkScanner("192.168.1.0/24")
        await scanner.start()
        result = scanner.last_result
        await scanner.stop()
    """

    def __init__(
        self,
        network_cidr: str,
        network_id: Optional[int] = None,
        ports: Optional[List[int]] = None,
        mode: ScanMode = ScanMode.BACKGROUND,
        timeout: float = SOCKET_TIMEOUT,
    ) -> None:
        self.network_cidr = network_cidr
        self.network_id   = network_id
        self.ports        = ports or DEFAULT_PORTS
        self.mode         = mode
        self.timeout      = timeout

        self._running: bool                      = False
        self._task: Optional[asyncio.Task]       = None
        self.last_result: Optional[RawScanData]  = None
        self.last_scan_at: Optional[datetime]    = None
        self._wake_event: asyncio.Event          = asyncio.Event()
        self.is_scanning_now: bool               = False

    @property
    def interval(self) -> int:
        """Intervalo de autoscan según modo actual."""
        return (
            ACTIVE_INTERVAL_SECONDS
            if self.mode == ScanMode.ACTIVE
            else BACKGROUND_INTERVAL_SECONDS
        )

    @property
    def semaphore_slots(self) -> int:
        """Slots del semáforo disponibles según modo."""
        return (
            ACTIVE_SLOTS
            if self.mode == ScanMode.ACTIVE
            else BACKGROUND_SLOTS
        )

    @property
    def is_running(self) -> bool:
        return self._running

    def set_active(self) -> None:
        """Cambia a modo ACTIVO. El usuario está viendo esta red."""
        _log(ICON_INFO, B_WHITE,
             f"Red {B_RED}{self.network_cidr}{NC} → modo {B_GREEN}ACTIVO{NC}")
        self.mode = ScanMode.ACTIVE
        self._wake_event.set()
        asyncio.create_task(self.broadcast_status())

    def set_background(self) -> None:
        """Cambia a modo BACKGROUND. El usuario salió de esta red."""
        _log(ICON_INFO, GRAY,
             f"Red {self.network_cidr} → modo {GRAY}BACKGROUND{NC}")
        self.mode = ScanMode.BACKGROUND
        self._wake_event.set()
        asyncio.create_task(self.broadcast_status())

    async def broadcast_status(self) -> None:
        """Emite el estado actual al frontend vía WebSockets."""
        if not self.network_id:
            return

        from routes.ws import manager
        
        last_scan_str = self.last_scan_at.isoformat() if self.last_scan_at else None
        
        # Calcular cuándo será el próximo escaneo
        elapsed = time.time() - self.last_scan_at.timestamp() if self.last_scan_at else 0
        remaining = max(0, self.interval - elapsed)
        next_scan_ts = time.time() + remaining
        next_scan_str = datetime.fromtimestamp(next_scan_ts).isoformat()

        message = {
            "type": "status_update",
            "mode": self.mode.name,
            "is_scanning": self.is_scanning_now,
            "last_scan_at": last_scan_str,
            "next_scan_at": next_scan_str
        }
        await manager.broadcast_to_network(self.network_id, message)

    async def start(self) -> None:
        """
        Inicia el ciclo de autoscan.
        El primer scan ocurre inmediatamente.
        """
        if self._running:
            _log(ICON_INFO, GRAY,
                 f"Scanner {self.network_cidr} ya está corriendo.")
            return

        _print_banner()
        self._running = True
        self._task    = asyncio.create_task(self._autoscan_loop())
        _log(ICON_CHECK, B_GREEN,
             f"Autoscan iniciado → {B_WHITE}{self.network_cidr}{NC}")

    async def stop(self) -> None:
        """Detiene el ciclo de autoscan limpiamente."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        _log(ICON_CROSS, GRAY,
             f"Autoscan detenido → {self.network_cidr}")

    async def scan_now(self) -> RawScanData:
        """
        Ejecuta un scan inmediato fuera del ciclo automático.
        Útil para trigger manual desde la UI o al cambiar de red.

        Consulta el SystemMonitor para obtener los slots disponibles
        según la carga actual del VPS compartido.

        Returns:
            RawScanData con los resultados del scan.
        """
        # Si el VPS está crítico, esperar antes de iniciar
        if system_monitor.should_pause:
            _log(ICON_WAIT, GRAY,
                 f"{self.network_cidr} → VPS crítico, esperando 5s...")
            await asyncio.sleep(5)

        # Slots dinámicos según carga del VPS, respetando el límite del modo
        vps_slots  = system_monitor.recommended_slots
        mode_limit = self.semaphore_slots
        slots      = min(vps_slots, mode_limit)
        
        semaphore = asyncio.Semaphore(slots)

        # Anunciamos inicio del escaneo
        self.is_scanning_now = True
        asyncio.create_task(self.broadcast_status())

        result = await scan_network(
            network_cidr=self.network_cidr,
            ports=self.ports,
            semaphore=semaphore,
            mode=self.mode,
            timeout=self.timeout,
        )
        self.last_result  = result
        self.last_scan_at = result.finished_at
        self.is_scanning_now = False
        
        from modules.discovery.sync_service import sync_scan_result
        await sync_scan_result(result)
        
        # Anunciamos fin del escaneo
        asyncio.create_task(self.broadcast_status())
        
        return result

    async def _autoscan_loop(self) -> None:
        """Loop interno: scan inmediato → espera interval → repite."""
        while self._running:
            try:
                # Pausar si el VPS está en estado crítico
                while system_monitor.should_pause and self._running:
                    _log(ICON_WAIT, B_RED,
                         f"{self.network_cidr} → VPS crítico "
                         f"{GRAY}(CPU/RAM >95%){NC} — scan pausado...")
                    await asyncio.sleep(10)

                # Respetar throttle delay si hay carga alta
                delay = system_monitor.throttle_delay
                if delay > 0:
                    _log(ICON_WAIT, GRAY,
                         f"{self.network_cidr} → throttle {delay}s "
                         f"{GRAY}(VPS bajo carga){NC}")
                    await asyncio.sleep(delay)

                # Slots dinámicos: mínimo entre VPS disponible y límite del modo
                vps_slots  = system_monitor.recommended_slots
                mode_limit = self.semaphore_slots
                slots      = min(vps_slots, mode_limit)

                semaphore = asyncio.Semaphore(slots)
                
                # Anunciamos que empieza el escaneo
                self.is_scanning_now = True
                asyncio.create_task(self.broadcast_status())

                result = await scan_network(
                    network_cidr=self.network_cidr,
                    ports=self.ports,
                    semaphore=semaphore,
                    mode=self.mode,
                    timeout=self.timeout,
                )
                self.last_result  = result
                self.last_scan_at = result.finished_at
                self.is_scanning_now = False
                
                from modules.discovery.sync_service import sync_scan_result
                await sync_scan_result(result)

                # Anunciamos que terminó el escaneo y pasamos el nuevo next_scan_at
                asyncio.create_task(self.broadcast_status())

                if self._running:
                    # Bucle inteligente de espera
                    while self._running:
                        elapsed = time.time() - self.last_scan_at.timestamp() if self.last_scan_at else 0
                        remaining = self.interval - elapsed

                        if remaining <= 0:
                            break # Ya es hora de escanear

                        next_ts = datetime.fromtimestamp(time.time() + remaining).strftime("%H:%M:%S")
                        
                        _log(ICON_WAIT, GRAY,
                             f"{self.network_cidr} → próximo scan: "
                             f"{B_WHITE}{next_ts}{NC} "
                             f"{GRAY}(en {int(remaining) // 60} min | "
                             f"slots actuales: {slots}){NC}")
                        
                        try:
                            self._wake_event.clear()
                            await asyncio.wait_for(self._wake_event.wait(), timeout=remaining)
                        except asyncio.TimeoutError:
                            break # Tiempo normal cumplido

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logging.error(
                    "Error en autoscan de %s: %s", self.network_cidr, exc
                )
                await asyncio.sleep(30)  # Backoff ante error


# ──────────────────────────────────────────────
# REGISTRO GLOBAL DE SCANNERS
# ──────────────────────────────────────────────

class ScannerRegistry:
    """
    Registro central de todos los NetworkScanner activos.

    Gestiona el ciclo de vida y los cambios de modo
    cuando el usuario navega entre redes en la UI.

    Uso:
        registry = ScannerRegistry()
        await registry.add_network("192.168.1.0/24")
        await registry.set_active_network("192.168.1.0/24")
        result = registry.get_last_result("192.168.1.0/24")
        await registry.remove_network("192.168.1.0/24")
        await registry.shutdown()
    """

    def __init__(self) -> None:
        self._scanners: Dict[str, NetworkScanner] = {}
        self._active_network: Optional[str]       = None

    @property
    def active_networks(self) -> List[str]:
        """Lista de todas las redes registradas."""
        return list(self._scanners.keys())

    def get_last_result(self, network_cidr: str) -> Optional[RawScanData]:
        """Retorna el último resultado de escaneo de una red."""
        scanner = self._scanners.get(network_cidr)
        return scanner.last_result if scanner else None

    def get_scanner(self, network_cidr: str) -> Optional[NetworkScanner]:
        """Retorna el scanner de una red."""
        return self._scanners.get(network_cidr)

    def get_all_statuses(self) -> List[Dict]:
        """Retorna el estado de todos los scanners."""
        return [
            {
                "cidr": cidr,
                "mode": scanner.mode.value,
                "slots": scanner.semaphore_slots,
                "interval": scanner.interval,
                "last_scan_at": scanner.last_scan_at.isoformat() if scanner.last_scan_at else None
            }
            for cidr, scanner in self._scanners.items()
        ]

    async def add_network(
        self,
        network_cidr: str,
        network_id: Optional[int] = None,
        ports: Optional[List[int]] = None,
    ) -> NetworkScanner:
        """
        Registra una nueva red y arranca su autoscan en BACKGROUND.

        Si la red ya existe, retorna el scanner existente.

        Args:
            network_cidr: Red en notación CIDR.
            network_id:   ID de la red en BD (opcional).
            ports:        Lista de puertos. Default: DEFAULT_PORTS.

        Returns:
            El NetworkScanner creado o existente.
        """
        if network_cidr in self._scanners:
            _log(ICON_INFO, GRAY, f"Red {network_cidr} ya registrada.")
            return self._scanners[network_cidr]

        scanner = NetworkScanner(
            network_cidr=network_cidr,
            network_id=network_id,
            ports=ports or DEFAULT_PORTS,
            mode=ScanMode.BACKGROUND,
        )
        self._scanners[network_cidr] = scanner
        await scanner.start()
        return scanner

    async def set_active_network(self, network_cidr: str) -> None:
        """
        Marca una red como ACTIVA (usuario la está mirando en la UI).

        La red previamente activa pasa a BACKGROUND automáticamente.
        Dispara un scan inmediato en la nueva red activa.

        Args:
            network_cidr: Red que el usuario está mirando.
        """
        if network_cidr not in self._scanners:
            _log(ICON_CROSS, B_RED,
                 f"Red {network_cidr} no registrada. "
                 f"Usa add_network() primero.")
            return

        # Bajar la red anterior a background
        if self._active_network and self._active_network != network_cidr:
            prev_scanner = self._scanners.get(self._active_network)
            if prev_scanner:
                prev_scanner.set_background()

        # Subir la nueva red a activo + trigger inmediato
        self._active_network = network_cidr
        self._scanners[network_cidr].set_active()
        
        _log(ICON_SCAN, B_RED,
             f"Wake up loop inmediato → {B_WHITE}{network_cidr}{NC}")

    def set_background_network(self, network_cidr: str) -> None:
        """
        Marca una red explícitamente como BACKGROUND.
        """
        if network_cidr in self._scanners:
            self._scanners[network_cidr].set_background()
            if self._active_network == network_cidr:
                self._active_network = None

    async def remove_network(self, network_cidr: str) -> None:
        """
        Elimina una red del registro y detiene su autoscan.

        Args:
            network_cidr: Red a eliminar.
        """
        scanner = self._scanners.pop(network_cidr, None)
        if scanner:
            await scanner.stop()
            if self._active_network == network_cidr:
                self._active_network = None

    async def shutdown(self) -> None:
        """Detiene todos los scanners. Llamar al cerrar la aplicación."""
        _log(ICON_INFO, GRAY, "Deteniendo todos los scanners...")
        tasks = [s.stop() for s in self._scanners.values()]
        await asyncio.gather(*tasks)
        self._scanners.clear()
        _log(ICON_CHECK, B_GREEN, "Todos los scanners detenidos.")