"""Diagnostic: dump all zones with context_layer and TF info."""

import asyncio
import json


async def check():
    import websockets  # type: ignore[import-not-found]

    uri = "ws://127.0.0.1:8000/ws"
    for tf_s in [180, 900]:
        async with websockets.connect(uri) as ws:
            await ws.send(
                json.dumps({"action": "subscribe", "symbol": "XAU/USD", "tf_s": tf_s})
            )
            for i in range(5):
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                msg = json.loads(raw)
                zones = msg.get("zones") or []
                if zones:
                    print("=== TF=%ds  total_zones=%d ===" % (tf_s, len(zones)))
                    for z in zones:
                        print(
                            "  %s  tf_s=%4d  ctx=%-14s  h=%.2f  l=%.2f  range=%.2f  status=%s  str=%.2f"
                            % (
                                z.get("kind", "?")[:8].ljust(8),
                                z.get("tf_s", 0),
                                z.get("context_layer", "none"),
                                z.get("high", 0),
                                z.get("low", 0),
                                z.get("high", 0) - z.get("low", 0),
                                z.get("status", "?"),
                                z.get("strength", 0),
                            )
                        )
                    break


asyncio.run(check())
