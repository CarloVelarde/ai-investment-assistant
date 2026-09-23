# Implementation Plan: Full-loop Hardening

**Document status:** Draft

**Execution status:** Not started; implementation of spec 010 is prerequisite

[Specification](SPEC.md) defines scenarios and completion. [Tasks](TASKS.md) tracks
evidence. Reuse the existing vertical slices; do not add a second application or a
generic replay framework just for the final milestone.

## 1. Audit final baseline and reuse tests

After spec 010, map S01–17 to current tests and identify missing integration edges.
Existing useful suites include market-history scenarios, session lifecycle, news
live/ingest, research scenarios/live/runner, event recovery, and the new delivery/
budget suites. Name actual tests in VALIDATION.md; module names alone are not proof.
Preserve intentionally historical offline negative-phrase fixtures.

Use one reusable test driver only where multiple scenarios need the same real
`run_live_session`, file-backed SQLite, controllable clock, market/news fakes,
structured fake model, and Discord transport. Reuse packaged bars and introduce
small sanitized news/research responses. Inject crashes and failures at boundaries,
not sleeps or provider outages. A fake successful receipt must pass the same
adapter/result validation as a real receipt.

## 2. Close deterministic gaps

Prioritize highest-risk paths: lost response/receipt, stale update, restart accounting,
long provider waits, delivery backlog blocking market recovery, and bidirectional
market/news cases. Record calls and persisted state before and after database reopen.
Pair normal-path end-to-end cases with focused boundary fault tests rather than
multiplying every scenario by every possible transport failure.

Assert realistic outcomes. Compatible news/market ordering follows existing grouping;
unclear news need not merge. A budget deferral is not a failed report; an uncertain
send is not successful notification; a synchronous pause is not uninterrupted ingest.
Cover cross-close, early-close/holiday inputs, UTC day boundary, and changed
configuration. For uncertain Discord delivery, test the 15-minute
boundary, longer provider waits, one resend with the same logical id, restart after
allowance consumption, and hold after any resend failure. Distinguish the accepted
possibility of one duplicate from unintended additional automatic sends.

Fix only reproduced bugs or acceptance gaps. Update the owning feature spec/decision
before intentional policy changes. Each correction brings a regression test and a
short explanation of before/after public behavior.

## 3. Prepare a concrete live-trial procedure

Create a reviewable run sheet in VALIDATION.md before any live action: duration,
watchlist/feed, isolated database, target test channel, test input source, maximum
model starts/classifications/spend, maximum messages, expected observations, and
stop conditions. Confirm the final price policy and actual account capabilities.
Use existing app/adapter entry points; a small bounded manual harness is acceptable
only if it reuses them and does not become another live pipeline.

Perform all automated checks first. Real messages and paid calls require explicit
operator authorization for that concrete trial. Keep the pending live gate visible
if authorization/access/time is unavailable; complete independent replay/runbook work.

## 4. Observe and review

Execute the minimum live evidence in SPEC; save sanitized results, not credentials
or raw account/provider dumps. Record observed loop pauses, recovery, actual receipt,
restart behavior, source validity, and estimates versus returned usage. Stop at any
trial limit or unsafe/inconsistent state. Do not test 429 by flooding providers.

Review market-only/news-only/combined reports for understandable triggers, source
support, competing explanations, uncertainty, and useful posture. Explicitly label
synthetic events and tool-access gaps. Convert any reproducible failure to an offline
regression before rerunning the relevant live step.

## 5. Close the evidence gate

Write operator guidance and tested backup/restore steps using a temporary database.
Record growth during the bounded trial and state that automatic retention is deferred.
Recheck docs against code, including historical versus current claims. Link each
product criterion to replay/live/review evidence and each limitation to its practical
impact. Do not expand infrastructure to hide an unresolved reliability issue.

Run all repository checks on the final revision and required Ubuntu CI. Record local
host/Python and any additional platform checks; do not infer native Windows support.
Mark spec and milestone complete only when every AC passes, including the live gate.
