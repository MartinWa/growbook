from django.contrib.auth.middleware import LoginRequiredMiddleware
from django.middleware.locale import LocaleMiddleware
from django.utils.cache import patch_cache_control
from django.utils.translation import gettext as _

from .uploads import PhotoLimitUploadHandler


class PhotoUploadLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and (
            request.path == "/capture/" or request.path.startswith("/garden/")
        ):
            request.upload_handlers.insert(0, PhotoLimitUploadHandler(request))
        return self.get_response(request)


class GardenLocaleMiddleware(LocaleMiddleware):
    """Use Swedish unless an explicit language cookie was chosen; ignore OS locale."""

    def process_request(self, request):
        from django.conf import settings
        from django.utils import translation

        chosen = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        language = chosen if chosen in {code for code, _ in settings.LANGUAGES} else "sv"
        translation.activate(language)
        request.LANGUAGE_CODE = language


class GardenLoginRequiredMiddleware(LoginRequiredMiddleware):
    def handle_no_permission(self, request, view_func):
        if request.headers.get("HX-Request") == "true":
            from django.http import HttpResponse

            return HttpResponse(
                _(
                    "Your session expired. Log in in another tab, then retry. "
                    "Keep this form open so your selected photos are not lost."
                ),
                status=401,
                content_type="text/plain; charset=utf-8",
            )
        return super().handle_no_permission(request, view_func)


class PrivateResponseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' blob: data:; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        response["X-Content-Type-Options"] = "nosniff"
        patch_cache_control(response, private=True, no_store=True)
        return response
