# API access

GrowBook can serve the garden as JSON to another tool on the same private network,
authenticated with a bearer token that you create and revoke yourself. A bearer token
carries the same permissions as its owner: it reads the garden like the screens and also
creates, changes, archives and deletes records through the same validated operations, so
the API cannot bypass a rule the interface enforces. Reads return the stored data
unchanged. A token never authenticates a browser page.

The API is served by the same installation on the same private origin described in
[Deployment](deployment.md); it adds no port, hostname or outbound connection.

## Create a token

Open the account menu (the user icon in the header) and choose **API tokens**
(`/accounts/tokens/`).

1. Give the token a name, for example the machine that will read the garden.
2. Optionally set an expiry. A token without one keeps working until you revoke it.
3. Submit the form. The secret is shown once in the response; copy it then.

Only a SHA-256 hash and an eight-character prefix of each secret are stored, so a lost
secret cannot be recovered or displayed again — replace it with a new token. The page
lists each token's name, prefix, creation time, expiry and state (active, revoked or
expired). Names are unique per account; two accounts may reuse the same name.

**Revoke** stops a token immediately: the next request that presents it is rejected.
A revoked or expired token can then be deleted, which asks for confirmation on its own page
first, then removes the row and frees the name. Creation, revocation and deletion are
ordinary form posts on signed-in pages and require the usual CSRF token.

## Authenticate a request

Send the secret in an `Authorization: Bearer` header:

```sh
curl -H "Authorization: Bearer <secret>" \
  "https://garden.example.internal/api/v1/plantings/?year=2026"
```

Every endpoint except the JSON error responses requires a valid token. A missing,
malformed, unknown, revoked or expired secret, or one whose account is no longer active,
returns `401` with a JSON body. Browser sessions are not accepted as API credentials,
and a bearer token does not authenticate any browser page.

Tokens read the same shared garden data for every account. There are no per-account
garden records, so a token created by a staff member sees exactly what a token created by
any other signed-in account sees.

## Endpoints

All paths are under `/api/v1/`. List endpoints accept `GET`, `HEAD`, `OPTIONS` and `POST`;
detail endpoints also accept `PUT`, `PATCH` and `DELETE`. Any other method returns `405`
without changing data. Authentication is checked first, so an unauthenticated request is
answered with `401` rather than `405`.

| Path | Returns |
|---|---|
| `/api/v1/` | The version, the authentication scheme and a link to each resource |
| `/api/v1/schema/` | This API's OpenAPI document as JSON; public, like the docs page |
| `/api/v1/crops/`, `/api/v1/crops/<id>/` | Crops with their categories |
| `/api/v1/varieties/`, `/api/v1/varieties/<id>/` | Varieties with their crop |
| `/api/v1/areas/`, `/api/v1/areas/<id>/` | Growing areas with their parent |
| `/api/v1/plantings/`, `/api/v1/plantings/<id>/` | Plantings with cycle, crop, variety and area |
| `/api/v1/events/`, `/api/v1/events/<id>/` | Planting events, including the linked diary entry |
| `/api/v1/evaluations/` | Plantings whose stored evaluation is not empty (read-only) |
| `/api/v1/observations/`, `/api/v1/observations/<id>/` | Diary observations and what each one links to |

Photos are not exposed or uploaded through the API, and neither are accounts, submission
receipts or the internal sort and search keys.

### Writes

`POST` on a list endpoint creates a record and answers `201` with it; `PUT` replaces a
record and `PATCH` changes only the given fields, both answering `200`; `DELETE` answers
`204`. Writes are validated by the same service operations the forms use, so the same
rules apply: names are unique per register scope, references must exist, and a record
that is still referenced cannot be deleted (archive it instead).

A create may send an `Idempotency-Key` header with a UUID that identifies the attempt:
sending the same key again answers with the record the first attempt created instead of
making a duplicate, and a value that is not a UUID is a `400`.

