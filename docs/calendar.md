# Calendar

The calendar shows one month at a time: planting events and the observations you chose
to show, so planned work and what actually happened sit on the same page. Selecting an
item opens a small panel; the records themselves are edited where they belong. Shared
behavior — saving, retries, navigation between pages — is in
[Getting started](getting-started.md).

## What appears

- **Planting events** appear automatically. An event with only a planned date shows as
  planned on that date; once it has an actual date it moves there and shows as done. An
  event whose actual date is in another month is not repeated in this one.
- **Diary observations** appear only when **Show in calendar** was ticked while writing or
  editing them. The tick is optional: an observation does not need to appear just because
  it has a date.
- **Planned tasks** are the calendar's own small notes for work ahead. A task with no
  completion date sits on its planned date and shows as planned. **Mark done** records the
  day it was done and moves it there; **Reopen task** puts it back on the planned date. A
  task may link one growing area, crop, variety or planting, and the link is what the area,
  crop, variety and planting filters match.
- **One item for one save.** When an observation records a planting event, the calendar
  shows a single item that opens both the observation and the event. The item sits on the
  event's date, so it follows the event when the two dates differ; the observation is
  placed on its own date only when the linked event falls outside the shown month.
  Clearing **Show in calendar** later keeps the event and removes only the observation's
  own label.
- **One item for one planting on one day.** An observation tagged with a planting is shown
  together with a single event of that planting on the observation's own date, even when
  the observation was written before the calendar existed. Only one clear match is paired:
  when the planting has several events that day, or the observation links several
  plantings, the records stay separate rather than guessing which event was meant. An
  observation that recorded the event itself always keeps it, and an event paired with
  one observation is never paired with a second.
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

## Opening an item

Selecting a label opens a panel with its full title, its date, what kind of item it is, its
note and links to the records it belongs to. A combined item links both the observation
and the planting event; an event links to its planting. A task shows its planned date when
it was completed on another day, and carries **Mark done** or **Reopen task**, **Edit task**
and **Delete task**. **Escape** or the close button closes the panel and returns focus to
the label you opened.

A day shows up to three labels. **+N more** opens the same panel listing everything on
that day, and each label there opens its own panel. The open item and the open day are
both in the address, so a link to one can be shared. Opening a specific item wins over an
opened day, and changing the month or a filter closes both.

Without JavaScript the panel is part of the page, the close control is an ordinary link,
and every item still reaches its record.

## Filters

**Filters** narrows the month by item type, growing area, crop, variety and planting. The
filters combine, and they mean the same as in the diary: a planting match also includes
its crop, variety and area, and an area filter includes its subareas. The chosen filters
appear as chips that remove them one at a time, with **Clear filters** to start over.

Changing a filter stays in the same month, and **Previous**, **Next** and **Current
month** keep the filters, so a filtered month can be walked through. Back returns to the
previous view with its filters and controls intact. A filter or address the calendar
cannot read is not found rather than silently ignored.

## Writing a task

**New task** in the heading opens a form with a date, an optional title, an optional note
and an optional linked record. One of the title or the note is required. **Edit task** in
the panel changes the same fields, and **Delete task** asks for confirmation. A completed
task keeps its completion day when it is edited.

Every save carries a save key and a version, so an interrupted save can be retried without
creating a second task, and a form opened in one tab cannot overwrite a change saved in
another. A retried save that is no longer current changes nothing and explains why.

## This time last year

The side column lists the same month one year earlier: planting events, observations,
tasks and any paired items, each linking to its record. It follows the active filters,
and a task appears on the day it was completed. **Add to this year** opens the same task
form with the previous year's title, note, link and day; the date is always yours to
choose, and saving creates an ordinary task in the shown month. Nothing is copied into
the current year automatically.

## Month note

Below **This time last year** is one shared note for the shown month. It is saved with
**Save note** and is visible to everyone who reads that month. A note saved in two tabs,
or a first note that appears while the form is open, is not overwritten: the save explains
that the note changed and keeps the text you wrote, so nothing is lost while you reload
to read the newer note. Changing a filter or moving to another month reloads the side
column too, so save a note before you leave the month.

## Month pictures

Each month carries one small decorative picture beside the heading. It is hidden, not
replaced by a placeholder, on narrow screens, and it carries no text for assistive
technology. The pictures are original artwork made for GrowBook and are committed as
720 × 360 WebP files, one per month.

## Read-only records

Planting events and diary observations are written where they belong: in
[Plantings](plantings.md) and [Diary](diary.md). The calendar edits only its own tasks and
notes, and the panel links every other item to its record.
