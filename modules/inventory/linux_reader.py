"""
inventory/linux_reader.py
────────────────────────────────────────────────────────────────
Lector de inventario via SSH + lshw/dmidecode para servidores Linux.

Cubre: servidores Linux, NAS con Linux, appliances con SSH habilitado.

ESTADO: Completamente comentado — requiere acceso SSH con credenciales
        o claves SSH configuradas en los servidores objetivo.

Para activar en el futuro:
  1. Coordinar con el administrador de Linux las credenciales SSH
     (usuario con sudo o acceso a lshw/dmidecode)
  2. pip install asyncssh  (cliente SSH async para Python)
  3. Configurar clave SSH del servidor de monitoreo en los targets
  4. Descomentar los bloques "# ACTIVAR"

Por qué está comentado:
  - SSH requiere credenciales o clave SSH configurada en cada servidor
  - Requiere autorización explícita del equipo de seguridad
  - Los servidores Linux productivos son sensibles — acceso controlado

Alternativa sin credenciales:
  SNMP (snmp_reader.py) puede obtener información básica de servidores
  Linux si el demonio snmpd está instalado y configurado.

Comandos que ejecutaría cuando esté activo:
  lshw -json        → inventario completo de hardware (requiere sudo)
  dmidecode         → info de BIOS/hardware (requiere sudo)
  uname -a          → versión del kernel
  cat /etc/os-release → distribución y versión
  free -m           → RAM
  df -h             → discos
  ip link show      → interfaces de red
  uptime -s         → desde cuándo está encendido

Información que obtendría cuando esté activo:
  - Distribución Linux y versión del kernel
  - CPU: modelo, cores, velocidad
  - RAM total y disponible en GB
  - Discos: dispositivos, capacidad y uso
  - Interfaces de red y MAC addresses
  - Fabricante y modelo del hardware (via dmidecode)
────────────────────────────────────────────────────────────────
"""

from typing import Optional
from modules.inventory.normalizer import DeviceInfo


# ──────────────────────────────────────────────
# CONFIGURACIÓN SSH
# ──────────────────────────────────────────────
#
# ┌─────────────────────────────────────────────────────────────┐
# │  PENDIENTE: Coordinación con administrador de Linux         │
# │                                                             │
# │  Opciones de autenticación (elegir una):                    │
# │   A) Clave SSH: agregar la clave pública del VPS de         │
# │      monitoreo al authorized_keys de cada servidor Linux    │
# │      → Más seguro, sin contraseñas en el código             │
# │                                                             │
# │   B) Usuario de servicio: crear usuario "monitor" con       │
# │      permisos limitados (solo lectura de hardware)          │
# │      → Más simple de implementar                            │
# └─────────────────────────────────────────────────────────────┘
#
# SSH_USERNAME: str      = "monitor"   # ← usuario de servicio
# SSH_KEY_PATH: str      = ""          # ← ruta a la clave privada SSH
# SSH_PASSWORD: str      = ""          # ← alternativa a clave (menos seguro)
# SSH_PORT: int          = 22
# SSH_TIMEOUT: float     = 10.0
# SSH_KNOWN_HOSTS: str   = None        # None = deshabilitar verificación host


# ──────────────────────────────────────────────
# FUNCIÓN PRINCIPAL
# ──────────────────────────────────────────────

async def read_device(ip: str, hostname: str = "unknown") -> Optional[DeviceInfo]:
    """
    Lee el inventario de un servidor Linux via SSH.

    Conecta via SSH y ejecuta lshw/dmidecode para obtener
    la información de hardware del servidor.

    Args:
        ip:       Dirección IP del servidor Linux.
        hostname: Hostname conocido (del DNS o resolución previa).

    Returns:
        DeviceInfo con la información del servidor.
        None si SSH no está disponible o las credenciales fallan.
    """
    # ── ACTIVAR cuando credenciales SSH estén disponibles ────────
    # raw = await _fetch_ssh_data(ip)
    # if raw is None:
    #     return None
    # return normalize_from_ssh(ip, raw)
    # ─────────────────────────────────────────────────────────────

    return None


async def is_ssh_available(ip: str) -> bool:
    """
    Verifica si un servidor acepta conexiones SSH en el puerto 22.

    Nota: Esto solo verifica conectividad TCP al puerto 22.
    No garantiza que las credenciales sean correctas.

    Args:
        ip: Dirección IP a verificar.

    Returns:
        True si el puerto SSH está abierto.
    """
    # ── ACTIVAR cuando credenciales SSH estén disponibles ────────
    # return await _test_ssh_connection(ip)
    # ─────────────────────────────────────────────────────────────

    return False


# ──────────────────────────────────────────────
# IMPLEMENTACIÓN SSH — Completamente comentada
# ──────────────────────────────────────────────
#
# Para activar: pip install asyncssh

