# ADR-0014: UDS Split-Brain Resilience

- **Статус**: Implemented
- **Дата оновлення**: 2026-03-01

## Context

During the audit and testing process (TRI-20260225-002), a split-brain vulnerability was identified in the `UnifiedDataStore.commit_final_bar` method. When a `CandleBar` is committed, the system writes it to three destinations:

1. `DiskLayer` (SSOT JSONL)
2. `RedisLayer` (Redis Snapshot)
3. `UpdatesBus` (Redis Pub/Sub)

Currently, the `commit_final_bar` method dictates that `ok = ssot_written`. If writing to the disk succeeds but writing to Redis or the UpdatesBus fails, `ok` is set to `True`. Because `ok = True`, the internal `_wm_by_key` (watermark) is advanced, and the ingested bar is stored in the `RamLayer`.
However, downstream components (like the UI or any system relying on Redis) will never receive this bar because the Redis write failed. The ingester will continue processing new bars, leaving the overall system in a split-brain state where the Disk/RAM state diverges from the Redis state.

## Decision Options

### Option A: Hard Failure (Block Ingester)

If either the Redis Snapshot or UpdatesBus write fails, `ok` must be set to `False` and the watermark MUST NOT be advanced. This forces the Ingester to halt and continually retry, ensuring no data skew between storage layers.
**Pros:** Guarantees absolute consistency.
**Cons:** A temporary Redis blip completely halts the ingestion pipeline.

### Option B: Degraded-But-Loud (Continue with Alerting)

If Redis fails, `ok` remains `True` (as Disk is the SSOT), and the watermark advances. However, the system must immediately and loudly flag the state as `degraded` and emit high-priority metrics/alerts indicating a split-brain scenario. The system will continue to ingest to Disk but will be out-of-sync with Redis until a manual or automated remediation (like a restart/replay) occurs.
**Pros:** Ingestion to the ultimate SSOT (Disk) is uninterrupted.
**Cons:** UI and downstream consumers will lack data or show stale data until recovery.

### Option C: Asynchronous Retry/Reconciliation

Similar to Option B, but the UDS internally queues failed Redis writes and attempts to replay them asynchronously.
**Pros:** Self-healing.
**Cons:** Significant architectural complexity added to UDS.

## Decision

**Option B: Degraded-But-Loud (Continue with Alerting)** is adopted.
The system will continue to prioritize the DiskLayer (SSOT) and emit a degraded warning channel (`degraded_reason:*`) during split-brain situations. Logging will capture explicit `[DEGRADED]` tags with required formats (`head:3 ... tail:3 ...`) for debugging.
Prometheus metrics collected (`ai_one_uds_split_brain_active`, etc.) mark the split-brain state prominently. A manual recovery process (`UnifiedDataStore.mark_split_brain_reconciled()`) handles the restoration of healthy state after manual replay resolves the gap.

## Consequences

- Ingestion pipelines are protected against single points of failure (Redis downtime does not stop disk capture).
- Monitoring stack explicitly knows when data skew is happening and can trigger automated resolution or ops manual recovery.

## Invariants & Design Constraints

- We cannot "rollback" an appended line to a JSONL file easily.
- Disk is the final SSOT.

## Related Items

- [TRI-20260225-002] UDS commit не атомарний
