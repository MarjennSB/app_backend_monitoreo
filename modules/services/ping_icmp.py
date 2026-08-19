"""
services/ping_icmp.py
────────────────────────────────────────────────────────────────
Verificación de disponibilidad de hosts vía ICMP y resolución
de hostname mediante cascada de métodos.

Responsabilidades:
  1. Verificar si un host está activo (ICMP ping)
  2. Resolver su hostname usando 3 métodos en cascada:
       PTR (DNS reversa) → NetBIOS (UDP 137) → SNMP* → "unknown"
  3. Detectar cambios de hostname entre ciclos de escaneo

* SNMP está preparado pero comentado — activar cuando el equipo
  de red provea el community string.

Inspirado en la lógica de dns.go y stats.HostnameChange de tcping
(github.com/pouriyajamshidi/tcping).
────────────────────────────────────────────────────────────────
"""

import asyncio
import platform
import socket
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ──────────────────────────────────────────────
# CONSTANTES DE RESOLUCIÓN
# ──────────────────────────────────────────────

# Timeout en segundos para cada método de resolución de hostname
DNS_PTR_TIMEOUT: float    = 1.0
NETBIOS_TIMEOUT: float    = 1.0

# Puerto NetBIOS Name Service (estándar, no modificar)
NETBIOS_PORT: int = 137

# ── SNMP — descomentar cuando el equipo de red provea el community string ──
# SNMP_PORT: int              = 161
# SNMP_COMMUNITY: str         = "public"   # ← reemplazar con el valor real
# SNMP_TIMEOUT: float         = 1.5
# SNMP_OID_SYSNAME: str       = "1.3.6.1.2.1.1.5.0"  # OID estándar sysName
# ──────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────
# MODELOS DE DATOS
# ──────────────────────────────────────────────

@dataclass
class HostnameChange:
    """
    Registra un cambio de hostname detectado en una IP.

    Inspirado en stats.HostnameChange de tcping:
    útil para detectar cuando una IP fue reasignada a otro equipo
    o cuando un dispositivo fue renombrado.

    Attributes:
        ip:           Dirección IP del host.
        old_hostname: Nombre anterior del dispositivo.
        new_hostname: Nombre nuevo detectado.
        detected_at:  Timestamp de cuando se detectó el cambio.
    """
    ip: str
    old_hostname: str
    new_hostname: str
    detected_at: datetime


@dataclass
class PingResult:
    """
    Resultado completo de la verificación de un host.

    Combina la disponibilidad ICMP con la resolución de hostname
    para dar una visión completa del estado del dispositivo.

    Attributes:
        ip:               Dirección IP verificada.
        is_alive:         True si respondió al ping ICMP.
        rtt_ms:           Round-Trip Time en milisegundos (precisión decimal).
        hostname:         Nombre del dispositivo o "unknown".
        hostname_method:  Método que resolvió el nombre ("dns-ptr" | "netbios" | "snmp" | "unknown").
        hostname_changed: True si el hostname cambió respecto al ciclo anterior.
        hostname_change:  Detalle del cambio si hostname_changed es True.
        checked_at:       Timestamp de la verificación.
    """
    ip: str
    is_alive: bool
    rtt_ms: float
    hostname: str
    hostname_method: str
    hostname_changed: bool
    checked_at: datetime
    hostname_change: Optional[HostnameChange] = None


# ──────────────────────────────────────────────
# UTILIDADES
# ──────────────────────────────────────────────

def _is_linux() -> bool:
    """Detecta el sistema operativo para el comando ping."""
    return platform.system().lower() == "linux"


def _nano_to_ms(nanoseconds: int) -> float:
    """
    Convierte nanosegundos a milisegundos con precisión decimal.

    Equivalente a utils.NanoToMillisecond de tcping: preserva
    los decimales que time.sleep() o int() truncarían.

    Args:
        nanoseconds: Tiempo en nanosegundos.

    Returns:
        Tiempo en milisegundos con punto decimal.
    """
    return nanoseconds / 1_000_000.0


# ──────────────────────────────────────────────
# MÉTODO 1 — DNS Reversa (PTR)
# ──────────────────────────────────────────────

async def _resolve_ptr(ip: str) -> Optional[str]:
    """
    Resuelve el hostname via DNS reversa (registro PTR).

    Funciona en redes con Active Directory o DNS interno configurado.
    Ejemplo: 192.168.1.50 → "PC-MARCOS.empresa.local"

    Args:
        ip: Dirección IP a resolver.

    Returns:
        Hostname si se resuelve, None si falla o no existe.
    """
    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, socket.gethostbyaddr, ip),
            timeout=DNS_PTR_TIMEOUT,
        )
        # gethostbyaddr devuelve (hostname, aliases, addresses)
        hostname = result[0]
        return hostname if hostname and hostname != ip else None
    except (socket.herror, socket.gaierror, asyncio.TimeoutError, OSError):
        return None


