"""The one local, controlled server bundled with the application (loopback only).

This package exists so a journey has a page it may safely visit. It is *not* a
source of search data: a ranking is read from the search engine's own result pages,
which is the business of :mod:`src.automation`.
"""
