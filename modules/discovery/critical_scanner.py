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
                
                # Detectar si se cayó
                if device.is_alive and not is_alive_now:
                    # El dispositivo VIP se acaba de caer
                    # Actualizar DB primero
                    device.is_alive = False
                    await self._dev_repo.upsert(
                        network_id=device.network_id,
                        ip=device.ip,
                        hostname=device.hostname or 'unknown',
                        hostname_method=device.hostname_method or 'unknown',
                        is_alive=False,
                        mac_address=device.mac_address
                    )
                    
                    # Notificar al WebSocket Global
                    alert_msg = {
                        "type": "CRITICAL_DEVICE_DOWN",
                        "device": {
                            "id": device.id,
                            "ip": device.ip,
                            "hostname": device.hostname
                        },
                        "timestamp": datetime.now().isoformat()
                    }
                    await manager.broadcast_global(alert_msg)
                
                # Detectar si volvió a subir
                elif not device.is_alive and is_alive_now:
                    device.is_alive = True
                    await self._dev_repo.upsert(
                        network_id=device.network_id,
                        ip=device.ip,
                        hostname=device.hostname or 'unknown',
                        hostname_method=device.hostname_method or 'unknown',
                        is_alive=True,
                        mac_address=device.mac_address
                    )
                    
                    alert_msg = {
                        "type": "CRITICAL_DEVICE_UP",
                        "device": {
                            "id": device.id,
                            "ip": device.ip,
                            "hostname": device.hostname
                        },
                        "timestamp": datetime.now().isoformat()
                    }
                    await manager.broadcast_global(alert_msg)

        tasks = [check_device(d) for d in vip_devices]
        await asyncio.gather(*tasks)

critical_scanner_engine = CriticalScanner()
