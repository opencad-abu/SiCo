"""Terminal data and product branding have separate, validated resource owners."""


def _resource_path(relative):
    from sicoresources import terminal_resource

    return terminal_resource(relative)


def _logo_candidates():
    from sicoresources import icon

    path = icon("brand", "logo.png")
    return (path,) if path is not None else ()
