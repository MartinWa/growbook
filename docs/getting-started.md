# Getting started

Growbook is a private, self-hosted garden diary with registers for areas, crops and
varieties, planting plans and a growing-cost overview. One installation is one shared
garden: every active account can read everything and add records.

- [Install and first sign-in](deployment.md#initialize-and-start) is in the deployment
  guide, together with [accounts and roles](administration.md#accounts-and-roles).
- Everything below applies to every screen. The feature guides reference it instead of
  repeating it: [Diary](diary.md), [Calendar](calendar.md),
  [Growing areas, crops and varieties](garden-registers.md), [Plantings](plantings.md) and
  [Economy](economy.md).

## Signing in and finding your way

There are no seeded credentials: an administrator creates accounts in **Operator**
(`/operator/`). After signing in, the main menu opens **Diary**, **Calendar**,
**Growing areas**, **Crops and varieties**, **Plantings** and **Economy**; `/garden/`
redirects to Growing areas. The account menu holds **Language**, **Season**, **Operator**
for administrators and **Log out**.

The interface is in Swedish by default; **English** is available in the account menu.
Language changes apply immediately and keep the current page and filters. Changing
language, season or filters never clears unfinished writing or selected photos.

## Seasons

**Season** in the account menu changes the colour scheme and the diary illustration:
**Spring**, **Summer**, **Autumn** and **Winter**. The default, **Automatic (follow
date)**, uses the garden's local date: March–May spring, June–August summer,
September–November autumn, December–February winter. Viewing older entries or changing a
date does not change the season.

The choice is remembered in this browser for a year, including after signing out; each
browser chooses independently. Without JavaScript, select a season and choose **Change**
to reload the page, and save unfinished forms first.

## Saving, errors and retries

Every form is a single save: a diary observation with its photos and planting action, or a
register record with its photo. When something fails, the form stays open with what you
entered.

- **A save either happened or it did not.** If a save seems to have failed or the
  connection dropped, retry the same content without changing it. Photos are not added
  twice; an identical retry is recognized as the same save instead of creating a duplicate
  record.
- **Validation messages** appear beside the affected fields and in a summary that links to
  them. Correct the fields and save again.
- **Someone changed the record while you had it open.** The save is rejected instead of
  overwriting the other change; reload to see the saved values and reapply your editing.
- **Repeated submissions** are ignored while a save is running.
- **Without JavaScript** every form still works. After a failed photo upload, select the
  photos again before retrying.

## Unsaved writing

While you type, forms keep your text and choices in the current browser tab. After a
reload, an accidental navigation or a failed save, the writing is restored and a notice
asks you to review it.

- Recovery covers diary capture and editing, crop, variety, growing-area, planting, event,
  cycle evaluation and economy forms. It lasts for the tab's session and is not shared
  between tabs, devices or accounts; signing out clears it.
- **Selected photos are never stored.** After recovery, select them again, or use **Save
  without missing photos** to acknowledge the omission. That button does not submit; save
  afterwards.
- **Discard unsaved writing** removes the draft and resets the form to its saved values.
  Cancel and ordinary navigation keep the draft.
- The browser's leave warning appears only while there are changes or uploads; browsers may
  omit it, especially on phones. Recovery does not depend on it.
- Drafts need JavaScript and available browser storage. If storage is blocked or full, a
  short message says so and editing still works.

## Photos

Photos can be added to diary entries, varieties, growing areas, plantings, crop and
planting categories, and edited with captions and order.

- **Add photos** opens the native image picker. Camera and library options come from the
  browser and operating system. Selected photos show previews and can be removed before
  saving; **Clear selected photos** empties the selection.
- **Limits** are 10 photos per diary entry, variety, area or planting, 20 MiB per photo,
  80 MiB per upload and 50 million decoded pixels per photo. A warning appears when the
  selected photos exceed a limit; the server enforces it as well. Each crop or planting
  category has at most one image, with the same file and pixel limits.
- **Formats** are JPEG, PNG, WebP and HEIC. Apple HDR JPEGs with one auxiliary grayscale
  gain map use the primary photo. Other multi-image and animated files are rejected.
  Photos are re-encoded for display, with orientation corrected and location/EXIF metadata
  removed.
- **Originals** are kept privately. Displayed images are protected derivatives; use
  **Download original** on the photo viewer. A signed-out download asks you to sign in
  again. There is no public media directory.
- Without JavaScript, the native file inputs stay visible; select all library photos at
  once.

## Version and updates

The footer shows **GrowBook**, the deployed version and a **GitHub** link; the version
links to releases. An orange dot with an **Update available** tooltip means a newer stable
release exists. Local development builds show their Git identifier or **Development** and
skip the check. The check contacts GitHub from the server and can be disabled; see
[Deployment configuration](deployment.md#configure-the-private-installation).

## Limits worth knowing

- **One shared garden.** Every active account reads all entries, records, photos and
  original downloads and maintains the registers; only an entry's author or an active
  administrator may edit or delete that entry. Roles are in
  [Administration](administration.md#accounts-and-roles).
- **Private by design.** Installations are meant for LAN or VPN access over HTTPS, not
  public Internet exposure. Verify a backup and a clean restore before recording real
  data; see [Backup and recovery](backup-and-recovery.md).
- **No portable export.** A backup restores an installation; it does not move garden data
  into another one.
- **Explicit, not automatic.** Diary tags are chosen by hand, with no text matching, and
  there is no structured harvest accounting: [Economy](economy.md) is a growing-cost
  estimate.
- **Archived is not deleted.** Archived records stay visible through existing links and
  filters but cannot be chosen for a new link; see
  [Garden registers](garden-registers.md#search-and-archive).
