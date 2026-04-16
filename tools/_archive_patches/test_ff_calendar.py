"""Quick test: Forex Factory calendar API."""

import asyncio
import aiohttp


async def test():
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    async with aiohttp.ClientSession() as s:
        async with s.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            print(f"Status: {r.status}")
            data = await r.json()
            print(f"Total events this week: {len(data)}")
            today_high = [
                e
                for e in data
                if "2026-04-07" in str(e.get("date", ""))
                and str(e.get("impact", "")) in ("High", "high")
            ]
            print(f"Today (2026-04-07) high-impact: {len(today_high)}")
            for e in today_high[:5]:
                d = str(e.get("date", "?"))[-8:]
                c = e.get("country", "?")
                t = e.get("title", "?")
                print(f"  {d} {c} {t}")
            if not today_high:
                # Show any events
                for e in data[:3]:
                    print(
                        f"  Sample: {e.get('date','?')} {e.get('country','?')} {e.get('title','?')} impact={e.get('impact','?')}"
                    )


asyncio.run(test())
