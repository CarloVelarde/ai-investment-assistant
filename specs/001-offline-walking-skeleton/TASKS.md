# Tasks: Offline Walking Skeleton

**Status:** Not started

## Task list

- [ ] 1. Define the minimal internal records for market data, news data, signals, events, and research reports.
- [ ] 2. Add a controllable clock with a fixed implementation for tests.
- [ ] 3. Add local JSON fixtures for one triggering and one non-triggering scenario.
- [ ] 4. Add fake market and news providers that load the fixtures.
- [ ] 5. Normalize raw market and news fixture data into internal records.
- [ ] 6. Implement the deterministic market rule.
- [ ] 7. Implement the deterministic news filter.
- [ ] 8. Correlate qualifying signals by symbol and timestamp.
- [ ] 9. Create one significant event when correlation succeeds.
- [ ] 10. Add fake research that runs only for a created event.
- [ ] 11. Add console notification output for the event and fake research report.
- [ ] 12. Wire the components into one synchronous pipeline.
- [ ] 13. Test the triggering scenario end to end.
- [ ] 14. Test the non-triggering scenario end to end.
- [ ] 15. Add focused unit tests for normalization, detection, correlation, and clock behavior.
- [ ] 16. Run Ruff, mypy, and pytest after project tooling is configured.
- [ ] 17. Update the specification or plan if implementation decisions change.

## Notes

- Complete tasks in order unless a dependency requires otherwise.
- Keep each task small and independently verifiable.
- Do not add live APIs, databases, background workers, or real AI integrations.
- Mark a task complete only after its validation passes.
