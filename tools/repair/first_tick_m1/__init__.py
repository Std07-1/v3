"""Ремонт значень open/high/low M1 з FXCM FIRST_TICK — інструмент слайса B ADR-0096 (§3.3 B).

Уся історія M1 у data_v3 записана з open = close попередньої свічки (дефолт SDK PREVIOUS_CLOSE). Цей пакет
перезабирає M1 у FIRST_TICK у staging ПОЗА data_v3 і замінює в SSOT лише o/h/low наявних ключів — там, де
доведено, що новий рядок той самий бар (c збігся, діапазон лише звузився) і брокер віддав справжній перший
тік (не «запечений», §1.4).

Фази (`python -m tools.repair.first_tick_m1 <фаза>`), кожна з власними рейками і rc:
  fetch    — .venv37 (Python 3.7), лише закритий ринок, кожен виклик SDK — дочірній процес зі свіжим cwd і
             жорстким таймаутом; пише лише staging.
  plan     — .venv, лише читання data_root і staging; план — чиста функція входів (sha кожного).
  apply    — .venv, rewrite_atomic переможців за планом при доведено зупинених записувачах і закритому ринку.
  verify   — .venv, лише читання: погляди TAIL/RANGE/PRIME до і після, жодної непланової зміни.
  rollback — .venv, дзеркало apply за маніфестом.

Модулі fetch-сторони (__init__, __main__, common, staging, fetch*, fetch_runner, fetch_child) мусять
розбиратись і імпортуватись у Python 3.7 і не тягнути платформних залежностей — гейт у
tests/test_first_tick_m1_staging.py.
"""
