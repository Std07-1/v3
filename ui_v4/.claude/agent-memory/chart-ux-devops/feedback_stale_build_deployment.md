---
name: stale-build-deployment-pattern
description: ws_server obслуговує старий bundle після npm run build якщо не перезапущений — частий deployment gap
type: feedback
---

ws_server (aiohttp) реєструє static routes ONE-TIME при старті через `app.router.add_static("/assets", ...)`.
Після `npm run build` dist/index.html оновлюється з новим bundle hash, але сервер продовжує обслуговувати старі файли з пам'яті/файлової системи через той самий static route.

**Why:** Виявлено під час POST-CHANGE VISUAL AUDIT ADR-0043 P5 (2026-03-24).
dist/index.html посилалась на index-DqJKbAgS.js (новий), але браузер завантажував index-B0JhKer4.js (старий) бо ws_server не перезапускався після rebuild.

**How to apply:** При будь-якому аудиті після npm run build — спочатку перевірити чи bundle що обслуговується браузером відповідає dist/index.html:
  1. `curl -s http://127.0.0.1:8000/ | grep 'assets/index'` — що сервер роздає
  2. `grep 'assets/index' ui_v4/dist/index.html` — що в dist
  Якщо не збігаються → потрібен перезапуск ws_server перед аудитом.

Команда перевірки: `curl -s http://127.0.0.1:8000/ | grep -o 'assets/index[^"]*'`
Команда fix: `python -m app.main --mode all` (перезапуск всього) або рестарт тільки ws_server.
