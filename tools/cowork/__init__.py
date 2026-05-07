"""`tools.cowork` — cowork-side runtime helpers (slice cowork.004+).

Currently houses:
    * `event_watcher` — polls TDA signal journal + bias snapshot and
      writes `event_flag.json` for `cowork.runner.should_run_now()`
      to consume as a secondary trigger.
"""
