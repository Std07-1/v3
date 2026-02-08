# UI чарт v3 (wheel fix під Lightweight Charts 4.1.3)

## Запуск

- Рекомендовано (модуль):
  - python -m ui_chart_v3 --data-root ./data_v3 --host 0.0.0.0 --port 8089
- Якщо data_root вже в config.json:
  - python -m ui_chart_v3 --host 0.0.0.0 --port 8089
- Прямий запуск:
  - python server.py --data-root ./data_v3 --host 0.0.0.0 --port 8089 --static-root ./static

## Конфіг

- --config <path> використовується для ui_debug та tf_allowlist.
- UI сервер читає config.json з кешем mtime (перевірка ~0.5s).

## UI поведінка (scrollback/cache)

- Scrollback тригериться при дефіциті лівого буфера ~2000 барів.
- Пачки догрузки: 5000 барів, для фаворитів ліміти x2.
- Active кеш: 60000 барів (або 120000 для фаворитів).
- Warm кеш: LRU до 6 ключів, по 20000 барів на ключ.
