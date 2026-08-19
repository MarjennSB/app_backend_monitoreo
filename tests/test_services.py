"""
Pruebas unitarias para modules/services
Enfocadas principalmente en el motor de estadísticas (analyzer.py).
"""

import pytest
from datetime import datetime
from modules.services.analyzer import Analyzer
from modules.services.tcp_check import PortState, ProbeResult


def test_analyzer_initial_state():
    """Verifica que el analizador inicie limpio."""
    analyzer = Analyzer(ip="127.0.0.1", port=80)
    stats = analyzer.stats
    
    assert stats.total_probes == 0
    assert stats.availability_percent == 0.0
    assert stats.ongoing_successful == 0
    assert stats.ongoing_failed == 0


def test_analyzer_record_success():
    """Verifica que un intento exitoso sume a las estadísticas positivas."""
    analyzer = Analyzer(ip="127.0.0.1", port=80)
    
    # Simula un puerto abierto con 15.5ms de RTT
    result = ProbeResult(
        ip="127.0.0.1", port=80, state=PortState.OPEN, 
        rtt_ms=15.5, probed_at=datetime.now()
    )
    analyzer.process_probe(result)
    stats = analyzer.stats
    
    assert stats.total_probes == 1
    assert stats.successful_probes == 1
    assert stats.failed_probes == 0
    assert stats.availability_percent == 100.0
    assert stats.ongoing_successful == 1
    assert stats.rtt.min_ms == 15.5
    assert stats.rtt.max_ms == 15.5


def test_analyzer_record_failure():
    """Verifica que un intento fallido (cerrado/error) cuente negativo."""
    analyzer = Analyzer(ip="127.0.0.1", port=80)
    
    # Simula un puerto cerrado (sin RTT)
    result = ProbeResult(
        ip="127.0.0.1", port=80, state=PortState.CLOSED, 
        rtt_ms=0.0, probed_at=datetime.now()
    )
    analyzer.process_probe(result)
    stats = analyzer.stats
    
    assert stats.total_probes == 1
    assert stats.successful_probes == 0
    assert stats.failed_probes == 1
    assert stats.availability_percent == 0.0
    assert stats.ongoing_failed == 1


def test_analyzer_mixed_availability():
    """Verifica el cálculo de disponibilidad con éxitos y fallos."""
    analyzer = Analyzer(ip="127.0.0.1", port=80)
    
    # 3 exitosos, 1 fallido = 75% disponibilidad
    analyzer.process_probe(ProbeResult(ip="127.0.0.1", port=80, state=PortState.OPEN, rtt_ms=10.0, probed_at=datetime.now()))
    analyzer.process_probe(ProbeResult(ip="127.0.0.1", port=80, state=PortState.OPEN, rtt_ms=12.0, probed_at=datetime.now()))
    analyzer.process_probe(ProbeResult(ip="127.0.0.1", port=80, state=PortState.CLOSED, rtt_ms=0.0, probed_at=datetime.now()))
    analyzer.process_probe(ProbeResult(ip="127.0.0.1", port=80, state=PortState.OPEN, rtt_ms=11.0, probed_at=datetime.now()))
    
    stats = analyzer.stats
    assert stats.total_probes == 4
    assert stats.successful_probes == 3
    assert stats.failed_probes == 1
    assert stats.availability_percent == 75.0
    
    # RTT check: min 10, max 12, avg 11
    assert stats.rtt.min_ms == 10.0
    assert stats.rtt.max_ms == 12.0
    assert stats.rtt.avg_ms == 11.0


def test_analyzer_downtime_tracking():
    """Verifica que las rachas de caída rompan el uptime."""
    analyzer = Analyzer(ip="127.0.0.1", port=80)
    
    analyzer.process_probe(ProbeResult(ip="127.0.0.1", port=80, state=PortState.OPEN, rtt_ms=10.0, probed_at=datetime.now()))
    analyzer.process_probe(ProbeResult(ip="127.0.0.1", port=80, state=PortState.CLOSED, rtt_ms=0.0, probed_at=datetime.now()))
    analyzer.process_probe(ProbeResult(ip="127.0.0.1", port=80, state=PortState.CLOSED, rtt_ms=0.0, probed_at=datetime.now()))
    
    stats = analyzer.stats
    assert stats.ongoing_successful == 0
    assert stats.ongoing_failed == 2
