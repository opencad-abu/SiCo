"""Legacy imports for shared chat projections.

Remove after supported callers migrate to sico.service.chat_* imports.
The service module owns the sole implementation.
"""

from sico.service.chat_text import activity_text as activity_text
from sico.service.chat_text import resource_notice as resource_notice
from sico.service.chat_text import tool_result_text as tool_result_text
