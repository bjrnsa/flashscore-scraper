"""Data loader for football matches with JSON additional data support."""

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd

from flashscore_scraper.data_loaders.base import BaseDataLoader


class Football(BaseDataLoader):
    """Flexible data loader for multi-sport database with JSON additional data support.

    This class provides a unified interface for loading and processing data
    from multiple sports, with support for sport-specific attributes stored
    in JSON format.
    """

    # football-specific column mappings
    COLUMN_MAPPINGS: Dict[str, str] = {
        "home_goals": "home_goals",
        "away_goals": "away_goals",
    }

    # Required additional data fields for football
    REQUIRED_ADDITIONAL_FIELDS: Dict[str, str] = {
        "home_goals_1st_half": "home_goals_h1",
        "away_goals_1st_half": "away_goals_h1",
        "home_goals_2nd_half": "home_goals_h2",
        "away_goals_2nd_half": "away_goals_h2",
    }

    def __init__(
        self,
        db_path: Union[str, Path] = "database/database.db",
        connection: Optional[sqlite3.Connection] = None,
        date_format: str = "%d.%m.%Y %H:%M",
    ):
        """Initialize the sport specific data loader."""
        super().__init__("football", db_path, connection, date_format)

    def load_matches(
        self,
        league: Optional[str] = None,
        seasons: Optional[List[str]] = None,
        date_range: Optional[Tuple[str, str]] = None,
        team_filters: Optional[Dict[str, Any]] = None,
        include_additional_data: bool = True,
    ) -> pd.DataFrame:
        """Load football match data with optional filters."""
        df = self._load_matches(
            league=league,
            seasons=seasons,
            date_range=date_range,
            team_filters=team_filters,
            include_additional_data=include_additional_data,
        )
        df = df.rename(columns=self.COLUMN_MAPPINGS)
        df = self._add_common_features(df)
        self.validate_data(df)
        return df

    def _validate_sport_specific(self, df: pd.DataFrame) -> None:
        """Perform football-specific validation.

        Currently no additional validation rules for football.
        This method can be extended in the future if needed.
        """
        pass

    def _add_sport_specific_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add football-specific features.

        Currently no additional features for football.
        This method can be extended in the future if needed.
        """
        return df


if __name__ == "__main__":
    # Load football data for a specific league and season
    loader = Football()
    df = loader.load_matches(
        league="Herre Handbold Ligaen",
        seasons=["2024/2025"],
        include_additional_data=True,
    )
