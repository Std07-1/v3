# Redis snapshots (мінімальний дизайн)

Цей документ описує **snapshot keyspace** (ohlcv:snap/tail + status + prime:ready) для прискорення читання UI. Redis **не є SSOT**: SSOT лишається на диску (JSONL).

Важливо: цей документ **не описує** preview keyspace (`{NS}:preview:*`) і updates bus (list+seq). Вони існують у системі, але це окремі контури.

## Цілі

- Прискорити cold-start UI (читання останнього стану за 10-50мс).
- Зменшити залежність UI від disk scan або rebuild.
- Зробити stale/ok видимими через TTL + payload_ts_ms.
- Не змінювати SSOT і логіку ingest.
- Не допустити «малого tail» як silent failure: при недостатній кількості барів повертаємо **partial+loud** (`redis_small_tail`) замість порожнього графіка.

## Не цілі

- Redis не прискорює появу final барів у ingest.
- Цей документ не є описом updates bus або preview plane.
- Повноцінні метрики/Prometheus тут не вводяться (є лише мінімальна телеметрія в `/api/status` і loud-логи).

## Ключі (snapshot keyspace)

У ключах використовується `symbol_key(symbol)` (див. `runtime/store/redis_keys.py`): символ нормалізується як `XAU/USD → XAU_USD`.

- `{NS}:ohlcv:snap:{symbol_key}:{tf_s}`
- `{NS}:ohlcv:tail:{symbol_key}:{tf_s}`
- `{NS}:status:snapshot`
- `{NS}:prime:ready`

## Контракти

### ohlcv:snap:{symbol_key}:{tf_s}

```json
{
  "v": 1,
  "symbol": "XAU/USD",
  "tf_s": 300,
  "bar": {
    "open_ms": 1770302400000,
    "close_ms": 1770302459999,
    "o": 88.048,
    "h": 88.247,
    "l": 87.9935,
    "c": 88.2425,
    "v": 75
  },
  "complete": true,
  "source": "history",
  "event_ts_ms": 1770302459999,
  "seq": 123456,
  "payload_ts_ms": 1770302461023
}
```

Правила:

- `bar.close_ms` зберігається як **end-incl** (тобто `close_time_ms_excl - 1`). Це **внутрішній cache-формат**, не публічний `bar_v1`.
- Якщо `complete=true`, то `event_ts_ms == bar.close_ms`.
- `seq` — монотонний (глобальний writer-seq у `RedisSnapshotWriter`).
- `payload_ts_ms` — час запису в Redis.

Нормалізація для API: коли UDS конвертує payload у публічні бари, він відновлює end-excl як `close_time_ms = close_ms + 1`.

### ohlcv:tail:{symbol_key}:{tf_s}

```json
{
  "v": 1,
  "symbol": "XAU/USD",
  "tf_s": 300,
  "bars": [
    {
      "open_ms": 1770302100000,
      "close_ms": 1770302159999,
      "o": 88.02,
      "h": 88.11,
      "l": 88.00,
      "c": 88.05,
      "v": 42
    }
  ],
  "complete": true,
  "source": "history",
  "last_seq": 123456,
  "payload_ts_ms": 1770302461023
}

### prime:ready

Ключ `{NS}:prime:ready` — це **сигнал готовності** snapshot-cache (і bootstrap), який використовують процеси/гейти для старту UI.

Важливо: `prime:ready` не гарантує, що всі `{NS}:ohlcv:*` ключі ще живі (див. секцію “stale readiness”).
```

## TTL політика (SSOT у config.json)

```json
{
  "redis": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 6379,
    "db": 1,
    "namespace": "v3_local",
    "allow_env_override": false,
    "ttl_by_tf_s": {
      "60": 1800,
      "180": 1800,
      "300": 3600,
      "900": 14400,
      "1800": 14400,
      "3600": 86400,
      "14400": 172800,
      "86400": 604800
    },
    "tail_n_by_tf_s": {
      "60": 1024,
      "180": 512,
      "300": 512,
      "900": 1024,
      "1800": 512,
      "3600": 512,
      "14400": 256,
      "86400": 128
    }
  }
}

Примітки:

- Поле `redis.namespace` **обов’язкове**.
- Ключ `redis.ns` **заборонений** (викликає fail-fast `redis_ns_key_forbidden`).
```

