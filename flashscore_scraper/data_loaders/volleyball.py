"""Multi-sport data loader for the flashscore_scraper package.

This module provides a flexible data loader for multi-sport databases
that efficiently handles JSON-based additional data fields.
"""

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd

from flashscore_scraper.data_loaders.base import BaseDataLoader


class Volleyball(BaseDataLoader):
    """Data loader for volleyball match data."""

    # Volleyball-specific column mappings
    COLUMN_MAPPINGS: Dict[str, str] = {
        "home_score": "home_sets",
        "away_score": "away_sets",
    }

    # Required additional data fields for volleyball
    REQUIRED_ADDITIONAL_FIELDS: Dict[str, str] = {
        "home_set1": "home_points_s1",
        "away_set1": "away_points_s1",
        "home_set2": "home_points_s2",
        "away_set2": "away_points_s2",
        "home_set3": "home_points_s3",
        "away_set3": "away_points_s3",
        "home_set4": "home_points_s4",
        "away_set4": "away_points_s4",
        "home_set5": "home_points_s5",
        "away_set5": "away_points_s5",
    }

    def __init__(
        self,
        db_path: Union[str, Path] = "database/database.db",
        connection: Optional[sqlite3.Connection] = None,
        date_format: str = "%d.%m.%Y %H:%M",
    ):
        """Initialize the sport specific data loader."""
        super().__init__("volleyball", db_path, connection, date_format)

    def load_matches(
        self,
        league: Optional[str] = None,
        seasons: Optional[List[str]] = None,
        date_range: Optional[Tuple[str, str]] = None,
        team_filters: Optional[Dict[str, Any]] = None,
        include_additional_data: bool = True,
    ) -> pd.DataFrame:
        """Load volleyball match data with optional filters."""
        df = self._load_matches(
            league=league,
            seasons=seasons,
            date_range=date_range,
            team_filters=team_filters,
            include_additional_data=include_additional_data,
        )
        df = df.rename(columns=self.COLUMN_MAPPINGS)
        df = self._add_common_features(
            df, home_goals="home_sets", away_goals="away_sets"
        )
        self.validate_data(df)
        return df

    def _validate_sport_specific(self, df: pd.DataFrame) -> None:
        """Perform volleyball-specific validation.

        Parameters
        ----------
        df : pd.DataFrame
            DataFrame to validate

        Raises:
        ------
        ValueError
            If volleyball-specific validation fails
        """
        if df.empty:
            return

        # Validate set scores are within valid range (typically 0-25, except 5th set 0-15)
        for set_num in range(1, 6):
            home_col = f"home_points_s{set_num}"
            away_col = f"away_points_s{set_num}"

            if home_col not in df.columns or away_col not in df.columns:
                continue

            max_points = 15 if set_num == 5 else 25
            invalid_scores = (
                (df[home_col] > max_points + 2)  # Allow for extended sets
                | (df[away_col] > max_points + 2)
                | (df[home_col] < 0)
                | (df[away_col] < 0)
            )

            if invalid_scores.any():
                invalid_matches = df[invalid_scores].index.tolist()
                self.logger.warning(
                    f"Found {len(invalid_matches)} matches with invalid set {set_num} scores: "
                    f"{invalid_matches[:5]}"
                )

    def _add_sport_specific_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add volleyball-specific features.

        Parameters
        ----------
        df : pd.DataFrame
            DataFrame to add features to

        Returns:
        -------
        pd.DataFrame
            DataFrame with added volleyball-specific features
        """
        if df.empty:
            return df

        # Calculate total points scored in each match
        point_columns = [
            col
            for col in df.columns
            if col.startswith(("home_points_", "away_points_"))
        ]
        if point_columns:
            df["total_points"] = df[point_columns].sum(axis=1)

        # Calculate number of sets played
        df["sets_played"] = df["home_sets"] + df["away_sets"]

        return df


if __name__ == "__main__":
    # Load volleyball data
    loader = Volleyball()
    df = loader.load_matches(
        include_additional_data=True,
    )
