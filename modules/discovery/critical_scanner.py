import asyncio
import logging
from datetime import datetime
from modules.storage.repository import DeviceRepository
from modules.discovery.scanner import ping_host
from routes.ws import manager

logger = logging.getLogger("critical_scanner")

class CriticalScanner:
    def __init__(self, interval_seconds: int = 60):
        self.interval_seconds = interval_seconds
        self._dev_repo = DeviceRepository()
        self.is_running = False
        self._task = None

    def start(self):
        if not self.is_running:
            self.is_running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("CriticalScanner started")

    def stop(self):
        self.is_running = False
        if self._task:
            self._task.cancel()
            logger.info("CriticalScanner stopped")

    async def _loop(self):
        while self.is_running:
            try:
                await self._scan_critical_devices()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in CriticalScanner: {e}")
            
            await asyncio.sleep(self.interval_seconds)

    async def _scan_critical_devices(self):
        vip_devices = await self._dev_repo.get_all_critical_devices()
        logger.info(f"Escaneando {len(vip_devices)} dispositivos VIP...")
        
        if not vip_devices:
            return

        # Limitar la concurrencia a 50 pings simultáneos para no saturar el OS
        sem = asyncio.Semaphore(50)

        async def check_device(device):
            async with sem:
                is_alive_now = await ping_host(device.ip)
                
                # Upsert actualizará el failed_pings_count automáticamente en la BD
                updated_device = await self._dev_repo.upsert(
                    network_id=device.network_id,
                    ip=device.ip,
                    hostname=device.hostname or 'unknown',
                    hostname_method=device.hostname_method or 'unknown',
                    is_alive=is_alive_now,
                    mac_address=device.mac_address
                )
                
                # ¡Lógica de Tolerancia (Anti-Flapping) para VIPs!
                alert_type = None
                
                # 1. Regla de Caída: 3 fallos consecutivos
                if not updated_device.is_alive and updated_device.failed_pings_count == 3:
                    alert_type = "CRITICAL_DEVICE_DOWN"
                
                # 2. Regla de Recuperación: Solo si estaba OFICIALMENTE caído (>= 3 fallos previos)
                elif updated_device.is_alive and not device.is_alive and getattr(device, 'failed_pings_count', 0) >= 3:
                    alert_type = "CRITICAL_DEVICE_UP"

                if alert_type:
                    alert_msg = {
                        "type": alert_type,
                        "device": {
                            "id": updated_device.id,
                            "ip": str(updated_device.ip),
                            "hostname": updated_device.hostname
                        },
                        "timestamp": datetime.now().isoformat()
                    }
                    await manager.broadcast_global(alert_msg)
                    
                    from modules.services.telegram_notifier import notify_in_background
                    notify_in_background(alert_type, str(updated_device.ip), updated_device.hostname)

        tasks = [check_device(d) for d in vip_devices]
        await asyncio.gather(*tasks)

critical_scanner_engine = CriticalScanner()
