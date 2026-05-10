"""WS probe: connect to ws_server and dump SMC portion of full frame."""

import asyncio, json, sys

try:
    import aiohttp
except ImportError:
    print("pip install aiohttp")
    sys.exit(1)

WS_URL = "ws://127.0.0.1:8000/ws"


async def main():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL) as ws:
            # Read initial full frame
            msg = await asyncio.wait_for(ws.receive(), timeout=5)
            if msg.type == aiohttp.WSMsgType.TEXT:
                d = json.loads(msg.data)
                print(f"Frame type: {d.get('type', '?')}")
                print(f"Symbol: {d.get('symbol', '?')}, TF: {d.get('tf', '?')}")

                # SMC fields
                zones = d.get("zones", [])
                swings = d.get("swings", [])
                levels = d.get("levels", [])
                trend_bias = d.get("trend_bias")
                zone_grades = d.get("zone_grades")
                bias_map = d.get("bias_map")
                pd_state = d.get("pd_state")
                narrative = d.get("narrative")
                signals = d.get("signals")

                print(f"\n=== SMC DATA ===")
                print(f"zones: {len(zones)}")
                print(f"swings: {len(swings)}")
                print(f"levels: {len(levels)}")
                print(f"trend_bias: {trend_bias}")
                print(f"zone_grades: {zone_grades is not None}")
                print(f"bias_map: {bias_map is not None}")
                print(f"pd_state: {pd_state is not None}")
                print(f"narrative: {narrative is not None}")
                print(f"signals: {len(signals) if signals else 0}")

                if zones:
                    print(f"\n=== ZONES (first 5) ===")
                    for z in zones[:5]:
                        print(
                            f"  {z.get('kind','?')} {z.get('side','?')} tf={z.get('tf_s','?')} "
                            f"grade={z.get('grade','?')} top={z.get('top','?')} bot={z.get('bot','?')} "
                            f"status={z.get('status','?')}"
                        )
                else:
                    print("\n!!! NO ZONES IN FRAME !!!")

                # Check all top-level keys
                print(f"\n=== ALL FRAME KEYS ===")
                print(sorted(d.keys()))

                # Switch to M15 XAU/USD and read again
                switch = {"action": "switch", "symbol": "XAU/USD", "tf": "M15"}
                await ws.send_str(json.dumps(switch))
                msg2 = await asyncio.wait_for(ws.receive(), timeout=5)
                if msg2.type == aiohttp.WSMsgType.TEXT:
                    d2 = json.loads(msg2.data)
                    z2 = d2.get("zones", [])
                    print(f"\n=== AFTER SWITCH TO XAU/USD M15 ===")
                    print(f"zones: {len(z2)}, swings: {len(d2.get('swings',[]))}")
                    for z in z2[:5]:
                        print(
                            f"  {z.get('kind','?')} {z.get('side','?')} tf={z.get('tf_s','?')} "
                            f"grade={z.get('grade','?')} status={z.get('status','?')}"
                        )
            else:
                print(f"Unexpected msg type: {msg.type}")


asyncio.run(main())
