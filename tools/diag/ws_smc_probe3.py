"""WS probe v3: detailed zone breakdown for FVG debug."""

import asyncio, json, aiohttp
from collections import Counter


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("ws://127.0.0.1:8000/ws") as ws:
            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            await ws.send_str(
                json.dumps({"action": "switch", "symbol": "XAU/USD", "tf": "M15"})
            )
            msg2 = await asyncio.wait_for(ws.receive(), timeout=5)
            d = json.loads(msg2.data)
            zones = d.get("zones", [])

            print(f"Total zones: {len(zones)}")
            kinds = Counter(z.get("kind") for z in zones)
            print(f"By kind: {dict(kinds)}")
            statuses = Counter(z.get("status") for z in zones)
            print(f"By status: {dict(statuses)}")

            print(f"\n=== ALL ZONES ===")
            for i, z in enumerate(zones):
                print(
                    f"  [{i}] kind={z.get('kind'):20s} status={z.get('status'):20s} "
                    f"tf={z.get('tf_s'):>5} high={z.get('high','?'):>10} low={z.get('low','?'):>10} "
                    f"strength={z.get('strength','?')} id={z.get('id','?')[:40]}"
                )

            # Check which would survive renderZones filtering
            fvg_count = sum(1 for z in zones if z["kind"].startswith("fvg"))
            ob_count = sum(1 for z in zones if z["kind"].startswith("ob"))
            pd_count = sum(1 for z in zones if z["kind"] in ("premium", "discount"))
            other = len(zones) - fvg_count - ob_count - pd_count
            print(
                f"\nFVG: {fvg_count}, OB: {ob_count}, P/D(skipped): {pd_count}, other: {other}"
            )
            print(f"Renderable (no P/D): {fvg_count + ob_count + other}")


asyncio.run(main())
