# Tasks: Offline Walking Skeleton

**Document status:** Approved

**Execution status:** Not started

## Task list

- [ ] 1. Add the two fixture pairs, minimal internal records, fixture readers, and normalization, with focused loading and normalization tests.
- [ ] 2. Add an injectable current-time dependency and fixed test implementation, with a test that proves core behavior does not read the real clock.
- [ ] 3. Implement the inclusive market rule, with passing, failing, and boundary tests.
- [ ] 4. Implement the fixed-phrase news filter, with passing and failing tests that cover case-insensitive matches in both the headline and summary.
- [ ] 5. Correlate signals and create exactly one event, with tests for normalized-symbol matching, symbol mismatch, and inside, at, and outside the 60-minute window.
- [ ] 6. Add fake structured research and a distinct console notifier, with tests proving research is gated by event creation and notification count and content are correct.
- [ ] 7. Wire the synchronous pipeline into the existing console entry point while preserving its configuration and logging bootstrap.
- [ ] 8. Add an end-to-end triggering-scenario test that asserts one event, one fake report, and one event notification.
- [ ] 9. Add an end-to-end non-triggering-scenario test that asserts no event, research call, or event notification.
- [ ] 10. Confirm the implementation adds no external-service, network, secret, or database dependency or runtime path.
- [ ] 11. Run `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy src`, and `uv run pytest`.

## Acceptance coverage

| Acceptance criteria | Covered by tasks |
| --- | --- |
| AC-01–AC-03 | 1, 7–9 |
| AC-04–AC-05 | 3–4, 8–9 |
| AC-06–AC-07 | 5, 8–9 |
| AC-08–AC-10 | 6, 8–9 |
| AC-11 | 2, 8–9 |
| AC-12 | 1, 7, 10–11 |
| AC-13 | 11 |

## Notes

- Complete tasks in order unless a dependency requires otherwise.
- Add tests with the behavior they validate; do not defer them to the end.
- Do not add live APIs, databases, background workers, or real AI integrations.
- Mark a task complete only after its validation passes.
- Record any required behavior or architecture change in its authoritative document before implementing it.