`PUT`, `PATCH` and `DELETE` require an `If-Match` header carrying the record's current
`updated_at` value in full, time zone included: a timestamp without an offset is refused
with `400`. The header turns a lost update into a `409` instead of overwriting a newer
change; without it the answer is `428`. A `409` also answers a save that was already
applied differently. The request bodies and every response are described by the
[OpenAPI document](#documented-schema).

The writable fields are the ones the read endpoint returns, minus `id`, `created_at`,
`updated_at`, `url` and computed references: `name`, `notes` and `archived` for crops;
`crop`, `name`, `description`, `source`, `experiences` and `archived` for varieties;
`name`, `parent`, `area_type`, `length_cm`, `width_cm`, `description`, `notes` and
`archived` for areas; `crop`, `start_year`, `variety`, `area`, `lifespan`, `end_year`,
`label`, `quantity`, `notes` and `evaluation` for plantings; `planting`, `kind`,
`planned_date`, `actual_date` and `note` for events; and `date`, `body`, `title`,
`links` and `show_in_calendar` for observations, where `links` is a list of
`{"type": "area"|"crop"|"variety"|"planting", "id": <id>}`. Evaluations are part of a
planting's cycle and stay read-only here.

A crop, variety or area created with `archived: true` is created archived, and `archived`
on `PUT` or `PATCH` is applied in the same validated write as the other fields. An
observation may be written with an empty `body` when it keeps at least one photo, which
the [diary rule](diary.md#writing-an-observation) already allows.

### Filters

Filters are query parameters on the list endpoints. Unknown parameter names are ignored,
a blank value filters nothing, and an unusable value returns `400`. Detail endpoints ignore
filters.

| Resource | Parameters |
|---|---|
| Crops | `archived=true\|false`, `category=<id>`, `search=<text>` |
| Varieties | `crop=<id>`, `archived=true\|false`, `search=<text>` |
| Areas | `parent=<id>\|none`, `archived=true\|false`, `area_type=<text>`, `search=<text>` |
| Plantings | `year=<1-9999>`, `crop=<id>`, `variety=<id>`, `area=<id>`, `cycle=<uuid>`, `status=planned\|ongoing\|completed`, `lifespan=annual\|persistent` |
| Events | `planting=<id>`, `kind=<event kind>`, `year=<1-9999>`, `crop=<id>`, `area=<id>` |
| Evaluations | `cycle=<uuid>`, `crop=<id>`, `variety=<id>`, `area=<id>`, `year=<1-9999>` |
| Observations | `from=<date>`, `to=<date>`, `year=<1-9999>`, `area=<id>`, `crop=<id>`, `variety=<id>`, `planting=<id>`, `calendar=true\|false`, `q=<text>` |

An `area` filter covers that growing area and its descendants, matching the filters in
the interface. A `search` value is compared with the interface's normalized name key, so
case is ignored for Swedish letters too: `ärtor` finds `Ärtor`. `year` means the growing year that belongs to the resource: for plantings and
evaluations an annual planting belongs to its start year and a persistent one to every year
it was grown, for events it selects the year of the planned or actual date, and for
observations it selects the diary date.

## Paging

List endpoints are paginated with `page` and `page_size`. The page size defaults to 50 and
is capped at 200, so a larger `page_size` returns 200 records; an unusable `page_size` falls
back to the default. A `page` beyond the last one returns `404`. Records are returned in a
stable order per resource, so a client can page without duplicates.

```json
{
  "count": 137,
  "next": "https://garden.example.internal/api/v1/plantings/?page=2",
  "previous": null,
  "results": [{"id": 12, "...": "..."}]
}
```

## Values

- Stored text is returned unchanged: `label`, `notes`, `description`, `source`,
  `experiences`, `evaluation`, `body` and `title` hold exactly what was saved, including
  an empty string when nothing was written.
- A value that was never recorded is `null`, never an invented zero: `quantity`,
  `length_cm`, `width_cm` and `end_year`. `quantity: 0` is a real recorded zero.
- Dates are `YYYY-MM-DD`; timestamps are ISO 8601 in the garden's configured time zone.
- Identifiers are stable integers, except a planting cycle, which is a UUID.
- `url` fields are absolute links to the corresponding interface page, built from the
  request, so they work behind any hostname.

## Errors

| Status | Body | Meaning |
|---|---|---|
| `400` | `{"detail": "...", "parameter": "year"}` or `{"name": ["..."]}` | A filter value cannot be used, a write body failed validation, an `Idempotency-Key` is not a UUID, or `If-Match` has no time zone |
| `401` | `{"detail": "A valid bearer token is required."}` | Missing, malformed, unknown, revoked or expired token, or an inactive account |
| `404` | `{"detail": "No Crop matches the given query."}` | Unknown identifier on a detail endpoint, or a `page` past the last one |
| `405` | `{"detail": "Method \"POST\" not allowed."}` | The method is not allowed on this endpoint (for example any write on `/evaluations/`) |
| `409` | `{"detail": "..."}` | The record changed since it was read (stale `If-Match`) |
| `428` | `{"detail": "Send If-Match with the record's current updated_at value."}` | A write without the `If-Match` header |

## Documented schema

The same OpenAPI 3.1 description is served live: `/api/docs/` renders it in the browser
with the self-hosted Swagger UI as a standalone page with no site chrome (no account or
token is needed — the page is public), and `GET /api/v1/schema/` returns the document
itself as JSON.

[`openapi.json`](openapi.json) is the committed copy of that document: every path,
its filters and the JSON schema of each response. It is generated from the same
serializers the API uses, and a test fails when the committed file and the code disagree.
Regenerate it after changing the API:

```sh
docker compose -f compose.development.yaml run --rm app \
  python manage.py api_schema --output docs/openapi.json
```

## Operational notes

- Treat a secret like a password: it opens the garden registers, plantings and diary for
  both reading and writing, with the same access as its account. Keep it in the client's
  own private configuration, never in a shared file or a URL.
- Revocation is immediate and per token, so rotate a credential by creating the new one,
  switching the client, then revoking the old one.
- A backup contains only token hashes, not usable secrets; restoring a database restores
  the rows, so review the token page after a restore and revoke what you no longer use.
- Keep the installation private as described in [Deployment](deployment.md): LAN or VPN
  access over HTTPS, no published ports, and no public DNS.
