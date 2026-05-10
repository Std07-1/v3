"""Quick WS probe: check if backend sends zones in full frame."""

import json, asyncio, aiohttp


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("http://127.0.0.1:8000/ws") as ws:
            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            data = json.loads(msg.data)
            ft = data.get("frame_type")
            sym = data.get("symbol")
            tf = data.get("tf")
            zones = data.get("zones", [])
            schema = data.get("meta", {}).get("schema_v")
            print(f"frame_type={ft} sym={sym} tf={tf} schema_v={schema}")
            print(f"zones_count={len(zones)}")
            for z in zones[:3]:
                print(
                    f"  zone: {z.get('id','?')[:45]} kind={z.get('kind')} status={z.get('status')}"
                )

            # switch to XAU/USD M15
            await ws.send_json({"action": "switch", "symbol": "XAU/USD", "tf": "M15"})
            msg2 = await asyncio.wait_for(ws.receive(), timeout=5)
            data2 = json.loads(msg2.data)
            ft2 = data2.get("frame_type")
            zones2 = data2.get("zones", [])
            zg2 = data2.get("zone_grades", {})
            print(
                f"\nAfter switch M15: frame_type={ft2} zones={len(zones2)} zone_grades={len(zg2)}"
            )
            for z in zones2[:5]:
                print(
                    f"  zone: {z.get('id','?')[:45]} kind={z.get('kind')} status={z.get('status')} str={z.get('strength','?')}"
                )

            await ws.close()


asyncio.run(main())
