"""runtime/agent_bridge — опційний адаптер для зовнішнього AI-клієнта (ADR-0090).

Платформа = торгова платформа; клієнт читає її. Усе, що існує заради клієнта
(конфіг, маршрути консолі, WakeEngine, overlay), збирається тут слайсами S4→S7.
S4: SSOT-резолвер секції `config.json:agent_bridge` (config.py).
S1: окремий процес (app.py, `python -m runtime.agent_bridge`), контекст (context.py),
маршрути консолі (routes_console.py) та «Очей» (routes_ochi.py), pure wake_cards.py,
thinking_archive.py — усе перенесено з ws_server.py move-only.
"""
