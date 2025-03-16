"""Base classes for data loaders.

This module provides abstract base classes and interfaces for data loaders
that handle multi-sport data with JSON-based additional data fields.
"""

import json
import logging
import sqlite3
from abc import ABC
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Union

import pandas as pd


class BaseDataLoader(ABC):
    """Flexible data loader for multi-sport database with JSON additional data support.

    This class provides a unified interface for loading and processing data
    from multiple sport_ids, with support for sport-specific attributes stored
    in JSON format.
    """

    # Class variable to store sport-specific column mappings
    COLUMN_MAPPINGS: ClassVar[Dict[str, str]] = {}

    # Class variable to store required additional_data fields and their new names
    REQUIRED_ADDITIONAL_FIELDS: ClassVar[Dict[str, str]] = {}

    def __init__(
        self,
        sport: str,
        db_path: Union[str, Path] = "database/database.db",
        connection: Optional[sqlite3.Connection] = None,
        date_format: str = "%d.%m.%Y %H:%M",
    ):
        """Initialize the base data loader.

        Parameters
        ----------
        sport : str
            Sport type (e.g., 'handball', 'football', 'basketball')
        db_path : Union[str, Path], optional
            Path to SQLite database file, by default "database/database.db"
        connection : Optional[sqlite3.Connection], optional
            Existing database connection, by default None
        date_format : str, optional
            Date format for parsing, by default "%d.%m.%Y %H:%M"
        """
        self.sport = sport.lower()
        self.db_path = Path(db_path)
        self.date_format = date_format
        self.conn = connection or self._create_connection()

        # Set up logging
        self.logger = logging.getLogger(f"{self.__class__.__name__}.{self.sport}")

        # Initialize cache for team mappings
        self.team_to_idx: Dict[str, int] = {}

        # Validate database structure
        self._validate_database()

    def _create_connection(self) -> sqlite3.Connection:
        """Establish database connection with error handling.

        Returns:
        -------
        sqlite3.Connection
            The SQLite database connection

        Raises:
        ------
        FileNotFoundError
            If the database file does not exist
        """
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found at {self.db_path}")

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # Enable foreign keys
        conn.execute("PRAGMA foreign_keys = ON")

        return conn

    def _validate_database(self) -> None:
        """Validate database structure for multi-sport data.

        Raises:
        ------
        ValueError
            If required tables are missing from the database
        """
        required_tables = {"sport_ids", "match_data", "flashscore_ids", "odds_data"}

        if self.conn is None:
            self.conn = self._create_connection()

        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}

        if not required_tables.issubset(existing_tables):
            missing = required_tables - existing_tables
            raise ValueError(f"Missing required tables: {', '.join(missing)}")

        # Verify sport exists in the database
        cursor.execute("SELECT id FROM sport_ids WHERE name = ?", (self.sport,))
        result = cursor.fetchone()

        if not result:
            raise ValueError(f"Sport '{self.sport}' not registered in the database")

        self.sport_id = result[0]

    def _get_current_teams(
        self,
        league: Optional[str] = None,
        seasons: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> pd.Series:
        """Retrieve current teams with optional filters.

        Parameters
        ----------
        league : Optional[str], optional
            League name, by default None
        seasons : Optional[List[str]], optional
            List of season identifiers, by default None
        filters : Optional[Dict[str, Any]], optional
            Additional filter criteria, by default None

        Returns:
        -------
        pd.Series
            Series of team names

        Raises:
        ------
        ValueError
            If no teams are found for the specified criteria
        """
        # Base query to get unique teams from match data
        base_query = """
            SELECT DISTINCT home_team as team_name
            FROM match_data
            WHERE sport_id = ?
        """
        params = [self.sport_id]

        # Add league filter if specified
        if league:
            base_query += " AND league = ?"
            params.append(league)

        # Add season filter if specified
        if seasons:
            season_placeholders = ", ".join(["?" for _ in seasons])
            base_query += f" AND season IN ({season_placeholders})"
            params.extend(seasons)

        # Add additional filters if specified
        if filters:
            for key, value in filters.items():
                if key in ["country", "league", "season"]:
                    base_query += f" AND {key} = ?"
                    params.append(value)

        # Union with away teams
        base_query += """
            UNION
            SELECT DISTINCT away_team as team_name
            FROM match_data
            WHERE sport_id = ?
        """
        params.append(self.sport_id)

        # Add league filter if specified
        if league:
            base_query += " AND league = ?"
            params.append(league)

        # Add season filter if specified
        if seasons:
            season_placeholders = ", ".join(["?" for _ in seasons])
            base_query += f" AND season IN ({season_placeholders})"
            params.extend(seasons)

        # Add additional filters if specified
        if filters:
            for key, value in filters.items():
                if key in ["country", "league", "season"]:
                    base_query += f" AND {key} = ?"
                    params.append(value)

        # Execute query
        teams = pd.read_sql(base_query, self.conn, params=params)["team_name"]

        if teams.empty:
            filter_desc = []
            if league:
                filter_desc.append(f"league='{league}'")
            if seasons:
                filter_desc.append(f"seasons={seasons}")
            if filters:
                filter_desc.extend([f"{k}='{v}'" for k, v in filters.items()])

            raise ValueError(
                f"No teams found for {self.sport} with filters: {', '.join(filter_desc)}"
            )

        return teams

    def _load_matches(
        self,
        league: Optional[str] = None,
        seasons: Optional[List[str]] = None,
        date_range: Optional[Tuple[str, str]] = None,
        country: Optional[str] = None,
        team_filters: Optional[Dict[str, Any]] = None,
        include_additional_data: bool = True,
    ) -> pd.DataFrame:
        """Load match data with flexible filtering options.

        Parameters
        ----------
        league : Optional[str], optional
            League name, by default None
        seasons : Optional[List[str]], optional
            List of season identifiers, by default None
        date_range : Optional[Tuple[str, str]], optional
            Tuple of (start_date, end_date) as strings, by default None
        team_filters : Optional[Dict[str, Any]], optional
            Additional filters for teams, by default None
        include_additional_data : bool, optional
            Whether to parse and include additional_data, by default True

        Returns:
        -------
        pd.DataFrame
            Processed DataFrame of match data

        Raises:
        ------
        sqlite3.Error
            If there is a database error
        """
        try:
            # Get teams based on filters
            teams = self._get_current_teams(league, seasons, team_filters)

            # Create team index mapping
            self.team_to_idx = {
                team: idx + 1 for idx, team in enumerate(sorted(teams.unique()))
            }

            # Build query for match data
            query = """
                SELECT
                    m.flashscore_id, m.country, m.league, m.season,
                    m.datetime, m.home_team, m.away_team,
                    m.home_goals, m.away_goals, m.result, m.additional_data
                FROM match_data m
                WHERE m.sport_id = ?
                AND m.home_team IN ({team_placeholders})
                AND m.away_team IN ({team_placeholders})
            """.format(team_placeholders=", ".join(["?" for _ in teams]))

            params = [self.sport_id]
            params.extend(teams)
            params.extend(teams)

            # Add league filter if specified
            if league:
                query += " AND m.league = ?"
                params.append(league)

            # Add country filter if specified
            if country:
                query += " AND m.country = ?"
                params.append(country)

            # Add season filter if specified
            if seasons:
                season_placeholders = ", ".join(["?" for _ in seasons])
                query += f" AND m.season IN ({season_placeholders})"
                params.extend(seasons)

            # Execute query
            df = pd.read_sql(
                query,
                self.conn,
                params=params,
                parse_dates="datetime",
            )

            # Apply date range filter if specified
            if date_range and not df.empty:
                df = df.query("@date_range[0] <= datetime <= @date_range[1]")

            if include_additional_data:
                df = self.process_additional_data(df)

            return df

        except sqlite3.Error as e:
            self.logger.error(f"Database error: {str(e)}")
            raise
        except Exception as e:
            self.logger.error(f"Error loading matches: {str(e)}")
            raise

    def load_odds(self, flashscore_ids: List[str]) -> pd.DataFrame:
        """Load odds data for given match IDs.

        Parameters
        ----------
        flashscore_ids : List[str]
            List of match IDs to load odds for

        Returns:
        -------
        pd.DataFrame
            DataFrame containing odds data

        Raises:
        ------
        sqlite3.Error
            If there is a database error
        """
        if not flashscore_ids:
            return pd.DataFrame()

        try:
            # Build query for odds data
            query = """
                SELECT
                    o.flashscore_id,
                    b.name as bookmaker_name,
                    o.home_odds, o.draw_odds, o.away_odds
                FROM odds_data o
                LEFT JOIN bookmaker_ids b ON o.bookmaker_id = b.id
                WHERE o.sport_id = ?
                AND o.flashscore_id IN ({id_placeholders})
            """.format(id_placeholders=", ".join(["?" for _ in flashscore_ids]))

            params = [self.sport_id]
            params.extend(flashscore_ids)

            # Execute query
            odds_df = pd.read_sql(query, self.conn, params=params)

            # Process odds data if not empty
            if not odds_df.empty:
                # Set index to flashscore_id for easier joining
                odds_df = odds_df.set_index("flashscore_id")

            return odds_df

        except sqlite3.Error as e:
            self.logger.error(f"Database error loading odds: {str(e)}")
            raise
        except Exception as e:
            self.logger.error(f"Error loading odds: {str(e)}")
            raise

    def load_fixtures(
        self,
        league: Optional[str] = None,
        seasons: Optional[List[str]] = None,
        country: Optional[str] = None,
    ) -> pd.DataFrame:
        """Load fixture data for given league and seasons.

        Parameters
        ----------
        league : Optional[str], optional
            League name to filter by, by default None
        seasons : Optional[List[str]], optional
            List of season identifiers to filter by, by default None
        country: Optional[str] = None,

        Returns:
        -------
        pd.DataFrame
            DataFrame containing fixture data

        Raises:
        ------
        sqlite3.Error
            If there is a database error
        """
        try:
            # Build query for fixture data
            query = """
                SELECT
                    f.flashscore_id, f.country, f.league, f.season,
                    f.datetime, f.home_team, f.away_team
                FROM upcoming_fixtures f
                WHERE f.sport_id = ?
            """
            params = [self.sport_id]

            # Add league filter if specified
            if league:
                query += " AND f.league = ?"
                params.append(league)

            # Add country filter if specified
            if country:
                query += " AND f.country = ?"
                params.append(country)

            # Add season filter if specified
            if seasons:
                season_placeholders = ", ".join(["?" for _ in seasons])
                query += f" AND f.season IN ({season_placeholders})"
                params.extend(seasons)

            # Execute query
            df = pd.read_sql(
                query,
                self.conn,
                params=params,
                parse_dates="datetime",
            )

            return df

        except sqlite3.Error as e:
            self.logger.error(f"Database error loading upcoming_fixtures: {str(e)}")
            raise

    def process_additional_data(
        self,
        df: pd.DataFrame,
        column: str = "additional_data",
        default: Any = None,
        flatten: bool = True,
    ) -> pd.DataFrame:
        """Parse a JSON column in a DataFrame.

        Parameters
        ----------
        df : pd.DataFrame
            DataFrame containing the JSON column
        column : str, optional
            Name of the column containing JSON strings, by default "additional_data"
        default : Any, optional
            Default value for parsing errors, by default None
        flatten : bool, optional
            Whether to flatten the JSON structure into separate columns, by default True

        Returns:
        -------
        pd.DataFrame
            DataFrame with parsed JSON data
        """
        if column not in df.columns or df.empty:
            return df

        # Function to safely parse JSON
        def safe_json_parse(json_str, default_val=None):
            if pd.isna(json_str):
                return default_val
            if isinstance(json_str, dict):
                return json_str
            try:
                return json.loads(json_str) if isinstance(json_str, str) else json_str
            except (json.JSONDecodeError, TypeError):
                return default_val

        # Parse the JSON column
        parsed_series = df[column].apply(
            lambda x: safe_json_parse(x, default_val=default)
        )

        if not flatten:
            # Just return the parsed objects in the column
            return df.assign(**{column: parsed_series})

        # Flatten the JSON into separate columns
        if parsed_series.empty:
            return df

        # Create new columns for each key
        result_df = df.copy()

        # Extract data using the mapping in REQUIRED_ADDITIONAL_FIELDS
        for orig_field, new_column_name in self.REQUIRED_ADDITIONAL_FIELDS.items():
            result_df[new_column_name] = parsed_series.apply(
                lambda x: x.get(orig_field) if isinstance(x, dict) else None
            )
            result_df[new_column_name] = pd.to_numeric(
                result_df[new_column_name], errors="coerce"
            ).astype("Int64")

        result_df = result_df.drop(columns="additional_data")

        return result_df

    def validate_data(self, df: pd.DataFrame, warnings: bool = False) -> bool:
        """Validate the loaded data.

        Parameters
        ----------
        df : pd.DataFrame
            DataFrame to validate

        Returns:
        -------
        bool
            True if data is valid, False otherwise

        Raises:
        ------
        ValueError
            If validation fails with specific error
        """
        if df.empty:
            return True

        # Check for required columns
        required_columns = ["home_goals", "away_goals", "result"]
        missing_columns = [col for col in required_columns if col not in df.columns]

        if missing_columns:
            raise ValueError(f"Missing required columns: {', '.join(missing_columns)}")

        # Validate score consistency if halftime scores are available
        if "home_goals_h1" in df.columns and "away_goals_h1" in df.columns:
            # Check if any matches have more goals at halftime than at full time
            invalid_home = df["home_goals_h1"] > df["home_goals"]
            invalid_away = df["away_goals_h1"] > df["away_goals"]

            if invalid_home.any() or invalid_away.any():
                invalid_matches = df[invalid_home | invalid_away].index.tolist()
                raise ValueError(
                    f"Found {len(invalid_matches)} matches with inconsistent scores "
                    f"(halftime > full time): {invalid_matches[:5]}"
                )

        # Validate result consistency
        invalid_result = (
            ((df["home_goals"] > df["away_goals"]) & (df["result"] != 1))
            | ((df["home_goals"] < df["away_goals"]) & (df["result"] != -1))
            | ((df["home_goals"] == df["away_goals"]) & (df["result"] != 0))
        )

        if invalid_result.any():
            invalid_matches = df[invalid_result].index.tolist()
            if warnings:
                self.logger.warning(
                    f"Found {len(invalid_matches)} matches with inconsistent results: "
                    f"{invalid_matches[:5]}"
                )

        # Allow subclasses to perform additional validation
        self._validate_sport_specific(df)

        return True

    def _validate_sport_specific(self, df: pd.DataFrame) -> None:
        """Perform sport-specific validation.

        This method can be overridden by subclasses to add additional
        validation rules specific to each sport.

        Parameters
        ----------
        df : pd.DataFrame
            DataFrame to validate

        Raises:
        ------
        ValueError
            If sport-specific validation fails
        """
        pass

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
