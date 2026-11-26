# API access

GrowBook can serve a read-only JSON copy of the garden to another tool on the same
private network, authenticated with a bearer token that you create and revoke yourself.
Nothing in the API writes: there is no endpoint that creates, changes or deletes a
record, and a token never authenticates a browser page.

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

All paths are under `/api/v1/` and accept `GET`, `HEAD` and `OPTIONS`; any other method
returns `405` without changing data. Authentication is checked first, so an unauthenticated
write is answered with `401` rather than `405`.

| Path | Returns |
|---|---|
| `/api/v1/` | The version, the authentication scheme and a link to each resource |
| `/api/v1/crops/`, `/api/v1/crops/<id>/` | Crops with their categories |
| `/api/v1/varieties/`, `/api/v1/varieties/<id>/` | Varieties with their crop |
| `/api/v1/areas/`, `/api/v1/areas/<id>/` | Growing areas with their parent |
| `/api/v1/plantings/`, `/api/v1/plantings/<id>/` | Plantings with cycle, crop, variety and area |
| `/api/v1/events/`, `/api/v1/events/<id>/` | Planting events, including the linked diary entry |
| `/api/v1/evaluations/` | Plantings whose stored evaluation is not empty |
| `/api/v1/observations/`, `/api/v1/observations/<id>/` | Diary observations and what each one links to |

Photos are not exposed, and neither are accounts, submission receipts or the internal
sort and search keys.

### Filters

Filters are query parameters on the list endpoints. Unknown parameter names are ignored;
an unusable value returns `400`. Detail endpoints ignore filters.

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
the interface. `year` means the growing year that belongs to the resource: for plantings and
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
| `400` | `{"detail": "...", "parameter": "year"}` | A filter value cannot be used |
| `401` | `{"detail": "A valid bearer token is required."}` | Missing, malformed, unknown, revoked or expired token, or an inactive account |
| `404` | `{"detail": "No Crop matches the given query."}` | Unknown identifier on a detail endpoint |
| `405` | `{"detail": "Method \"POST\" not allowed."}` | A write method was attempted |

## Documented schema

[`openapi.json`](openapi.json) is the OpenAPI 3.1 description of version 1: every path,
its filters and the JSON schema of each response. It is generated from the same
serializers the API uses, and a test fails when the committed file and the code disagree.
Regenerate it after changing the API:

```sh
docker compose -f compose.development.yaml run --rm app \
  python manage.py api_schema --output docs/openapi.json
```

## Operational notes

- Treat a secret like a password: it grants read access to the garden registers,
  plantings and diary. Keep it in the client's own private configuration, never in a
  shared file or a URL.
- Revocation is immediate and per token, so rotate a credential by creating the new one,
  switching the client, then revoking the old one.
- A backup contains only token hashes, not usable secrets; restoring a database restores
  the rows, so review the token page after a restore and revoke what you no longer use.
- Keep the installation private as described in [Deployment](deployment.md): LAN or VPN
  access over HTTPS, no published ports, and no public DNS.
