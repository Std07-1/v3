# ADR-0015: Calendar Pause / Flat Bar Interpretation

## Context
During QA (TRI-20260225-003) regarding `derive_engine.py`, it was identified that `derive_bar` aggregates lower timeframe bars (like M1) into higher timeframe bars (like M5) but uses `calendar_pause_flat` bars to bypass missing data. 

Specifically, if a 5-minute bucket requires 5 M1 bars to create a single complete M5 bar, and 2 of those minutes fall under a `calendar_pause`, the core `has_range` check allows the creation of a "complete" M5 bar out of only 3 M1 bars. The resulting M5 bar receives `complete=True`, `src=derived`, and an extension marker `partial_calendar_pause=True`.

The question emerges: **Is a bar derived from a partial slice of trading minutes truly "complete" in the context of our architecture?**

## Decision Options

### Option A: Retain Current Behavior (Status Quo)
A bar remains `complete=True` if the bucket time has elapsed and all *expected trading minutes* for that instrument are present, even if some were skipped due to `calendar_pause`.
**Pros:** M5/M15/H1 bars will cleanly form out of shorter active sessions without triggering missing-data cascading failures.
**Cons:** We violate the strict mathematical rule that `1 M5 = 5 M1 bars`. A `complete` M5 bar might represent only 2 minutes of real trading data.

### Option B: Partial Degradation
Modify `derive_bar` or `aggregate_bars` to return `complete=False` (or reject derivation entirely) if `calendar_pause_count > 0`. 
**Pros:** Strict mathematical parity. A completed M5 bar is guaranteed to contain 5 real M1 bars.
**Cons:** Breaks charts and technical analysis for illiquid instruments or partial sessions (like holidays), resulting in permanent gaps on higher timeframes because those buckets will never satisfy the strict requirement.

### Option C: Explicit "Incomplete but Final" State
Introduce a new designation or clarify the SSOT meaning of `complete`. `complete` simply means "bucket time has elapsed and no more data will arrive for this period", not "bucket has 100% volume". The current extensions (`partial_calendar_pause`) remain the primary way for downstream consumers to understand the bar's nature.
**Pros:** Clarifies semantics without breaking code.

## Decision
**Option C: Explicit "Incomplete but Final" State** is adopted.
The marker `complete=True` designates that no more data will arrive for this period, rather than representing 100% volume/time integrity. The extensions dictionary (e.g. `partial_calendar_pause=True`) must be utilized by downstream consumers to infer if it is a full slice of trading activity or an expedited compilation bypassing static pauses.

## Consequences
- No breaking changes injected into `derive_engine.py` or charting modules.
- Clarified semantics regarding "completeness" in cases of partial trading sessions.

## Invariants & Design Constraints
- `derive_engine` acts as the SSOT for missing HTF data.

## Related Items
- [TRI-20260225-003] derive_bar може зібрати HTF з <N trading-barів
