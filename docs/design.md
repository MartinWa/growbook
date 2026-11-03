# Interface foundations

The interface uses the existing self-hosted `static/css/growbook.css`, system
fonts and server-rendered Django templates. There is no frontend build step.

## Tokens

Colour roles are `--paper`, `--surface`, `--ink`, `--muted`, `--green`,
`--line`, `--error`, `--warning` and `--focus`. Warning and success surfaces
have separate tokens, and `--field-line` provides the stronger border used
by inputs. Keep error and status meaning in text as well as colour.

Spacing uses `--space-1`, `2`, `3`, `4`, `6`, `8` and `12`, in quarter-rem
increments. The rem unit follows the user's preferred font size. Small,
medium and large radii are quarter-, half- and three-quarter-rem. The base
font is 106.25% of the browser default, with a 1.6 line height.

## Components

- Buttons and `.button` links share a minimum 44 by 44 px target. The default
  is the primary green action; `.quiet` is secondary and `.danger` is
  destructive. Use one primary action in a form. Disabled controls retain
  their label; an uploading form uses `aria-busy` and a visible status line.
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
- `.disclosure-menu` uses native `details`/`summary` with a bordered overlay.
  Account and photo-options menus are nonmodal. Escape returns focus to their summary;
  clicking outside closes them without taking focus from the clicked control.
- `.filter-drawer` uses a modal `dialog`, with first/last Tab handling, Escape,
  a labelled close action and focus returned to its trigger. The same filter
  form moves between the drawer and sidebar at 900 px. Without JavaScript,
  the filter disclosure remains available.

## Capture layout and interaction

The dedicated capture panel is at most 700 px wide. Save and Cancel are
full-width actions on small screens, with Save first. Quick-add uses the same
form, a three-row note, compact date and a link to the dedicated page. The
note grows with its content where native `field-sizing` is supported, with
a maximum height and ordinary textarea scrolling for longer text.
Garden-link guidance follows the dedicated form's actions.

With JavaScript, Add photos is one button that opens the native image picker
directly. Camera/library options and their language are supplied by the browser
and operating system. Without JavaScript, separate camera and library inputs
are visible in one column below 600 px and
two columns above it. Photo limits use a short line and a native disclosure
with the full formats and byte limits.

`components/photo_picker.html` and `photos.js` share this behavior across capture,
observation editing and Garden forms. Garden and edit inputs share the existing
`photos` field name, so Django combines both sources through its multiple-file
widget. Selection and removal update native FileLists where supported; previews
use object URLs, released on replacement or page exit. Removing a preview keeps
focus on another removal control or the chooser. Errors link back to the chooser
when native inputs are hidden. Selected counts show the chosen number out of the
configured capacity; on edit forms the capacity subtracts existing photos kept after
any marked removals.
Count warnings include retained photos and do not
discard files or replace server validation.
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
invalid; on phones the open drawer announces the check-filters message too. A
successful quick save returns a receipt and a fresh submission key; focus moves to
the saved entry when it matches the current results, or stays on the receipt link
otherwise.

Keyboard focus uses a visible three-pixel outline. Button transitions last
140 ms and are disabled when `prefers-reduced-motion` requests reduced motion.
The strict content security policy remains unchanged; icons, styles and
scripts require no runtime downloads from third parties.

Original downloads are available in the photo viewer's Photo options disclosure,
with keyboard and Escape handling shared with the account menu. Diary photos link
to the viewer; their captions show ordinary photo-maintenance actions.

For local operation and checks, use the canonical
[README instructions](../README.md#test-and-check). Browser checks cannot
verify device-native camera/library pickers; those require the intended phone.

## Garden layout and interaction

Garden cards link to each register and its creation form, with active record counts.
Archive controls use a native disclosure, apply on checkbox change and retain an
Apply fallback without JavaScript. An active archive state includes a clear link.

Planting forms show crop and start year first. Native disclosures group additional
details and notes/photos; populated or invalid groups open automatically. A typed
end year stays visible when switching to Annual so it can be corrected. Dependent
variety failures open their group and expose a retry action.

Planting filters share the diary sidebar/drawer and URL history behavior. Returned
rows initialize independent expansion controls after each filter update. Tables
keep one Details control in the identifying cell. Below 650 px, planting rows show
batch, crop and area; other values remain in expanded details. Register rows hide
the status column but keep archived badges alongside names. Nested event tables
retain their own horizontal scrolling.
