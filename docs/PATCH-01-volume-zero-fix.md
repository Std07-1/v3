# PATCH-01: ui_v4 · volume preview "0" axis label — fix

## Metadata

| Field      | Value                                              |
| ---------- | -------------------------------------------------- |
| ID         | PATCH-01                                           |
| Area       | ui_v4 / chart                                      |
| Initiative | tp3-quality-of-life                                |
| Scope      | mini (1 LOC, 1 file)                               |
| Status     | READY                                              |
| Depends on | — (independent of ADR-0066)                        |

---

## Symptom

Volume histogram (bottom of chart, 10.5% of canvas height) renders a `0`
label on the right edge price scale during live preview ticks. Visible in
XAU/USD M30 screenshots — bottom-right of volume area shows a persistent
`0` while the actual volume bars on the left have meaningful heights.

The histogram bars themselves render correctly. The issue is **the axis
label only**.

## Root cause

**Single root cause** — in
[`ui_v4/src/chart/engine.ts:223-227`](../ui_v4/src/chart/engine.ts#L223-L227)
the volume `HistogramSeries` is created without `lastValueVisible: false`,
so LWC defaults to `lastValueVisible: true` and renders the last data
point's value on the right edge of the price scale.

The preview/forming candle for the current bar carries `v = 0` from two
server paths:

1. **Tick-relay forming candles**
   ([`ws_server.py:1040-1042`](../runtime/ws/ws_server.py#L1040-L1042))
   explicitly emit `"v": 0, "complete": false` — by design, since
   tick-level volume aggregation is not synthesized in this path.
2. **Main delta path**
   ([`candle_map.py:155-159`](../runtime/ws/candle_map.py#L155-L159))
   maps broker bars; on preview reads from broker the volume may
   legitimately be 0 if the broker hasn't aggregated ticks for the
   in-progress bar yet (FXCM tick-volume contract).

When `v=0` is the latest value in the histogram series, LWC renders `0`
as the right-edge axis label. That label is the visible noise.

The histogram **bars themselves** at `value=0` render with zero pixel
height — invisible. Only the axis label is annoying. Therefore the fix
is purely a render-config change, not a data-path change.

## Fix

Single line: set `lastValueVisible: false` on the volume histogram options.

Reasoning:

- Volume on a chart is read by the trader as **relative bar height**
  (current bar vs neighbours), not as an absolute number on the right axis.
- The right-edge value adds no information that the histogram bars
  themselves don't already convey.
- This is the standard configuration for decorative volume series in
  mature trading UIs.

The original PATCH-01 draft also proposed skipping `volumeSeries.update()`
for `bar.complete === false`. Investigation showed:

- The `Candle` type in ui_v4
  ([types.ts:8-15](../ui_v4/src/types.ts#L8-L15)) does **not declare
  `complete`**.
- The wire format is **inconsistent**: tick-relay candles carry
  `complete: false`, regular delta candles do not (the field is stripped
  by `map_bar_to_candle_v4`).
- For broker preview candles with legitimate non-zero in-progress volume,
  skipping the update would **regress** real-time volume display.

Therefore the skip-update branch is dropped from this patch. Only the
axis-label config flag is changed.

---

## Diff

Single change in `ui_v4/src/chart/engine.ts`, in the `HistogramSeries`
constructor block:

```diff
// ui_v4/src/chart/engine.ts (~line 224)

    // ─── Volume histogram (V3 parity: chart_adapter_lite.js:221-243) ───
    this.volumeSeries = this.chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: '',
+     lastValueVisible: false,    // PATCH-01: hide preview "0" axis label
    });
```

That is the entire patch. No other file touched.

---

## Scope

| Field        | Value                          |
| ------------ | ------------------------------ |
| LOC          | 1 added                        |
| Files        | 1 (`ui_v4/src/chart/engine.ts`) |
| Patch class  | mini                           |
| Verify level | mini (≥1 check)                |

---

## Verify

1. **Visual check (primary)** — open `localhost:8000` (or
   `aione-smc.com`) on M30 XAU/USD. Look at the right edge of the volume
   area at the bottom of the chart. Before patch: `0` label visible.
   After patch: no label.
2. **Bar close test** — wait for an M30 close (max 30 min). The volume
   bar finalizes with the real aggregated value. The bar continues
   rendering at the correct height. No axis label appears.
3. **TypeScript build** — `cd ui_v4 && npm run build` succeeds with no
   new errors. (`lastValueVisible` is a documented LWC v5 option, no
   type mismatch.)

---

## Risks

| Risk                                          | Severity | Mitigation                                                                 |
| --------------------------------------------- | -------- | -------------------------------------------------------------------------- |
| Loss of "exact volume number" reading         | None     | Volume is decorative on this chart — read by relative height. Crosshair tooltip already shows exact volume on hover (engine.ts:267-268). |
| Regression on other LWC features              | None     | `lastValueVisible` is series-scoped; affects only the volume series axis label. |
| Future relative-volume display blocked        | None     | Not blocked — relative volume is a separate ADR; this patch is a clean substrate. |

---

## Changelog entry

```json
{
  "id": "PATCH-01",
  "ts": "<auto>",
  "area": "ui_v4",
  "initiative": "tp3-quality-of-life",
  "status": "applied",
  "scope": "mini",
  "files": ["ui_v4/src/chart/engine.ts"],
  "summary": "Volume histogram: hide right-edge '0' axis label on preview",
  "details": "Set lastValueVisible: false on the HistogramSeries options. The axis label rendered '0' during preview ticks because the in-progress bar carries v=0 from tick-relay path and (sometimes) from broker preview. Bars themselves render correctly at 0px height — only the axis label was visible noise.",
  "why": "Volume on this chart is read by relative bar height; the absolute right-edge label provides no information and creates persistent visual noise during live ticks. Crosshair tooltip already exposes exact volume on hover.",
  "goal": "Clean volume rail with no phantom zero label during preview ticks",
  "risks": "None — volume is decorative; SMC analysis does not consume absolute volume from this UI series",
  "rollback_steps": [
    "Remove 'lastValueVisible: false' line from HistogramSeries options in ui_v4/src/chart/engine.ts"
  ],
  "notes": "Future: relative-volume highlight (option D from RECON — current-bar volume / N-bar SMA, only color anomaly bars) is a separate ADR. This patch is the clean substrate for it."
}
```

---

## Exit gates

After apply:

```sh
python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json
```

This patch touches only `ui_v4/src/chart/engine.ts` (UI render config) —
no critical gates intersect this surface. Expected status quo:

- 5 known FAILs (`preview_not_on_disk`, `preview_plane/api_splitbrain`,
  `ui_live_candle_plane/overlay_anchor_sentinel`,
  `htf_available/allowlist_htf`, `unexpected_gap_budget`) remain FAIL —
  pre-existing, documented exceptions.
- No new FAILs introduced.

If new FAILs appear: rollback immediately, file an issue, do not advance.

---

## Rollback

Single line removal:

```sh
git revert <PATCH-01-commit-sha>
```

No data migration. No dependencies. No coordinated rollback with any
other PATCH or ADR.

Estimated rollback time: 30 seconds (revert + redeploy).

---

## Notes

This patch is **independent of ADR-0066** (Visual Identity System). It
can be applied:

- Immediately, before ADR-0066 is even drafted
- In parallel with PATCH 02–06 of ADR-0066
- After ADR-0066 is fully implemented

There is no ordering dependency. The volume axis label is purely a chart
rendering issue, unrelated to identity, palette, or chrome layout.

### Why simpler than original draft

The original PATCH-01 proposed two changes (A — `lastValueVisible:false`,
plus C — skip preview volume update via `bar.complete`). RECON revealed:

1. The `0` axis label is the only visible symptom — bars at `value=0`
   render at 0px height, invisible.
2. The wire format is inconsistent (tick-relay carries `complete`, main
   delta path strips it via `map_bar_to_candle_v4`), so a `bar.complete`
   check would only filter some preview paths.
3. Skipping volume updates for legitimately-non-zero broker preview
   would regress real-time volume display.

So C is dropped, A alone covers the entire visible symptom. 1 LOC
instead of 6.
