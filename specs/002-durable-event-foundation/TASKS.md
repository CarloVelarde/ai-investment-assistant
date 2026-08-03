# Tasks: Durable Event Foundation

**Document status:** Approved

**Execution status:** Not started

## Tasks

- [ ] 1. Define the market and news signal data, including unique IDs, importance levels, source details, and tests for valid values.
- [ ] 2. Add the database-path setting and the first SQLite database layout. Test that setup is safe to repeat and that data survives closing and reopening a temporary database.
- [ ] 3. Save and reload signals, events, reports, notification attempts, failures, and source details. Test that database rows return as normal application models.
- [ ] 4. Let market-only and news-only signals create events. Test how related signals are grouped and prove the same signal ID is handled only once.
- [ ] 5. Save same- or lower-importance signals quietly. Request new research for higher importance, a new market time window, or significant news. Test that several waiting updates result in work only for the newest update.
- [ ] 6. Save each research step and fake report. Test that an older report cannot be sent after newer information arrives and that only the newest event update is researched.
- [ ] 7. Save every notification attempt and mark an event update complete only after success. Test failed delivery, one success per update, and immediate eligibility for later important information.
- [ ] 8. Save failures and recover after restart. Test waiting research, interrupted research, saved reports awaiting notification, and retryable failures without repeating finished steps.
- [ ] 9. Replace the Milestone 1 rule that required paired market and news signals with the shared saved-event flow. Keep the fake research and console notification.
- [ ] 10. Add full-flow offline tests for a first run, exact repeat, same-importance repeat, higher importance, news added to an event, and restart recovery. Use a controllable clock.
- [ ] 11. Connect the console command to an ignored local SQLite file and bundled offline scenario. Verify that a second run with the same database produces no duplicate report or event notification.
- [ ] 12. Update active documentation if implementation details change, confirm that no later-milestone dependency or service was added, and run all repository checks.

## Acceptance coverage

| Criteria | Tasks |
| --- | --- |
| AC-01 | 2–3 |
| AC-02 | 4, 10 |
| AC-03–AC-05 | 4–6, 10 |
| AC-06 | 7, 10 |
| AC-07–AC-08 | 6–8, 10 |
| AC-09 | 9, 11 |
| AC-10 | 2, 6–8, 10–12 |
| AC-11–AC-12 | 12 |

Add tests with each behavior. Mark a task complete only after its tests pass. Record any change to product scope or stable architecture in the document that owns that decision before changing this feature.
