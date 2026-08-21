"""
services/telegram_notifier.py
────────────────────────────────────────────────────────────────
Servicio para enviar notificaciones de estado (Flapping) a Telegram.
Lee el Token y Chat ID desde las variables de entorno.
────────────────────────────────────────────────────────────────
"""
import os
import httpx
import logging
import asyncio

log = logging.getLogger("mvp_monitoreo.telegram")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

async def send_telegram_alert(alert_type: str, ip: str, hostname: str):
    """
    Envía un mensaje formateado al grupo de Telegram.
    Es una llamada asíncrona ("fire-and-forget") para no bloquear el escáner.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Faltan credenciales de Telegram en .env, omitiendo alerta.")
        return

    # Formatear el mensaje según el tipo
    if alert_type == "CRITICAL_DEVICE_DOWN":
        icon = "🔴"
        title = "ALERTA CRÍTICA: SERVIDOR CAÍDO"
        message = f"El dispositivo ha superado el límite de fallos consecutivos y se considera oficialmente fuera de línea."
    elif alert_type == "CRITICAL_DEVICE_UP":
        icon = "🟢"
        title = "RECUPERACIÓN: SERVIDOR ONLINE"
        message = f"El dispositivo ha vuelto a responder y se ha estabilizado en la red."
    else:
        return

    text = (
        f"{icon} *{title}*\n\n"
        f"🖥 *Hostname:* {hostname}\n"
        f"🌐 *IP:* {ip}\n\n"
        f"_{message}_"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=payload, timeout=5.0)
            if res.status_code != 200:
                log.error(f"Error de Telegram: {res.text}")
            else:
                log.info(f"Alerta enviada a Telegram para {ip}")
    except Exception as e:
        log.error(f"Excepción al enviar alerta a Telegram: {e}")

# Wrapper para correrlo en background (fire and forget)
def notify_in_background(alert_type: str, ip: str, hostname: str):
    asyncio.create_task(send_telegram_alert(alert_type, ip, hostname))
