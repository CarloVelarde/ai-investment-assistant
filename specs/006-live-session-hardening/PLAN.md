# Implementation Plan: Live Session Hardening

**Document status:** Approved

## Approach

Two slices on the existing live path. Do not add a process, package, or provider.

1. **Stop unfinished dailies from emitting.** Fix completeness and the emit predicate. Prove it with a fake open-session clock and an Alpaca-shaped today `1Day` bar.
2. **Add `session_gap`.** New rule + window on the existing signal contract. Run it once per session when prior close and today’s open are known. Prove crossings, non-crossings, weekend, morning start, and restart quiet.

Keep Milestone 3 fast/daily numbers. Keep the stock socket as it is.

## Key decisions

- Completeness: a `1Day` bar is incomplete when `end_at > now` (that session has not closed). `process_market_bar` already skips incomplete bars for evaluation.
- Emit predicate for replay: `cutoff <= bar.end_at <= now`, not only `end_at >= cutoff`.
- Do not quiet-replay an incomplete daily into `last_emitted_importance`.
- Gap baseline = last completed `1Day` close. Observed = `open` of the first regular-session `1Min` bar (09:30 ET). Never today’s REST daily.
- Trigger: after backfill if that minute exists; else on first regular-session ingest that day.
- Thresholds 3% / 5% / 8%, rearm 1.5%, no volume dampening.
- Same event manager. Same fake research / console notify.

## Data flow

```
startup backfill
  → persist history
  → mark/omit in-progress today 1Day
  → quiet-replay completed older bars
  → emit only bars with cutoff <= end_at <= now
  → if today’s first regular minute exists: evaluate session_gap
open socket
  → each completed minute: fast detector (unchanged)
  → first regular minute of today: session_gap if not already done
after close
  → completed 1Day bars → existing daily detector
```

## Validation

- Fake clock open vs closed. Fake REST pages. No network.
- AC-01 / AC-03: open-session today daily that would be HIGH or a 5-day vs SPY cross → no event. Second fetch with a moved close still no daily event.
- AC-02: advance clock past 16:00 ET → completed today daily may emit.
- AC-04–AC-06: gap fixtures (480 vs 450, +2%, −3%, Friday→Monday, start at 10:25 with 09:30 already stored, restart).
- AC-07: existing fast/daily tests still pass; milestone scope test still forbids news/Discord/workers.

## Tradeoffs

- Same 3/5/8 ladder as the hour rule will fire on jumpy names (AMD/TSLA). That is accepted until live evidence says to raise it.
- Missing 09:30 IEX minute uses the next regular minute’s open. Slightly late, still a gap, not a 1-hour move.
- We do not add same-day open-to-close here. That is a different question.
