# Tasks: Offline Walking Skeleton

**Document status:** Approved

**Execution status:** Not started

## Tasks

- [ ] 1. Add both fixture pairs, minimal records, fixture readers, and normalization with focused tests.
- [ ] 2. Add injected current time and a fixed test implementation; prove core logic does not read the real clock.
- [ ] 3. Implement the inclusive market rule with passing, failing, and boundary tests.
- [ ] 4. Implement fixed-phrase news filtering with passing and failing headline, summary, and case tests.
- [ ] 5. Correlate signals into exactly one event; test normalized-symbol matching and times inside, at, and outside 60 minutes.
- [ ] 6. Add the labeled fake report and marked console notification; test research gating and notification count and content.
- [ ] 7. Wire the pipeline into the existing console entry point without changing its configuration or logging bootstrap.
- [ ] 8. Add the triggering end-to-end test for one event, report, and notification.
- [ ] 9. Add the non-triggering end-to-end test for no event, research, or notification.
- [ ] 10. Confirm no external dependency or runtime path was added, then run all repository checks.

## Acceptance coverage

| Criteria | Tasks |
| --- | --- |
| AC-01–AC-02 | 1, 7–9 |
| AC-03–AC-04 | 3–5, 8–9 |
| AC-05–AC-07 | 6, 8–9 |
| AC-08 | 2, 8–9 |
| AC-09–AC-10 | 10 |

Add tests with each behavior and mark a task complete only after its validation passes. Record any required scope or architecture change in its owning document first.
