"""Subtle alternating bands for click-heavy session history trees."""

from __future__ import annotations


def enable_row_stripes(tree):
    """Turn on zebra rows and mark the view for the theme's alternate colour."""
    tree.setAlternatingRowColors(True)
    tree.setProperty("stripeRows", True)
    return tree
