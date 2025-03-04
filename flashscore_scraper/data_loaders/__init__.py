"""Data loaders for the flashscore_scraper package.

This package contains data loaders for different sports that handle
JSON-based additional data fields and sport-specific data processing.
"""

from .base import BaseDataLoader
from .football import Football
from .handball import Handball
from .volleyball import Volleyball

__all__ = [
    "BaseDataLoader",
    "Football",
    "Handball",
    "Volleyball",
]
