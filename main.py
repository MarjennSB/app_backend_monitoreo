"""
main.py
────────────────────────────────────────────────────────────────
Punto de entrada de MvpMonitoreo.

Equivalente a bootstrap/app.php + artisan serve de Laravel.

Responsabilidades:
  - Arrancar FastAPI
  - Conectar a PostgreSQL al iniciar (lifespan)
  - Crear las tablas si no existen
  - Arrancar el SystemMonitor del VPS
  - Inicializar el ScannerRegistry con las redes activas de la BD
  - Registrar las rutas de api.py
  - Cerrar todo limpiamente al apagar

Ejecutar:
  uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Para producción:
  uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
  (workers=1 es OBLIGATORIO — el ScannerRegistry usa asyncio interno)
────────────────────────────────────────────────────────────────
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.system_monitor import system_monitor
from modules.storage.database import db
from modules.discovery.scanner import ScannerRegistry
from routes.api import router, set_scanner_registry

# ──────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mvp_monitoreo")


# ──────────────────────────────────────────────
# LIFESPAN — Arranque y cierre de la aplicación
# Equivalente a AppServiceProvider::boot() de Laravel
# ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Gestiona el ciclo de vida completo de la aplicación.

    ARRANQUE (yield):
      1. Conecta a PostgreSQL
      2. Crea tablas si no existen
      3. Arranca el monitor de recursos del VPS
      4. Carga redes activas de la BD y arranca sus scanners
      5. Inyecta el registry en la API

    CIERRE (después del yield):
      1. Detiene todos los scanners activos
      2. Detiene el monitor del VPS
      3. Desconecta de PostgreSQL
    """
    # ── ARRANQUE ──────────────────────────────
    log.info("═" * 50)
    log.info("  MvpMonitoreo — Iniciando...")
    log.info("═" * 50)

    # 1. PostgreSQL
    log.info("Conectando a PostgreSQL...")
    await db.connect()
    log.info("Creando tablas si no existen...")
    await db.create_tables()

    # 2. Monitor del VPS (CPU/RAM)
    log.info("Arrancando monitor de recursos del VPS...")
    system_monitor.start()

    # 3. Scanner Registry
    log.info("Inicializando ScannerRegistry...")
    registry = ScannerRegistry()

    # 4. Cargar redes activas de la BD y arrancar sus scanners
    from modules.storage.repository import NetworkRepository
    net_repo = NetworkRepository()
    active_networks = await net_repo.get_active()

    if active_networks:
        log.info("Cargando %d redes activas de la BD...", len(active_networks))
        for network in active_networks:
            await registry.add_network(
                network_cidr=network.cidr,
                network_id=network.id,
            )
            log.info("  ✓ Scanner activo: %s (%s)", network.cidr, network.vlan_name or "Sin VLAN")
    else:
        log.info("No hay redes configuradas. Agregar via POST /networks.")

    # 5. Inyectar registry en la API
    set_scanner_registry(registry)

    # 6. Arrancar Escáner de Dispositivos Críticos (VIP)
    from modules.discovery.critical_scanner import critical_scanner_engine
    log.info("Arrancando CriticalScanner (VIP)...")
    critical_scanner_engine.start()

    # 7. Arrancar Tarea en Segundo Plano para Sincronización de Google Sheets (cada 3 horas)
    import asyncio
    from modules.inventory.sheets_sync import process_csv_inventory
    from routes.api import GOOGLE_SHEETS_CSV_URL, _inv_repo

    async def sheets_sync_task():
        """Descarga y sincroniza Google Sheets cada 3 horas."""
        while True:
            # Esperar 3 horas (10800 segundos) entre sincronizaciones
            await asyncio.sleep(10800)
            log.info("Ejecutando sincronización automática desde Google Sheets (Cron 3h)...")
            try:
                processed_data = await asyncio.to_thread(process_csv_inventory, GOOGLE_SHEETS_CSV_URL)
                if processed_data:
                    await _inv_repo.bulk_upsert_inventory_and_hostnames(processed_data)
                    log.info(f"Sincronización automática exitosa: {len(processed_data)} equipos actualizados.")
                else:
                    log.warning("Sincronización automática falló o el archivo está vacío.")
            except Exception as e:
                log.error(f"Error en tarea automática de Google Sheets: {e}")

    sync_task = asyncio.create_task(sheets_sync_task())
    log.info("Tarea de sincronización de Google Sheets iniciada (Cron 3h).")

    log.info("═" * 50)
    log.info("  MvpMonitoreo listo en http://0.0.0.0:8000")
    log.info("  Docs: http://0.0.0.0:8000/docs")
    log.info("═" * 50)

    yield  # La aplicación corre aquí

    # ── CIERRE ────────────────────────────────
    log.info("Cerrando MvpMonitoreo...")
    
    # Cancelar tarea de Google Sheets
    sync_task.cancel()

    log.info("Deteniendo scanners...")
    await registry.shutdown()
    critical_scanner_engine.stop()

    log.info("Deteniendo monitor del VPS...")
    system_monitor.stop()

    log.info("Desconectando de PostgreSQL...")
    await db.disconnect()

    log.info("MvpMonitoreo cerrado correctamente.")


# ──────────────────────────────────────────────
# APLICACIÓN FastAPI
# Equivalente a $app = new Application() de Laravel
# ──────────────────────────────────────────────

app = FastAPI(
    title="MvpMonitoreo",
    description=(
        "API de monitoreo de red empresarial. "
        "Escaneo automático de dispositivos, "
        "estadísticas de disponibilidad e inventario de hardware."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",       # Swagger UI
    redoc_url="/redoc",     # ReDoc
)

# ── CORS — Permitir que el frontend consuma la API ────────────
# En producción, cambiar allow_origins por el dominio real del frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # ← En producción: ["https://tu-frontend.com"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi import Depends
from modules.auth.dependencies import get_current_user
from routes.auth import router as auth_router
from routes.ws import router as ws_router

# ── Rutas ─────────────────────────────────────────────────────
# Equivalente a require base_path('routes/api.php') de Laravel
app.include_router(auth_router, prefix="/api/v1/auth")
app.include_router(router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
app.include_router(ws_router, prefix="/ws", tags=["WebSockets"])


# ──────────────────────────────────────────────
# ROOT — Redirección a docs
# ──────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    """Redirecciona a la documentación de la API."""
    return {
        "app":     "MvpMonitoreo",
        "version": "1.0.0",
        "docs":    "/docs",
        "api":     "/api/v1",
        "health":  "/api/v1/health",
    }