# ──────────────────────────────────────────────
# MÉTODO 2 — NetBIOS (UDP 137)
# ──────────────────────────────────────────────

def _build_netbios_query() -> bytes:
    """
    Construye el paquete de consulta NetBIOS Name Service.

    El paquete sigue el formato RFC 1002 para consultas NBNS.
    Pregunta por el nombre del nodo (*SMBSERVER o el primero disponible).

    Returns:
        Bytes del paquete de consulta NetBIOS.
    """
    transaction_id = b"\xab\xcd"
    flags          = b"\x00\x00"
    questions      = b"\x00\x01"
    answer_rrs     = b"\x00\x00"
    authority_rrs  = b"\x00\x00"
    additional_rrs = b"\x00\x00"

    # Nombre codificado: "*" + padding a 16 bytes + tipo NBSTAT
    encoded_name = b"\x20CKAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\x00"
    qtype        = b"\x00\x21"  # NBSTAT
    qclass       = b"\x00\x01"  # IN

    return (
        transaction_id + flags + questions + answer_rrs +
        authority_rrs + additional_rrs + encoded_name + qtype + qclass
    )


def _parse_netbios_response(data: bytes) -> Optional[str]:
    """
    Extrae el nombre del dispositivo de la respuesta NetBIOS.

    Busca el primer nombre de tipo 0x00 (nombre de estación de trabajo)
    en la tabla de nombres retornada por el dispositivo.

    Args:
        data: Bytes de la respuesta recibida.

    Returns:
        Nombre del dispositivo sin espacios de relleno, o None.
    """
    try:
        # El header NetBIOS ocupa 12 bytes
        # La respuesta comienza con la sección de nombres después del header
        if len(data) < 57:
            return None

        # Número de nombres en la tabla (byte en posición 56)
        num_names = data[56]
        if num_names == 0:
            return None

        # Cada entrada de nombre ocupa 18 bytes (16 nombre + 1 tipo + 1 flags)
        for i in range(num_names):
            offset = 57 + (i * 18)
            if offset + 18 > len(data):
                break

            name_bytes = data[offset: offset + 15]
            name_type  = data[offset + 15]
            flags      = data[offset + 16]

            # Tipo 0x00 = nombre de grupo/estación de trabajo
            # flags & 0x80 = nombre de grupo (ignorar, queremos el de la máquina)
            if name_type == 0x00 and not (flags & 0x80):
                name = name_bytes.decode("ascii", errors="ignore").rstrip()
                if name:
                    return name

        return None
    except Exception:
        return None


async def _resolve_netbios(ip: str) -> Optional[str]:
    """
    Resuelve el hostname via NetBIOS Name Service (UDP 137).

    Funciona para dispositivos Windows y equipos con Samba.
    No requiere DNS configurado — pregunta directamente al dispositivo.

    Args:
        ip: Dirección IP a consultar.

    Returns:
        Nombre NetBIOS del dispositivo o None si no responde.
    """
    loop = asyncio.get_event_loop()

    def _query_sync() -> Optional[str]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(NETBIOS_TIMEOUT)
        try:
            query = _build_netbios_query()
            sock.sendto(query, (ip, NETBIOS_PORT))
            response, _ = sock.recvfrom(1024)
            return _parse_netbios_response(response)
        except (socket.timeout, OSError, ConnectionRefusedError):
            return None
        finally:
            sock.close()

    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _query_sync),
            timeout=NETBIOS_TIMEOUT + 0.5,
        )
    except asyncio.TimeoutError:
        return None


# ──────────────────────────────────────────────
# MÉTODO 3 — SNMP sysName (UDP 161)
# ──────────────────────────────────────────────
# ESTADO: Comentado — activar cuando el equipo de red provea
#         el community string de lectura (SNMP v2c read-only).
#
# Instrucciones para activar:
#   1. Confirmar con el equipo de red el community string
#   2. Reemplazar SNMP_COMMUNITY con el valor real
#   3. Descomentar todo el bloque
#   4. Agregar al cascada en resolve_hostname(): llamar a _resolve_snmp(ip)
#
# Este método cubre: switches Cisco/HP/Ubiquiti, routers, impresoras,
# APs, NAS y UPS que tengan SNMP habilitado.
# ─────────────────────────────────────────────
#
# async def _resolve_snmp(ip: str) -> Optional[str]:
#     """
#     Resuelve el nombre del dispositivo via SNMP v2c (OID sysName).
#
#     OID 1.3.6.1.2.1.1.5.0 = sysName (RFC 1213)
#     Retorna el nombre configurado en el dispositivo de red.
#
#     Args:
#         ip: Dirección IP del dispositivo.
#
#     Returns:
#         sysName del dispositivo o None si SNMP no disponible.
#     """
#     # Requiere: pip install pysnmp
#     # from pysnmp.hlapi.asyncio import (
#     #     getCmd, SnmpEngine, CommunityData, UdpTransportTarget,
#     #     ContextData, ObjectType, ObjectIdentity
#     # )
#     # try:
#     #     iterator = getCmd(
#     #         SnmpEngine(),
#     #         CommunityData(SNMP_COMMUNITY, mpModel=1),
#     #         UdpTransportTarget((ip, SNMP_PORT), timeout=SNMP_TIMEOUT, retries=0),
#     #         ContextData(),
#     #         ObjectType(ObjectIdentity(SNMP_OID_SYSNAME)),
#     #     )
#     #     error_indication, error_status, _, var_binds = await iterator
#     #     if error_indication or error_status:
#     #         return None
#     #     for var_bind in var_binds:
#     #         value = str(var_bind[1])
#     #         return value if value else None
#     # except Exception:
#     #     return None
#     pass


