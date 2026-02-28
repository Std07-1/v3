# SMC Engine (core/smc/) — MVP

- Pure logic only (NO I/O)
- Типи: SmcZone, SmcSwing, SmcLevel, SmcData
- Відповідає ADR-0024, UI types.ts
- Для XAU/USD, TF=60..86400

## Контракти

- SmcZone: order block, FVG, liquidity
- SmcSwing: high/low structure
- SmcLevel: key price levels
- SmcData: aggregate for one bar

## Інваріанти

- S0: pure logic, NO I/O
- S1: не пише в UDS
- S2: deterministic
- S3: zone IDs deterministic
- S4: performance < max_compute_ms
- S5: config SSOT
- S6: wire format = UI types

---

## TODO MVP

- [x] types.py: типи
- [ ] engine.py: pure logic
- [ ] tests/
