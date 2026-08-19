# Implementation Plan: Live Session Hardening

**Document status:** Approved

## Approach

Two slices on the existing live path. Do not add a process, package, or provider.

1. **Issue 1 — unfinished daily prices.** Mark today’s still-open daily row incomplete. Do not run `multi_day_move`, `drawdown_from_high`, or `relative_to_spy` on it. Prove it with a fake clock during regular hours and an Alpaca-shaped today daily bar. After the clock is closed, those rules may still run on the finished day.
2. **Issue 2 — session-open gap.** Add `session_gap`: last finished regular close vs today’s 09:30 open. Run it once per session. Prove crossings, non-crossings, weekend, a late morning start, and restart quiet.

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
