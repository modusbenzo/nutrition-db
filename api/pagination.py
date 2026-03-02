"""
Custom pagination for search results.

Uses LimitOffsetPagination to avoid COUNT(*) on millions of rows.
"""

from rest_framework.pagination import LimitOffsetPagination


class SearchPagination(LimitOffsetPagination):
    """Fast pagination without full COUNT(*) — ideal for large tables."""

    default_limit = 25
    max_limit = 100
