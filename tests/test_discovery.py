"""
Pruebas unitarias para modules/discovery
"""

import pytest
from modules.discovery.scanner import ScanMode

def test_scan_mode_values():
    """Verifica que los modos de escaneo estén correctamente definidos."""
    assert ScanMode.ACTIVE.value == "active"
    assert ScanMode.BACKGROUND.value == "background"
