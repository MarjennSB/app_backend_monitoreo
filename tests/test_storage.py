"""
Pruebas unitarias para modules/storage
Enfocadas en mapeo de datos e hidratación de modelos (from_record).
"""

import pytest
from datetime import datetime
from modules.storage.models import NetworkModel, DeviceModel


def test_network_model_from_record():
    """Verifica que el modelo se construya correctamente desde un dict/record."""
    now = datetime.now()
    row = {
        "id": 1,
        "cidr": "192.168.10.0/24",
        "vlan_id": 10,
        "scan_interval": 300,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    
    model = NetworkModel.from_record(row)
    assert model.id == 1
    assert model.cidr == "192.168.10.0/24"
    assert model.vlan_id == 10
    assert model.is_active is True


def test_device_model_from_record():
    """Verifica que los valores nulos se manejen correctamente."""
    now = datetime.now()
    row = {
        "id": 100,
        "network_id": 1,
        "ip": "10.0.0.5",
        "hostname": "unknown",
        "hostname_method": "unknown",
        "mac_address": None,        # Puede ser nulo
        "is_alive": False,
        "last_seen_at": None,       # Puede ser nulo
        "first_seen_at": now,
        "created_at": now,
        "updated_at": now,
    }
    
    model = DeviceModel.from_record(row)
    assert model.id == 100
    assert model.ip == "10.0.0.5"
    assert model.mac_address is None
    assert model.is_alive is False
    assert model.last_seen_at is None
