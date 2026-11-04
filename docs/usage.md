# Using growbook

This guide covers the screens, save behavior and no-JavaScript fallbacks. The
[main README](../README.md#current-features) remains the source of truth for
implemented features and deliberate limitations. Setup commands are in the
[main README](../README.md#run-locally) and [deployment guide](deployment.md).

## Application version

The muted footer at the bottom right shows **GrowBook**, the version and **GitHub**.
The version links to releases and **GitHub** links to the project. A small orange dot
beside the version means a newer stable release is available; its tooltip and
screen-reader text say **Update available**. Signed-in pages
check through the application server with JavaScript. Without JavaScript, or if the
check cannot reach GitHub, the version and links remain usable. Local development runs
show the base version. Hover over the version for its exact Git identifier, including
local changes or commits after a release. Builds without version metadata show
**Development**. See
[deployment configuration](deployment.md#application-version-and-update-check).

## Garden

Open **Garden** for crop, variety and growing-area cards with active record counts and
creation links. Each register table has a labelled **Details** control; several rows can
stay expanded, ordinary record links work without JavaScript, and phones show secondary
columns inside expanded details.

- Crop names are unique globally; variety names within a crop. Area names are unique
  among top-level areas or among siblings with the same parent. Comparison uses Unicode
  NFC, collapsed whitespace and casefolding; display names and notes are not translated.
  Archived records still reserve their names.
- Areas may have a parent, a descriptive type and optional positive length/width in
  centimetres. A parent cannot be itself or a descendant; dimensions do not enforce a
  layout or capacity.
- Archive hides a record from default lists and new associations, not from direct links
  or existing associations. It does not archive child areas or varieties. Open
  **Archive options** and use **Include archived records** to show archived records;
  with JavaScript the checkbox applies on change, with **Apply** as fallback.
  **Hide archived records** restores the default list.
- Edit an area or variety to add photos; edit photo details to change captions or order.
  Deletion requires confirmation. A crop with varieties or an area with children cannot
  be deleted; archive it or reorganize its dependents first. Deleting an allowed record
  removes its photos with coordinated file cleanup after the database commit. Referenced
  crops, varieties and areas cannot be deleted.
- A variety used by any planting cannot change crop through an ordinary edit, even when
  the planting has ended. Reconcile its planting references explicitly first; changing
  the variety does not silently reclassify plantings.

## Saving changes and retries

Register, planting and event forms reject stale edits instead of overwriting another
member's changes. After a failed or uncertain create/edit save, keep the form open and
retry the same content and save key; photos are not appended twice. With JavaScript,
validation updates feedback beside the fields while preserving fields and selected
files. Without it, reselect photos as instructed before retrying. A genuinely changed
form is a new submission; do not reuse a confirmed key for different content.

Register and planting retries resolve matching immutable receipts before checking
current association eligibility. An identical retry returns the saved record even if
the original crop or parent was archived or associations changed later. New submissions
still validate current associations and edit versions.

## Photos and uploads

Observation, variety, area and planting upload forms share **Add photos**, which opens
the native image picker directly with JavaScript. Camera/library options and labels
depend on the browser and operating system. Previews and removal controls stay in the
form. With JavaScript, successive choices accumulate where the browser supports assigning
FileLists; otherwise a new choice replaces files from that source, and **Clear selected
photos** empties it. Without JavaScript both native inputs remain visible; choose all
library photos at once.

Selected-photo counts show the number chosen out of the available capacity. **Photo
limits** lists formats and byte limits. A count warning includes existing photos and,
when editing an observation, subtracts photos marked for removal; the server enforces
the limit. On phone-sized screens with enough height, full forms keep Save and Cancel
visible at the bottom while scrolling.

## Plantings and histories

Open **Plantings** for a growing-year table. Filters use a sidebar at 900 px and a
drawer below that width; changes apply automatically and Enter submits a typed year.
Without JavaScript, use **Apply**. Enter a year or use **Previous year** and
**Next year**; **All years** shows one row per planting across its full history. Crop,
variety and area histories default to all years, and table links keep an explicitly
selected year. **Details** expands multiple rows independently to show events, notes,
evaluation and photos. Register details link to their **Planting history**.

- **New planting** requires a crop and start year (1–9999). Variety and final growing
  area may be unknown; no placeholder records are created. **More details** groups
  variety, area, lifespan, end year, batch label and quantity; **Notes and photos** groups
  notes, evaluation and uploads. Populated or invalid groups stay open. Variety choices
  follow the crop without reloading and clear an incompatible selection. Without
  JavaScript, use **Update variety choices** before choosing a variety; the server also
  rejects mismatches and ineligible new associations.
- **Batch label** distinguishes similar rows; several batches may share identical
  year/crop/variety/area values. Quantity is a nonnegative plant count: blank is unknown,
  zero is a known zero.
- **Annual** is the default and belongs only to the start year, with no end year. With
  JavaScript a blank end year is hidden; an entered end year is preserved for
  correction. **Persistent** belongs to every year from start through its optional end
  year, inclusive; a blank end year means ongoing. Ending a persistent planting keeps
  its events, evaluation and photos. Viewing another year never copies the planting or
  counts the original quantity as repeated production.
- Filter by crop, variety or area, or find **Unassigned area only** or **Unknown variety
  only**. Area filters include the selected area and all descendants, once per batch,
  showing the actual assigned place. Archived register items stay visible in existing
  histories and associations.
- **New planting event** supports sowing, transplanting, harvest start/end and other
  milestones. Planned and actual dates are separate and at least one is required; dates
  do not determine the growing year. Repeated event types are valid. Use separate
  planting rows for independently counted or evaluated batches. Event edits and deletion
  use version checks; deletion requires confirmation.
- Edit a planting to add cumulative evaluation text or photos. Photo controls match
  register photos. Planting deletion is permanent and removes its events and photos;
  ending a persistent planting is the history-preserving alternative.

Each planting has one current area. Correcting that assignment or reparenting an area
changes past-year results too. For an established plant that moves, prefer ending the
old planting and creating a new one at the destination; this preserves year-level
presence, not exact movement dates, lineage or historical layouts.

## Diary

Open **Diary** to add an observation or browse observations newest first. Quick-add
accepts a note, photos or both; **More options** opens the dedicated capture page and
does not carry the quick draft over. With JavaScript, a confirmation warns before
leaving a non-empty draft; a confirmed save clears quick-add, shows a receipt and
refreshes the first results page with current filters. Failed saves keep the draft and
selected files in the open form. Without JavaScript, reselect photos as instructed and
retry with the same save key.

Search and filters use a sidebar on wide screens and a drawer on phones. With JavaScript,
search applies after a short pause or Enter, other filters apply on change, and chips
remove individual filters. Paging and Back preserve filter URLs; updating results keeps
an unfinished quick-add and its selected files. Without JavaScript, open the filter
disclosure and submit the form. Search matches note text only; the year, area, crop,
variety and planting controls combine in one form. A planting filter also matches its
crop, variety and area, and an area filter includes descendants. Results are deduplicated
and paging preserves active filters.

Open a photo and use **Photo options → Download original**. Originals stay out of the
ordinary diary view and are included in operational backups.

The account menu contains Language, Log out and, for staff, Operator. Language changes
apply immediately with JavaScript and retain the page and filters; a submit button
remains without JavaScript. The login page has its own selector.

- Search is a literal substring match after Unicode NFC normalization and casefolding:
  uppercase and lowercase match, diacritics do not (`Ångström` finds `ångström`, but
  `Angstrom` does not). Captions and linked record names are not searched.
- Open an observation to read its note, links and photos. The author may edit or delete
  it; an active staff administrator may edit or delete any entry; other active accounts
  can read it. The list shows **Edit and tag** only on entries the signed-in account may
  change. A deactivated author cannot write, even while signed in.
- Edit changes the date, note, garden links and photos. A planting link also matches its
  crop, variety and area. Each link points to exactly one area, crop, variety or planting,
  and the same record can be linked once. New links must target active records; existing
  links to archived records remain selected and filterable. An entry without links shows
  **Add garden links** on its page, and the edit form opens the link picker by default.
- Edits use the entry's version token; a stale form is rejected rather than overwriting a
  newer change. Reload to see saved values. With JavaScript only errors are replaced and
  the form stays open; without it, date, note and link selections are kept, but photos
  must be reselected before retrying.
- Delete requires confirmation and permanently removes the note, links and photos,
  including private originals. Linked registers, plantings and their photos remain. A
  stale deletion is rejected and asks you to reload.
- On a photo page, the author or an administrator can change the caption and order, or
  delete the photo with confirmation. A photo-only observation must keep at least one
  photo; add a note before removing its last one.
- Record details and histories show the latest linked observations with a count and a
  link to the filtered diary.
