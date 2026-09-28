"""Read and revise turn settings in the editor's authoritative draft."""

from copy import deepcopy

from sico.service.published import thaw


def draft_options(editor, catalog):
    value = editor.turn_options
    return thaw(value if value is not None else catalog["selected"])


def model_row(catalog, options):
    return next((row for row in catalog["models"]
                 if row["model"] == options.get("model")), {})


def store_options(editor, options, catalog):
    selected = thaw(catalog["selected"])
    selected.setdefault("serviceTier", "default")
    value = deepcopy(options)
    value.setdefault("serviceTier", "default")
    value = None if value == selected else value
    if editor.turn_options != value:
        editor.turn_options = value
        editor._revise_draft()
