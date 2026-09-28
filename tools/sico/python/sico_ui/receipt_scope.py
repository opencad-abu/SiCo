"""One command's originating page, independent of its business completion."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReceiptScope:
    handle: object
    activation: int
    reviewing: object
    connection: tuple
    target: object

    @classmethod
    def capture(cls, page, context, handle=None):
        return cls(handle or page.session, page.activation, page.reviewing,
                   (context.instance_id, context.generation), context)

    def current(self, page, context, api, closing):
        try:
            return (not closing and self.handle == page.session
                    and api.owns(self.handle) and self.activation == page.activation
                    and self.reviewing == page.reviewing
                    and self.connection == api.connection(self.handle)
                    and self.target == context)
        except ValueError:
            # The runtime can retire between the ownership and connection reads.
            return False
