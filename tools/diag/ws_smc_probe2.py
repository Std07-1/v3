"""WS probe v2: dump raw zone wire format."""

import asyncio, json, aiohttp


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("ws://127.0.0.1:8000/ws") as ws:
            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            # switch to XAU/USD M15
            await ws.send_str(
                json.dumps({"action": "switch", "symbol": "XAU/USD", "tf": "M15"})
            )
            msg2 = await asyncio.wait_for(ws.receive(), timeout=5)
            d2 = json.loads(msg2.data)
            print(f"Frame size: {len(msg2.data)} bytes")
            print(f"zones: {len(d2.get('zones',[]))}")
            print(f"swings: {len(d2.get('swings',[]))}")
            print(f"levels: {len(d2.get('levels',[]))}")
            zones = d2.get("zones", [])
            if zones:
                print(f"\nZone[0] keys: {sorted(zones[0].keys())}")
                print(f"Zone[0]: {json.dumps(zones[0], indent=2)[:600]}")
            print(f"\ntrend_bias: {d2.get('trend_bias')}")
            print(f"zone_grades: {d2.get('zone_grades') is not None}")
            print(f"bias_map: {d2.get('bias_map') is not None}")
            print(
                f"pd_state: {json.dumps(d2.get('pd_state'))[:200] if d2.get('pd_state') else None}"
            )
            print(f"shell: {d2.get('shell') is not None}")
            print(f"narrative: {d2.get('narrative') is not None}")

            # Also check if frame goes through nginx
            async with s.ws_connect(
                "ws://127.0.0.1:80/ws", headers={"Host": "aione-smc.com"}
            ) as ws2:
                msg3 = await asyncio.wait_for(ws2.receive(), timeout=5)
                await ws2.send_str(
                    json.dumps({"action": "switch", "symbol": "XAU/USD", "tf": "M15"})
                )
                msg4 = await asyncio.wait_for(ws2.receive(), timeout=5)
                d4 = json.loads(msg4.data)
                print(f"\n=== VIA NGINX ===")
                print(f"Frame size: {len(msg4.data)} bytes")
                print(f"zones: {len(d4.get('zones',[]))}")
                print(f"swings: {len(d4.get('swings',[]))}")


asyncio.run(main())
