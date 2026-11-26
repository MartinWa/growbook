"""Bounded page-number pagination for the read-only API."""

from rest_framework.pagination import PageNumberPagination


class BoundedPageNumberPagination(PageNumberPagination):
    """A client may page through results but cannot request an unbounded page."""

    page_size_query_param = "page_size"
    max_page_size = 200
