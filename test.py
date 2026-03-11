import asyncio
import websockets # pip install websockets

async def hello():
    uri = "ws://localhost:8000/ws"
    try:
        async with websockets.connect(uri) as websocket:
            print("✅ Connexion réussie ! Le serveur est à jour.")
    except Exception as e:
        print(f"❌ Échec de connexion : {e}")

if __name__ == "__main__":
    asyncio.run(hello())