"""
inventory/snmp_reader.py
────────────────────────────────────────────────────────────────
Lector de inventario via SNMP v2c para equipos de red.

Cubre: switches, routers, access points, impresoras, NAS, UPS.

ESTADO: Estructura completa — llamadas SNMP comentadas.

Para activar:
  1. Confirmar community string con el equipo de red
  2. Reemplazar SNMP_COMMUNITY con el valor real
  3. Ejecutar: pip install pysnmp
  4. Descomentar los bloques marcados con "# ACTIVAR"

Mientras esté comentado, read_device() retorna None y el sistema
usa build_empty_device() del normalizer como fallback.

OIDs estándar utilizados (RFC 1213 / MIB-II):
  sysDescr    → 1.3.6.1.2.1.1.1.0  Descripción del sistema
  sysName     → 1.3.6.1.2.1.1.5.0  Nombre del dispositivo
  sysLocation → 1.3.6.1.2.1.1.6.0  Ubicación física
  sysContact  → 1.3.6.1.2.1.1.4.0  Contacto responsable
  sysUpTime   → 1.3.6.1.2.1.1.3.0  Uptime en TimeTicks
  ifDescr     → 1.3.6.1.2.1.2.2.1.2  Nombres de interfaces
  hrMemorySize → 1.3.6.1.2.1.25.2.2.0  RAM total (HOST-MIB)
────────────────────────────────────────────────────────────────
"""

import asyncio
import logging
from typing import Optional

from modules.inventory.normalizer import DeviceInfo, normalize_from_snmp, build_empty_device


# ──────────────────────────────────────────────
# CONFIGURACIÓN SNMP
# ──────────────────────────────────────────────

# ┌─────────────────────────────────────────────────────────────┐
# │  PENDIENTE: Confirmar con el equipo de red                  │
# │                                                             │
# │  Preguntar: "¿SNMP habilitado? ¿Community string de         │
# │              lectura?" (típicamente "public")               │
# │                                                             │
# │  Cuando lo confirmen:                                       │
# │    1. Cambiar SNMP_COMMUNITY al valor real                  │
# │    2. Descomentar los bloques "# ACTIVAR" más abajo         │
# │    3. pip install pysnmp                                    │
# └─────────────────────────────────────────────────────────────┘
SNMP_COMMUNITY: str   = "public"   # ← reemplazar con el valor real
SNMP_PORT: int        = 161
SNMP_TIMEOUT: float   = 2.0
SNMP_RETRIES: int     = 1
SNMP_VERSION: int     = 1          # 0=v1, 1=v2c


# OIDs estándar a consultar
_OID_SYS_DESCR    = "1.3.6.1.2.1.1.1.0"
_OID_SYS_NAME     = "1.3.6.1.2.1.1.5.0"
_OID_SYS_LOCATION = "1.3.6.1.2.1.1.6.0"
_OID_SYS_CONTACT  = "1.3.6.1.2.1.1.4.0"
_OID_SYS_UPTIME   = "1.3.6.1.2.1.1.3.0"
_OID_IF_DESCR     = "1.3.6.1.2.1.2.2.1.2"   # tabla (necesita walk)
_OID_HR_MEM_SIZE  = "1.3.6.1.2.1.25.2.2.0"  # HOST-MIB RAM


# ──────────────────────────────────────────────
# FUNCIÓN PRINCIPAL
# ──────────────────────────────────────────────

async def read_device(ip: str) -> Optional[DeviceInfo]:
    """
    Lee el inventario de un dispositivo via SNMP.

    Consulta los OIDs estándar del dispositivo y normaliza
    los resultados a DeviceInfo usando normalizer.py.

    Args:
        ip: Dirección IP del dispositivo a consultar.

    Returns:
        DeviceInfo si SNMP responde correctamente.
        None si el dispositivo no tiene SNMP o el community es incorrecto.
    """
    # ── ACTIVAR cuando community string esté confirmado ──────────
    # raw = await _fetch_snmp_data(ip)
    # if raw is None:
    #     return None
    # return normalize_from_snmp(ip, raw)
    # ─────────────────────────────────────────────────────────────

    # Mientras esté comentado → retorna None (fallback en inventory_service)
    return None


async def is_snmp_available(ip: str) -> bool:
    """
    Verifica rápidamente si un dispositivo responde a SNMP.

    Útil para filtrar hosts antes de intentar un read completo.
    Solo consulta sysName (OID más ligero).

    Args:
        ip: Dirección IP a verificar.

    Returns:
        True si el dispositivo responde con SNMP.
    """
    # ── ACTIVAR cuando community string esté confirmado ──────────
    # result = await _get_single_oid(ip, _OID_SYS_NAME)
    # return result is not None
    # ─────────────────────────────────────────────────────────────

    return False


# ──────────────────────────────────────────────
# IMPLEMENTACIÓN SNMP — Comentada hasta activar
# ──────────────────────────────────────────────
#
# Para activar:
#   pip install pysnmp
#   Descomentar todo el bloque siguiente

