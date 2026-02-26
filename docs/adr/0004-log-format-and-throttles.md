# ADR-0004: Формат лог-рядків і throttle антиспам

> **Статус**: IMPLEMENTED  
> **Дата**: 2026-02-22  
> **Контекст**: Log-triage (aione_top, UDS) — уніфікація споживання логів і зменшення спаму в логах.  
> **Навігація**: [docs/index.md](index.md)

---

## 1. Контекст

- **aione_top** парсить `logs/*.log` для панелі "Recent Events" (WARNING/ERROR + ключові події). Супервізор пише у `logs/<label>.err.log` та `logs/<label>.out.log`; формат рядка в більшості процесів — `YYYY-MM-DD HH:MM:SS,NNN | LEVEL | msg`, у **ws_server** — стандартний Python: `%(asctime)s %(levelname)s %(name)s: %(message)s` (без ` | `).
- **UDS** при warmup і при кожному commit_final_bar drop виводив кожен випадок окремим рядком, що призводило до спаму (десятки/сотні рядків за хвилину).

## 2. Рішення

### 2.1 Контракт формату лог-рядка (aione_top)

aione_top приймає **два** формати (по черзі):

1. **Канонічний**: `YYYY-MM-DD HH:MM:SS,NNN | LEVEL | message`  
   - Використовується: connector, m1_poller, tick_*, ui (при логуванні у файли з форматом ` | LEVEL | `).

2. **Альтернативний**: `YYYY-MM-DD HH:MM:SS,NNN LEVEL logger_name: message`  
   - Відповідає стандартному `logging` (наприклад ws_server з `basicConfig`).  
   - Регулярний вираз: після мілісекунд — пробіли, LEVEL, пробіли, `logger_name:`, пробіли, message.

Зміна контракту: раніше приймався лише формат 1; тепер приймаються обидва. Існуючі логери з форматом 1 не змінюються.

### 2.2 Glob для log-файлів (aione_top)

- Основний пошук: `logs/*.log`.
- Якщо результат порожній — fallback: `logs/*.err.log` та `logs/*.out.log` (файли супервізора).
- Рекомендація: запускати aione_top з **кореня проєкту** (де є каталог `logs/`).

### 2.3 Throttle в UDS (антиспам)

| Джерело | Було | Стало |
|--------|------|--------|
| **commit_final_bar drop** (stale/duplicate) | Один WARNING на кожен дроп | Один WARNING раз на 30 с з підсумком: `stale=N duplicate=M example symbol=... tf_s=... open_ms=... wm_open_ms=...` |
| **виправлено геометрію** (_log_geom_fix) | Один WARNING на кожне виправлення | Один WARNING раз на 30 с: `total=N example source=... tf_s=... sorted=... dedup_dropped=...` |

Метрики (OBS `inc_writer_drop`, `inc_uds_geom_fix`) залишаються без змін — кожна подія враховується; зменшується лише кількість рядків у логах.

## 3. Інваріанти

- Поведінка UDS (watermark, commit, geom fix) не змінюється; змінюється лише частота та формат лог-рядків.
- aione_top лише розширює прийнятні формати та glob; існуючі джерела логів не зобов’язані змінювати формат.

## 4. Файли змін

- `aione_top/collectors.py`: `_log_files_glob()`, `_LOG_LINE_ALT_RE`, використання в `collect_log_tail`.
- `aione_top/display.py`: підказка про cwd та `--log-dir logs --stdio files`.
- `runtime/store/uds.py`: throttle для commit_final_bar drop (вже був), throttle для `_log_geom_fix` (модульні змінні + один рядок / 30 с).

## 5. Довгі прогони (операційна перевірка)

Після 1–2 год роботи платформи перевірити відсутність спаму:

```bash
rg -c "WS_CANDLE_MAP_DROPPED|WS_DELTA_ERROR|SSOT_DROP|виправлено геометрію" logs/
```

- "виправлено геометрію" очікується не частіше ніж раз на 30 с (один підсумковий рядок).
- commit_final_bar drops — не частіше ніж раз на 30 с на процес з UDS writer.
