"""This module contains the models used in the flashscore_scraper package."""

from .base import FlashscoreConfig, LeagueConfig, MatchOdds, MatchResult, SportConfig

__all__ = [
    "MatchResult",
    "LeagueConfig",
    "SportConfig",
    "FlashscoreConfig",
    "MatchOdds",
]
