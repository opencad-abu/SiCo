"""Legacy import for the shared local process owner.

Migrate consumers to sico.local_process. Remove this same-object export only
after supported downstream imports migrate in an incompatible API version.
"""

from ..local_process import LocalProcess as LocalProcess
