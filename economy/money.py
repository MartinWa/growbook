"""Exact two-decimal money and deterministic, total-preserving integer splits."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

MAX_CENTS = 999_999_999_999
CENT = Decimal("0.01")


def decimal_value(value, field, *, places=2):
    if isinstance(value, (float, bool)) or not isinstance(value, (str, int, Decimal)):
        raise ValidationError({field: _("Enter a valid decimal number.")})
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValidationError({field: _("Enter a valid decimal number.")}) from exc
    if not number.is_finite() or abs(number) > Decimal(MAX_CENTS):
        raise ValidationError({field: _("Enter a valid decimal number.")})
    if number != number.quantize(Decimal(1).scaleb(-places)):
        raise ValidationError(
            {field: _("Use no more than %(places)s decimal places.") % {"places": places}}
        )
    return number


def to_cents(value, field="amount"):
    amount = decimal_value(value, field)
    if amount < 0 or amount * 100 > MAX_CENTS:
        raise ValidationError({field: _("Enter an amount from 0 to 9,999,999,999.99.")})
    return int(amount * 100)


def money(cents):
    return (Decimal(cents) / 100).quantize(CENT)


def rounded_cents(value):
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def split_cents(total, weights):
    """Largest remainders; caller order breaks ties and zero weights never get a share."""
    denominator = sum(weights)
    if denominator <= 0:
        raise ValueError("A split needs a positive total weight.")
    divisions = [divmod(total * weight, denominator) for weight in weights]
    shares = [whole for whole, _ in divisions]
    remaining = total - sum(shares)
    order = sorted(range(len(weights)), key=lambda index: (-divisions[index][1], index))
    for index in order[:remaining]:
        shares[index] += 1
    return shares
