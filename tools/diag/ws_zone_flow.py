"""WS zone flow probe: track zones across full + delta frames."""

import json, asyncio, aiohttp


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("http://127.0.0.1:8000/ws") as ws:
            # First message (config frame)
            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            d = json.loads(msg.data)
            print(
                f"[1] frame_type={d.get('frame_type')} zones={len(d.get('zones', []))}"
            )

            # Switch to XAU/USD M15
            await ws.send_json({"action": "switch", "symbol": "XAU/USD", "tf": "M15"})

            zones_state = []
            for i in range(8):
                msg = await asyncio.wait_for(ws.receive(), timeout=10)
                d = json.loads(msg.data)
                ft = d.get("frame_type")
                if ft == "full" or ft == "replay":
                    zones_state = d.get("zones", [])
                    zg = d.get("zone_grades", {})
                    print(
                        f"[{i+2}] FULL zones={len(zones_state)} zone_grades={len(zg)} candles={len(d.get('candles', []))}"
                    )
                    for z in zones_state[:5]:
                        print(
                            f"    {z.get('id','?')[:45]} kind={z.get('kind')} st={z.get('status')}"
                        )
                elif ft == "delta":
                    sd = d.get("smc_delta")
                    bm = d.get("bias_map")
                    mm = d.get("momentum_map")
                    pd = d.get("pd_state")
                    if sd:
                        nz = sd.get("new_zones", [])
                        uz = sd.get("updated_zones", [])
                        mi = sd.get("mitigated_zone_ids", [])
                        ns = sd.get("new_swings", [])
                        print(
                            f"[{i+2}] DELTA smc_delta: new_z={len(nz)} upd_z={len(uz)} mitig={len(mi)} new_sw={len(ns)}"
                        )
                    else:
                        candles = d.get("candles", [])
                        print(
                            f"[{i+2}] DELTA (no smc_delta) candles={len(candles)} bm={bm is not None} mm={mm is not None} pd={pd is not None}"
                        )
                elif ft == "heartbeat":
                    print(f"[{i+2}] heartbeat")
                else:
                    print(f"[{i+2}] {ft} zones={len(d.get('zones', []))}")

            print(f"\nFinal zones_state count: {len(zones_state)}")
            await ws.close()


asyncio.run(main())
