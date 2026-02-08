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
