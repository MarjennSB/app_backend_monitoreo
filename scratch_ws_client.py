import asyncio
import websockets

async def test():
    try:
        async with websockets.connect("ws://127.0.0.1:8000/ws/networks/1") as websocket:
            print("Connected!")
            msg = await websocket.recv()
            print("Received:", msg)
    except Exception as e:
        print("Error:", repr(e))

asyncio.run(test())
