# Interface foundations

The interface uses the committed, self-hosted `static/css/app.css`, built from
`assets/app.css` with the pinned Tailwind CSS and daisyUI versions in `package.json`,
system body fonts, a self-hosted Barlow Condensed heading font and server-rendered
Django templates. Rebuilding that stylesheet needs
Node.js and pnpm; running the application does not.

## Tokens

Colour roles map to daisyUI theme variables: `--color-base-100`, `--color-base-200`,
`--color-base-300`, `--color-base-content`, `--color-primary`, `--color-secondary`,
`--color-accent`, `--color-neutral`, `--color-info`, `--color-success`,
`--color-warning`, `--color-error` and `--color-focus`. Warning, success and error
surfaces have their own roles and content colours. Keep error and status meaning in
text as well as colour.

The `growbook` theme in `assets/app.css` supplies shared controls and status colours.
Four palettes selected through the root's `data-season` attribute change the page and
card surfaces, text, borders, primary and secondary actions, accents, muted text and
focus colour. Spring uses sage and cream, summer deeper leaf green and warm ivory,
autumn earth brown and parchment, and winter blue green and cool pale surfaces.
Status roles retain the same meaning in every season.
The browser-local choice and automatic date rules are described in
[Seasonal appearance](usage.md#seasonal-appearance).

Page headings use regular, upright Barlow Condensed, uppercase presentation and
responsive sizing from roughly 36 to 56 px at the default text size. The
[font's original license and pinned source](../third_party/barlow-condensed/README.md)
are retained with the application. The GrowBook wordmark keeps its system font.
Spacing uses Tailwind's quarter-rem
steps. `--radius-selector`, `--radius-field` and `--radius-box` set the small,
medium and large radii. The base font is 106.25% of the browser default, with a
1.6 line height, and `--size-field` gives a 44 px control target at that size.

The four main pages share a 1200 px content limit, horizontal padding aligned with
the header, and the same heading offset. Their illustrated headings reserve the
same height, with growing-area, crop/variety and planting illustrations filling
nearly half the heading width. Narrow layouts reduce the heading height.

## Components

- Buttons and links use daisyUI's `btn` with `btn-primary` for the primary
  action, `btn-outline` for secondary actions and `btn-error` for destructive ones.
  Use one primary action in a form. Disabled controls retain their label; an
  uploading form uses `aria-busy` and a visible status line.
  Generated photo removal/clear controls use the same button classes. Filter chips
  grow with wrapped text and retain the shared minimum control height.
- `.field` places a label above its control, with `.field-help` and
  `.field-errors` underneath. Create/edit forms mark required and optional
  fields in words and associate help/errors through `aria-describedby`.
  Their error summaries link to fields; HTMX feedback also updates adjacent
  errors and `aria-invalid` without replacing the draft or selected files.
- `.panel` uses a surface, border and large radius; `.panel-header` separates
  its heading and introductory text from the form. Panels have no shadow.
- Login and `.empty-state` panels reuse the decorative favicon mark. Empty states
  have an explanation and one action for creating a record or adjusting the view.
  The self-hosted 180×180 touch icon uses the same artwork on an opaque background.
- `.save-confirmation`, `.error-summary` and `.archive-label` use the shared
  status colours. Save feedback keeps its live-region semantics.
- `.icon` renders the inline [Lucide subset](../third_party/lucide/README.md)
  with a two-pixel stroke. Icons accompany text labels and are hidden from
  assistive technology with `aria-hidden="true"` and `focusable="false"`.
  A control's accessible name comes from its text.
- `.disclosure-menu` uses native `details`/`summary`. The account menu and the
  viewer's Photo options open as bordered overlays; photo limits open inline
  below their summary. All are nonmodal. Escape returns focus to their summary;
  clicking outside closes them without taking focus from the clicked control.
- `.filter-disclosure` uses native `details`/`summary` above Diary and Plantings
  content at every width. It starts collapsed unless filters have errors; active
  choices stay visible in the results. Expanded fields use an adaptive grid.
  The Clear filters action is a button, with How filters work aligned to the right
  in the same action row.
  With JavaScript, Escape closes it and returns focus to the summary without
  clearing the search. Without JavaScript, the same disclosure and form remain usable.

## Capture layout and interaction

The diary has a decorative, text-free seasonal tree header above its collapsed
filters. Its four optimized, self-hosted WebP illustrations occupy the same banner
area; headings remain ordinary translated HTML. Growing areas, crops and varieties,
and plantings share a heading with a fixed decorative WebP illustration. Their
text and controls use the selected seasonal palette.

The diary uses one inline composer with a compact date, optional title, three-row note
and generous minimum writing area. Editing
an existing observation also includes its saved photos and a Cancel action. The
note grows with its content where native `field-sizing` is supported, with
a maximum height and ordinary textarea scrolling for longer text.
The inline composer and observation editing put **Add tags** and **Add photos** on the
left of one action row and **Save observation** on the right. The row wraps on narrow
screens. Native grouped tag checkboxes are enhanced into a searchable popover with
removable chips; see the [diary workflow](usage.md#diary). Existing observations align
their edit actions to the right. Optional entry titles appear beneath the date in
regular, uppercase Barlow Condensed and wrap at every width. Cards with notes and
photos put photos beside the complete note on wide screens and above it on narrow
ones. Photo-only cards use
the available width. Images use the protected display derivative and preserve their
aspect ratio; galleries show every photo. Notes remain fully visible at every width.

With JavaScript, Add photos is one button that opens the native image picker
directly. Camera/library options and their language are supplied by the browser
and operating system. Without JavaScript, separate camera and library inputs
are visible in one column below 600 px and
two columns above it. Photo limits appear with warnings after a selection exceeds
the configured count or byte limits.

`components/photo_picker.html` and `photos.js` share this behavior across capture,
observation editing and register forms. Register and edit inputs share the existing
`photos` field name, so Django combines both sources through its multiple-file
widget. Selection and removal update native FileLists where supported; previews
use object URLs, released on replacement or page exit. Removing a preview keeps
focus on another removal control or the chooser. Errors link back to the chooser
when native inputs are hidden. Selected counts show the chosen number; count warnings
include existing photos kept after any marked removals. Warnings do not discard files
or replace server validation.
When FileList assignment is unavailable, native replacement selection remains
usable and a Clear selected photos action empties both inputs.

Full-form actions stick to the bottom below 600 px when the viewport is at least
600 px tall. Their surface and safe-area padding keep controls legible; field/error
scroll margins leave space for the actions. Quick-add keeps its normal layout.

Diary results, counts, filter chips and pagination share one server-rendered
partial. The shared `filters.js` module replaces list results and updates filter URLs;
it does not cache private HTML in history or browser storage. Filtering leaves
the capture form and FileLists in place. Invalid filter responses still replace the
results summary, copy field errors back beside the fields and mark the controls
invalid and open the disclosure so errors remain visible. A
successful quick save returns a receipt and a fresh submission key; focus moves to
the saved entry when it matches the current results, or stays on the receipt link
otherwise.

Keyboard focus uses a visible three-pixel outline. Button transitions use
daisyUI's 200 ms timing and are disabled when `prefers-reduced-motion` requests
reduced motion. The strict content security policy remains unchanged; icons,
styles and scripts require no runtime downloads from third parties.

With JavaScript, the main header sticks to the top and its measured height keeps
anchor targets below it as navigation wraps or text size changes. The header and
form actions return to normal flow when either consumes more than a quarter of
the viewport height. Without JavaScript, the header stays in normal document flow.
On narrow screens, main navigation moves below the wordmark and account control.

`season.js` changes only appearance attributes and the browser's season cookie,
preserving the current form and its files. The server reads the same bounded choice
before rendering, so it also works without JavaScript and survives page navigation.
The ordinary POST form retains CSRF protection and returns only to validated
root-relative paths.

Original downloads are available in the photo viewer's Photo options disclosure,
with keyboard and Escape handling shared with the account menu. Diary photos link
to the viewer; their captions show ordinary photo-maintenance actions.

For local operation and checks, use the canonical
[development guide](development.md#test-and-check). Browser checks cannot
verify device-native camera/library pickers; those require the intended phone.

## Register layout and interaction

Main navigation links directly to Diary, Growing areas, Crops and varieties, and
Plantings; the former Garden landing URL redirects to Growing areas. Growing areas
reuse the crop-category grid and card styles for top-level areas and child areas.
Cards open dedicated pages with ancestor navigation, child cards before the parent's
information, protected photos, linked plantings and diary observations. Their links and
archive controls use ordinary HTML navigation at every screen width.
Crop headings use native disclosures; variety names select their information independently
of those disclosures. Name lists use `.record-list`, `.record-name` and `.record-info`,
with underlined names, the selected name highlighted and archived records labelled.
Varieties show one thumbnail when available. Record observations use a native disclosure
with their total count; the link to all observations appears inside when the five-item
preview omits older entries. Crop maintenance links sit in their own disclosure.
The combined register uses a compact search/archive form with the shared filter module,
debounced search, request cancellation, URL history, visible errors and a Retry action.
The compact form stays above results at every width. Search, archive and creation
actions share a row when they fit and wrap on narrower screens. The search label and
help remain available to assistive technology; the placeholder identifies the visible
field. Submit and retry controls occupy space only when available.
A single archive-visibility action
beside creation links preserves search, shows its selected state and uses ordinary GET
navigation without JavaScript.

Crop/variety and planting results share `.record-workspace`: a list and one selected
details panel. JavaScript moves the existing detail containers into that panel and
closes the previous selection when another name is activated. At 900 px and wider
the list and details sit side by side; below that width they stack. Details remain
visible when a list branch closes. Before selection, the panel explains how to choose
a record. Without JavaScript the panel stays hidden and names link to full records.
Selections bring off-screen details into view and move focus to their labelled region.
**Back to list** returns focus to the selected name and reveals its ancestor groups.
This navigation happens during activation. Delayed responses do not repeat it,
and browser history retains its saved scroll position.

Growing-area cards and planting results use bounded area reads and parent IDs to
retain ancestor context and cut legacy cycles defensively. Planting results render
the hierarchy iteratively, supporting deep nesting without recursive template includes.
Native details/summary controls keep planting-area expansion usable with keyboard and
without JavaScript. Growing-area cards retain archived ancestors of active children.
Planting branches retain ancestors, omit empty subtrees and open
when their matching subtree has one crop/variety pair. Batches attach only to their own
area, with their names selecting information through the shared register module. An unassigned
group follows the hierarchy. Indentation is reduced on narrow screens, names wrap and controls retain
their touch targets and focus outlines. Browser history restores crop-register disclosure
states, the selected details and scroll after returning from editing. Stored state contains
the list URL, record IDs, disclosure flags and scroll position. Information reloads from
authenticated endpoints; no record content is cached in history.
Other result replacements use the default disclosure states.

Planting forms show crop and start year first. Native disclosures group additional
details and notes/photos; populated or invalid groups open automatically. A typed
end year stays visible when switching to Annual so it can be corrected. Dependent
variety failures open their group and expose a retry action.

Planting filters share the diary disclosure and URL history behavior. Returned
lists initialize the shared details panel after each filter update. Planting
lists show area and batch names at every width; all other fields and history links are
in opened information. Nested event tables retain their own horizontal scrolling.
The filter toggle and creation action share one row when space allows; expanded
filters still span the content width. Result counts and growing-year navigation
share a compact row, with active filter chips underneath.
