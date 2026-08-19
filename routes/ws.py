import logging
from typing import Dict, List
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from modules.storage.repository import NetworkRepository
from routes.api import get_scanner_registry

router = APIRouter()
logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        # Mapea un network_id (int) a una lista de WebSockets conectados
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, network_id: int):
        await websocket.accept()
        if network_id not in self.active_connections:
            self.active_connections[network_id] = []
        self.active_connections[network_id].append(websocket)
        logger.info(f"Cliente conectado al WS para la red {network_id}")

        # Marcar la red como ACTIVA en el escáner
        registry = get_scanner_registry()
        if registry is not None:
            net_repo = NetworkRepository()
            network = await net_repo.get_by_id(network_id)
            if network:
                await registry.set_active_network(network.cidr)

    async def disconnect(self, websocket: WebSocket, network_id: int):
        if network_id in self.active_connections:
            if websocket in self.active_connections[network_id]:
                self.active_connections[network_id].remove(websocket)
            if not self.active_connections[network_id]:
                del self.active_connections[network_id]
                
                # Si ya no quedan clientes, pasar la red a BACKGROUND
                registry = get_scanner_registry()
                if registry is not None:
                    net_repo = NetworkRepository()
                    network = await net_repo.get_by_id(network_id)
                    if network:
                        registry.set_background_network(network.cidr)
                        logger.info(f"Red {network_id} pasada a BACKGROUND por falta de oyentes WS.")

        logger.info(f"Cliente desconectado del WS para la red {network_id}")

    async def broadcast_to_network(self, network_id: int, message: dict):
        if network_id in self.active_connections:
            for connection in self.active_connections[network_id]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.error(f"Error enviando mensaje WS a red {network_id}: {e}")

    # --- GLOBAL ALERTS ---
    global_connections: List[WebSocket] = []

    async def connect_global(self, websocket: WebSocket):
        await websocket.accept()
        self.global_connections.append(websocket)
        logger.info("Cliente conectado al WS Global de alertas")

    def disconnect_global(self, websocket: WebSocket):
        if websocket in self.global_connections:
            self.global_connections.remove(websocket)
            logger.info("Cliente desconectado del WS Global de alertas")

    async def broadcast_global(self, message: dict):
        for connection in self.global_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error enviando mensaje WS Global: {e}")

manager = ConnectionManager()

@router.websocket("/networks/{network_id}")
async def websocket_network_endpoint(websocket: WebSocket, network_id: int):
    await manager.connect(websocket, network_id)
    try:
        while True:
            # En este MVP, el cliente solo escucha. 
            # Pero necesitamos recibir texto para detectar si el cliente cierra abruptamente.
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket, network_id)

@router.websocket("/alerts")
async def websocket_alerts_endpoint(websocket: WebSocket):
    await manager.connect_global(websocket)
    try:
        while True:
            # Mantener conexión viva
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_global(websocket)
    except Exception as e:
        logger.error(f"Error en websocket_alerts_endpoint: {e}")
        manager.disconnect_global(websocket)
