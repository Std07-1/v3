"""Check SmcRunner zone grades for XAU/USD M15."""

import json, asyncio, aiohttp


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("http://127.0.0.1:8000/ws") as ws:
            # Get default full frame
            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            d = json.loads(msg.data)
            print(
                f"[DEFAULT] frame_type={d.get('frame_type')} zones={len(d.get('zones',[]))}"
            )
            print(f"  zone_grades keys: {list(d.get('zone_grades',{}).keys())[:5]}")

            # Switch to XAU/USD M15
            await ws.send_json({"action": "switch", "symbol": "XAU/USD", "tf": "M15"})
            msg2 = await asyncio.wait_for(ws.receive(), timeout=5)
            d2 = json.loads(msg2.data)
            ft = d2.get("frame_type")
            zones = d2.get("zones", [])
            zg = d2.get("zone_grades", {})
            bm = d2.get("bias_map", {})
            mm = d2.get("momentum_map", {})
            pd = d2.get("pd_state")
            print(f"\n[M15 SWITCH] frame_type={ft}")
            print(f"  zones={len(zones)}")
            print(f"  zone_grades={len(zg)}")
            if zg:
                for zid, info in list(zg.items())[:3]:
                    print(f"    {zid[:40]}: {info}")
            print(f"  bias_map={bm}")
            print(f"  momentum_map keys={list(mm.keys())[:5]}")
            print(f"  pd_state={pd}")

            # Check if maybe we need to wait for the second full frame (scrollback)
            try:
                msg3 = await asyncio.wait_for(ws.receive(), timeout=3)
                d3 = json.loads(msg3.data)
                ft3 = d3.get("frame_type")
                zg3 = d3.get("zone_grades", {})
                zones3 = d3.get("zones", [])
                print(
                    f"\n[NEXT] frame_type={ft3} zones={len(zones3)} zone_grades={len(zg3)}"
                )
                if zg3:
                    for zid, info in list(zg3.items())[:3]:
                        print(f"    {zid[:40]}: {info}")
            except asyncio.TimeoutError:
                print("\n[NEXT] timeout (no more frames)")

            await ws.close()


asyncio.run(main())
