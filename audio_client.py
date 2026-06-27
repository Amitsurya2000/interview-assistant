import asyncio
import json
import os
import sys

import sounddevice as sd
import numpy as np
import websockets

WS_URL = os.getenv("WS_URL", "ws://127.0.0.1:8123/")
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "")
SAMPLE_RATE = 16000
CHUNK_DURATION = 0.1
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION)


def list_audio_devices():
    print("Available audio devices:")
    print("-" * 60)
    devices = sd.query_devices()
    print(devices)
    print("-" * 60)
    print("For system audio capture on Windows, look for 'Loopback' or 'Windows WASAPI' devices.")
    print("Use --device <device_id> to select a specific device.")


class AudioStreamer:
    def __init__(self, device_id=None, ws_url=None, auth_token=None):
        self.device_id = device_id
        self.ws_url = ws_url or WS_URL
        self.auth_token = auth_token or AUTH_TOKEN
        self.ws = None
        self.running = False
        self.connection_count = 0

    async def start(self):
        self.running = True
        while self.running:
            try:
                await self.connect_and_stream()
            except KeyboardInterrupt:
                print("\n[client] shutdown requested")
                break
            except Exception as e:
                print(f"[client] error: {e}")
                if self.running:
                    print("[client] reconnecting in 3s...")
                    await asyncio.sleep(3)

    async def connect_and_stream(self):
        self.connection_count += 1
        print(f"[client] connecting to {self.ws_url} (attempt #{self.connection_count})...")
        async with websockets.connect(
            self.ws_url, max_size=2**20, ping_interval=None,
            ping_timeout=None, close_timeout=10, compression=None
        ) as ws:
            self.ws = ws
            print(f"[client] connected to {self.ws_url}")

            if self.auth_token:
                await ws.send(json.dumps({"cmd": "auth", "token": self.auth_token}))
                auth_resp = json.loads(await ws.recv())
                if auth_resp.get("auth") != "ok":
                    print(f"[client] auth failed")
                    return
                print("[client] authenticated")

            await ws.send(json.dumps({"cmd": "hello", "client": "audio_streamer"}))
            await self.capture_and_stream()

    async def capture_and_stream(self):
        loop = asyncio.get_running_loop()

        def audio_callback(indata, frames, callback_time, status):
            if status:
                print(f"[client] audio status: {status}")
            if self.running and self.ws and self.ws.open:
                audio_bytes = (indata * 32767).astype(np.int16).tobytes()
                try:
                    asyncio.run_coroutine_threadsafe(
                        self.ws.send(audio_bytes), loop
                    )
                except Exception:
                    pass

        try:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype='float32',
                device=self.device_id,
                callback=audio_callback,
                blocksize=CHUNK_SIZE,
            )
            with stream:
                print(f"[client] streaming audio (device: {self.device_id or 'default'})")
                while self.running:
                    await asyncio.sleep(1)
        except Exception as e:
            print(f"[client] stream error: {e}")

    def stop(self):
        self.running = False


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Interview Assistant Audio Client")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices")
    parser.add_argument("--device", type=int, default=None, help="Audio device ID")
    parser.add_argument("--ws", default=None, help="WebSocket URL (default: $WS_URL or ws://127.0.0.1:8123/)")
    parser.add_argument("--token", default=None, help="Auth token (default: $AUTH_TOKEN)")
    args = parser.parse_args()

    if args.list_devices:
        list_audio_devices()
        return

    ws_url = args.ws or WS_URL
    token = args.token or AUTH_TOKEN

    streamer = AudioStreamer(device_id=args.device, ws_url=ws_url, auth_token=token)
    try:
        await streamer.start()
    except KeyboardInterrupt:
        pass
    finally:
        streamer.stop()


if __name__ == "__main__":
    asyncio.run(main())