# # ACTIVAR — inicio del bloque SNMP
#
# from pysnmp.hlapi.asyncio import (
#     getCmd, nextCmd, bulkCmd,
#     SnmpEngine, CommunityData, UdpTransportTarget,
#     ContextData, ObjectType, ObjectIdentity,
# )
#
#
# async def _get_single_oid(ip: str, oid: str) -> Optional[str]:
#     """
#     Consulta un único OID SNMP y retorna su valor como string.
#
#     Args:
#         ip:  Dirección IP del dispositivo.
#         oid: OID SNMP a consultar.
#
#     Returns:
#         Valor del OID como string, o None si falla.
#     """
#     try:
#         iterator = getCmd(
#             SnmpEngine(),
#             CommunityData(SNMP_COMMUNITY, mpModel=SNMP_VERSION),
#             UdpTransportTarget(
#                 (ip, SNMP_PORT),
#                 timeout=SNMP_TIMEOUT,
#                 retries=SNMP_RETRIES,
#             ),
#             ContextData(),
#             ObjectType(ObjectIdentity(oid)),
#         )
#         error_indication, error_status, _, var_binds = await iterator
#
#         if error_indication or error_status:
#             return None
#
#         for var_bind in var_binds:
#             return str(var_bind[1]).strip()
#
#     except Exception as exc:
#         logging.debug("SNMP get OID %s en %s: %s", oid, ip, exc)
#         return None
#
#
# async def _walk_table_oid(ip: str, oid: str) -> list[str]:
#     """
#     Hace un walk sobre una tabla SNMP y retorna todos los valores.
#
#     Usado para obtener la lista de interfaces (ifDescr).
#
#     Args:
#         ip:  Dirección IP del dispositivo.
#         oid: OID raíz de la tabla a recorrer.
#
#     Returns:
#         Lista de strings con los valores de la tabla.
#     """
#     values = []
#     try:
#         async for (error_indication, error_status, _, var_binds) in nextCmd(
#             SnmpEngine(),
#             CommunityData(SNMP_COMMUNITY, mpModel=SNMP_VERSION),
#             UdpTransportTarget(
#                 (ip, SNMP_PORT),
#                 timeout=SNMP_TIMEOUT,
#                 retries=SNMP_RETRIES,
#             ),
#             ContextData(),
#             ObjectType(ObjectIdentity(oid)),
#             lexicographicMode=False,  # Solo retorna filas de esta tabla
#         ):
#             if error_indication or error_status:
#                 break
#             for var_bind in var_binds:
#                 values.append(str(var_bind[1]).strip())
#
#     except Exception as exc:
#         logging.debug("SNMP walk OID %s en %s: %s", oid, ip, exc)
#
#     return values
#
#
# async def _fetch_snmp_data(ip: str) -> Optional[dict]:
#     """
#     Consulta todos los OIDs relevantes del dispositivo en paralelo.
#
#     Retorna un diccionario crudo que normalizer.normalize_from_snmp()
#     convierte a DeviceInfo.
#
#     Args:
#         ip: Dirección IP del dispositivo.
#
#     Returns:
#         Diccionario con los valores SNMP, o None si no responde.
#     """
#     # Consultar OIDs básicos en paralelo
#     sys_name, sys_descr, sys_location, sys_contact, sys_uptime, ram_raw = (
#         await asyncio.gather(
#             _get_single_oid(ip, _OID_SYS_NAME),
#             _get_single_oid(ip, _OID_SYS_DESCR),
#             _get_single_oid(ip, _OID_SYS_LOCATION),
#             _get_single_oid(ip, _OID_SYS_CONTACT),
#             _get_single_oid(ip, _OID_SYS_UPTIME),
#             _get_single_oid(ip, _OID_HR_MEM_SIZE),
#         )
#     )
#
#     # Si sysName no responde → SNMP no disponible
#     if sys_name is None and sys_descr is None:
#         return None
#
#     # Obtener lista de interfaces (walk)
#     interfaces = await _walk_table_oid(ip, _OID_IF_DESCR)
#
#     # Convertir RAM de KB a MB (HOST-MIB reporta en KB)
#     ram_mb = None
#     if ram_raw:
#         try:
#             ram_mb = int(ram_raw) // 1024
#         except ValueError:
#             pass
#
#     # Convertir TimeTicks a int para normalizer
#     uptime_ticks = 0
#     if sys_uptime:
#         try:
#             # pysnmp retorna TimeTicks como objeto; str() da "X ticks"
#             # Extraer solo el número
#             uptime_ticks = int(str(sys_uptime).split()[0])
#         except (ValueError, IndexError):
#             pass
#
#     return {
#         "sysName":     sys_name or "unknown",
#         "sysDescr":    sys_descr or "",
#         "sysLocation": sys_location or "",
#         "sysContact":  sys_contact or "",
#         "sysUpTime":   uptime_ticks,
#         "interfaces":  interfaces,
#         "ram_mb":      ram_mb,
#     }
#
# # ACTIVAR — fin del bloque SNMP