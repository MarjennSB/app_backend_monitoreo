"""
Pruebas unitarias para modules/inventory
Enfocadas en la normalización y detección automática de dispositivos.
"""

import pytest
from modules.inventory.normalizer import (
    detect_device_type,
    detect_manufacturer,
    format_uptime,
    DeviceInfo
)


def test_detect_device_type():
    """Verifica que las descripciones se mapeen al tipo correcto."""
    assert detect_device_type("Cisco IOS Software, Catalyst 4500 L3 Switch") == "switch"
    assert detect_device_type("MikroTik RouterOS 6.48") == "router"
    assert detect_device_type("HP LaserJet Pro MFP M428fdw") == "printer"
    assert detect_device_type("Synology DiskStation DS920+") == "nas"
    assert detect_device_type("Windows 10 Pro") == "workstation"
    assert detect_device_type("Ubuntu Server 22.04 LTS") == "server"
    assert detect_device_type("Un dispositivo genérico") == "unknown"


def test_detect_manufacturer():
    """Verifica que las descripciones se mapeen al fabricante correcto."""
    assert detect_manufacturer("Cisco IOS Software") == "Cisco"
    assert detect_manufacturer("HP ProCurve Switch") == "HP"
    assert detect_manufacturer("Ubiquiti UniFi Security Gateway") == "Ubiquiti"
    assert detect_manufacturer("Microsoft Windows Server 2019") == "Microsoft"
    assert detect_manufacturer("Algo desconocido") == "unknown"


def test_format_uptime():
    """Verifica la conversión de segundos a string legible."""
    assert format_uptime(45) == "< 1 minuto"
    assert format_uptime(120) == "2 min"
    assert format_uptime(3600) == "1 hora"
    assert format_uptime(90000) == "1 día, 1 hora"
    assert format_uptime(172800) == "2 días"


def test_device_info_properties():
    """Verifica las propiedades computadas del modelo unificado."""
    device = DeviceInfo(
        ip="192.168.1.50",
        uptime_seconds=172800,  # 2 días
        read_method="snmp",
        device_type="switch"
    )
    
    assert device.is_enriched is True
    assert device.uptime_days == 2
    
    empty_device = DeviceInfo(ip="10.0.0.1")
    assert empty_device.is_enriched is False
