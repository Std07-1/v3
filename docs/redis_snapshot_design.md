# Redis snapshots (мінімальний дизайн)

Цей документ описує мінімальний дизайн Redis snapshot cache без pub/sub. Redis не є SSOT. SSOT залишається на диску (JSONL).

## Цілі

- Прискорити cold-start UI (читання останнього стану за 10-50мс).
- Зменшити залежність UI від disk scan або rebuild.
- Зробити stale/ok видимими через TTL + payload_ts_ms.
- Не змінювати SSOT і логіку ingest.
- Не допустити «малого tail» у cold-load: при недостатній кількості барів UI переходить на диск.

## Не цілі

- Redis не прискорює появу final барів у ingest.
- Немає stream/commands/pub-sub (окремий етап).
- Немає метрик (лише loud-логи).

## Ключі (snapshot-only)

- {NS}:ohlcv:snap:{symbol}:{tf_s}
- {NS}:ohlcv:tail:{symbol}:{tf_s}
- {NS}:status:snapshot
- (опц.) {NS}:price:last:{symbol}

## Контракти

### ohlcv:snap:{symbol}:{tf_s}

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

- close_ms end-incl.
- якщо complete=true, то event_ts_ms == bar.close_ms.
- seq монотонний для (symbol, tf_s) або глобальний, але стабільний.
- payload_ts_ms = час запису в Redis.

### ohlcv:tail:{symbol}:{tf_s}

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
```

## TTL політика (SSOT у config.json)

```json
{
  "redis": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 6379,
    "db": 0,
    "ns": "v3",
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
```

## Redis підключення (env override)

Параметри host/port/db/ns можуть бути перекриті env змінними, щоб ізолювати local/prod:

- `FXCM_REDIS_HOST`
- `FXCM_REDIS_PORT`
- `FXCM_REDIS_DB`
- `FXCM_REDIS_NS`

Якщо env не задані, використовується `redis.*` із config.json.

## Мінімум барів для cold-load

UI використовує поріг min_coldload_bars_by_tf_s. Якщо Redis tail коротший за цей поріг, /api/bars переходить на диск.

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

## Поведінка (без календар-гейту)

- Якщо бар прийшов: пишемо SSOT -> оновлюємо snapshots.
- Якщо бар не прийшов: нічого не публікуємо.
- Якщо тики є, а барів нема: лог code=bar_missing_with_ticks (rate-limit).
- Якщо тики зникли: calendar_open використовується як пояснення.

UI політики читання:

- /api/bars із prefer_redis читає Redis tail/snap, але при малому tail або miss переходить на disk.
- /api/updates читає Redis updates bus (list+seq); disk лишається тільки для recovery при redis_down (degraded).
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
