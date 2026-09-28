"""Present defaults-probe updates and busy state for the selected cell."""

from __future__ import annotations


class DefaultsPresentation:
    def __init__(self, *, defaults, drafts, selection, form, generation_result,
                 source_text, controller_busy, enable_run, set_busy_cursor, invalidate):
        self.defaults = defaults
        self.drafts = drafts
        self._selection = selection
        self._form = form
        self._generation_result = generation_result
        self._source_text = source_text
        self._controller_busy = controller_busy
        self._enable_run = enable_run
        self._set_busy_cursor = set_busy_cursor
        self._invalidate = invalidate
        self._busy_cursor = False

    def cancel(self) -> None:
        self.defaults.cancel()
        self.update_cursor()

    def loading(self) -> bool:
        return self.defaults.busy

    def update_cursor(self) -> None:
        """Show a non-blocking busy cursor while PDK defaults are loading."""

        loading = self.loading()
        if loading == self._busy_cursor:
            return
        self._busy_cursor = loading
        if loading:
            self._set_busy_cursor(True)
        else:
            self._set_busy_cursor(False)

    def refresh_form(self) -> None:
        applied = self.defaults.take_updates()
        if not applied:
            return
        if self._generation_result() is not None:
            self._invalidate("PDK runtime defaults loaded")
        if (self._selection.active, self._form.dialect) in applied:
            self._form.load(self.drafts.view(self._selection.active, self._form.dialect))

    def enqueue(self, key, *, dialects=None) -> None:
        if key not in self.drafts or not self._source_text().strip():
            return
        self._selection.save()
        self.defaults.enqueue(key, dialects or (self._form.dialect,))
        self.refresh_form()
        self._enable_run(not self._controller_busy() and not self.defaults.busy)
        self.update_cursor()

    def advance(self) -> None:
        self.defaults.advance()
        self.refresh_form()
        self._enable_run(not self._controller_busy() and not self.defaults.busy)
        self.update_cursor()

    def apply(self, report, *, requested_key=None, automatic_probe=False) -> None:
        self._selection.save()
        self.defaults.apply(
            report, requested_key=requested_key, automatic_probe=automatic_probe,
        )
        self.refresh_form()
