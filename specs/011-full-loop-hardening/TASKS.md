# Tasks: Full-loop Hardening

**Document status:** Draft

**Execution status:** Not started — waiting for spec 010 implementation

[Behavior](SPEC.md) · [Approach](PLAN.md)

## Planning

- [x] Define Milestone 8 scope, scenario matrix, product-success mapping, separate
  live gate, and dependencies during the spec 010 documentation review.

## Execution

- [ ] 1. Confirm all spec 010 acceptance criteria pass. Audit the final code/test
  baseline and create VALIDATION.md with revision, environment, S01–17 coverage,
  missing integration cases, and explicitly pending live evidence.
- [ ] 2. Complete market/episode/correlation replay (S01–04, S06, S08–09), including
  rises, drops, gaps, finished daily rules, both compatible input orders, and
  nonstandard-session limitations. Use existing policies without speculative tuning.
- [ ] 3. Complete news/research/evidence replay (S05–07, S10–12, S16), including
  positive/negative/unclear news, rejected inputs, missing keys/tools, invalid
  citations/output, and accounting for failed/stale/unknown work.
- [ ] 4. Complete final-loop delivery/restart/migration/budget replay (S09, S12–15,
  S17). Prove fairness and minute recovery after external work; operator commands
  resolve held work and settled replay stays quiet. Verify one automatic uncertain
  resend after 15 minutes, persistent deadline/allowance, longer provider waits,
  recognizable possible duplicates, and no further automatic attempts on failure.
- [ ] 5. Turn discovered failures into regressions and minimal fixes. Record affected
  criteria, changed owning docs, before/after observations, and retest evidence.
- [ ] 6. Prepare the bounded live run sheet after automated checks pass. Specify
  duration, symbols, test channel/database, input source, costs/calls/messages, and
  stop conditions. Obtain concrete trial authorization before paid calls/messages.
- [ ] 7. Run final-version real monitoring/classifier/research/tool/Discord checks
  and restart verification. Record source/alert review and actual observations;
  label synthetic inputs, missing capabilities, and pending evidence honestly.
- [ ] 8. Finish operator start/status/recovery/backup/stop instructions and the
  limitations record. Verify backup/restore using a temporary database; do not
  mutate private runtime data. Review all 12 product success criteria against evidence.
- [ ] 9. Run final repository checks and required CI; reconcile current docs and
  statuses. Mark Milestone 8/MVP complete only after every acceptance criterion passes.

## Acceptance coverage

| Criterion | Tasks | Required evidence |
| --- | --- | --- |
| AC-01 | 1–4 | Named S01–17 tests and repeat-replay counts |
| AC-02 | 3–5 | Fault/restart/accounting state and regressions |
| AC-03 | 6–7 | Authorized bounded live run and restart observations |
| AC-04 | 3, 7 | Cited report and Discord alert review |
| AC-05 | 4, 8 | Working commands/runbook, backup test, secret checks |
| AC-06 | 5, 7–8 | Product-criteria evidence map and unresolved-issue disposition |
| AC-07 | 9 | Final check/CI output and consistent document status |

## Current evidence

No Milestone 8 execution, integrated live trial, or readiness claim is recorded yet.
The specification review does not satisfy any acceptance criterion above.
