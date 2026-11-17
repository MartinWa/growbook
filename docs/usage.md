# Using growbook

This guide covers the screens, save behavior and no-JavaScript fallbacks. The
[main README](../README.md#current-features) remains the source of truth for
implemented features and deliberate limitations. Setup commands are in the
[development guide](development.md#container-development) and [deployment guide](deployment.md).

## Application version

The muted footer shows **GrowBook**, the version and **GitHub**; the version links to
releases. An orange dot and an **Update available** tooltip mean a newer stable release
exists. Signed-in pages check through the application server with JavaScript; without
it, or when GitHub is unreachable, the links still work and no indicator appears. Local
runs show the base version and its exact Git identifier on hover; builds without metadata
show **Development**. See
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
`/garden/` redirects to **Growing areas**. Creation and archive actions
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
are no user-created categories. **New category** is available on this overview and
in **Manage categories**, which opens the category maintenance list. Open a category
card to browse and search only its crops and varieties. **Uncategorized** opens crops
without category links; **Show all crops and varieties** opens the full register. Search and archive
controls retain their scope.
Empty categories remain available. **New crop** from a category page links the
new crop to that category; from Uncategorized it leaves the crop unlinked.
Creating a crop or variety from the register returns to the same category, search
and archive filters after Save or Cancel. Failed saves keep that destination.
The same category image appears on its card and in the register heading. Transparent
PNG and WebP images retain their transparency. Uncategorized uses its own built-in
illustration on its card and in its heading; categories without an image use the
default crop heading illustration.

From **Manage categories**, edit a category to change its name or crop links, remove
its image, or upload one replacement image. Selecting another image replaces the
saved image on a successful save. Open the current image from the edit form for viewing and private original
download. Category forms use the same save-key retries and stale-edit protection
as the registers. Save and Cancel return to category management. Its **Back to list**
link preserves the originating browsing context. A category has at most one image,
with the existing file and decoded-pixel limits. The overview, forms and category
navigation work without JavaScript.

Category deletion requires confirmation and removes its image and crop links.
Crops, varieties, plantings and diary records remain. A crop with no remaining
category links appears in Uncategorized; deleting the final category returns to
category management. **Back to list** retains a surviving browsing origin or opens
the overview, where Uncategorized remains available.

Varieties use normalized name ordering within each crop. **Search crops and varieties**
matches literal substrings of variety or crop names after Unicode NFC,
whitespace normalization and casefolding. Diacritics remain significant; notes and other
fields are not searched. Search and the archive choice combine in the filter URL.
Clearing search keeps the archive choice; hiding archived varieties keeps the search. With JavaScript,
search applies after a short pause or Enter, and Back restores the filter URL and results.
Failed requests keep the current results and show **Retry**. Without JavaScript, use **Apply**.
Search opens matching groups. Active varieties remain visible under archived crops,
marked by the crop's archive badge.

**Growing areas** opens an overview of top-level area cards. Each card shows its name
and first photo when available, using the same layout as crop-category cards. Open a
card for the area's own page. Child-area cards appear before the parent's description,
dimensions, notes and photos. Each child opens its own page with the same
layout; the navigation above the heading links to the overview and ancestor areas.
Area pages show only plantings assigned directly to that area, across all years.
Each child area's plantings appear on its own page. The latest five linked diary
observations include descendant areas and archived associations. Each linked record
appears once. Select a planting or observation to open it;
**View all observations** appears when older observations exist.
**Plantings YEAR (count)** opens Plantings with the current year and that area selected;
its count and results include only plantings assigned directly to that area. This
selection continues through year changes and **All years**. The Plantings section
retains its year filters and histories. **Manage growing areas** on the overview opens
one maintenance list for top-level and child areas, identified by their full paths.
Use **Include archived** there to maintain archived areas. Edit, Archive/Restore,
confirmed Delete and photo maintenance are available in this management flow.
Save and Cancel from area editing, as well as archive/delete and photo operations,
return to management with the selected area and archive choice retained. Direct area
edit links use the same flow. Area browsing pages retain readable information and photos.

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
- Edit an area through management, or a variety through its details, to add photos;
  edit photo details to change captions or order.
  Deletion requires confirmation. A crop with varieties or an area with children cannot
  be deleted; archive it or reorganize its dependents first. Deleting an allowed record
  removes its photos with coordinated file cleanup after the database commit. Referenced
  crops, varieties and areas cannot be deleted.
- A variety used by any planting cannot change crop through an ordinary edit, even when
  the planting has ended. Reconcile its planting references explicitly first; changing
  the variety does not silently reclassify plantings.

Deleting a crop or variety from a category returns to that category with search and
archive filters preserved, including when the final visible record is removed.
Confirmation, Cancel and deletion errors retain that destination. Direct deletion
links without a valid surviving origin return to the register overview.

Variety **Experiences** displays its editable notes followed by linked evaluations
from completed plantings of that exact variety. Each planting member appears once,
with its growing years, assigned area and completion date when known. Older records
and archived associations are included. Evaluation corrections and clearing appear
immediately; reopening a cycle hides its result while retaining the planting's
evaluation. The display works in both the details panel and the full record page.

## Saving changes and retries

Diary, register, planting, event and economy forms reject stale edits instead of overwriting
another member's changes. After a failed or uncertain create/edit save, keep the form open and
retry the same content and save key; photos are not appended twice. With JavaScript,
validation updates feedback beside the fields while preserving fields and selected
files. Without it, reselect photos as instructed before retrying. A genuinely changed
form is a new submission; do not reuse a confirmed key for different content.

With JavaScript, save forms ignore repeated submissions while a save is running.
Diary filters can still update results without changing the draft or selected photos.

### Unsaved writing

With JavaScript, diary capture/edit and crop, variety, growing-area, planting,
event, cycle evaluation and economy forms recover unfinished text and related selections
after reload or returning to the form in the same tab. Drafts use `sessionStorage`,
separately for each account and form/record. Filters, language and season do not
create separate drafts. Adding a variety has a separate draft for each target batch.
Recovery lasts for the tab's session; closing the tab ends it. Drafts are not shared
with other tabs or devices. Explicit logout clears these
drafts, including in other open tabs when the browser supports logout notifications.

A recovery notice asks you to review the writing. **Discard unsaved writing**
removes the draft and resets the form to saved/default values. Cancel and ordinary
navigation keep the draft. The browser's native leave/reload warning appears only
while changes or uploads remain; browsers may omit it, especially on mobile.
Drafts are written while typing and when the page is hidden, without depending on
the leave warning. Diary filters and JavaScript season/language changes preserve
the current form and selected files.

Selected photo bytes and previews are never stored. After recovery, select the
same photos again, or use **Save without missing photos** before saving. That
button acknowledges the omission; it does not submit the form. For an uncertain
photo save, retry with the same original photos and save key or inspect the saved
record first. Receipt checks still reject changed retry content.

Confirmed saves clear their draft; validation errors, conflicts, expired sessions,
network failures and uncertain responses keep it and the original save key.
An unconfirmed submission restores its original values and save key for retry,
including when the earlier save changed the record or cycle members. Retry the
same content before making corrections; a matching receipt acknowledges that save,
while a stale submission that never committed is still rejected. Discard returns
to the current saved values.
For writing that was never submitted, a changed record or cycle member keeps the
current saved values in the form and offers the earlier writing in read-only fields
for selection and copying. Recovery does not replace stale version tokens with
fresh ones to replay an old edit.

Blocked or full browser storage shows a discreet recovery-unavailable message;
editing and normal submission still work. Search, filters, account, category and
deletion forms do not store drafts. Without JavaScript, native forms remain usable,
but unsaved writing has no browser recovery.

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
categories are ordered **Planned**, **Ongoing**, then **Completed**, each with its own
built-in illustration on its card and heading; every batch is one cycle with exactly one
status. **New category** creates a shared custom category, such as
For Sale. Assign a cycle to any number of custom categories through creation or
**Edit cycle**. An ongoing cycle assigned to For Sale appears in both cards.
Category names persist across years; each card's count and year describe the view.
Custom categories follow in normalized name order. **Manage categories** opens their
maintenance list; Save, Cancel and confirmed deletion return there. **Back to list**
preserves the overview's year and filters. Each custom category has one optional private
image, reused on its card and heading. Add, replace or remove it through category editing;
transparent PNG/WebP images retain transparency, and a default illustration appears
when no image is selected. Image limits and save retries match crop categories.
Confirmed category deletion removes its image and membership links while preserving
cycles, members, history and planting photos.

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
Rows show status only when the category does not supply it, and exact growing-year
ranges in All years. Crop, variety and area history lists retain both. Matching
batch members expand independently in the list. The compact panel shows the name,
status and selected member's area and years, followed by **Planting details**.
For a batch with several varieties, use the compact **Selected variety** switcher;
it changes the member's quantity, notes, evaluation, photos, events, observations
and editing actions together. Cycle status, actual dates and custom categories
remain shared. Failed switches keep the selected member visible and allow retry.
**Complete planting cycle** is primary for Ongoing, and **Start cycle** for Planned.
**Edit planting** and **Add variety** remain secondary; **Edit cycle** corrects
shared status, dates and categories. **Cultivation events (count)** keeps **Add
event** available and expands through **View events** when populated. **Diary
observations (count)** expands its latest five entries and links to older entries
when present. Empty sections stay brief. Photos remain viewable on the planting
details; their Edit photo details and Delete photo actions appear under **Saved
photos** in **Edit planting**, inside **Notes and photos**. Photo Save, Cancel and
confirmed deletion return to that edit form with its category/year context kept.
Save pending planting changes before opening photo maintenance. The photo viewer
also returns there when opened from editing. The form reloads
with the current planting version after photo maintenance. Existing history links
remain available.
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
  It also offers each member's existing evaluation in a separate optional field.
  Edit the text or clear it intentionally; omitted values are preserved. **Edit cycle**
  offers the same fields for corrections to Completed. A stale cycle or member edit
  must be reloaded before a new save; identical retries acknowledge the earlier save.
  Each persistent member's inclusive end year becomes that date's year; annual
  members keep their start year and have no end year. The viewed year never supplies
  the transition date. Invalid date ordering or incompatible member years reject
  the entire save. After saving, the category list refreshes; **Open record** reaches
  the cycle even if it no longer matches that category.
- **Edit cycle** is the route for correcting status and actual dates, including
  returning to Planned or reopening a Completed cycle. Reopening clears the completion
  date and persistent end years. Changing shared categories leaves member records
  intact. Transitions and corrections preserve IDs, events, photos and diary links;
  evaluations change only when supplied. Cycles with unknown actual dates can be
  corrected here.
- Filter by crop, variety or area, or find **Unassigned area only** or **Unknown variety
  only**. Area filters normally include the selected area and all descendants; each
  cycle counts once and member details show the actual assigned place.
  Archived register items stay visible in existing
  histories and associations.
- **New planting event** supports sowing, transplanting, replanting, harvest start/end
  and other milestones. Planned and actual dates are separate and at least one is required; dates
  do not determine the growing year or change lifecycle status. Repeated event types
  are valid. Each planting
  in a batch has its own events and evaluation. Event edits and deletion
  use version checks; deletion requires confirmation.
- Edit a planting to add cumulative evaluation text or photos. Photo controls match
  register photos. Planting deletion is permanent and removes its events and photos;
  completing a cycle is the history-preserving alternative. Deletion from a status or
  custom category returns to the same category with its year/All years and other filters
  retained, including an empty result. Confirmation, Cancel and errors retain that context.

Each planting has one current area. Correcting that assignment or reparenting an area
changes past-year results too. For an established plant that moves, prefer ending the
old planting and creating a new one at the destination; this preserves year-level
presence, not exact movement dates, lineage or historical layouts.

## Economy

Open **Economy** from the main menu. **Economy for this cycle** on a planting opens its
filtered estimates. Costs stay on the Economy pages. Filter by year, crop, variety
or planting cycle; **Compare varieties** adds the variety overview. Use **Apply** to
submit filters, or the neighbouring year links to retain them while changing year.

The yearly overview separates **Money spent** on purchases dated in that year from
**Growing costs** attributed to it. Both yearly differences use income received by
sale date in the selected year. Cycle estimates associate plant sales with their
source's growing year, which can differ from the year the income was received.
Crop and variety totals include directly assigned costs as well as their portions
of shared purchases; assigning a portion to a planting replaces its direct attribution.

**New expense** requires a description, purchase date and total price. The date
defaults to today in the garden's timezone and must be within the current year,
regardless of the year being browsed. Saved purchases retain that recorded year.
Open **More purchase details** for category, optional purchased quantity and unit,
crop/variety assignment, or equipment useful life. Litres and kilograms accept up
to three decimal places; items are whole numbers. Purchased quantity is separate
from price. A variety supplies its crop. Leave assignments blank to allocate later.

After saving, open **Allocate expense**. Choose a growing year and enter exactly one
monetary amount, percentage or estimated quantity used. Then choose selected cycles
or all cycles in that year, and divide the chosen amount by current plant quantities
or equally between cycles. For example, assigning 40% of a 100 purchase distributes
40 and leaves 60 available. A quantity uses the purchase's recorded unit and price;
routine watering or inventory logging is unnecessary. Use 100% to charge a whole
seed packet even when seeds remain unused. For equipment, percentages and quantities
apply to the selected year's annual share.

Ordinary unassigned costs stay in the purchase year. Assigning a portion to another
growing year moves only that portion's growing cost; money spent remains in the
purchase year. The purchase page shows assigned costs, unassigned costs and the
balance still available for new allocations. Edit or delete an allocation to change
its reserved amount or selected group.

Plant-count allocation recalculates from each cycle's current **Quantity**, including
every variety in a batch. Update that existing quantity to the successfully grown
count, including plants subsequently sold. An all-year group includes new matching
cycles automatically; a selected group includes new varieties within its selected
cycles. Recalculation redistributes the chosen amount without consuming the remaining
balance. Known zero quantities receive no share. If any quantity is unknown, all
are zero, or the group is empty, the split stays pending and its amount remains
visibly reserved and unassigned. It cannot also be assigned elsewhere. Equal division
does not require plant counts; cost per plant still needs a known positive count.
Pending plant-count splits also make cost-per-plant estimates unavailable.

For reusable equipment, choose **Equipment** and a **Useful life (whole years)**.
Its purchase cost is divided across consecutive calendar years starting in the
purchase year, including unused years. A 900 purchase with three years contributes
900 to money spent in its purchase year and 300 to growing costs in each year.
The purchase page separates the selected year's share and assignments from future
scheduled shares, with links to each year. Monetary splits preserve the exact total
after rounding to two decimal places. Changing cost or useful life updates the
schedule; reduce or remove allocations first if they would exceed a new annual
share or fall outside the new period. Saved allocations retain their chosen amounts.

**Record plant sale** accepts a description, date, whole number sold, either a unit
price or total amount, and a cycle or variety. A cycle supplies its crop and growing
year and supplies its variety when it has one member. For a shared cycle, select a
variety if known; a cycle-wide sale can leave it blank. A variety without a cycle
uses the sale date's year. Sales leave planting quantities unchanged. Cycle estimates
show allocated cost, cost per plant, sales income and income minus allocated cost.

The account/settings menu's **Currency label** defaults to SEK and is remembered
in this browser for one year.
Changing it to EUR or another label leaves every numeric amount unchanged: it
performs no conversion. Enter all amounts on one consistent numeric basis. With
JavaScript, label changes preserve the current page and unfinished form. Without
JavaScript, choose the label and use **Change**; save unfinished changes first.

All active members can create, edit and delete economy records. Saves and draft
recovery follow [Saving changes and retries](#saving-changes-and-retries). Deletion
requires confirmation. Deleting a purchase also removes its allocations and future
equipment shares. Financially referenced plantings, crops and varieties are protected
from deletion until the references are removed or reassigned. A directly referenced
variety cannot change crop, and a sold variety cannot be changed out of its source
cycle while its sales still refer to it. This is an estimate for personal growing and
occasional plant sales; harvested produce sales and formal accounting are outside its scope.

## Diary

Capture's optional **Link to a planting** section starts collapsed. Its bordered
control and chevron open the action selector; the chevron changes direction when
expanded. Select one action:

- **Start a new planting** requires a crop, or derives it from the selected variety.
  Variety and growing area are optional. It defaults to Ongoing, using the diary date
  for the actual start date and its year for the start year. Initial status, label,
  quantity, lifespan, end year, categories, notes, evaluation and planting photos
  remain editable.
- **Add an event** accepts a planting in any status and year. The picker includes crop,
  variety, area, year, status, label and batch context. Actual date defaults to the diary
  date; planned and actual dates stay independent. Repeated event types are allowed and
  events preserve cycle status.
- **Change cycle status** loads the selected planting's recorded cycle dates,
  categories and evaluations. **Start**, **Complete**, **Reopen** and **Return to Planned**
  fill the status controls; **Current status** also allows corrections between all three
  statuses. Saved dates stay intact until edited. Newly supplied dates follow changes
  to the diary date with JavaScript until manually overridden. Completion offers one
  prefilled, optional cumulative evaluation per member. Status applies to the whole
  cycle, with the same date-order and persistent end-year rules as Plantings.

The affected planting is linked automatically, with an already selected tag appearing
once. Its crop, variety and area supply the usual derived diary filters. Planting and
event notes are optional; the observation can be entered once. **Add photos** uploads
diary photos to the observation. **Add planting photos** appears when starting a new
planting, stays inside the planting fields, and uploads optional photos to that
planting. Each owner
has its own photo-count limit; the total byte limit applies to the whole upload.

One save commits the observation, action, links, evaluations and photos together.
Failures preserve the draft with JavaScript. Retry the same fields, photos and save key
after an uncertain response; the action runs once even if the planting changes later.
Editing, unlinking or deleting the observation does not repeat or undo its earlier action.
Without JavaScript, choose an existing planting and use **Load planting details** before
editing cycle fields. Untouched new-planting and event date defaults use the submitted
diary date; set cycle dates explicitly. After a failed save or a choice update,
select uploaded photos again before retrying.

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

The picker starts with compact expandable groups and counts for plantings, growing areas,
crops and varieties. Choose **All**, **Plantings**, **Growing areas**, **Crops** or
**Varieties** to scope browsing and search. Plantings are grouped by area, with the
observation's year first and **Other years** expandable; varieties are grouped by crop.
Growing areas show full parent paths. Search reveals matching groups, including planting
crop, variety, full area path, label and year. Match counts follow the selected type;
clearing search restores compact browsing. Group headings show selected counts and chips
remain visible across filters and collapsed groups. Enter from search opens/focuses the
first matching choice. Changing the observation date updates the planting ranking without
restricting links or changing selections. Without JavaScript, native disclosures and
checkboxes support both capture and editing.

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

Diary cards show saved captions below their photos. Observation pages show compact
photo previews; select one to view the larger image. The photo viewer has a direct
**Download original** button above the image. Authors and administrators also have
**Edit photo details** on the left and **Delete photo** on the right; deletion still
requires confirmation. Originals stay out of the ordinary diary view and are included
in operational backups. If your session expires, downloading opens the sign-in page
so you can regain access.

The account menu contains Language, Season, Currency label, Log out and, for staff, Operator. Language changes
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
