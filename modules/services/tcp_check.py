"""
services/tcp_check.py
────────────────────────────────────────────────────────────────
Verificación de puertos TCP con medición precisa de RTT.

Responsabilidades:
  - Verificar si un puerto TCP específico está abierto
  - Medir el RTT con precisión decimal (nanosegundos → ms)
  - Gestionar timeouts y clasificar tipos de error
  - Controlar concurrencia via semáforo externo

Inspirado en la lógica de tcp.go y utils.NanoToMillisecond de tcping
(github.com/pouriyajamshidi/tcping): preserva decimales del RTT,
usa MaxDuration para casos donde la conexión tarda más del timeout,
y separa el manejo de éxito/fallo en funciones distintas.
────────────────────────────────────────────────────────────────
"""

import asyncio
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ──────────────────────────────────────────────
# CONSTANTES
# ──────────────────────────────────────────────

# Timeout por defecto para un intento de conexión TCP
DEFAULT_TCP_TIMEOUT: float = 1.5


# ──────────────────────────────────────────────
# ENUMERACIONES
# ──────────────────────────────────────────────

class PortState(Enum):
    """Estado posible de un puerto TCP verificado."""
    OPEN     = "open"      # Conexión exitosa → servicio activo
    CLOSED   = "closed"    # Recibió RST → puerto cerrado activamente
    FILTERED = "filtered"  # Timeout → firewall o host inalcanzable
    ERROR    = "error"     # Error inesperado (DNS, red, etc.)


# ──────────────────────────────────────────────
# MODELOS DE DATOS
# ──────────────────────────────────────────────

@dataclass
class ProbeConfig:
    """
    Configuración de un probe TCP individual.

    Inspirado en config.Config de tcping: centraliza todos los
    parámetros de un probe para pasarlos como un solo objeto.

    Attributes:
        ip:        Dirección IP del host objetivo.
        port:      Puerto TCP a verificar.
        timeout:   Tiempo máximo de espera en segundos.
        semaphore: Semáforo asyncio para controlar concurrencia.
    """
    ip: str
    port: int
    timeout: float = DEFAULT_TCP_TIMEOUT
    semaphore: Optional[asyncio.Semaphore] = None


@dataclass
class ProbeResult:
    """
    Resultado de un único intento de conexión TCP.

    Combina el estado del puerto con métricas de rendimiento.
    Inspirado en stats.Statistics de tcping, adaptado a un
    resultado puntual (no acumulado — eso lo gestiona analyzer.py).

    Attributes:
        ip:          Dirección IP verificada.
        port:        Puerto TCP verificado.
        state:       Estado del puerto (open/closed/filtered/error).
        is_open:     Atajo booleano → state == OPEN.
        rtt_ms:      RTT en milisegundos con decimales. 0.0 si falló.
        error_msg:   Descripción del error si state != OPEN.
        probed_at:   Timestamp exacto del inicio del probe.
    """
    ip: str
    port: int
    state: PortState
    rtt_ms: float
    probed_at: datetime
    error_msg: str = ""

    @property
    def is_open(self) -> bool:
        """True si el puerto está abierto."""
        return self.state == PortState.OPEN


# ──────────────────────────────────────────────
# UTILIDADES DE TIEMPO
# ──────────────────────────────────────────────

def _nano_to_ms(nanoseconds: int) -> float:
    """
    Convierte nanosegundos a milisegundos con precisión decimal.

    Equivalente directo de utils.NanoToMillisecond en tcping:
    evita el truncamiento de decimales que haría int() o
    duration.Milliseconds().

    Args:
        nanoseconds: Tiempo medido en nanosegundos.

    Returns:
        Tiempo en milisegundos con punto decimal (ej: 2.347 ms).
    """
    return nanoseconds / 1_000_000.0


def _max_duration(actual_ns: int, timeout_seconds: float) -> float:
    """
    Retorna el mayor entre el tiempo real y el timeout configurado.

    Equivalente a utils.MaxDuration en tcping: si la conexión tardó
    más que el timeout (por condiciones de red), usa el tiempo real
    para que las métricas sean honestas.

    Args:
        actual_ns:       Tiempo real de la conexión en nanosegundos.
        timeout_seconds: Timeout configurado en segundos.

    Returns:
        El mayor de los dos valores, en milisegundos.
    """
    actual_ms  = _nano_to_ms(actual_ns)
    timeout_ms = timeout_seconds * 1000.0
    return max(actual_ms, timeout_ms)


# ──────────────────────────────────────────────
# MANEJADORES DE ESTADO (patrón de tcping)
# ──────────────────────────────────────────────

def _handle_success(
    ip: str,
    port: int,
    rtt_ns: int,
    probed_at: datetime,
) -> ProbeResult:
    """
    Procesa un probe TCP exitoso.

    Equivalente a handleConnSuccess de tcping: calcula el RTT
    con precisión decimal y construye el resultado de éxito.

    Args:
        ip:        IP del host.
        port:      Puerto verificado.
        rtt_ns:    RTT medido en nanosegundos.
        probed_at: Timestamp de inicio del probe.

    Returns:
        ProbeResult con state=OPEN y RTT calculado.
    """
    return ProbeResult(
        ip=ip,
        port=port,
        state=PortState.OPEN,
        rtt_ms=round(_nano_to_ms(rtt_ns), 3),
        probed_at=probed_at,
    )


