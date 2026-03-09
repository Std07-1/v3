# LIC-07 — FXCM ForexConnect SDK (forexconnect==1.6.43)

- **Дата**: 2026-03-08
- **Статус**: Reviewed — Proprietary / EULA-governed
- **Ризик**: S0 (найвищий — vendor proprietary dependency)

---

## 1. Факти

| Аспект | Деталі |
|--------|--------|
| Пакет | `forexconnect==1.6.43` (PyPI) |
| PyPI License metadata | "Other/Proprietary License" |
| Офіційна EULA | Вимагає sign EULA перед використанням; "for personal use and abides by our EULA" |
| Визначення "Software" в EULA | Прямо включає API's |
| Тип прав | Limited, revocable, non-sublicenseable, non-exclusive, non-transferable |
| Дозволене використання | Personal, non-commercial use (якщо інше не погоджено письмово) |

## 2. Заборони за публічною EULA

| Дія | Дозволено? | Підстава |
|-----|-----------|----------|
| Redistribute SDK | **Ні** | EULA прямо забороняє redistribution |
| Create derivative works of SDK | **Ні** | EULA прямо забороняє derivative works |
| Sublicense rights to downstream users | **Ні** | Non-sublicenseable, non-transferable |
| Vendor SDK binaries/wheels/DLL у репо | **Ні** | = redistribution |
| Комерційне / командне / публічне використання | **Лише з окремою письмовою домовленістю з FXCM** | EULA допускає інший режим лише за окремою письмовою згодою |

## 3. Статус у цьому репозиторії

| Перевірка | Результат |
|-----------|----------|
| SDK binaries (.dll, .so, .pyd, .whl) у репо | ✅ **Немає** — чисто |
| SDK headers (.h) у репо | ✅ **Немає** |
| SDK documentation у репо | ✅ **Немає** |
| Integration wrapper code | ✅ `runtime/ingest/broker/fxcm/provider.py` — лише wrapper (~300 LOC), не SDK source |
| requirements.txt | ⚠️ `forexconnect==1.6.43` — reference to proprietary dependency, **не redistributes сам SDK** |

## 4. Оцінка requirements.txt

`requirements.txt` посилається на зовнішню proprietary-залежність — це **не те
саме**, що розповсюджувати SDK у репо. Але це **не передає жодних прав**
downstream-користувачам: кожен downstream-користувач все одно отримує пакет,
який на PyPI позначений як "Other/Proprietary License". Тобто requirements.txt
— це **reference to proprietary dependency**, а не легалізація її включення чи
перепоширення.

## 5. Конфлікт у джерелах

Історичний PDF документації FXCM API містить розділ "License — MIT License". Однак це суперечить:

- Нинішній офіційній EULA на сайті FXCM
- Вимозі підписати EULA в офіційному репо
- Proprietary-метаданим пакета на PyPI

**Консервативна позиція**: трактувати `forexconnect` як proprietary / EULA-governed, доки FXCM письмово не підтвердить інше саме для SDK і саме для конкретного способу розповсюдження.

## 6. Політика для цього репозиторію

1. **НЕ вендорити** ForexConnect SDK, wheel, DLL/SO, headers, SDK docs у репозиторій.
2. Зберігати інтеграцію як **optional proprietary dependency**.
3. Явно зазначити, що користувач має самостійно:
   - Отримати SDK / прийняти FXCM EULA
   - Мати FXCM account
4. Репозиторій **не надає прав** на сам SDK.
5. Integration wrapper (`provider.py`) — це авторський код проєкту, не derivative work of SDK.

## 7. Open Action

Якщо потрібне redistribution, team/commercial use, або право включати SDK в source-available distribution:

- Потрібен **written permission / separate agreement** від FXCM
- Контакт: `api@fxcm.com`
- EULA допускає інший режим лише *"unless ... agreed otherwise in writing"*

Commercial, team, hosted, or redistributed use requires separate written permission from FXCM.

## 8. Дисклеймер

> Цей документ — compliance reading published texts, **не юридична консультація**.
> Для офіційного юридичного висновку зверніться до кваліфікованого юриста.