## Redis підключення (env override)

Параметри host/port/db/namespace можуть бути перекриті env змінними **лише якщо** `redis.allow_env_override=true`:

- `FXCM_REDIS_HOST`
- `FXCM_REDIS_PORT`
- `FXCM_REDIS_DB`

- `FXCM_REDIS_NS`

Якщо `allow_env_override=false`, env-значення **ігноруються** і UDS логує `UDS_REDIS_ENV_OVERRIDE_IGNORED` (і, за наявності, `UDS_REDIS_SPEC_MISMATCH`).

## Мінімум барів для cold-load

UDS використовує поріг `min_coldload_bars_by_tf_s`. Якщо Redis tail коротший за цей поріг, `read_window()` повертає **partial window** з warning `redis_small_tail` + `meta.extensions.partial=true`.

Disk fallback залежить від `ReadPolicy.disk_policy`. У поточному UI server (`/api/bars`) використовується `disk_policy="never"`, тобто disk не є hot-path.

```json
{
  "min_coldload_bars_by_tf_s": {
    "300": 300,
    "900": 200,
    "1800": 150,
    "3600": 100
  }
}
```

UI показує stale/miss через:

- ключ відсутній (miss), або
- now_ms - payload_ts_ms > ttl * 1000 (stale).

## Failure mode: stale readiness (TTL mismatch)

`prime:ready` зазвичай має довший TTL (наприклад 6 годин), ніж snapshot TTL для окремих TF (наприклад TF=300 має TTL 3600s). Через це можливий сценарій:

- `{NS}:prime:ready` ще існує,
- але `{NS}:ohlcv:tail:*` і `{NS}:ohlcv:snap:*` вже протухли.

Це має трактуватись як **degraded-but-loud**: UI/read-path має показати warning(и) на кшталт `redis_empty`/`redis_ttl_invalid`/`redis_fallback:*`, а не “мовчки” повертати порожній графік.

## Поведінка (без календар-гейту)

- Якщо бар прийшов: пишемо SSOT -> оновлюємо snapshots.
- Якщо бар не прийшов: нічого не публікуємо.
- Якщо тики є, а барів нема: лог code=bar_missing_with_ticks (rate-limit).
- Якщо тики зникли: calendar_open використовується як пояснення.

UI політики читання:

- `/api/bars`: query params `prefer_redis/force_disk` **ігноруються** (warn-only). `prefer_redis` виставляється сервером як внутрішня policy для final cold-load; disk read контролюється `disk_policy` (у UI server зараз `never`).
- `/api/updates` читає updates bus (list+seq) з Redis; disk не є hot-path.
- /api/bars для preview TF (60/180) читає ізольований preview keyspace (curr/tail/updates).

## Status snapshot (людський)

```json
{
  "v": 1,
  "boot_id": "20260205T205720Z",
  "now_ms": 1770302461023,
  "redis": {"ok": true},
  "bars": {"last_final_close_ms": 1770302459999},
  "gaps": {
    "m5_backlog_bars": 120,
    "m5_gap_from_ms": 1770295200000,
    "m5_gap_to_ms": 1770302400000,
    "policy": "manual_tool_required"
  },
  "cache": {
    "primed": true,
    "prime_partial": false,
    "priming_ts_ms": 1770302461000,
    "primed_counts": {"XAU/USD:300": 512}
  },
  "degraded": [],
  "errors": [],
  "warnings": [],
  "last_error": null
}
```

## Write-through / write-behind

Режим 0 (рекомендований зараз):

- Disk write (final) як є, синхронно.
- Redis write-through одразу після формування бару (last + tail).

Режим 1 (пізніше):

- Write-behind тільки для tail/derived, з bounded queue.
- Переповнення -> degraded-but-loud + forced flush.

## Логи (без метрик)

- REDIS_SNAP_PUT symbol=.. tf_s=.. seq=.. ttl=..
- REDIS_SNAP_MISS symbol=.. tf_s=..
- REDIS_DOWN code=redis_unavailable action=degrade_disk_only
