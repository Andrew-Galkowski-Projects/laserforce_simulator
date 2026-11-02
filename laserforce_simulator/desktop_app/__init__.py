"""DESK-01 (ADR-0048): the Desktop App's settings, URLconf, sign-in and launcher.

A plain package, not an ``INSTALLED_APP``: it has no models. The Hosted deploy
imports only ``notice`` and ``build_info`` (through ``accounts/me/``).
"""
