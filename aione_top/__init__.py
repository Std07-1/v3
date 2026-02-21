"""aione-top — htop-подібний монітор для торгової платформи v3.

Ізольований read-only інструмент. Не імпортує runtime/core/ui.
Джерела даних: psutil (OS), Redis (v3_local), disk (data_v3 freshness).

Запуск: python -m aione_top [--interval 3] [--config config.json]
"""
