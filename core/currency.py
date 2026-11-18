"""Browser-local currency labels; records and numeric amounts have no currency."""

CURRENCY_COOKIE = "growbook_currency"
CURRENCY_COOKIE_AGE = 365 * 24 * 60 * 60
CURRENCY_LABELS = ("SEK", "EUR", "USD", "GBP", "NOK", "DKK")


def currency_context(request):
    label = request.COOKIES.get(CURRENCY_COOKIE, "SEK")
    return {
        "currency_label": label if label in CURRENCY_LABELS else "SEK",
        "currency_labels": CURRENCY_LABELS,
    }
