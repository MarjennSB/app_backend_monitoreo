"""
inventory/wmi_reader.py
────────────────────────────────────────────────────────────────
Lector de inventario via WMI para dispositivos Windows.

Cubre: PCs, laptops y servidores Windows de la red empresarial.

ESTADO: Completamente comentado — requiere credenciales de
        administrador de cada equipo Windows.

Para activar en el futuro:
  1. Coordinar con el administrador de Windows/AD las credenciales
     (usuario de dominio con permisos WMI en los equipos)
  2. pip install wmi  (solo funciona en servidor Windows)
     O pip install impacket  (funciona en Linux via WMI remoto)
  3. Descomentar los bloques "# ACTIVAR"

Por qué está comentado:
  - WMI requiere usuario + contraseña de CADA equipo o del dominio
  - En una red empresarial con 300+ equipos, esto requiere
    autorización explícita del departamento de TI/seguridad
  - Sin credenciales, WMI silenciosamente no responde

Alternativa sin credenciales:
  SNMP (snmp_reader.py) puede obtener información básica de PCs
  Windows si el servicio SNMP está habilitado en ellos.

Información que obtendría cuando esté activo:
  - Nombre del equipo, fabricante, modelo
  - Sistema operativo (versión exacta)
  - CPU: modelo, cores, velocidad
  - RAM total en GB
  - Discos: tamaño y uso
  - Interfaces de red y MAC addresses
────────────────────────────────────────────────────────────────
"""

from typing import Optional
from modules.inventory.normalizer import DeviceInfo, build_empty_device


# ──────────────────────────────────────────────
# CONFIGURACIÓN WMI
# ──────────────────────────────────────────────
#
# ┌─────────────────────────────────────────────────────────────┐
# │  PENDIENTE: Coordinación con administrador de Windows/AD    │
# │                                                             │
# │  Necesario:                                                 │
# │   - Usuario de dominio con permisos WMI (solo lectura)      │
# │   - Autorización del equipo de seguridad                    │
# │   - Lista de equipos a incluir (no todos necesariamente)    │
# └─────────────────────────────────────────────────────────────┘
#
# WMI_USERNAME: str  = ""    # ← usuario de dominio con permisos WMI
# WMI_PASSWORD: str  = ""    # ← contraseña (usar variable de entorno)
# WMI_DOMAIN: str    = ""    # ← nombre del dominio (ej: "EMPRESA")
# WMI_TIMEOUT: float = 10.0  # WMI puede tardar más que SNMP


# ──────────────────────────────────────────────
# FUNCIÓN PRINCIPAL
# ──────────────────────────────────────────────

async def read_device(ip: str, hostname: str = "unknown") -> Optional[DeviceInfo]:
    """
    Lee el inventario de un equipo Windows via WMI remoto.

    Args:
        ip:       Dirección IP del equipo Windows.
        hostname: Hostname conocido (del DNS o NetBIOS).

    Returns:
        DeviceInfo con la información del equipo.
        None si WMI no está disponible o las credenciales fallan.
    """
    # ── ACTIVAR cuando credenciales WMI estén disponibles ────────
    # raw = await _fetch_wmi_data(ip)
    # if raw is None:
    #     return None
    # return normalize_from_wmi(ip, raw)
    # ─────────────────────────────────────────────────────────────

    return None


async def is_wmi_available(ip: str) -> bool:
    """
    Verifica si un equipo acepta conexiones WMI.

    Args:
        ip: Dirección IP a verificar.

    Returns:
        True si el equipo responde a WMI con las credenciales.
    """
    # ── ACTIVAR cuando credenciales estén disponibles ────────────
    # return await _test_wmi_connection(ip)
    # ─────────────────────────────────────────────────────────────

    return False


# ──────────────────────────────────────────────
# IMPLEMENTACIÓN WMI — Completamente comentada
# ──────────────────────────────────────────────
#
# Para activar: pip install impacket  (para WMI remoto desde Linux)
#               O pip install wmi     (para WMI local en Windows)

# # ACTIVAR — inicio del bloque WMI
#
# import asyncio
# from modules.inventory.normalizer import normalize_from_wmi
#
# # Queries WMI a ejecutar
# _WMI_QUERIES = {
#     "computer":  "SELECT Name, Manufacturer, Model, TotalPhysicalMemory FROM Win32_ComputerSystem",
#     "os":        "SELECT Caption, Version, OSArchitecture, LastBootUpTime FROM Win32_OperatingSystem",
#     "cpu":       "SELECT Name, NumberOfCores, MaxClockSpeed FROM Win32_Processor",
#     "disk":      "SELECT Size, MediaType FROM Win32_DiskDrive",
#     "network":   "SELECT Description, MACAddress, NetConnectionID FROM Win32_NetworkAdapter WHERE PhysicalAdapter=True",
# }
#
#
# async def _fetch_wmi_data(ip: str) -> Optional[dict]:
#     """
#     Ejecuta queries WMI en el equipo Windows y retorna datos crudos.
#
#     Usa impacket para WMI remoto desde un servidor Linux.
#     Si el servidor es Windows, puede usar el módulo wmi nativo.
#
#     Args:
#         ip: Dirección IP del equipo Windows.
#
#     Returns:
#         Diccionario con la información del equipo, o None si falla.
#     """
#     # Implementación con impacket (WMI remoto desde Linux):
#     # from impacket.dcerpc.v5.dcom import wmi
#     # from impacket.dcerpc.v5.dcomrt import DCOMConnection
#     # ...
#     #
#     # O implementación con wmi nativo (solo en servidor Windows):
#     # import wmi
#     # conn = wmi.WMI(computer=ip, user=WMI_USERNAME, password=WMI_PASSWORD)
#     # ...
#
#     # Ejemplo de estructura que debe retornar:
#     # return {
#     #     "hostname":       "PC-MARCOS",
#     #     "manufacturer":   "Dell Inc.",
#     #     "model":          "OptiPlex 7090",
#     #     "ram_mb":         16384,
#     #     "os_caption":     "Microsoft Windows 11 Pro",
#     #     "os_version":     "10.0.22621",
#     #     "cpu_model":      "Intel(R) Core(TM) i5-10500 CPU @ 3.10GHz",
#     #     "disk_gb":        512.0,
#     #     "interfaces":     ["Intel(R) Ethernet Connection I219-LM"],
#     #     "uptime_seconds": 172800,
#     #     "uptime_str":     "2 días",
#     # }
#     pass
#
#
# async def _test_wmi_connection(ip: str) -> bool:
#     """Verifica si el equipo acepta conexiones WMI."""
#     try:
#         # Intentar query mínimo
#         data = await _fetch_wmi_data(ip)
#         return data is not None
#     except Exception:
#         return False
#
# # ACTIVAR — fin del bloque WMI