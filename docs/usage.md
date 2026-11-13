# Using growbook

This guide covers the screens, save behavior and no-JavaScript fallbacks. The
[main README](../README.md#current-features) remains the source of truth for
implemented features and deliberate limitations. Setup commands are in the
[development guide](development.md#container-development) and [deployment guide](deployment.md).

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

## Seasonal appearance

The account menu's **Season** selector changes the application's colours and the
diary's tree illustration. **Spring**, **Summer**, **Autumn** and **Winter** keep that
appearance until another choice is made. **Automatic (follow date)** is the default:
March–May uses spring, June–August summer, September–November autumn and
December–February winter, using today's date in the garden's configured timezone.
Viewing older diary years or changing an observation date does not change the season.
An open page with JavaScript recalculates the automatic season each minute and when
the page regains focus.

The choice is remembered in this browser for one year, including after signing out;
each browser chooses independently. Changing it with JavaScript keeps the current
page, filters, unfinished note and selected photos. Without JavaScript, select the
season and use **Change** to reload the current page; save an unfinished form first.
The login page also offers the selector. Colours apply throughout the application.
The diary illustration changes with the season; **Growing areas**, **Crops and varieties**
and **Plantings** have default illustrations with translated headings. Category
and growing-area headings use their first uploaded image when available.

## Growing areas, crops and varieties

Open **Growing areas** or **Crops and varieties** directly from the main menu.
Existing `/garden/` links redirect to **Growing areas**. Creation and archive actions
appear above the lists, with crop and variety search alongside them.
With JavaScript, selecting a variety or planting shows one record in a shared
details panel beside the list on wide screens and below it on narrow screens. Selecting
another record replaces those details; selecting the same name again closes them.
If the details would open outside the viewport, selection scrolls to them and moves
keyboard focus there. **Back to list** returns to the selected name, opening any closed
ancestor groups needed to show it. Browser Back from editing keeps its saved scroll position.
Crops and varieties share one register. Crop names are grouping headings: activate a
heading with the mouse, Enter or Space to reveal its varieties.
Groups start collapsed, several can remain open, and crops without varieties remain visible.
Each variety shows its first photo as a thumbnail when available. Select its underlined
name to open information and photos. **Diary observations (count)** expands its latest
five observations; **View all observations** appears inside when older observations exist.
**Manage crops** opens maintenance links for crop notes, editing and archiving.

**New category** creates a shared crop category with a name, optional private image
and selected crops. Categories have one level above crops; a crop can belong to
several categories. Varieties remain attached to their existing crop. Category
names use the same Unicode-aware uniqueness rules as the other registers and are
not translated.

**Crops and varieties** always opens the category overview, including when there
are no user-created categories. **New category** is available only on this overview.
Category cards have **Edit** and **Delete** actions. Open a category to browse and
search only its crops and varieties. **Uncategorized** opens crops without category
links; **Show all crops and varieties** opens the full register. Search and archive
controls retain their scope.
Empty categories remain available. **New crop** from a category page links the
new crop to that category; from Uncategorized it leaves the crop unlinked.
Creating a crop or variety from the register returns to the same category, search
and archive filters after Save or Cancel. Failed saves keep that destination.
The same category image appears on its card and in the register heading. Transparent
PNG and WebP images retain their transparency. Uncategorized uses the default crop
illustration on its card and in its heading; categories without an image also use
that heading illustration. Older filtered register links open the full register.

Edit a category to change its name or crop links, remove its image, or upload one
replacement image. Selecting another image replaces the saved image on a successful
save. Open the current image from the edit form for viewing and private original
download. Category forms use the same save-key retries and stale-edit protection
as the registers. A category has at most one image, with the existing file and
decoded-pixel limits. The overview, forms and category navigation work without
JavaScript.

Category deletion requires confirmation and removes its image and crop links.
Crops, varieties, plantings and diary records remain. A crop with no remaining
category links appears in Uncategorized; deleting the final category returns to
the overview, where Uncategorized remains available.

Varieties use normalized name ordering within each crop. **Search crops and varieties**
matches literal substrings of variety or crop names after Unicode NFC,
whitespace normalization and casefolding. Diacritics remain significant; notes and other
fields are not searched. Search and the archive choice combine in the filter URL.
Clearing search keeps the archive choice; hiding archived varieties keeps the search. With JavaScript,
search applies after a short pause or Enter, and Back restores the filter URL and results.
Failed requests keep the current results and show **Retry**. Without JavaScript, use **Apply**.
Search opens matching groups. Active varieties remain visible under archived crops,
marked by the crop's archive badge. Existing crop-list URLs open the combined register
and retain their search and archive choices.

**Growing areas** opens an overview of top-level area cards. Each card shows its name
and first photo when available, using the same layout as crop-category cards. Open a
card for the area's own page. Child-area cards appear before the parent's description,
dimensions, notes and photo controls. Each child opens its own page with the same
layout; the navigation above the heading links to the overview and ancestor areas.
Area pages show only plantings assigned directly to that area, across all years.
Each child area's plantings appear on its own page. The latest five linked diary
observations include descendant areas and archived associations. Each linked record
appears once. Select a planting or observation to open it;
**View all observations** appears when older observations exist.
**Plantings YEAR (count)** opens Plantings with the current year and that area selected;
its count and results include only plantings assigned directly to that area. This
selection continues through year changes and **All years**. The Plantings section
retains its year filters and histories. **Cancel** from area editing returns to the area's page, retaining
**Include archived** when selected; saved edits retain that choice too.

Archived ancestors of active areas remain available as cards leading to their children;
unrelated archived branches stay hidden until **Include archived** is selected.
The overview, child cards, record links and archive controls work without JavaScript.
Variety names open full records without JavaScript. **Open record** inside a variety's
details panel, or opening its name in a new tab, opens the full record.

- Crop names are unique globally; variety names within a crop. Area names are unique
  among top-level areas or among siblings with the same parent. Comparison uses Unicode
  NFC, collapsed whitespace and casefolding; display names and notes are not translated.
  Archived records still reserve their names.
- Areas may have a parent, a descriptive type and optional positive length/width in
  centimetres. A parent cannot be itself or a descendant; dimensions do not enforce a
  layout or capacity. Type accepts custom text and suggestions, including **Raised bed**
  (**Pallkrage** in Swedish). Stored type text keeps the language in which it was entered.
- Archive hides a record from default lists and new associations, not from direct links
  or existing associations. It does not archive child areas or varieties. Toggle
  **Include archived** beside the creation actions to show or hide archived records
  with one click, including without JavaScript. The selected state stays visible.
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

Diary, register, planting and event forms reject stale edits instead of overwriting
another member's changes. After a failed or uncertain create/edit save, keep the form open and
retry the same content and save key; photos are not appended twice. With JavaScript,
validation updates feedback beside the fields while preserving fields and selected
files. Without it, reselect photos as instructed before retrying. A genuinely changed
form is a new submission; do not reuse a confirmed key for different content.

With JavaScript, save forms ignore repeated submissions while a save is running.
Diary filters can still update results without changing the draft or selected photos.

Create/edit retries resolve matching immutable receipts before checking current
association eligibility and edit versions. An identical retry acknowledges the saved
submission and returns the record's current state, even if associations changed later.
Diary edit retries also work after the original photo removals were applied; selected
uploads are not appended again. New submissions still validate current associations,
photo limits and edit versions. A deleted target is not recreated by a retry.

## Photos and uploads

Observation, variety, area and planting upload forms share **Add photos**, which opens
the native image picker directly with JavaScript. Camera/library options and labels
depend on the browser and operating system. Previews and removal controls stay in the
form. With JavaScript, successive choices accumulate where the browser supports assigning
FileLists; otherwise a new choice replaces files from that source, and **Clear selected
photos** empties it. Without JavaScript both native inputs remain visible; choose all
library photos at once.

Selected-photo counts show the number chosen. Limits appear in a warning when the
selected count, an individual file or the total upload exceeds them. A count warning
includes existing photos and, when editing an observation, subtracts photos marked
for removal; the server enforces the limit. On phone-sized screens with enough height,
full forms keep their save actions visible at the bottom while scrolling.

## Plantings and histories

Open **Plantings** for category cards styled like Growing areas. The built-in
categories are **Ongoing**, **Planned** and **Completed**; every batch is one cycle
with exactly one status. **New category** creates a shared custom category, such as
For Sale. Assign a cycle to any number of custom categories through creation or
**Edit cycle**. An ongoing cycle assigned to For Sale appears in both cards.
Category names persist across years; each card's count and year describe the view.
Edit or delete custom categories from their cards. Confirmed category deletion removes
membership links while preserving cycles, members, history and photos.

**Planting filters** starts
collapsed above the results at every screen width, alongside **New planting** when
space allows. Counts and growing-year links share a row, with active filter chips below.
Expand the filters to change choices;
changes apply automatically and Enter submits a typed year. Active choices remain
visible as chips when collapsed. Remove an adjustable choice with its chip; scoped
crop, variety and area choices stay fixed. Use **All years** to leave the year overview.
Failed requests keep existing results and show **Retry**; invalid choices open the filters.
Without JavaScript, use **Apply**. Enter a year or use **Previous year** and
**Next year**; **All years** shows each matching cycle once across its full history.
Crop, variety and area histories default to all years. Choose a category to see one
row per matching cycle, then select its name to open the shared details panel.
The row shows its current status and exact growing-year ranges. Batch members expand
independently; the panel includes status, categories, dates and all members, plus the
selected member's quantity, events, notes, evaluation, photos and diary observations.
Ordinary links open full detail pages without JavaScript. **Planting categories**
returns to the overview with the year and filters preserved.

All filters apply together to the same member. A batch with three matching varieties
counts once; a cycle appears if at least one member matches all selected filters.
Lists expose the matching members, while cycle details make the whole batch accessible.
Custom-category counts overlap and can sum to more than the overview's unique cycle
total. Empty categories remain visible. Past-year views show current status and current
category membership, rather than reconstructing past lifecycle state.
History links in member information retain an explicitly selected year. Blank quantity
is shown as a dash and zero as zero.

- **New planting** requires a crop and start year (1–9999). Variety and final growing
  area may be unknown; no placeholder records are created. The visible **Planting label**
  defaults to crop-variety, or the crop alone when variety is unknown. With JavaScript
  it follows selections, including after a failed save, until you edit it;
  without JavaScript a blank label is filled
  when saved. **More details** groups variety, area, lifespan, end year and quantity;
  **Notes and photos** groups
  notes, evaluation and uploads. Populated or invalid groups stay open. Variety choices
  follow the crop without reloading and clear an incompatible selection. Without
  JavaScript, use **Update variety choices** before choosing a variety; the server also
  rejects mismatches and ineligible new associations.
  If a save interrupts loading variety choices and then fails, use **Retry variety
  choices** to reload them while keeping the draft and selected photos.
  The initial status defaults to **Planned**, with explicit **Ongoing** and
  **Completed** choices. Actual-date fields default to today and can be changed.
  Ongoing requires an actual start date; Completed requires a completion date.
  Creating from a status card preselects that status; creating from a custom category
  preselects its membership. The initial choice remains editable.
- **Planting label** names an individual planting; several batches may share identical
  year/crop/variety/area values. Quantity is a nonnegative plant count: blank is unknown,
  zero is a known zero.
- **Add variety to batch** on a planting's details starts another member of that batch,
  preselecting its crop, year, area and lifespan. Choose an unused variety of the same
  active crop; the action is hidden when the crop is archived.
  If the inherited area is no longer eligible, it stays selected as unavailable;
  choose an eligible area or explicitly leave it unassigned before saving.
  Each member keeps its own label, quantity, events, evaluation, photos and diary
  links; the new member inherits the cycle's status, dates and categories.
  Shared batches expand to show their matching members. Cycle headings use the
  earliest remaining member's label and a member's photo when available.
  Filters show only matching members. Editing or deleting one member affects that
  member. A known variety cannot be cleared while the batch has other members;
  an initially unknown variety can remain unknown or be assigned later.
- New area selections offer active areas without children. Parents with archived
  children are also omitted.
  An existing parent-area assignment remains available when editing that planting.
  Saves also check this rule if the area gains children while a form is open.
  **New planting** on an active area without children preselects that area and returns
  there after Save or Cancel. Creating from a list filter preselects the area only
  when it is eligible for a new assignment. Area pages group direct plantings from
  all years with photo thumbnails; **Planting batches** counts batches. They offer
  **All years**, the current year, **Previous year** and **Next year**.
- **Annual** is the default and belongs only to the start year, with no end year. With
  JavaScript a blank end year is hidden; an entered end year is preserved for
  correction. **Persistent** belongs to every year from start through its optional end
  year, inclusive; a blank end year leaves year membership unbounded and does not
  determine lifecycle status. Setting an end year keeps events, evaluation and photos.
  Viewing another year never copies the planting or
  counts the original quantity as repeated production.
- **Start cycle** changes Planned to Ongoing and asks for the actual start date,
  defaulting to today in the garden's timezone. It preserves growing-year assignments.
  **Complete cycle** changes Ongoing to Completed and asks for the completion date.
  Each persistent member's inclusive end year becomes that date's year; annual
  members keep their start year and have no end year. The viewed year never supplies
  the transition date. Invalid date ordering or incompatible member years reject
  the entire save. After saving, the category list refreshes; **Open record** reaches
  the cycle even if it no longer matches that category.
- **Edit cycle** is the route for correcting status and actual dates, including
  returning to Planned or reopening a Completed cycle. Reopening clears the completion
  date and persistent end years. Changing shared categories leaves member records
  intact. Transitions and corrections preserve IDs, events, evaluation, photos and
  diary links. Older automatically classified cycles can have unknown actual dates;
  correct their classification here. The initial upgrade rule is documented in
  [deployment](deployment.md#upgrade-an-existing-installation).
- Filter by crop, variety or area, or find **Unassigned area only** or **Unknown variety
  only**. Area filters normally include the selected area and all descendants; each
  cycle counts once and member details show the actual assigned place.
  Archived register items stay visible in existing
  histories and associations.
- **New planting event** supports sowing, transplanting, harvest start/end and other
  milestones. Planned and actual dates are separate and at least one is required; dates
  do not determine the growing year or change lifecycle status. Repeated event types
  are valid. Each planting
  in a batch has its own events and evaluation. Event edits and deletion
  use version checks; deletion requires confirmation.
- Edit a planting to add cumulative evaluation text or photos. Photo controls match
  register photos. Planting deletion is permanent and removes its events and photos;
  completing a cycle is the history-preserving alternative.

Each planting has one current area. Correcting that assignment or reparenting an area
changes past-year results too. For an established plant that moves, prefer ending the
old planting and creating a new one at the destination; this preserves year-level
presence, not exact movement dates, lineage or historical layouts.

## Diary

Open **Diary** to add an observation or browse observations newest first. The inline
composer accepts a note, photos or both, with an optional title and tags. A confirmed
save clears the composer, shows a receipt and refreshes the first results page with
current filters. Failed saves keep the draft and selected files in the open form.
Without JavaScript, reselect photos as instructed and
retry with the same save key.

**Title** is optional and accepts up to 200 characters. It appears beneath the date
on cards and observation pages, and in linked observation previews. Leave it blank
to use the date as the heading. An observation needs a note or at least one photo,
even when it has a title.

The inline composer and observation editing place **Add tags** and **Add photos** on
the left of the action row, with **Save observation** aligned right. The row wraps on
narrow screens.
Both forms share the same tag picker. **Add tags** opens a bounded selector with its own
scrolling choices. Choose several using the checkboxes or click anywhere in a result
row; each selection appears as a removable chip. Nothing needs to be selected for a
general observation. Pointer activation keeps focus on the button; keyboard activation
focuses search. **Done** or Escape closes the selector and returns focus to **Add tags**.

Plantings for the observation's year, including ongoing persistent plantings, appear
first, followed by growing areas. **Other years** and **Crops and varieties** reveal
secondary choices. Search finds these immediately, including planting crop, variety,
full area path, batch and year. Year ranks suggestions without restricting links.
Without JavaScript, grouped native checkboxes remain available.

Tags store only the explicit choices. A planting also supplies its current crop,
known variety, assigned area and ancestor areas for retrieval; a variety supplies its
crop. Changing a planting's relationships or area nesting changes past observations'
filter results too. No redundant tags or location snapshot are created. Capture saves
the observation, tags, photos and immutable receipt together. An identical retry matches
that receipt before current tag eligibility, even if a target was later archived,
changed or removed after unlinking. Choosing the same tags in a different order is an
identical retry. A confirmed quick save clears tags and starts a fresh save key.
Diary filters do not preselect tags for a new observation.

**Search and filters** starts collapsed above the diary content at every screen width;
entries use the full content width. Active choices stay visible when collapsed.
With JavaScript, Escape closes the filters and returns focus to their heading;
invalid choices open the filters, and failed requests keep results and show **Retry**.
Search applies after a short pause or Enter, other filters apply on change, and chips
remove individual filters. Paging and Back preserve filter URLs; updating results keeps
an unfinished quick-add and its selected files. Without JavaScript, open the filter
disclosure and submit the form. Search matches titles and notes; the year, area, crop,
variety and planting controls combine in one form. A planting filter also matches its
crop, variety and area, and an area filter includes descendants. Results are deduplicated
and paging preserves active filters.

Observation pages show compact photo previews; select one to view the larger image.
Open a photo and use **Photo options → Download original**. Originals stay out of the
ordinary diary view and are included in operational backups.

The account menu contains Language, Season, Log out and, for staff, Operator. Language changes
apply immediately with JavaScript and retain the page and filters; a submit button
remains without JavaScript. The login page has its own selector.

- Search is a literal substring match after Unicode NFC normalization and casefolding:
  uppercase and lowercase match, diacritics do not (`Ångström` finds `ångström`, but
  `Angstrom` does not). Captions and linked record names are not searched.
- Open an observation to read its title, note, links and photos. The author may edit or
  delete it; an active staff administrator may edit or delete any entry; other active
  accounts can read it. The list shows **Edit** only on entries the signed-in account may
  change. A deactivated author cannot write, even while signed in.
- Edit changes the date, optional title, note, garden links and photos. A planting link
  also matches its crop, variety and area. Each link points to exactly one area, crop,
  variety or planting, and the same record can be linked once. New direct links to crops, varieties and areas
  must target active records; existing archived links remain visible and removable.
  Older or ended plantings, including those with archived associations, stay selectable.
  Ordinary **Edit** includes the same picker and preserves existing explicit links.
- Edits use the entry's version token; a stale form is rejected rather than overwriting a
  newer change. Identical retries use immutable edit receipts; follow
  [Saving changes and retries](#saving-changes-and-retries) after an uncertain save.
  Reload after a version conflict to see saved values. With JavaScript only errors are
  replaced and the form stays open; without it, date, title, note and link selections are kept,
  but photos must be reselected before retrying.
- Delete requires confirmation and permanently removes the title, note, links and photos,
  including private originals. Linked registers, plantings and their photos remain. A
  stale deletion is rejected and asks you to reload.
- On a photo page, the author or an administrator can change the caption and order, or
  delete the photo with confirmation. A photo-only observation must keep at least one
  photo; add a note before removing its last one.
- Record details and histories show the latest linked observations with a count and a
  link to the filtered diary.
