"""equinox.intelligence
-----------------------

Public entry points for the intelligence helpers.

This module exposes the high-level Recommender class so callers can import
``from equinox.intelligence import Recommender`` without referencing the
internal module path.
"""

from .recommender import Recommender  # re-export for convenience

__all__ = ["Recommender"]

