"""Require explicit supported route methods on submitted schCreateWire calls."""

from .skill_forms import executable_calls


def wire_errors(roots, text):
    for _, operator, arguments in executable_calls(roots):
        if operator.value != "schCreateWire":
            continue
        # A cellview expression may contain infix access tokens (cv~>parent).
        # Literal entry methods delimit it without interpreting that expression.
        entry = next(
            (
                index
                for index, arg in enumerate(arguments)
                if arg.token.kind == "string"
                and text[arg.token.start : arg.token.end] in {'"draw"', '"route"'}
            ),
            None,
        )
        index = entry + 1 if entry is not None else 2
        method = arguments[index] if index < len(arguments) else None
        if (
            method is None
            or method.prefixes
            or method.token.kind != "string"
            or text[method.token.start : method.token.end] not in {'"direct"', '"full"'}
        ):
            yield (
                "wire_route_method_required",
                'schCreateWire t_routeMethod must be the literal "direct" or "full"; '
                '"flight" and unresolved route methods are forbidden, including with "draw".',
                method.token if method is not None else operator,
            )
