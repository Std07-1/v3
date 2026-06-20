# Перекладач — локальний автономний веб-сервіс

Швидкий, точний і легкий перекладач, що працює **повністю на власному VPS**
без Google / DeepL / OpenAI. Етап 1 — текстовий переклад.

```
Nginx ──▶ Frontend (PWA) ──▶ FastAPI ──▶ Redis cache
                                    └────▶ Pluggable engine (NLLB / Argos / mock)
```

## Що вже є (Етап 1)

- **PWA-фронтенд** (vanilla JS): адаптивний, встановлюється як застосунок,
  офлайн-доступний UI через Service Worker, темна/світла тема, swap мов,
  автовизначення джерела, копіювання, `Ctrl/Cmd+Enter`.
- **FastAPI backend**: `/api/health`, `/api/languages`, `/api/translate`.
- **Pluggable рушій** — міняється одним env, без правок коду:
  - `mock` — детермінований, без моделей (тести / CI / smoke);
  - `nllb` — NLLB-200 distilled-600M через CTranslate2 (int8) — **основний**,
    найкращий баланс швидкість/якість для UA↔CS / UA↔HU;
  - `argos` — Argos Translate, легкий fallback.
- **Redis-кеш** з graceful degradation (немає Redis → in-memory, гучний лог).
- **Мови**: uk, en, cs, hu, ru (розширюється в `app/languages.py`).
- **Docker-compose**: nginx + backend + redis.

## Швидкий старт (локально, без моделей)

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
TRANSLATE_ENGINE=mock uvicorn app.main:app --reload
# відкрий http://127.0.0.1:8000
```

Тести:

```bash
cd backend && python -m pytest
```

## Прод-рушій NLLB (точний переклад)

```bash
cd backend
pip install -r requirements-nllb.txt
bash scripts/convert_nllb.sh                 # один раз: конвертація у CT2/int8
export TRANSLATE_ENGINE=nllb
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Docker

```bash
cp .env.example .env          # за потреби зміни TRANSLATE_ENGINE
docker compose up --build     # http://127.0.0.1:8080
```

> За замовчуванням compose використовує `engine=nllb` і чекає модель у
> `backend/models/`. Для запуску без моделі: `TRANSLATE_ENGINE=mock docker compose up`.

## API

`POST /api/translate`

```json
{ "text": "Ти відпочиваєш, сестричко?", "source": "auto", "target": "en" }
```

```json
{ "text": "...", "source": "uk", "target": "en", "engine": "nllb", "cached": false }
```

## Конфігурація

Усе через env з префіксом `TRANSLATE_` (див. `.env.example`). Ключові:
`TRANSLATE_ENGINE`, `TRANSLATE_REDIS_URL`, `TRANSLATE_NLLB_MODEL_DIR`,
`TRANSLATE_NLLB_COMPUTE_TYPE`, `TRANSLATE_MAX_CHARS`.

## Мінімальні вимоги VPS

| Сценарій | vCPU | RAM | SSD |
|---|---|---|---|
| Текст (NLLB-600M int8) | 2 | 4 GB | 20 GB |
| + Голос (пізніше) | 4 | 8 GB | — |

## Дорожня карта

- **Етап 2** — кращі моделі (NLLB-1.3B / M2M100) для вищої якості.
- **Етап 3** — голос → текст (Faster-Whisper).
- **Етап 4** — текст → голос (Piper TTS).
- Історія перекладів, словник, улюблені фрази.

## Структура

```
translate/
├── backend/          FastAPI + рушії + тести
│   ├── app/
│   │   ├── engines/  base + mock + nllb_ct2 + argos + registry
│   │   ├── main.py   маршрути API + роздача PWA
│   │   ├── service.py   кеш -> рушій оркестрація
│   │   ├── cache.py  Redis / memory / null
│   │   ├── languages.py / detect.py / config.py / schemas.py
│   │   └── ...
│   ├── scripts/convert_nllb.sh
│   └── tests/
├── frontend/         PWA (html/css/js + sw + manifest)
├── nginx/nginx.conf
└── docker-compose.yml
```
