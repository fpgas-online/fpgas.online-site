"""Serve ttsite.urls on the tinytapeout.fpgas.online host; leave every other host alone."""

from django.conf import settings


def serves_ttsite(request):
    """Whether the request came to the tinytapeout.fpgas.online host."""
    return request.get_host().split(":", 1)[0].lower() == settings.TTSITE_HOST.lower()


class TTSiteHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if serves_ttsite(request):
            request.urlconf = "ttsite.urls"
        return self.get_response(request)
