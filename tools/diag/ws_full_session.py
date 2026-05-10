"""Full WS session sim: connect → config → full → N deltas. Track zones."""

import json, asyncio, aiohttp


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("http://127.0.0.1:8000/ws") as ws:
            for i in range(6):
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=8)
                except asyncio.TimeoutError:
                    print(f"[{i+1}] TIMEOUT")
                    break
                d = json.loads(msg.data)
                ft = d.get("frame_type")
                sym = d.get("symbol")
                tf = d.get("tf")
                zones = d.get("zones", [])
                swings = d.get("swings", [])
                levels = d.get("levels", [])
                zg = d.get("zone_grades", {})
                tb = d.get("trend_bias")
                cn = len(d.get("candles", []))
                sd = d.get("smc_delta")
                bm = d.get("bias_map")
                schema = d.get("meta", {}).get("schema_v")
                seq = d.get("meta", {}).get("seq")

                print(f"[{i+1}] frame_type={ft} sym={sym} tf={tf} seq={seq}")
                print(
                    f"     candles={cn} zones={len(zones)} swings={len(swings)} levels={len(levels)}"
                )
                print(f"     zone_grades={len(zg)} trend_bias={tb} schema_v={schema}")
                if sd:
                    print(
                        f"     smc_delta: new_z={len(sd.get('new_zones',[]))} new_sw={len(sd.get('new_swings', []))}"
                    )
                if zones:
                    fvg_count = sum(
                        1 for z in zones if z.get("kind", "").startswith("fvg")
                    )
                    ob_count = sum(
                        1 for z in zones if z.get("kind", "").startswith("ob")
                    )
                    pd_count = sum(
                        1 for z in zones if z.get("kind") in ("premium", "discount")
                    )
                    other = len(zones) - fvg_count - ob_count - pd_count
                    print(
                        f"     zone_kinds: fvg={fvg_count} ob={ob_count} pd={pd_count} other={other}"
                    )
                print()

            await ws.close()


asyncio.run(main())
