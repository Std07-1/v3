"""Локальний автономний перекладач — backend (FastAPI).

Архітектура: PWA -> nginx -> FastAPI -> (Redis cache) -> pluggable engine.
Жодних зовнішніх API: переклад виконується локально вибраним рушієм.
"""

__version__ = "0.1.0"
