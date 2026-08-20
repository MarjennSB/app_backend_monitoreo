import pandas as pd
import logging
import ipaddress
from typing import List, Dict, Any

log = logging.getLogger("sheets_sync")

def _parse_ram(val: str) -> int:
    """Convierte '16 GB' o '16' a 16384 MB."""
    if pd.isna(val):
        return None
    val = str(val).upper().replace("GB", "").strip()
    try:
        return int(float(val) * 1024)
    except ValueError:
        return None

def _parse_disk(val: str) -> float:
    """Convierte '1TB' a 1000.0, '512GB' a 512.0."""
    if pd.isna(val):
        return 0.0
    val = str(val).upper().strip()
    try:
        if "TB" in val:
            num = float(val.replace("TB", "").strip())
            return num * 1000.0
        elif "GB" in val:
            num = float(val.replace("GB", "").strip())
            return num
        else:
            return float(val)
    except ValueError:
        return 0.0

def process_csv_inventory(file_path: str) -> List[Dict[str, Any]]:
    """
    Lee el CSV del inventario y retorna una lista de diccionarios
    listos para ser inyectados en la base de datos.
    """
    try:
        df = pd.read_csv(file_path)
    except Exception as e:
        log.error(f"Error al leer el CSV: {e}")
        return []

    processed_data = []

    for index, row in df.iterrows():
        ip_raw = str(row.get('IP', '')).strip()
        
        # Validación estricta de IP para evitar que PostgreSQL falle (DataError INET)
        try:
            ip = str(ipaddress.ip_address(ip_raw))
        except ValueError:
            # Ignorar IPs inválidas como "VARIABLE", "-", vacíos o textos raros
            continue

        hostname = str(row.get('HOSTNAME', 'unknown')).strip()
        contact = str(row.get('NOMBRES Y APELLIDOS', '')).strip()
        
        ubicacion = str(row.get('UBICACION', '')).strip()
        area = str(row.get('AREA', '')).strip()
        location = f"{ubicacion} - {area}".strip(' -')

        device_type = str(row.get('TIPO DE EQUIPO', 'unknown')).strip()
        cpu_model = str(row.get('PROCESADOR', '')).strip()
        model = str(row.get('PLACA MADRE', 'unknown')).strip()
        os_info = str(row.get('SISTEMA OPERATIVO', '')).strip()
        
        # Convertimos la gráfica
        graph = str(row.get('GRAFICA', '')).strip()

        # Parseo de RAM
        ram_mb = _parse_ram(row.get('MEMORIA'))

        # Parseo de Discos (CAPACIDAD 1 y CAPACIDAD 5)
        cap1 = _parse_disk(row.get('CAPACIDAD 1'))
        cap5 = _parse_disk(row.get('CAPACIDAD 5'))
        disk_gb = cap1 + cap5
        if disk_gb == 0.0:
            disk_gb = None

        processed_data.append({
            "ip": ip,
            "hostname": hostname if hostname and hostname.lower() != "nan" else "unknown",
            "contact": contact if contact.lower() != "nan" else "",
            "location": location if location.lower() != "nan" else "",
            "device_type": device_type if device_type.lower() != "nan" else "unknown",
            "cpu_model": cpu_model if cpu_model.lower() != "nan" else "",
            "ram_mb": ram_mb,
            "model": model if model.lower() != "nan" else "unknown",
            "os_info": os_info if os_info.lower() != "nan" else "",
            "disk_gb": disk_gb,
            "graph": graph if graph.lower() != "nan" else ""
        })

    return processed_data
