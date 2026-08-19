"""
services/analyzer.py
────────────────────────────────────────────────────────────────
Motor de estadísticas acumuladas por host y servicio.

Responsabilidades:
  - Mantener estadísticas históricas entre ciclos de escaneo
  - Calcular uptime/downtime acumulado con timestamps precisos
  - Calcular RTT: mínimo, máximo, promedio
  - Detectar y registrar transiciones de estado (up→down, down→up)
  - Calcular disponibilidad (%) como métrica de SLA

Inspirado en stats.Statistics, stats.LongestTime, stats.RTTResult
y los manejadores handleConnSuccess/handleConnFailure de tcping
(github.com/pouriyajamshidi/tcping).

El Analyzer es STATEFUL: debe mantenerse vivo entre ciclos
de escaneo. Cada host en cada red tiene su propio Analyzer.
────────────────────────────────────────────────────────────────
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

from modules.services.ping_icmp import PingResult, HostnameChange
from modules.services.tcp_check import ProbeResult, PortState


# ──────────────────────────────────────────────
# MODELOS DE ESTADÍSTICAS
# ──────────────────────────────────────────────

@dataclass
class RTTStats:
    """
    Estadísticas de Round-Trip Time acumuladas.

    Equivalente a stats.RTTResult de tcping: preserva min/max/avg
    con precisión decimal para mostrar tendencias de latencia.

    Attributes:
        min_ms:      RTT mínimo registrado en milisegundos.
        max_ms:      RTT máximo registrado en milisegundos.
        avg_ms:      RTT promedio acumulado en milisegundos.
        has_results: True si hay al menos una medición.
    """
    min_ms: float = 0.0
    max_ms: float = 0.0
    avg_ms: float = 0.0
    has_results: bool = False


@dataclass
class LongestPeriod:
    """
    Registro del período más largo de uptime o downtime.

    Equivalente a stats.LongestTime de tcping: permite saber
    cuánto tiempo estuvo el dispositivo sin interrupciones (mejor
    uptime) o cuánto estuvo caído en el peor incidente.

    Attributes:
        start:    Inicio del período.
        end:      Fin del período.
        duration: Duración total del período.
    """
    start: Optional[datetime] = None
    end: Optional[datetime]   = None
    duration: timedelta        = field(default_factory=timedelta)

    @property
    def duration_str(self) -> str:
        """Duración legible para mostrar en UI."""
        total = int(self.duration.total_seconds())
        h, rem = divmod(total, 3600)
        m, s   = divmod(rem, 60)
        if h > 0:
            return f"{h}h {m}m {s}s"
        if m > 0:
            return f"{m}m {s}s"
        return f"{s}s"


@dataclass
class StateTransition:
    """
    Registro de una transición de estado del host/servicio.

    Captura el momento exacto en que un dispositivo pasó de
    estar activo a inactivo o viceversa. Fundamental para
    que el soporte vea el historial de incidentes.

    Attributes:
        from_state:  Estado previo ("up" o "down").
        to_state:    Estado nuevo ("up" o "down").
        occurred_at: Timestamp de la transición.
        duration:    Duración del estado previo antes de cambiar.
    """
    from_state: str
    to_state: str
    occurred_at: datetime
    duration: timedelta


@dataclass
class ServiceStats:
    """
    Estadísticas acumuladas de un host o puerto específico.

    Equivalente completo de stats.Statistics de tcping, adaptado
    para el contexto de monitoreo continuo multihost.

    El equipo de soporte puede ver en tiempo real:
    - ¿Está el dispositivo activo ahora?
    - ¿Cuántos ciclos lleva activo sin interrupciones?
    - ¿Cuánto tiempo estuvo caído la última vez?
    - ¿Cuál es su RTT promedio?
    - ¿Cuál es su disponibilidad histórica?

    Attributes:
        ip:                       IP del host.
        port:                     Puerto (None si es estadística de host).
        hostname:                 Nombre resuelto del dispositivo.
        is_up:                    Estado actual.
        total_probes:             Total de verificaciones realizadas.
        successful_probes:        Verificaciones exitosas.
        failed_probes:            Verificaciones fallidas.
        ongoing_successful:       Racha actual de éxitos consecutivos.
        ongoing_failed:           Racha actual de fallos consecutivos.
        total_uptime:             Uptime acumulado total.
        total_downtime:           Downtime acumulado total.
        start_of_uptime:          Inicio del período de uptime actual.
        start_of_downtime:        Inicio del período de downtime actual.
        longest_uptime:           Período de uptime más largo histórico.
        longest_downtime:         Período de downtime más largo histórico.
        last_successful_probe:    Timestamp del último éxito.
        last_failed_probe:        Timestamp del último fallo.
        rtt:                      Estadísticas RTT acumuladas.
        rtt_history:              Lista de últimos RTT para tendencias.
        hostname_changes:         Historial de cambios de nombre.
        transitions:              Historial de transiciones up/down.
        monitoring_since:         Inicio del monitoreo.
    """
    ip: str
    port: Optional[int]
    hostname: str
    is_up: bool                          = False
    total_probes: int                    = 0
    successful_probes: int               = 0
    failed_probes: int                   = 0
    ongoing_successful: int              = 0
    ongoing_failed: int                  = 0
    total_uptime: timedelta              = field(default_factory=timedelta)
    total_downtime: timedelta            = field(default_factory=timedelta)
    start_of_uptime: Optional[datetime]  = None
    start_of_downtime: Optional[datetime] = None
    longest_uptime: LongestPeriod        = field(default_factory=LongestPeriod)
    longest_downtime: LongestPeriod      = field(default_factory=LongestPeriod)
    last_successful_probe: Optional[datetime] = None
    last_failed_probe: Optional[datetime]     = None
    rtt: RTTStats                        = field(default_factory=RTTStats)
    rtt_history: List[float]             = field(default_factory=list)
    hostname_changes: List[HostnameChange] = field(default_factory=list)
    transitions: List[StateTransition]   = field(default_factory=list)
    monitoring_since: datetime           = field(default_factory=datetime.now)

    # Máximo de RTT históricos a conservar en memoria
    MAX_RTT_HISTORY: int = 100

    @property
    def availability_percent(self) -> float:
        """
        Porcentaje de disponibilidad histórica.

        Métrica de SLA: successful_probes / total_probes × 100.
        """
        if self.total_probes == 0:
            return 0.0
        return round((self.successful_probes / self.total_probes) * 100, 2)

    @property
    def current_uptime_duration(self) -> Optional[timedelta]:
        """Duración del período de uptime actual (si está activo)."""
        if self.is_up and self.start_of_uptime:
            return datetime.now() - self.start_of_uptime
        return None

    @property
    def current_downtime_duration(self) -> Optional[timedelta]:
        """Duración del período de downtime actual (si está caído)."""
        if not self.is_up and self.start_of_downtime:
            return datetime.now() - self.start_of_downtime
        return None


# ──────────────────────────────────────────────
# CLASE PRINCIPAL — Analyzer
# ──────────────────────────────────────────────

class Analyzer:
    """
    Motor de estadísticas acumuladas por host.

    Mantiene el estado histórico entre ciclos de escaneo y
    aplica la lógica de transición up/down inspirada en los
    manejadores handleConnSuccess / handleConnFailure de tcping.

    Cada host tiene su propio Analyzer, que vive mientras
    el NetworkScanner esté activo para esa red.

    Uso:
        analyzer = Analyzer(ip="192.168.1.50", port=None)
        analyzer.process_ping(ping_result)
        analyzer.process_probe(tcp_result)
        stats = analyzer.stats
        print(stats.availability_percent)
    """

    def __init__(self, ip: str, port: Optional[int] = None) -> None:
        self.stats = ServiceStats(
            ip=ip,
            port=port,
            hostname="unknown",
        )

    # ── Procesadores públicos ──────────────────

    def process_ping(self, result: PingResult) -> None:
        """
        Procesa el resultado de un ping ICMP y actualiza estadísticas.

        Actualiza:
          - Estado del host (is_up)
          - Hostname y detección de cambios
          - Rachas de éxito/fallo
          - Uptime/downtime acumulados
          - Períodos más largos históricos
          - RTT acumulado

        Args:
            result: PingResult producido por ping_icmp.ping_host().
        """
        now = result.checked_at

        # Actualizar hostname y registrar cambios
        self._update_hostname(result)

        # Actualizar counters y estado
        self.stats.total_probes += 1

        if result.is_alive:
            self._handle_success_ping(result, now)
        else:
            self._handle_failure_ping(result, now)

    def process_probe(self, result: ProbeResult) -> None:
        """
        Procesa el resultado de un probe TCP y actualiza estadísticas.

        Similar a process_ping pero para un puerto específico.
        Acumula RTT adicional y actualiza el estado del servicio.

        Args:
            result: ProbeResult producido por tcp_check.check_port().
        """
        now = result.probed_at
        self.stats.total_probes += 1

        if result.is_open:
            self._handle_success_probe(result, now)
        else:
            self._handle_failure_probe(result, now)

    # ── Manejadores internos (patrón tcping) ──

    def _handle_success_ping(self, result: PingResult, now: datetime) -> None:
        """
        Procesa un ping exitoso.

        Equivalente a handleConnSuccess de tcping:
        - Si venía de downtime → registra fin de downtime y transición
        - Actualiza uptime acumulado y racha de éxitos
        - Acumula RTT

        Args:
            result: PingResult exitoso.
            now:    Timestamp del probe.
        """
        s = self.stats

        # Transición down → up
        if not s.is_up and s.start_of_downtime:
            downtime_duration = now - s.start_of_downtime
            s.total_downtime += downtime_duration

            # Actualizar el período de downtime más largo
            self._update_longest(s.longest_downtime, s.start_of_downtime, now)

            # Registrar la transición
            s.transitions.append(StateTransition(
                from_state="down",
                to_state="up",
                occurred_at=now,
                duration=downtime_duration,
            ))

            s.start_of_downtime  = None
            s.ongoing_failed     = 0
            s.start_of_uptime    = now

        # Inicializar uptime si es la primera vez
        if s.start_of_uptime is None:
            s.start_of_uptime = now

        s.is_up                 = True
        s.successful_probes    += 1
        s.ongoing_successful   += 1
        s.last_successful_probe = now

        # Acumular RTT
        if result.rtt_ms > 0:
            self._update_rtt(result.rtt_ms)

    def _handle_failure_ping(self, result: PingResult, now: datetime) -> None:
        """
        Procesa un ping fallido.

        Equivalente a handleConnFailure de tcping:
        - Si venía de uptime → registra fin de uptime y transición
        - Actualiza downtime acumulado y racha de fallos

        Args:
            result: PingResult fallido.
            now:    Timestamp del probe.
        """
        s = self.stats

        # Transición up → down
        if s.is_up and s.start_of_uptime:
            uptime_duration = now - s.start_of_uptime
            s.total_uptime += uptime_duration

            # Actualizar el período de uptime más largo
            self._update_longest(s.longest_uptime, s.start_of_uptime, now)

            # Registrar la transición
            s.transitions.append(StateTransition(
                from_state="up",
                to_state="down",
                occurred_at=now,
                duration=uptime_duration,
            ))

            s.start_of_uptime  = None
            s.ongoing_successful = 0
            s.start_of_downtime  = now

        # Inicializar downtime si es la primera vez
        if s.start_of_downtime is None:
            s.start_of_downtime = now

        s.is_up             = False
        s.failed_probes    += 1
        s.ongoing_failed   += 1
        s.last_failed_probe = now

    def _handle_success_probe(self, result: ProbeResult, now: datetime) -> None:
        """Maneja un TCP probe exitoso."""
        s = self.stats
        if not s.is_up:
            s.start_of_uptime = now
        s.is_up                 = True
        s.successful_probes    += 1
        s.ongoing_successful   += 1
        s.ongoing_failed        = 0
        s.last_successful_probe = now
        if result.rtt_ms > 0:
            self._update_rtt(result.rtt_ms)

    def _handle_failure_probe(self, result: ProbeResult, now: datetime) -> None:
        """Maneja un TCP probe fallido."""
        s = self.stats
        if s.is_up:
            s.start_of_downtime = now
        s.is_up             = False
        s.failed_probes    += 1
        s.ongoing_failed   += 1
        s.ongoing_successful = 0
        s.last_failed_probe = now

    # ── Helpers ───────────────────────────────

    def _update_hostname(self, result: PingResult) -> None:
        """
        Actualiza el hostname en las estadísticas.

        Registra el cambio en el historial si el nombre del
        dispositivo cambió respecto al ciclo anterior.

        Args:
            result: PingResult con el hostname resuelto.
        """
        if result.hostname != "unknown":
            self.stats.hostname = result.hostname

        if result.hostname_changed and result.hostname_change:
            self.stats.hostname_changes.append(result.hostname_change)

    def _update_rtt(self, rtt_ms: float) -> None:
        """
        Acumula una medición de RTT y actualiza min/max/avg.

        Equivalente al manejo de s.RTT en tcping: mantiene
        historial limitado y estadísticas en tiempo constante.

        Args:
            rtt_ms: Nuevo valor de RTT en milisegundos.
        """
        rtt = self.stats.rtt
        history = self.stats.rtt_history

        history.append(rtt_ms)

        # Limitar el historial en memoria
        if len(history) > self.stats.MAX_RTT_HISTORY:
            history.pop(0)

        rtt.min_ms      = min(history)
        rtt.max_ms      = max(history)
        rtt.avg_ms      = round(sum(history) / len(history), 3)
        rtt.has_results = True

    @staticmethod
    def _update_longest(
        record: LongestPeriod,
        start: datetime,
        end: datetime,
    ) -> None:
        """
        Actualiza el período más largo si el actual lo supera.

        Equivalente a utils.SetLongestDuration de tcping.

        Args:
            record: LongestPeriod a actualizar (uptime o downtime).
            start:  Inicio del período que acaba de terminar.
            end:    Fin del período (momento actual).
        """
        duration = end - start
        if record.duration == timedelta() or duration >= record.duration:
            record.start    = start
            record.end      = end
            record.duration = duration


# ──────────────────────────────────────────────
# REGISTRO DE ANALYZERS POR HOST
# ──────────────────────────────────────────────

class AnalyzerRegistry:
    """
    Registro central de Analyzers por red y host.

    Mantiene un Analyzer por cada (red, IP) activa, preservando
    el historial estadístico entre ciclos de escaneo.

    Uso:
        registry = AnalyzerRegistry()
        analyzer = registry.get_or_create("192.168.1.0/24", "192.168.1.50")
        analyzer.process_ping(ping_result)
        stats = registry.get_stats("192.168.1.0/24", "192.168.1.50")
    """

    def __init__(self) -> None:
        # Estructura: { "cidr": { "ip": Analyzer } }
        self._registry: dict[str, dict[str, Analyzer]] = {}

    def get_or_create(
        self,
        network_cidr: str,
        ip: str,
        port: Optional[int] = None,
    ) -> Analyzer:
        """
        Retorna el Analyzer existente o crea uno nuevo.

        Args:
            network_cidr: Red a la que pertenece el host.
            ip:           IP del host.
            port:         Puerto (None para estadística de host).

        Returns:
            Analyzer correspondiente al host.
        """
        if network_cidr not in self._registry:
            self._registry[network_cidr] = {}

        key = f"{ip}:{port}" if port else ip

        if key not in self._registry[network_cidr]:
            self._registry[network_cidr][key] = Analyzer(ip=ip, port=port)

        return self._registry[network_cidr][key]

    def get_stats(
        self,
        network_cidr: str,
        ip: str,
        port: Optional[int] = None,
    ) -> Optional[ServiceStats]:
        """
        Retorna las estadísticas de un host/puerto específico.

        Args:
            network_cidr: Red del host.
            ip:           IP del host.
            port:         Puerto (None para estadística de host).

        Returns:
            ServiceStats o None si no existe.
        """
        key      = f"{ip}:{port}" if port else ip
        analyzer = self._registry.get(network_cidr, {}).get(key)
        return analyzer.stats if analyzer else None

    def get_all_stats(self, network_cidr: str) -> List[ServiceStats]:
        """
        Retorna todas las estadísticas de una red.

        Args:
            network_cidr: Red a consultar.

        Returns:
            Lista de ServiceStats de todos los hosts monitoreados.
        """
        return [
            a.stats
            for a in self._registry.get(network_cidr, {}).values()
        ]

    def remove_network(self, network_cidr: str) -> None:
        """
        Elimina todos los Analyzers de una red del registro.

        Args:
            network_cidr: Red a eliminar.
        """
        self._registry.pop(network_cidr, None)

# Instancia global compartida por toda la aplicación
analyzer_registry = AnalyzerRegistry()
