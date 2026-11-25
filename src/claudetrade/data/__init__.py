"""Point-in-time data layer.

Every stored value carries ``source`` and ``available_at`` (UTC): the earliest moment it
could have been known. All reads go through :class:`PITStore` with an ``as_of`` timestamp
and can never return anything with ``available_at > as_of``.
"""

from claudetrade.data.errors import DataValidationError, SnapshotIntegrityError
from claudetrade.data.pit import PITStore
from claudetrade.data.snapshots import SnapshotStore
from claudetrade.data.universe import Universe

__all__ = [
    "DataValidationError",
    "PITStore",
    "SnapshotIntegrityError",
    "SnapshotStore",
    "Universe",
]