# # ACTIVAR — inicio del bloque SSH
#
# import asyncssh
# import json
# from modules.inventory.normalizer import normalize_from_ssh
#
# # Comandos a ejecutar remotamente
# _CMD_OS_RELEASE  = "cat /etc/os-release 2>/dev/null"
# _CMD_UNAME       = "uname -r"
# _CMD_UPTIME      = "cat /proc/uptime 2>/dev/null"
# _CMD_MEM         = "grep MemTotal /proc/meminfo 2>/dev/null"
# _CMD_CPU         = "grep 'model name' /proc/cpuinfo 2>/dev/null | head -1"
# _CMD_DISK        = "lsblk -dno NAME,SIZE 2>/dev/null"
# _CMD_INTERFACES  = "ip -o link show 2>/dev/null | awk '{print $2}'"
# _CMD_LSHW        = "sudo lshw -json 2>/dev/null"          # Requiere sudo
# _CMD_DMIDECODE   = "sudo dmidecode -t system 2>/dev/null"  # Requiere sudo
#
#
# async def _run_command(conn, cmd: str) -> str:
#     """
#     Ejecuta un comando en el servidor remoto via SSH.
#
#     Args:
#         conn: Conexión SSH activa (asyncssh.SSHClientConnection).
#         cmd:  Comando a ejecutar.
#
#     Returns:
#         Salida del comando como string, o "" si falla.
#     """
#     try:
#         result = await conn.run(cmd, timeout=SSH_TIMEOUT)
#         return result.stdout.strip()
#     except Exception:
#         return ""
#
#
# async def _fetch_ssh_data(ip: str) -> Optional[dict]:
#     """
#     Conecta via SSH y recolecta información del servidor Linux.
#
#     Ejecuta múltiples comandos en paralelo para minimizar el tiempo.
#
#     Args:
#         ip: Dirección IP del servidor.
#
#     Returns:
#         Diccionario con los datos del sistema, o None si falla.
#     """
#     try:
#         # Configurar opciones de conexión
#         connect_kwargs = {
#             "host": ip,
#             "port": SSH_PORT,
#             "username": SSH_USERNAME,
#             "connect_timeout": SSH_TIMEOUT,
#             "known_hosts": SSH_KNOWN_HOSTS,
#         }
#
#         # Autenticación: clave SSH tiene prioridad sobre contraseña
#         if SSH_KEY_PATH:
#             connect_kwargs["client_keys"] = [SSH_KEY_PATH]
#         elif SSH_PASSWORD:
#             connect_kwargs["password"] = SSH_PASSWORD
#
#         async with asyncssh.connect(**connect_kwargs) as conn:
#
#             # Ejecutar comandos ligeros en paralelo
#             os_raw, uname, uptime_raw, mem_raw, cpu_raw, ifaces_raw = (
#                 await asyncio.gather(
#                     _run_command(conn, _CMD_OS_RELEASE),
#                     _run_command(conn, _CMD_UNAME),
#                     _run_command(conn, _CMD_UPTIME),
#                     _run_command(conn, _CMD_MEM),
#                     _run_command(conn, _CMD_CPU),
#                     _run_command(conn, _CMD_INTERFACES),
#                 )
#             )
#
#             # Parsear OS
#             os_info = _parse_os_release(os_raw) + f" (kernel {uname})"
#
#             # Parsear uptime (segundos desde boot)
#             uptime_s = None
#             if uptime_raw:
#                 try:
#                     uptime_s = int(float(uptime_raw.split()[0]))
#                 except (ValueError, IndexError):
#                     pass
#
#             # Parsear RAM (MemTotal en kB → MB)
#             ram_mb = None
#             if mem_raw:
#                 try:
#                     ram_kb = int(mem_raw.split()[1])
#                     ram_mb = ram_kb // 1024
#                 except (ValueError, IndexError):
#                     pass
#
#             # CPU
#             cpu_model = ""
#             if cpu_raw and ":" in cpu_raw:
#                 cpu_model = cpu_raw.split(":", 1)[1].strip()
#
#             # Interfaces (filtrar loopback y virtuales)
#             interfaces = [
#                 iface.rstrip(":")
#                 for iface in ifaces_raw.splitlines()
#                 if iface and not iface.startswith("lo")
#             ]
#
#             return {
#                 "hostname":      "",          # Se obtiene del DNS
#                 "os_info":       os_info,
#                 "uptime_seconds": uptime_s,
#                 "uptime_str":    "",          # normalizer lo calcula
#                 "ram_mb":        ram_mb,
#                 "cpu_model":     cpu_model,
#                 "disk_gb":       None,        # Parseado de lsblk si se necesita
#                 "interfaces":    interfaces,
#             }
#
#     except asyncssh.Error as exc:
#         logging.debug("SSH fallo en %s: %s", ip, exc)
#         return None
#     except Exception as exc:
#         logging.error("Error inesperado SSH en %s: %s", ip, exc)
#         return None
#
#
# def _parse_os_release(raw: str) -> str:
#     """
#     Parsea el contenido de /etc/os-release y extrae el nombre legible.
#
#     Args:
#         raw: Contenido crudo de /etc/os-release.
#
#     Returns:
#         Nombre y versión del SO (ej: "Ubuntu 22.04.3 LTS").
#     """
#     values = {}
#     for line in raw.splitlines():
#         if "=" in line:
#             key, _, val = line.partition("=")
#             values[key.strip()] = val.strip().strip('"')
#     return values.get("PRETTY_NAME", values.get("NAME", "Linux"))
#
#
# async def _test_ssh_connection(ip: str) -> bool:
#     """Verifica si el servidor acepta conexiones SSH."""
#     try:
#         async with asyncssh.connect(
#             host=ip,
#             port=SSH_PORT,
#             username=SSH_USERNAME,
#             connect_timeout=3.0,
#             known_hosts=None,
#         ):
#             return True
#     except Exception:
#         return False
#
# # ACTIVAR — fin del bloque SSH