"""
core/system_monitor.py
────────────────────────────────────────────────────────────────
Monitor de recursos del servidor VPS en tiempo real.

Propósito CRÍTICO:
  El VPS comparte recursos con 2-3 sistemas pesados adicionales.
  Este módulo vigila CPU y RAM del servidor y ajusta dinámicamente
  los slots del semáforo del scanner para que MvpMonitoreo nunca
  sature el servidor ni interfiera con los demás sistemas.

Regla de oro implementada:
  MvpMonitoreo no debe usar más del 20-25% del CPU disponible.
  Si otros sistemas consumen el 70%, reducimos a 3-5 slots.
  Si el VPS está tranquilo (20% CPU), escalamos a 30 slots.

Integración con scanner.py:
  El ScannerRegistry consulta SystemMonitor antes de cada ciclo
  para obtener el número de slots permitidos en ese momento.

Adaptado de tcp_port_checker/system_monitor.py con mejoras:
  - Compatible con asyncio (sin bloqueos en el event loop)
  - Historial de tendencias para decisiones más estables
  - API clara para integrar con scanner y routes/api.py

Requiere: pip install psutil
────────────────────────────────────────────────────────────────
"""

import asyncio
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import psutil


# ──────────────────────────────────────────────
# CONSTANTES DE CONFIGURACIÓN
# ──────────────────────────────────────────────

# Intervalo de muestreo del monitor en segundos
MONITOR_INTERVAL: float = 0.5

# Límites del semáforo para el scanner
MIN_SEMAPHORE_SLOTS: int = 2    # Mínimo absoluto (siempre escanea algo)
MAX_SEMAPHORE_SLOTS: int = 30   # Máximo cuando el VPS está libre

# Umbrales de CPU y RAM para activar throttling
THROTTLE_CPU_THRESHOLD: float    = 80.0  # % CPU total del VPS
THROTTLE_MEMORY_THRESHOLD: float = 85.0  # % RAM total del VPS

# Umbrales para pausar completamente el scan
PAUSE_CPU_THRESHOLD: float    = 95.0  # % CPU → pausa total
PAUSE_MEMORY_THRESHOLD: float = 95.0  # % RAM → pausa total

# Cuántas muestras usar para análisis de tendencia
TREND_WINDOW: int = 20

# Varianza máxima para considerar el sistema "estable"
STABILITY_VARIANCE_THRESHOLD: float = 15.0

# Tiempo mínimo de estabilidad antes de aumentar slots (segundos)
STABILITY_DURATION: float = 5.0


# ──────────────────────────────────────────────
# MODELOS DE DATOS
# ──────────────────────────────────────────────

@dataclass
class SystemSnapshot:
    """
    Instantánea del estado del sistema en un momento dado.

    Attributes:
        cpu_percent:          % CPU total del VPS (todos los cores).
        memory_percent:       % RAM usada del VPS.
        memory_available_gb:  GB de RAM disponibles.
        cpu_count:            Número de cores lógicos del VPS.
        recommended_slots:    Slots de semáforo recomendados para el scanner.
        should_throttle:      True si el sistema está bajo carga alta.
        should_pause:         True si el sistema está crítico (>95%).
        throttle_delay_s:     Segundos que el scanner debe esperar.
        captured_at:          Timestamp de la medición.
    """
    cpu_percent: float
    memory_percent: float
    memory_available_gb: float
    cpu_count: int
    recommended_slots: int
    should_throttle: bool
    should_pause: bool
    throttle_delay_s: float
    captured_at: datetime


@dataclass
class SystemHealthSummary:
    """
    Resumen de salud del servidor para mostrar en la UI.

    Consumido por routes/api.py para el dashboard de soporte.
    Permite al equipo ver si el servidor está bajo presión.

    Attributes:
        cpu_health:       "Excellent" | "Good" | "Medium" | "High" | "Critical"
        memory_health:    "Excellent" | "Good" | "Medium" | "High" | "Critical"
        overall_health:   "Good" | "Attention Required" | "Critical"
        is_stable:        True si la carga está estable (baja varianza).
        throttle_active:  True si el scanner está siendo limitado.
        scanner_slots:    Slots actuales del semáforo del scanner.
        uptime_seconds:   Segundos que lleva corriendo el monitor.
        cpu_percent:      % CPU actual.
        memory_percent:   % RAM actual.
        recommendations:  Lista de recomendaciones si hay problemas.
    """
    cpu_health: str
    memory_health: str
    overall_health: str
    is_stable: bool
    throttle_active: bool
    scanner_slots: int
    uptime_seconds: float
    cpu_percent: float
    memory_percent: float
    recommendations: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────
