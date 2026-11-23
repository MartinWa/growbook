# Calendar

The calendar shows one month at a time: planting events and the observations you chose
to show, so planned work and what actually happened sit on the same page. It is
read-only: opening an item leads to the record that owns it. Shared behavior — saving,
retries, navigation between pages — is in [Getting started](getting-started.md).

## What appears

- **Planting events** appear automatically. An event with only a planned date shows as
  planned on that date; once it has an actual date it moves there and shows as done. An
  event whose actual date is in another month is not repeated in this one.
- **Diary observations** appear only when **Show in calendar** was ticked while writing or
  editing them. The tick is optional: an observation does not need to appear just because
  it has a date.
- **One item for one save.** When an observation records a planting event, the calendar
  shows a single item that opens both the observation and the event. The item sits on the
  event's date, so it follows the event when the two dates differ; the observation is
  placed on its own date only when the linked event falls outside the shown month.
  Clearing **Show in calendar** later keeps the event and removes only the observation's
  own label.
- **One item for one planting on one day.** An observation tagged with a planting is shown
  together with a single event of that planting on the observation's own date, even when
  the observation was written before the calendar existed. Only one clear match is paired;
  when the planting has several events that day, or the observation links several
  plantings, the records stay separate rather than guessing which event was meant.
- **Photo-only observations without a title** are labelled with their localized date, so
  they still have a name in the month.
- **Today** is marked, and month and weekday names follow the interface language.

## Finding your way

- **Calendar** in the main menu opens the current month. **Previous**, **Next** and
  **Current month** move between months; the current month is taken from the garden's
  timezone, not the browser's.
- Weeks start on Monday. On wide screens the month is a table with one column per weekday;
  on narrow screens it becomes a list of the days that have something on them.
- **2000** is the earliest year, and **next year** the latest. A month outside that range
  is not found rather than an empty page, so old links fail visibly.

## Read-only

The calendar only reads existing records. Planned work is written where it belongs: as a
planting event in [Plantings](plantings.md), or as a diary observation with **Show in
calendar** ticked.