# ──────────────────────────────────────────────
# CASCADA DE RESOLUCIÓN DE HOSTNAME
# ──────────────────────────────────────────────

async def resolve_hostname(ip: str) -> tuple[str, str]:
    """
    Resuelve el hostname de un host usando cascada de métodos.

    Orden de intentos:
      1. DNS Reversa PTR → rápido, cubre redes con AD/DNS interno
      2. NetBIOS UDP 137 → cubre dispositivos Windows/Samba
      3. SNMP sysName    → (comentado) cubre switches/routers/impresoras
      4. "unknown"       → fallback si ningún método funciona

    Args:
        ip: Dirección IP a resolver.

    Returns:
        Tupla (hostname, method) donde method indica cuál método funcionó.
        Ej: ("PC-MARCOS.empresa.local", "dns-ptr")
            ("SW-PISO2", "netbios")
            ("unknown", "unknown")
    """
    # 1° — DNS Reversa PTR
    hostname = await _resolve_ptr(ip)
    if hostname:
        return hostname, "dns-ptr"

    # 2° — NetBIOS (Windows / Samba)
    hostname = await _resolve_netbios(ip)
    if hostname:
        return hostname, "netbios"

    # 3° — SNMP sysName
    # Descomentar cuando el community string esté disponible:
    # hostname = await _resolve_snmp(ip)
    # if hostname:
    #     return hostname, "snmp"

    return "unknown", "unknown"


# ──────────────────────────────────────────────
# FUNCIÓN PRINCIPAL — Ping + Hostname
# ──────────────────────────────────────────────

async def ping_host(
    ip: str,
    previous_hostname: Optional[str] = None,
) -> PingResult:
    """
    Verifica si un host está activo y resuelve su hostname.

    Ejecuta ping ICMP y resolución de hostname en paralelo
    para minimizar el tiempo total de verificación.

    Si previous_hostname se provee, detecta automáticamente
    si el hostname del dispositivo cambió entre ciclos.

    Args:
        ip:                Dirección IP a verificar.
        previous_hostname: Hostname conocido del ciclo anterior
                           (para detectar cambios).

    Returns:
        PingResult con disponibilidad, RTT y hostname del host.
    """
    checked_at = datetime.now()

    # Ejecutar ping e hostname en paralelo
    ping_task     = asyncio.create_task(_icmp_ping(ip))
    hostname_task = asyncio.create_task(resolve_hostname(ip))

    is_alive, rtt_ms = await ping_task
    hostname, method = await hostname_task

    # Detectar cambio de hostname (lógica inspirada en tcping HostnameChange)
    hostname_changed = False
    hostname_change: Optional[HostnameChange] = None

    if (
        previous_hostname is not None
        and previous_hostname != "unknown"
        and hostname != "unknown"
        and hostname != previous_hostname
    ):
        hostname_changed = True
        hostname_change  = HostnameChange(
            ip=ip,
            old_hostname=previous_hostname,
            new_hostname=hostname,
            detected_at=checked_at,
        )

    return PingResult(
        ip=ip,
        is_alive=is_alive,
        rtt_ms=rtt_ms,
        hostname=hostname,
        hostname_method=method,
        hostname_changed=hostname_changed,
        hostname_change=hostname_change,
        checked_at=checked_at,
    )


async def _icmp_ping(ip: str) -> tuple[bool, float]:
    """
    Ejecuta un ping ICMP y mide el RTT con precisión decimal.

    Usa subprocess no bloqueante para compatibilidad multiplataforma.
    El RTT se mide desde Python (puede diferir levemente del RTT
    reportado por el propio comando ping, pero es consistente).

    Args:
        ip: Dirección IP a verificar.

    Returns:
        Tupla (is_alive, rtt_ms) donde rtt_ms es el tiempo en ms.
    """
    if _is_linux():
        cmd = ["ping", "-c", "1", "-W", "1", ip]
    else:
        cmd = ["ping", "-n", "1", "-w", "1000", ip]

    t_start = time.monotonic_ns()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        elapsed_ns = time.monotonic_ns() - t_start
        is_alive   = proc.returncode == 0
        rtt_ms     = _nano_to_ms(elapsed_ns) if is_alive else 0.0
        return is_alive, round(rtt_ms, 3)
    except Exception:
        return False, 0.0