# CLASE PRINCIPAL
# ──────────────────────────────────────────────

class SystemMonitor:
    """
    Monitor de recursos del VPS en tiempo real.

    Corre en un hilo de background (daemon thread) para no bloquear
    el event loop de asyncio. El scanner consulta este monitor antes
    de cada ciclo para saber cuántos slots puede usar.

    Diseñado para un VPS compartido donde MvpMonitoreo compite por
    recursos con 2-3 sistemas adicionales.

    Uso:
        monitor = SystemMonitor()
        monitor.start()

        # En el scanner, antes de cada ciclo:
        slots = monitor.recommended_slots
        semaphore = asyncio.Semaphore(slots)

        # Para la UI:
        health = monitor.get_health_summary()

        # Al cerrar la app:
        monitor.stop()

    También soporta context manager:
        with SystemMonitor() as monitor:
            slots = monitor.recommended_slots
    """

    def __init__(self) -> None:
        self._is_running: bool                      = False
        self._thread: Optional[threading.Thread]    = None
        self._lock: threading.RLock                 = threading.RLock()
        self._start_time: Optional[float]           = None

        # Historial circular para análisis de tendencia
        self._cpu_history: deque[float]    = deque(maxlen=TREND_WINDOW)
        self._memory_history: deque[float] = deque(maxlen=TREND_WINDOW)

        # Estadísticas de throttling
        self._throttle_active: bool         = False
        self._throttle_start: Optional[float] = None
        self._total_throttle_time: float    = 0.0
        self._total_measurements: int       = 0

        # Snapshot actual (actualizado por el hilo de fondo)
        self._current: SystemSnapshot = self._build_initial_snapshot()

    # ── API pública para el scanner ───────────────

    @property
    def recommended_slots(self) -> int:
        """
        Número de slots recomendados para el semáforo del scanner.

        Esta es la propiedad principal que usa scanner.py antes
        de cada ciclo de escaneo. Thread-safe.
        """
        with self._lock:
            return self._current.recommended_slots

    @property
    def should_pause(self) -> bool:
        """
        True si el VPS está en estado crítico y el scan debe pausarse.

        El scanner debe esperar hasta que should_pause sea False.
        """
        with self._lock:
            return self._current.should_pause

    @property
    def throttle_delay(self) -> float:
        """Segundos que el scanner debe esperar entre ciclos si hay carga."""
        with self._lock:
            return self._current.throttle_delay_s

    def get_snapshot(self) -> SystemSnapshot:
        """Retorna una copia del snapshot actual. Thread-safe."""
        with self._lock:
            return self._current

    # ── API pública para la UI ────────────────────

    def get_health_summary(self) -> SystemHealthSummary:
        """
        Retorna el resumen de salud del servidor para la UI.

        Consumido por routes/api.py para mostrar en el dashboard.
        """
        with self._lock:
            snap  = self._current
            uptime = (
                time.time() - self._start_time
                if self._start_time else 0.0
            )

        cpu_health    = self._classify_resource(snap.cpu_percent)
        memory_health = self._classify_resource(snap.memory_percent)

        if cpu_health in ("High", "Critical") or memory_health in ("High", "Critical"):
            overall = "Critical" if "Critical" in (cpu_health, memory_health) else "Attention Required"
        else:
            overall = "Good"

        return SystemHealthSummary(
            cpu_health=cpu_health,
            memory_health=memory_health,
            overall_health=overall,
            is_stable=self.is_stable(),
            throttle_active=snap.should_throttle,
            scanner_slots=snap.recommended_slots,
            uptime_seconds=round(uptime, 1),
            cpu_percent=snap.cpu_percent,
            memory_percent=snap.memory_percent,
            recommendations=self._get_recommendations(snap),
        )

    def is_stable(self) -> bool:
        """
        True si la carga del VPS ha sido estable en los últimos segundos.

        El scanner puede aumentar slots solo cuando el sistema es estable.
        Un sistema inestable (carga saltando) necesita ser conservador.
        """
        required_samples = max(
            5, int(STABILITY_DURATION / MONITOR_INTERVAL)
        )

        with self._lock:
            if len(self._cpu_history) < required_samples:
                return False

            recent_cpu    = list(self._cpu_history)[-required_samples:]
            recent_memory = list(self._memory_history)[-required_samples:]

        cpu_variance    = max(recent_cpu) - min(recent_cpu)
        memory_variance = max(recent_memory) - min(recent_memory)

        return (
            cpu_variance    < STABILITY_VARIANCE_THRESHOLD and
            memory_variance < STABILITY_VARIANCE_THRESHOLD
        )

    # ── Ciclo de vida ─────────────────────────────

    def start(self) -> None:
        """Inicia el monitor en un hilo de fondo (daemon)."""
        if self._is_running:
            return

        self._is_running  = True
        self._start_time  = time.time()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="SystemMonitor",
        )
        self._thread.start()
        # Espera la primera medición antes de retornar
        time.sleep(MONITOR_INTERVAL * 2)

    def stop(self) -> None:
        """Detiene el monitor limpiamente."""
        self._is_running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def __enter__(self) -> "SystemMonitor":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()

    # ── Loop de monitoreo ────────────────────────

    def _monitor_loop(self) -> None:
        """
        Loop principal — corre en hilo de fondo cada MONITOR_INTERVAL.

        Recolecta CPU y RAM, actualiza historial y recalcula
        el snapshot con las recomendaciones para el scanner.
        """
        while self._is_running:
            try:
                cpu    = psutil.cpu_percent(interval=None)
                mem    = psutil.virtual_memory()

                with self._lock:
                    self._cpu_history.append(cpu)
                    self._memory_history.append(mem.percent)
                    self._total_measurements += 1

                    self._current = self._build_snapshot(
                        cpu_percent=cpu,
                        memory_percent=mem.percent,
                        memory_available_gb=mem.available / (1024 ** 3),
                    )
                    self._update_throttle_accounting()

            except Exception:
                pass  # Monitor nunca debe crashear la app

            time.sleep(MONITOR_INTERVAL)

    # ── Cálculos internos ─────────────────────────

    def _build_snapshot(
        self,
        cpu_percent: float,
        memory_percent: float,
        memory_available_gb: float,
    ) -> SystemSnapshot:
        """
        Construye el snapshot actual con todos los valores calculados.
        Llamar solo con _lock adquirido.
        """
        slots         = self._calculate_slots(cpu_percent, memory_percent)
        should_throttle = (
            cpu_percent    > THROTTLE_CPU_THRESHOLD or
            memory_percent > THROTTLE_MEMORY_THRESHOLD
        )
        should_pause = (
            cpu_percent    > PAUSE_CPU_THRESHOLD or
            memory_percent > PAUSE_MEMORY_THRESHOLD
        )
        throttle_delay = self._calculate_throttle_delay(
            cpu_percent, memory_percent
        )

        return SystemSnapshot(
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            memory_available_gb=round(memory_available_gb, 2),
            cpu_count=psutil.cpu_count() or 2,
            recommended_slots=slots,
            should_throttle=should_throttle,
            should_pause=should_pause,
            throttle_delay_s=throttle_delay,
            captured_at=datetime.now(),
        )

    def _build_initial_snapshot(self) -> SystemSnapshot:
        """Snapshot conservador para antes de la primera medición."""
        return SystemSnapshot(
            cpu_percent=0.0,
            memory_percent=0.0,
            memory_available_gb=0.0,
            cpu_count=psutil.cpu_count() or 2,
            recommended_slots=MIN_SEMAPHORE_SLOTS,
            should_throttle=False,
            should_pause=False,
            throttle_delay_s=0.0,
            captured_at=datetime.now(),
        )

    def _calculate_slots(
        self,
        cpu_percent: float,
        memory_percent: float,
    ) -> int:
        """
        Calcula los slots de semáforo recomendados según la carga del VPS.

        Usa tendencia histórica para evitar oscilaciones bruscas.
        El scanner ajustará su semáforo con este valor antes de cada ciclo.

        Lógica:
          CPU+RAM < 20% → slots máximos (30)
          CPU+RAM 20-40% → slots altos (20)
          CPU+RAM 40-60% → slots medios (15)
          CPU+RAM 60-80% → slots bajos (8)
          CPU+RAM > 80%  → slots mínimos (2-5)
          CPU+RAM > 95%  → slots absolutos (2)
        """
        # Usar promedio tendencial si hay historial
        if len(self._cpu_history) >= 10:
            avg_cpu = sum(list(self._cpu_history)[-10:]) / 10
            avg_mem = sum(list(self._memory_history)[-10:]) / 10
        else:
            avg_cpu = cpu_percent
            avg_mem = memory_percent

        max_usage = max(avg_cpu, avg_mem)

        if max_usage > 95:
            slots = MIN_SEMAPHORE_SLOTS       # 2 — crítico
        elif max_usage > 80:
            slots = 5                          # alto
        elif max_usage > 60:
            slots = 8                          # medio-alto
        elif max_usage > 40:
            slots = 15                         # medio
        elif max_usage > 20:
            slots = 20                         # bajo
        else:
            slots = MAX_SEMAPHORE_SLOTS        # 30 — libre

        return max(MIN_SEMAPHORE_SLOTS, min(slots, MAX_SEMAPHORE_SLOTS))

    @staticmethod
    def _calculate_throttle_delay(
        cpu_percent: float,
        memory_percent: float,
    ) -> float:
        """
        Calcula cuántos segundos debe esperar el scanner entre ciclos.

        Delay progresivo: a mayor carga, más pausa.
        """
        max_usage = max(cpu_percent, memory_percent)

        if max_usage > 95:
            return 3.0   # Crítico
        elif max_usage > 90:
            return 2.0   # Muy alto
        elif max_usage > 85:
            return 1.0   # Alto
        elif max_usage > 80:
            return 0.5   # Medio-alto
        elif max_usage > 70:
            return 0.2   # Medio
        else:
            return 0.0   # Sin throttle

    def _update_throttle_accounting(self) -> None:
        """
        Actualiza el tiempo total acumulado de throttling.
        Llamar solo con _lock adquirido.
        """
        throttling = self._current.should_throttle

        if throttling and not self._throttle_active:
            self._throttle_active = True
            self._throttle_start  = time.time()
        elif not throttling and self._throttle_active:
            if self._throttle_start:
                self._total_throttle_time += time.time() - self._throttle_start
            self._throttle_active = False

    @staticmethod
    def _classify_resource(percent: float) -> str:
        """Clasifica un % de uso en nivel de salud legible."""
        if percent < 30:
            return "Excellent"
        elif percent < 60:
            return "Good"
        elif percent < 80:
            return "Medium"
        elif percent < 95:
            return "High"
        else:
            return "Critical"

    @staticmethod
    def _get_recommendations(snap: SystemSnapshot) -> list[str]:
        """Genera recomendaciones legibles para mostrar en la UI."""
        recs = []

        if snap.cpu_percent > 90:
            recs.append(
                "CPU crítico — el scanner está en slots mínimos. "
                "Considerar migrar algún sistema a otro servidor."
            )
        elif snap.cpu_percent < 20:
            recs.append(
                "CPU bajo — el scanner puede aprovechar más recursos."
            )

        if snap.memory_percent > 85:
            recs.append(
                "RAM alta — riesgo de swap. Revisar procesos en el VPS."
            )

        if snap.should_pause:
            recs.append(
                "Sistema en estado crítico — el autoscan está pausado "
                "hasta que la carga disminuya."
            )

        return recs


# ──────────────────────────────────────────────
# INSTANCIA GLOBAL — singleton del proceso
# ──────────────────────────────────────────────

# Una sola instancia compartida por toda la aplicación.
# El scanner y la API la consultan sin crear instancias extra.
#
# Uso en scanner.py:
#   from core.system_monitor import system_monitor
#   slots = system_monitor.recommended_slots
#
# Uso en routes/api.py:
#   from core.system_monitor import system_monitor
#   health = system_monitor.get_health_summary()

system_monitor = SystemMonitor()
