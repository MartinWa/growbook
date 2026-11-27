"""Bearer credentials for the API.

One resolver keeps API authentication and the token page in step: the presented secret
is hashed and compared in constant time, and only an active, unexpired token of an
active account authenticates. Browser sessions never authenticate an API call.
"""

import hashlib
import hmac

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from core.models import PREFIX_LENGTH, ApiToken

CREDENTIAL_MESSAGE = "A valid bearer token is required."


def presented_secret(request):
    """The raw secret from an Authorization header, or None when there is none."""
    header = request.headers.get("Authorization", "")
    scheme, separator, secret = header.partition(" ")
    if scheme.lower() != "bearer" or not separator or not secret.strip():
        return None
    return secret.strip()


def resolve_token(secret):
    """The credential this secret belongs to, or None when it cannot be used."""
    if not secret:
        return None
    supplied = hashlib.sha256(secret.encode()).hexdigest()
    # The prefix narrows the candidates; the hash comparison decides.
    for token in ApiToken.objects.filter(prefix=secret[:PREFIX_LENGTH]).select_related("user"):
        if not hmac.compare_digest(token.key_hash, supplied):
            continue
        if not token.is_active or not token.user.is_active:
            return None
        return token
    return None


class ApiTokenAuthentication(BaseAuthentication):
    """Authenticate one bearer credential per request."""

    def authenticate(self, request):
        secret = presented_secret(request)
        if secret is None:
            return None
        token = resolve_token(secret)
        if token is None:
            raise AuthenticationFailed(CREDENTIAL_MESSAGE)
        request.api_token = token
        return token.user, token

    def authenticate_header(self, request):
        return 'Bearer realm="api"'