def _handle_failure(
    ip: str,
    port: int,
    elapsed_ns: int,
    timeout: float,
    error: Exception,
    probed_at: datetime,
) -> ProbeResult:
    """
    Procesa un probe TCP fallido.

    Equivalente a handleConnFailure de tcping: clasifica el tipo
    de fallo (closed vs filtered) y registra el tiempo transcurrido.

    La distinción closed/filtered es importante para el soporte:
    - CLOSED  → el host existe pero el puerto está inactivo
    - FILTERED → el host no responde (apagado, firewall, fuera de red)

    Args:
        ip:         IP del host.
        port:       Puerto verificado.
        elapsed_ns: Tiempo transcurrido en nanosegundos.
        timeout:    Timeout configurado para clasificar filtered.
        error:      Excepción capturada.
        probed_at:  Timestamp de inicio del probe.

    Returns:
        ProbeResult con state=CLOSED o FILTERED según el error.
    """
    if isinstance(error, asyncio.TimeoutError):
        state     = PortState.FILTERED
        error_msg = f"timeout after {timeout}s"
    elif isinstance(error, ConnectionRefusedError):
        state     = PortState.CLOSED
        error_msg = "connection refused (RST)"
    else:
        state     = PortState.ERROR
        error_msg = str(error)

    return ProbeResult(
        ip=ip,
        port=port,
        state=state,
        rtt_ms=round(_max_duration(elapsed_ns, timeout), 3),
        probed_at=probed_at,
        error_msg=error_msg,
    )


# ──────────────────────────────────────────────
# FUNCIÓN PRINCIPAL — Probe TCP
# ──────────────────────────────────────────────

async def check_port(cfg: ProbeConfig) -> ProbeResult:
    """
    Verifica si un puerto TCP está abierto mediante socket connect.

    Implementa el mismo patrón que tcp.go de tcping:
      1. Registra el timestamp de inicio
      2. Intenta la conexión TCP
      3. Mide el RTT con nanosegundos
      4. Delega a _handle_success o _handle_failure

    Si cfg.semaphore está configurado, respeta el límite de
    conexiones simultáneas del semáforo global.

    Args:
        cfg: ProbeConfig con IP, puerto, timeout y semáforo.

    Returns:
        ProbeResult con el estado del puerto y el RTT medido.
    """
    probed_at = datetime.now()

    if cfg.semaphore:
        async with cfg.semaphore:
            return await _do_tcp_connect(cfg, probed_at)
    else:
        return await _do_tcp_connect(cfg, probed_at)


async def _do_tcp_connect(
    cfg: ProbeConfig,
    probed_at: datetime,
) -> ProbeResult:
    """
    Ejecuta la conexión TCP y mide el RTT.

    Usa sock.setblocking(False) + loop.sock_connect() para que
    la operación sea completamente no bloqueante (compatible con
    asyncio sin bloquear el event loop).

    Args:
        cfg:       ProbeConfig con los parámetros del probe.
        probed_at: Timestamp de inicio del probe.

    Returns:
        ProbeResult construido por _handle_success o _handle_failure.
    """
    loop      = asyncio.get_event_loop()
    sock      = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    t_start   = time.monotonic_ns()

    sock.setblocking(False)
    try:
        await asyncio.wait_for(
            loop.sock_connect(sock, (cfg.ip, cfg.port)),
            timeout=cfg.timeout,
        )
        elapsed_ns = time.monotonic_ns() - t_start
        return _handle_success(cfg.ip, cfg.port, elapsed_ns, probed_at)

    except Exception as exc:
        elapsed_ns = time.monotonic_ns() - t_start
        return _handle_failure(
            cfg.ip, cfg.port, elapsed_ns, cfg.timeout, exc, probed_at
        )
    finally:
        sock.close()


# ──────────────────────────────────────────────
# FUNCIÓN DE CONVENIENCIA — Múltiples puertos
# ──────────────────────────────────────────────

async def check_ports(
    ip: str,
    ports: list[int],
    timeout: float = DEFAULT_TCP_TIMEOUT,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> list[ProbeResult]:
    """
    Verifica múltiples puertos de un host en paralelo.

    Función de conveniencia que crea un ProbeConfig por puerto
    y los ejecuta concurrentemente respetando el semáforo.

    Args:
        ip:        Dirección IP del host.
        ports:     Lista de puertos TCP a verificar.
        timeout:   Timeout por puerto en segundos.
        semaphore: Semáforo global de concurrencia (opcional).

    Returns:
        Lista de ProbeResult, uno por cada puerto verificado.
    """
    tasks = [
        check_port(ProbeConfig(ip=ip, port=port, timeout=timeout, semaphore=semaphore))
        for port in ports
    ]
    return list(await asyncio.gather(*tasks))