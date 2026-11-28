# Administration

For the person running an installation and for administrators of a shared garden. Daily
use is described in [Getting started](getting-started.md) and the feature guides; this page
covers accounts, access, data safety and updates.

## Accounts and roles

There is no public signup and no seeded credentials. Accounts are created in **Operator**
(`/operator/`), the Django administration site, which only staff accounts can open.

- **Active account**: reads everything in the shared garden and maintains registers,
  plantings, events and economy records. An active account may edit or delete its own diary
  entries, and any entry if it is staff.
- **Staff administrator**: opens **Operator** and may edit or delete any diary entry.
  Marking an account staff is what grants the operator screens; the diary, photo, register,
  planting and economy screens in **Operator** are read-only and are not the way to maintain
  records — use the normal application screens.
- **Deactivated account**: cannot sign in, and a deactivated author cannot write even while
  signed in. Deactivation keeps the author's entries and their attribution.

The garden is shared within one installation. Every active account reads all entries,
records, photos and original downloads; only the author or an administrator may change an
entry. There are no per-user gardens and no read-only member roles.

An account also manages its own API tokens in **API tokens** in the account menu: it can
create, revoke and delete them, and cannot see another account's tokens. A token reads the
same shared data as the account that created it; see [API access](api.md).

Create the first account before signing in:

```sh
docker compose run --rm growbook python manage.py createsuperuser
```

## First-run checklist

1. Install and start the instance as described in [Deployment](deployment.md).
2. Create the first account, sign in and open **Operator** to create the other accounts.
3. Open **Growing areas**, add your areas, then crops and varieties and their categories.
   See [Garden registers](garden-registers.md).
4. Add or plan plantings, then write observations in the **Diary**. See
   [Plantings](plantings.md) and [Diary](diary.md).
5. If you want growing costs, set the currency label in configuration and add purchases in
   **Economy**. See [Economy](economy.md).
6. Take a backup and verify a clean restore before recording real data; see
   [Backup and recovery](backup-and-recovery.md).

## Access and privacy

Keep the installation private: HTTPS through an existing managed reverse proxy, reachable
over LAN or VPN, with no published ports, public DNS or router forwarding. Deployments are
not hardened for public Internet exposure. The proxy, environment configuration and
ownership settings are covered in
[Deployment](deployment.md#configure-the-private-installation).

- In production, sign-in sessions use secure cookies over HTTPS. Signing out clears the
  session and the browser's unsaved drafts where the browser supports logout
  notifications.
- Repeated sign-in failures are limited in the application, not in the proxy: five failures
  for the same username from the same connection address lock that pair out of the sign-in
  page and **Operator** for one hour, and a successful sign-in clears the count. Every address
  is counted separately, so a stranger failing sign-ins cannot lock the account owner out, and
  a retry while locked out does not restart the hour. The address is the connection address:
  behind the proxy the server resolves it from the `X-Forwarded-For` value the trusted proxy
  sets when `GROWBOOK_TRUST_PROXY=true`, and the limit itself never reads a request header.
  While a username and address are locked out, a sign-in POST is answered with a bare 429 page
  instead of the form. Failed attempts and lockouts are logged on the `axes` logger, so they
  appear in the rotating container log; no table of submitted usernames, addresses or user
  agents is kept.
- Lockout state is one counter row per username and address. It stops blocking when the hour
  passes, and `manage.py axes_reset` removes it sooner, while `axes_reset_username` and
  `axes_reset_ip` clear one account or address.
- The CSRF cookie stays readable by the application's own JavaScript
  (`CSRF_COOKIE_HTTPONLY` stays `False`) because `static/js/capture-actions.js` and
  `static/js/form-feedback.js` read the `csrftoken` cookie to send their requests. The
  session cookie keeps `HttpOnly`, and the content security policy keeps other scripts out.
- Uploaded media is served only to signed-in accounts. Originals are downloads, not public
  files, and there is no public media directory.
- The only outbound request is the optional release check described in
  [Getting started](getting-started.md#version-and-updates). Disable it with
  `GROWBOOK_UPDATE_CHECK=false` for an offline installation.

## Data, backups and upgrades

- All application state lives in one SQLite database and a media directory under
  `GROWBOOK_DATA_DIR`, on a filesystem local to the application host. SMB/NFS-mounted
  databases are not supported; runtime details are in
  [Deployment](deployment.md#sqlite-runtime).
- Back up with the application's backup command. Never copy a live SQLite database file.
  The procedure, restore test and orphan cleanup are in
  [Backup and recovery](backup-and-recovery.md).
- Apply image updates as described in
  [Upgrade an existing installation](deployment.md#upgrade-an-existing-installation), with a
  verified backup first. Migrations run at startup unless you configure otherwise.
- `cleanup_media` removes photo files that no record references, for example after a crash
  between publishing a file and committing the database. Run it without `--apply` first to
  see what would be removed.

## Version and releases

The footer shows the deployed version and an **Update available** indicator as described in
[Getting started](getting-started.md#version-and-updates). Updates are installed manually
by pulling or building the new image; there is no automatic deployment. Release builds,
version stamping and update-check configuration are in [Deployment](deployment.md) and
[Development](development.md#release-images).

Report vulnerabilities through the private channel in [SECURITY.md](../SECURITY.md). Use
synthetic examples in issues and never attach real photos, backups, credentials or private
configuration.
