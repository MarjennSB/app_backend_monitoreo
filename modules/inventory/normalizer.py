"""
inventory/normalizer.py
────────────────────────────────────────────────────────────────
Estandarización de datos de hardware de múltiples fuentes.

Responsabilidades:
  - Definir el modelo unificado DeviceInfo
  - Normalizar la salida de SNMP, WMI y SSH al mismo formato
  - Detectar el tipo de dispositivo por su descripción
  - Ser la única interfaz que consume el resto del sistema

Contrato:
  Entrada:  Datos crudos de snmp_reader / wmi_reader / linux_reader
  Salida:   DeviceInfo  (va hacia storage/ y routes/api.py)

Este módulo NO hace ninguna conexión de red.
────────────────────────────────────────────────────────────────
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ──────────────────────────────────────────────
# MODELO UNIFICADO — DeviceInfo
# ──────────────────────────────────────────────

@dataclass
class DeviceInfo:
    """
    Información de inventario estandarizada de un dispositivo.

    Este es el único modelo que consume el resto del sistema.
    Sin importar si los datos vienen de SNMP, WMI o SSH,
    siempre llegan en este formato.

    Attributes:
        ip:           Dirección IP del dispositivo.
        hostname:     Nombre del dispositivo (del DNS o SNMP sysName).
        device_type:  Tipo clasificado del dispositivo.
        manufacturer: Fabricante detectado (Cisco, HP, Microsoft...).
        model:        Modelo específico del hardware.
        description:  Descripción técnica completa del dispositivo.
        location:     Ubicación física (SNMP sysLocation).
        contact:      Responsable del dispositivo (SNMP sysContact).
        os_info:      Sistema operativo / firmware / versión de IOS.
        interfaces:   Lista de interfaces de red del dispositivo.
        uptime_str:   Tiempo encendido en formato legible.
        uptime_seconds: Tiempo encendido en segundos (para cálculos).
        ram_mb:       RAM total en MB (si el dispositivo lo reporta).
        cpu_model:    Modelo de CPU (PCs y servidores).
        disk_gb:      Disco total en GB (PCs y servidores).
        read_method:  Método que obtuvo los datos ("snmp"/"wmi"/"ssh"/"none").
        last_updated: Timestamp de la última actualización del inventario.
    """
    ip: str
    hostname: str                       = "unknown"
    device_type: str                    = "unknown"
    manufacturer: str                   = "unknown"
    model: str                          = "unknown"
    description: str                    = ""
    location: str                       = ""
    contact: str                        = ""
    os_info: str                        = ""
    interfaces: list[str]               = field(default_factory=list)
    uptime_str: str                     = ""
    uptime_seconds: Optional[int]       = None
    ram_mb: Optional[int]               = None
    cpu_model: str                      = ""
    disk_gb: Optional[float]            = None
    read_method: str                    = "none"
    last_updated: datetime              = field(default_factory=datetime.now)

    @property
    def is_enriched(self) -> bool:
        """True si se obtuvo información más allá del hostname."""
        return self.read_method != "none" and self.device_type != "unknown"

    @property
    def uptime_days(self) -> Optional[int]:
        """Uptime en días (para mostrar en UI)."""
        if self.uptime_seconds is not None:
            return self.uptime_seconds // 86400
        return None


# ──────────────────────────────────────────────
# DETECCIÓN DE TIPO DE DISPOSITIVO
# ──────────────────────────────────────────────

# Palabras clave por tipo de dispositivo
# Orden importa: más específico primero
_DEVICE_TYPE_KEYWORDS: dict[str, list[str]] = {
    "switch": [
        "switch", "catalyst", "procurve", "powerconnect", "nexus",
        "unifi switch", "aruba", "extremexos", "comware",
    ],
    "router": [
        "router", "gateway", "cisco ios", "junos", "mikrotik routeros",
        "edgerouter", "pfsense", "opnsense",
    ],
    "access_point": [
        "access point", "wireless", "wifi", "wap", "unifi ap",
        "aironet", "aruba ap",
    ],
    "firewall": [
        "firewall", "fortigate", "palo alto", "asa", "checkpoint",
        "sophos", "fortios",
    ],
    "printer": [
        "printer", "print server", "laserjet", "officejet", "xerox",
        "ricoh", "canon", "brother", "kyocera", "epson",
    ],
    "nas": [
        "nas", "storage", "synology", "qnap", "freenas", "truenas",
        "netapp",
    ],
    "ups": [
        "ups", "uninterruptible", "apc", "eaton", "powerware",
    ],
    "camera": [
        "camera", "cam", "nvr", "dvr", "hikvision", "dahua", "axis",
    ],
    "server": [
        "server", "windows server", "ubuntu server", "centos", "debian",
        "red hat", "vmware esxi", "proxmox", "hyper-v",
    ],
    "workstation": [
        "windows 10", "windows 11", "macos", "ubuntu desktop",
        "workstation", "desktop",
    ],
    "voip": [
        "voip", "pbx", "asterisk", "freepbx", "cisco unified",
        "polycom", "yealink",
    ],
}

# Fabricantes comunes por palabras clave en la descripción
_MANUFACTURER_KEYWORDS: dict[str, list[str]] = {
    "Cisco":        ["cisco", "ios", "catalyst", "aironet", "nexus"],
    "HP":           ["hp", "hewlett", "procurve", "proliant", "laserjet"],
    "Juniper":      ["juniper", "junos"],
    "MikroTik":     ["mikrotik", "routeros"],
    "Ubiquiti":     ["ubiquiti", "unifi", "edgerouter", "airmax"],
    "Aruba":        ["aruba"],
    "Fortinet":     ["fortinet", "fortigate", "fortios"],
    "Palo Alto":    ["palo alto", "pan-os"],
    "Dell":         ["dell", "powerconnect", "poweredge"],
    "Synology":     ["synology"],
    "QNAP":         ["qnap"],
    "APC":          ["apc", "schneider electric"],
    "Xerox":        ["xerox"],
    "Ricoh":        ["ricoh"],
    "Brother":      ["brother"],
    "Microsoft":    ["windows", "microsoft"],
    "Apple":        ["macos", "apple"],
    "VMware":       ["vmware", "esxi"],
    "Hikvision":    ["hikvision", "hik"],
    "Dahua":        ["dahua"],
}


def detect_device_type(description: str, sys_descr: str = "") -> str:
    """
    Detecta el tipo de dispositivo por su descripción.

    Usa las palabras clave de _DEVICE_TYPE_KEYWORDS en orden
    de especificidad. Combina description y sys_descr para
    mayor precisión.

    Args:
        description: Descripción del dispositivo (sysDescr o similar).
        sys_descr:   Descripción adicional SNMP si está disponible.

    Returns:
        Tipo de dispositivo o "unknown" si no se detecta.
    """
    combined = (description + " " + sys_descr).lower()

    for device_type, keywords in _DEVICE_TYPE_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            return device_type

    return "unknown"


def detect_manufacturer(description: str) -> str:
    """
    Detecta el fabricante por palabras clave en la descripción.

    Args:
        description: Descripción del dispositivo.

    Returns:
        Nombre del fabricante o "unknown".
    """
    lower = description.lower()

    for manufacturer, keywords in _MANUFACTURER_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return manufacturer

    return "unknown"


def uptime_ticks_to_seconds(timeticks: int) -> int:
    """
    Convierte TimeTicks SNMP a segundos.

    SNMP reporta uptime en centésimas de segundo (TimeTicks).
    1 segundo = 100 ticks.

    Args:
        timeticks: Valor TimeTicks de SNMP (centésimas de segundo).

    Returns:
        Segundos de uptime.
    """
    return timeticks // 100


def format_uptime(seconds: int) -> str:
    """
    Convierte segundos a string legible de uptime.

    Args:
        seconds: Uptime en segundos.

    Returns:
        String como "47 días, 3 horas, 22 minutos".
    """
    days    = seconds // 86400
    hours   = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60

    parts = []
    if days:
        parts.append(f"{days} {'día' if days == 1 else 'días'}")
    if hours:
        parts.append(f"{hours} {'hora' if hours == 1 else 'horas'}")
    if minutes and not days:  # Mostrar minutos solo si no hay días
        parts.append(f"{minutes} min")

    return ", ".join(parts) if parts else "< 1 minuto"


# ──────────────────────────────────────────────
# FUNCIONES DE NORMALIZACIÓN
# ──────────────────────────────────────────────

def normalize_from_snmp(ip: str, raw: dict) -> DeviceInfo:
    """
    Normaliza datos crudos de SNMP a DeviceInfo.

    Args:
        ip:  Dirección IP del dispositivo.
        raw: Diccionario con los OIDs obtenidos de snmp_reader.

    Returns:
        DeviceInfo estandarizado.
    """
    sys_name   = raw.get("sysName", "unknown")
    sys_descr  = raw.get("sysDescr", "")
    sys_loc    = raw.get("sysLocation", "")
    sys_contact = raw.get("sysContact", "")
    uptime_ticks = raw.get("sysUpTime", 0)
    interfaces = raw.get("interfaces", [])
    ram_mb     = raw.get("ram_mb")

    uptime_s   = uptime_ticks_to_seconds(uptime_ticks) if uptime_ticks else None
    uptime_str = format_uptime(uptime_s) if uptime_s else ""

    device_type  = detect_device_type(sys_descr)
    manufacturer = detect_manufacturer(sys_descr)

    return DeviceInfo(
        ip=ip,
        hostname=sys_name,
        device_type=device_type,
        manufacturer=manufacturer,
        model=raw.get("model", "unknown"),
        description=sys_descr,
        location=sys_loc,
        contact=sys_contact,
        os_info=sys_descr,
        interfaces=interfaces,
        uptime_str=uptime_str,
        uptime_seconds=uptime_s,
        ram_mb=ram_mb,
        read_method="snmp",
        last_updated=datetime.now(),
    )


def normalize_from_wmi(ip: str, raw: dict) -> DeviceInfo:
    """
    Normaliza datos crudos de WMI (Windows) a DeviceInfo.

    Args:
        ip:  Dirección IP del dispositivo.
        raw: Diccionario con los campos WMI obtenidos de wmi_reader.

    Returns:
        DeviceInfo estandarizado.
    """
    os_caption  = raw.get("os_caption", "")
    os_version  = raw.get("os_version", "")
    os_info     = f"{os_caption} {os_version}".strip()

    return DeviceInfo(
        ip=ip,
        hostname=raw.get("hostname", "unknown"),
        device_type=detect_device_type(os_info),
        manufacturer=raw.get("manufacturer", detect_manufacturer(os_info)),
        model=raw.get("model", "unknown"),
        description=os_info,
        os_info=os_info,
        interfaces=raw.get("interfaces", []),
        uptime_str=raw.get("uptime_str", ""),
        uptime_seconds=raw.get("uptime_seconds"),
        ram_mb=raw.get("ram_mb"),
        cpu_model=raw.get("cpu_model", ""),
        disk_gb=raw.get("disk_gb"),
        read_method="wmi",
        last_updated=datetime.now(),
    )


def normalize_from_ssh(ip: str, raw: dict) -> DeviceInfo:
    """
    Normaliza datos crudos de SSH + lshw (Linux) a DeviceInfo.

    Args:
        ip:  Dirección IP del dispositivo.
        raw: Diccionario con los campos lshw obtenidos de linux_reader.

    Returns:
        DeviceInfo estandarizado.
    """
    os_info = raw.get("os_info", "")

    return DeviceInfo(
        ip=ip,
        hostname=raw.get("hostname", "unknown"),
        device_type=detect_device_type(os_info),
        manufacturer=raw.get("manufacturer", detect_manufacturer(os_info)),
        model=raw.get("model", "unknown"),
        description=os_info,
        os_info=os_info,
        interfaces=raw.get("interfaces", []),
        uptime_str=raw.get("uptime_str", ""),
        uptime_seconds=raw.get("uptime_seconds"),
        ram_mb=raw.get("ram_mb"),
        cpu_model=raw.get("cpu_model", ""),
        disk_gb=raw.get("disk_gb"),
        read_method="ssh",
        last_updated=datetime.now(),
    )


def build_empty_device(ip: str, hostname: str = "unknown") -> DeviceInfo:
    """
    Crea un DeviceInfo mínimo cuando ningún lector funciona.

    Útil como fallback: el dispositivo fue descubierto por discovery
    pero inventory no pudo obtener más información.

    Args:
        ip:       Dirección IP del dispositivo.
        hostname: Hostname si ya fue resuelto por ping_icmp.

    Returns:
        DeviceInfo vacío con lo mínimo disponible.
    """
    return DeviceInfo(
        ip=ip,
        hostname=hostname,
        read_method="none",
        last_updated=datetime.now(),
    )