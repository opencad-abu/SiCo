"""Legacy import boundary; common cadenv owns vendor EDA environment restoration.

Remove this facade after callers switch to the common API in the SiCo migration.
"""

from cadenv import restore_eda_temp_environment

__all__ = ["restore_eda_temp_environment"]
