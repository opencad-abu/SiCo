"""Legacy imports for shared chat projections.

Remove after supported callers migrate to sico.service.chat_* imports.
The service module owns the sole implementation.
"""

from sico.service.chat_reconcile import reconcile_notice as reconcile_notice
