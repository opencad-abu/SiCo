"""Accent outlines shared by compact form controls."""

from .theme import active_palette


def accent_field_stylesheet(palette=None):
    """One red-brown outline around an editable value field."""
    palette = active_palette() if palette is None else palette
    return (
        "QSpinBox {"
        f" border: 1px solid {palette.accent};"
        " border-radius: 3px;"
        " padding: 2px 6px;"
        " }"
    )


def checkbox_stylesheet(palette=None):
    """Red-brown outline on the checkbox indicator, not its whole row."""
    palette = active_palette() if palette is None else palette
    return (
        "QCheckBox::indicator {"
        " width: 15px; height: 15px;"
        f" border: 2px solid {palette.accent};"
        " border-radius: 2px;"
        " background-color: transparent;"
        " }"
        " QCheckBox::indicator:checked {"
        f" background-color: {palette.accent};"
        " }"
    )
