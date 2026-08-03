# Implementation Plan: Durable Event Foundation

**Document status:** Approved

## Approach

Replace Milestone 1's temporary, in-memory event handling with one event manager that saves its work in SQLite. Offline market and news signals will pass through this manager independently. The saved event status will act as the local work list, so no separate queue or worker is needed.

## Data kept by the application

Market and news signals share a few clear details:

- a unique signal ID;
- a ticker written in one standard form, such as `TSLA`;
- when the signal happened;
- an ordered importance level: `MODERATE`, `HIGH`, or `CRITICAL`;
- source details needed to trace and replay it.

A market signal also records its direction, detection rule, and time window. A news signal records its category and direction when known. Milestone 3 will decide which price movements produce each importance level.

An event stores the signals that belong together, its current importance, reached market time windows, current update number, processing status, and last successful notification time. Reports, notification attempts, and failures point to a specific event update number. This prevents an old report from being sent after newer information arrives.

## Handling one signal

The event manager will handle each signal in one all-or-nothing database write:

1. Stop if the unique signal ID was already handled.
2. Find the related active event using the rules in the specification, or create one.
3. Save the signal and its connection to the event.
4. Decide whether the signal is a routine repeat or an important update.
5. Mark a new or importantly changed event as waiting for research.

If several important signals arrive before work finishes, the event's update number moves forward, but only the newest update is processed. The application checks the update number again before notification so it cannot send an outdated report.

## SQLite storage

Use Python's built-in `sqlite3` support behind one small storage class. Use SQLite's built-in database version number to identify the first database layout. Do not add an ORM (a larger database framework), migration library, or general storage framework.

Keep five groups of saved data:

| Saved data | What it is for |
| --- | --- |
| Signals | Each unique market or news signal, its source, its event, and the update it affected |
| Events | The evolving situation, its importance, current update number, status, and delivery state |
| Reports | The fake research result for a specific event update |
| Notification attempts | When notification was tried and whether it succeeded |
| Failures | Which step failed, whether it can be retried, and a safe error description |

The storage class converts database rows into normal application models before returning them. Tests will close and reopen a temporary database file to prove the data survives a real restart.

## Saving progress and recovering

- Save `QUEUED` before research can begin.
- Save `RESEARCHING` before calling the fake research function.
- Save the report and `REPORTED` together before notification.
- Save each notification attempt. Change the event to `NOTIFIED` and record the delivery time only after success.
- Save a failure and the step where it happened. A later run retries only that unfinished step.
- If the application restarts while an event says `RESEARCHING`, safely return it to the research step.
- Never notify from a report that belongs to an older event update.

The database will reject a repeated signal ID and a second successful notification for the same event update. This protects normal replay and known restart cases. Milestone 7 will add Discord-specific protection for a crash during live message delivery.

## Application flow

Add a setting for the database path. Its default local database file must be ignored by Git, while tests use temporary paths.

The existing console command will run a bundled offline scenario through:

> offline signal → group into an event → save waiting work → fake research → save report → console notification

Running the same scenario a second time with the same database should produce no new report or event notification. More focused tests will cover market-only events, news-only events, ordinary repeats, important updates, and recovery without making the command more complicated.

## Validation

- Set up the database repeatedly, then close and reopen it without losing data.
- Create events from market-only and news-only signals.
- Ignore exact repeats and save ordinary same-importance updates quietly.
- Request research again for higher importance, a new market time window, or significant news.
- Keep related signals in one event and skip outdated pending updates.
- Record successful and failed notification attempts correctly.
- Recover waiting, interrupted, reported, and retryable failed work from the correct step.
- Save reports before notification and allow only one successful notification per event update.
- Run the offline scenario once, then replay it without duplicate work.
- Run every repository check.

## Intentional limits

- The grouping rules are small because Milestone 3 will define market-trend opening and closing, while Milestone 5 will define richer news matching.
- A time-based notification cooldown waits until Milestone 7 because it would add no useful decision here: ordinary repeats are already quiet and important updates must pass through.
- Synchronous SQLite access is enough for one local application process.
- The application can recover from saved states, but it cannot know with certainty whether an outside service received a message if the process crashed before recording success.
