# Feature Specification: Durable Event Foundation

**Document status:** Approved

## Purpose

Remember important market and news activity, avoid repeating the same work, and continue unfinished work after the application restarts.

## Scope

### In scope

- Accept offline market and news signals that have already been judged important.
- Allow either a market signal or a news signal to start an event on its own.
- Store signals, events, reports, notification attempts, failures, and source details in SQLite.
- Use one event manager to decide whether a signal starts a new event, belongs to an existing event, or is only a routine repeat.
- Reuse the fake research and console notification from Milestone 1.
- Continue unfinished research or notification work after a restart.

### Out of scope

- Calculating price signals, deciding market thresholds, and deciding when a price trend has ended. Milestone 3 owns this work.
- Live market or news services, AI news classification, real AI research, Discord, scheduling, or continuous monitoring.
- Extra database frameworks, background workers, or support for several application processes writing at once.
- A time-based notification cooldown or production-level delivery guarantees. Milestone 7 owns these behaviors.

## Behavior

### Receiving and grouping signals

A signal is a saved indication that market movement or news may be important. An event is the ongoing situation that groups related signals for one ticker.

Each signal has a unique ID, ticker, time, importance level, and enough source information to explain where it came from. Tickers use one standard form, such as changing `tsla` to `TSLA`. A market signal also describes whether price is rising or falling, which rule detected it, and which time window it covers. A news signal describes its category and direction when known. All news signals used here have already passed the future news-significance check.

Market and news signals do not depend on each other. Either can create an event and request research.

The event manager uses deliberately simple grouping rules:

- Market signals for the same ticker and direction belong to the same still-open event, whether market movement or news started it.
- News joins an existing market event only when there is one clear match for that ticker and direction.
- If there is no clear match, news joins or creates an event using its ticker and category. The application does not guess between several possible market events.

Milestones 3 and 5 will add fuller market-trend and news-matching rules. This milestone only builds the durable foundation they will share.

### Deciding when to research again

- The same signal ID is accepted only once.
- Another signal at the same or a lower importance level is saved as useful history, but it does not start more research.
- A higher importance level, a newly reached market time window, or a new significant news signal is an important update and requests research again. For example, a five-day decline can add important context after an earlier one-hour drop.
- If several important updates arrive before work finishes, the application researches the latest complete view once. It does not send older, incomplete versions.
- A successfully completed update cannot be researched or notified again.
- A later important update remains eligible immediately, even if the earlier notification was just sent.

Milestone 2 records when notification succeeds but does not add a separate clock-based mute. Routine repeats are already quiet, while important changes must remain visible. Milestone 7 will add the live notification cooldown.

### Saving progress and recovering

Each event update has one of these saved statuses:

| Status | Meaning |
| --- | --- |
| `QUEUED` | Waiting for research |
| `RESEARCHING` | Research started but has not finished |
| `REPORTED` | The report is saved and waiting for notification |
| `NOTIFIED` | Notification succeeded |
| `FAILED` | Research or notification failed and may need another attempt |

After a restart:

- waiting or interrupted research starts again;
- a saved report continues to notification without repeating research;
- a retryable failure continues from the step that failed;
- a completed update stays finished unless new important information arrives.

The report must be saved before notification begins. Notification is complete only after success is saved.

## Acceptance criteria

- [x] AC-01: The SQLite database can be set up more than once safely and preserves signals, events, reports, notification attempts, failures, and source details.
- [x] AC-02: A market signal by itself and a significant news signal by itself can each create one event waiting for research.
- [x] AC-03: Repeating the same signal does nothing, while a different signal at the same or lower importance is saved without starting more research.
- [x] AC-04: A higher importance level, a newly reached market time window, or new significant news makes the existing event ready for research again.
- [x] AC-05: Related signals stay in one event, and several pending updates produce research and notification only for the latest complete update.
- [x] AC-06: Successful notification marks that update complete; failed notification does not; later important information remains eligible immediately.
- [x] AC-07: A report is saved before notification, and repeating a completed update does not repeat research or notification.
- [x] AC-08: Restart tests prove that interrupted research, a saved report awaiting notification, and retryable failures continue from the correct step.
- [x] AC-09: Running the same offline console scenario twice against the same database produces no duplicate research or event notification.
- [x] AC-10: Tests control time, use temporary databases and test replacements for future services, and need no network, secrets, or external services.
- [x] AC-11: This milestone adds no market calculations, live integrations, background worker, or unnecessary database framework.
- [x] AC-12: Ruff formatting and linting, mypy, and pytest pass.

## Constraints

- Keep one local, straightforward application process and one place that writes to SQLite.
- Use repeatable unique IDs and a controllable clock so tests always produce the same result.
- Keep database details out of the event manager's decision rules.
- Retain source, feed, and timing details needed to trace a signal later.
- Do not require both market and news signals; their pairing in Milestone 1 was only a demonstration.
- Do not promise perfect duplicate prevention if the application crashes after an outside service receives a message but before success is saved. Milestone 7 will handle live-delivery safeguards.
