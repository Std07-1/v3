# Поточний стан проекту (Apr 14, 2026)

## Deployed & running
- Bot: smc_trader_v3, pid 364916, supervisor managed
- TSM (ThesisStateMachine): IDLE/WATCHING/CLOSED states, persisted in directives
- EventJournal: records all events to event_journal.json
- Monitor v2: CHECK 0-4 cycle, every 30s
- Budget guard: emergency cap active

## Problem: Silent Archi
- Root cause: ObservationRouter (Haiku gate) blocks when price far from zone
- Morning briefing disabled (if False guard)
- ADR-034 (Wake Conditions) written, NOT implemented

## Architecture state
- ADR-033 (Dual-Mode): deployed, running
- ADR-034 (Wake Conditions): ADR written, zero code
- v3/ADR-0048 (Platform WakeEngine): ADR written, zero code on platform

## Git
- Local branch: rebuild/mechanical-skeleton (15 commits ahead of master)
- Master frozen: c090017 (freeze: v3.3-pre-rebuild)
- No git on VPS (deploy = scp)

## Next work items
1. Replace Haiku gate with wake conditions (ADR-034)
2. Bot writes wake_conditions to Redis
3. Bot subscribes to wake:notify PubSub
4. Re-enable morning briefing via session_open condition
5. Integration with v3 WakeEngine (ADR-0048, platform side)
