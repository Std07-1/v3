# Third-Party Notices

Цей файл перелічує сторонні залежності та їхні ліцензії.

---

## Open-Source Dependencies (Python)

| Пакет | Версія | Ліцензія | Сумісність |
|-------|--------|----------|------------|
| redis | 5.0.1 | MIT | ✅ Сумісна |
| numpy | 1.21.6 | BSD-3-Clause | ✅ Сумісна |
| pandas | 1.1.5 | BSD-3-Clause | ✅ Сумісна |
| python-dotenv | ≥0.21 | BSD-3-Clause | ✅ Сумісна |
| aiohttp | ≥3.8 | Apache-2.0 | ✅ Сумісна |

## Open-Source Dependencies (npm / UI v4)

| Пакет | Ліцензія | Сумісність |
|-------|----------|------------|
| svelte | MIT | ✅ Сумісна |
| vite | MIT | ✅ Сумісна |
| lightweight-charts | Apache-2.0 | ✅ Сумісна |
| rollup | MIT | ✅ Сумісна |

---

## Proprietary Dependencies

### FXCM ForexConnect SDK (`forexconnect==1.6.43`)

**Ліцензія**: Proprietary / EULA-governed (PyPI metadata: "Other/Proprietary License")

**Цей репозиторій НЕ містить і НЕ розповсюджує ForexConnect SDK.**
Файл `requirements.txt` посилається на зовнішній PyPI-пакет як optional dependency.

**Для використання ForexConnect SDK кожен користувач зобов'язаний самостійно:**

1. Прийняти [FXCM EULA](https://www.fxcm.com/uk/forms/eula/) перед завантаженням/використанням SDK
2. Мати активний FXCM account
3. Дотримуватись умов FXCM EULA щодо використання SDK

**Цей репозиторій не надає жодних прав на FXCM ForexConnect SDK.**
Integration wrapper code (`runtime/ingest/broker/fxcm/provider.py`) є авторським кодом проєкту, а не частиною SDK.

Детальний аналіз: [`docs/compliance/fxcm-sdk-license-review.md`](docs/compliance/fxcm-sdk-license-review.md)

---

> Цей перелік актуальний станом на 2026-03-08. Для юридичних питань зверніться до ліцензій відповідних пакетів.
