"""Diagnostic: deep-dive into confluence scoring inputs for OB zones."""

import asyncio
import json


async def check():
    import websockets  # type: ignore[import-not-found]

    uri = "ws://127.0.0.1:8000/ws"
    for tf_s in [900, 3600]:
        async with websockets.connect(uri) as ws:
            await ws.send(
                json.dumps({"action": "subscribe", "symbol": "XAU/USD", "tf_s": tf_s})
            )
            for i in range(8):
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                msg = json.loads(raw)
                # Collect all keys to understand frame structure
                # zones/swings may be top-level or under smc
                smc = msg.get("smc") or {}
                zones = msg.get("zones") or smc.get("zones") or []
                swings = msg.get("swings") or smc.get("swings") or []
                zg = msg.get("zone_grades") or {}

                # Show frame overview
                keys = sorted(msg.keys())
                candles = msg.get("candles", [])
                print(
                    "TF=%ds frame#%d keys=%s candles=%d zones=%d swings=%d zg=%d"
                    % (tf_s, i, keys, len(candles), len(zones), len(swings), len(zg))
                )

                ob_zones = [z for z in zones if z.get("kind", "").startswith("ob_")]
                fvg_zones = [z for z in zones if z.get("kind", "").startswith("fvg")]
                struct = [
                    s
                    for s in swings
                    if s.get("kind", "").startswith(("bos_", "choch_"))
                ]
                pure_swings = [
                    s for s in swings if s.get("kind") in ("swing_high", "swing_low")
                ]

                if ob_zones:
                    print(
                        "  ob=%d  fvg=%d  pure_swings=%d  struct=%d"
                        % (len(ob_zones), len(fvg_zones), len(pure_swings), len(struct))
                    )
                    for ob in ob_zones[:2]:
                        zid = ob.get("id", "?")
                        gi = zg.get(zid, {})
                        print("  OB: %s" % zid[:55])
                        print(
                            "    tf_s=%s anchor=%s kind=%s strength=%s status=%s"
                            % (
                                ob.get("tf_s"),
                                ob.get("anchor_bar_ms"),
                                ob.get("kind"),
                                ob.get("strength"),
                                ob.get("status"),
                            )
                        )
                        print(
                            "    high=%.2f low=%.2f mid=%.2f context_layer=%s"
                            % (
                                ob.get("high", 0),
                                ob.get("low", 0),
                                (ob.get("high", 0) + ob.get("low", 0)) / 2,
                                ob.get("context_layer"),
                            )
                        )
                        print(
                            "    grade=%s score=%s factors=%s"
                            % (gi.get("grade"), gi.get("score"), gi.get("factors"))
                        )
                        anchor = ob.get("anchor_bar_ms", 0)
                        near_sw = [
                            s
                            for s in pure_swings
                            if abs(s.get("time_ms", 0) - anchor) < 20 * tf_s * 1000
                        ]
                        print("    nearby_swings(20bars)=%d" % len(near_sw))
                        for s in near_sw[:3]:
                            print(
                                "      %s t=%s p=%.2f"
                                % (s.get("kind"), s.get("time_ms"), s.get("price", 0))
                            )
                        near_fvg = [
                            z
                            for z in fvg_zones
                            if abs(z.get("anchor_bar_ms", 0) - anchor)
                            < 10 * tf_s * 1000
                        ]
                        print("    nearby_fvg(10bars)=%d" % len(near_fvg))
                        for f in near_fvg[:3]:
                            print(
                                "      %s anchor=%s"
                                % (f.get("id", "?")[:40], f.get("anchor_bar_ms"))
                            )
                    break  # got data, move to next TF


asyncio.run(check())
