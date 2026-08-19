import asyncio
from modules.storage.database import db

async def test_db():
    await db.connect()
    device = await db.fetch_one("SELECT * FROM devices WHERE ip = '192.168.1.37'")
    print(dict(device) if device else "Not found")
    await db.disconnect()

if __name__ == "__main__":
    asyncio.run(test_db())
