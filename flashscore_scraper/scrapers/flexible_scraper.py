"""Module for flexible sports data scraping with filtering capabilities."""

import logging
from typing import Dict, List, Mapping, Optional, Set, TypedDict, Union

from flashscore_scraper.scrapers.base import BaseScraper
from flashscore_scraper.scrapers.match_data_scraper import MatchDataScraper
from flashscore_scraper.scrapers.odds_data_scraper import OddsDataScraper

# Configure logging
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


class MatchRecord(TypedDict):
    """Type definition for match record dictionary."""

    match_id: str
    sport_name: str
    sport_id: int
    country: str
    league: str
    season: str


class FlexibleScraper(BaseScraper):
    """A flexible scraper that allows selective data extraction based on filters.

    This scraper combines functionality from MatchDataScraper and OddsDataScraper,
    adding the ability to filter matches based on various criteria such as sport,
    league, season, and country. It provides efficient database operations and
    progress tracking.

    Attributes:
    ----------
    filters : Dict[str, List[str]]
        Active filters for data selection
    data_scraper : MatchDataScraper
        Component for scraping match details
    odds_scraper : OddsDataScraper
        Component for scraping betting odds
    """

    def __init__(
        self,
        db_path: str = "database/database.db",
        filters: Optional[Mapping[str, Union[str, List[str]]]] = None,
    ):
        """Initialize the FlexibleScraper with database path and optional filters.

        Parameters
        ----------
        db_path : str, optional
            Path to SQLite database file. Parent directories will be created
            if they don't exist. Defaults to "database/database.db".
        filters : Optional[Mapping[str, Union[str, List[str]]]], optional
            Filters to apply during scraping. Can be a mapping of filter names
            to either single strings or lists of strings. Defaults to None.

            Supported filters:
            - sports: List[str]
                Names of sports to include (e.g., ["football", "basketball"])
            - leagues: List[str]
                Names of leagues to include (e.g., ["Premier League", "La Liga"])
            - seasons: List[str]
                Seasons to include in "YYYY/YYYY" format (e.g., ["2023/2024"])
            - countries: List[str]
                Countries to include (e.g., ["England", "Spain"])

        Raises:
        ------
        ValueError
            If any filters are invalid or if season format is incorrect
        """
        super().__init__(db_path)
        self.filters: Dict[str, List[str]] = {}

        # Process and validate filters
        if filters:
            for key, value in filters.items():
                if isinstance(value, str):
                    self.filters[key] = [value.strip()]  # Clean input
                elif isinstance(value, list):
                    self.filters[key] = [
                        str(v).strip() for v in value
                    ]  # Clean and convert
                else:
                    raise ValueError(
                        f"Filter values must be strings or lists, got {type(value)}"
                    )
        self._validate_filters()

        # Initialize component scrapers
        self.data_scraper = MatchDataScraper(db_path)
        self.odds_scraper = OddsDataScraper(db_path)

    def _validate_filters(self) -> None:
        """Validate the provided filters for correctness and format.

        This method performs comprehensive validation of all filter types:
        - Checks for valid filter keys
        - Validates season format and values
        - Ensures all filter values are non-empty strings
        - Verifies season years are within valid range and consecutive

        Raises:
        ------
        ValueError
            If any of the following conditions are met:
            - Invalid filter keys are provided
            - Season format is incorrect (must be "YYYY/YYYY")
            - Season years are not consecutive
            - Season years are outside valid range (1900-2100)
            - Empty filter values are provided
            - Filter values contain invalid characters
        """
        # Define valid filter keys and validate
        valid_filter_keys = {"sports", "leagues", "seasons", "countries"}
        if invalid_keys := set(self.filters.keys()) - valid_filter_keys:
            raise ValueError(
                f"Invalid filter keys: {invalid_keys}. "
                f"Valid keys are: {', '.join(sorted(valid_filter_keys))}"
            )

        # Validate all filter values are non-empty and contain valid characters
        for key, values in self.filters.items():
            if not values:  # Check for empty list
                raise ValueError(f"Empty filter list provided for: {key}")

            for value in values:
                if not value or not value.strip():
                    raise ValueError(f"Empty string value found in filter: {key}")
                if any(c in value for c in "\"';"):  # Basic SQL injection prevention
                    raise ValueError(
                        f"Invalid characters found in {key} filter value: {value}"
                    )

        # Validate season format and values
        if "seasons" in self.filters:
            current_year = 2025  # TODO: Get from system or config
            for season in self.filters["seasons"]:
                if not isinstance(season, str):
                    raise ValueError(
                        f"Season must be a string, got {type(season)}: {season}"
                    )

                if season.count("/") != 1:
                    raise ValueError(
                        f"Invalid season format: {season}. "
                        "Must contain exactly one forward slash (e.g., '2023/2024')"
                    )

                try:
                    start, end = map(int, season.split("/"))

                    # Validate year range
                    if not (1900 <= start <= current_year + 5):
                        raise ValueError(
                            f"Start year {start} out of valid range "
                            f"(1900-{current_year + 5})"
                        )
                    if not (1900 <= end <= current_year + 5):
                        raise ValueError(
                            f"End year {end} out of valid range "
                            f"(1900-{current_year + 5})"
                        )

                    # Validate consecutive years
                    if end != start + 1:
                        raise ValueError(
                            f"Invalid season: {season}. Years must be consecutive "
                            f"(got {start} and {end})"
                        )

                except ValueError as e:
                    if str(e).startswith("Invalid season"):
                        raise  # Re-raise our custom error
                    raise ValueError(
                        f"Invalid season format: {season}. "
                        "Years must be valid integers (e.g., '2023/2024')"
                    ) from e

    def _apply_filters(self, query: str) -> tuple[str, list]:
        """Apply filters to a SQL query.

        Parameters
        ----------
        query : str
            Base SQL query

        Returns:
        -------
        tuple[str, list]
            Modified query with placeholders and list of parameters
        """
        conditions = []
        params = []

        filter_mappings = {
            "sports": ("s.name", "sports"),
            "leagues": ("m.league", "leagues"),
            "seasons": ("m.season", "seasons"),
            "countries": ("m.country", "countries"),
        }

        for _, filter_key in filter_mappings.items():
            if filter_key[1] in self.filters:
                filter_values = self.filters[filter_key[1]]
                placeholders = ",".join(["?" for _ in filter_values])
                conditions.append(f"{filter_key[0]} IN ({placeholders})")
                params.extend(filter_values)

        if conditions:
            query += " AND " if "WHERE" in query else " WHERE "
            query += " AND ".join(conditions)

        return query, params

    def _get_filtered_matches(self, odds: bool = False) -> List[MatchRecord]:
        """Get matches that match the current filters.

        Returns:
        -------
        List[MatchRecord]
            List of matching match records
        """
        # Use INNER JOIN for sports since it's a required relationship
        # Use EXISTS for better performance than LEFT JOIN with IS NULL
        query = """
            SELECT m.match_id, s.name as sport_name, s.id as sport_id
            FROM match_ids m
            INNER JOIN sports s ON m.sport_id = s.id
            WHERE NOT EXISTS (
                SELECT 1 FROM {table} d
                WHERE d.flashscore_id = m.match_id
            )
        """.format(table="odds_data" if odds else "match_data")

        query, params = self._apply_filters(query)
        with self.db_manager.get_cursor() as cursor:
            # Use server-side cursor for large result sets
            cursor.execute(query, params)
            return cursor.fetchall()

    def scrape(
        self, headless: bool = True, batch_size: int = 100, scrape_odds: bool = False
    ) -> Dict[str, Dict[str, int]]:
        """Execute the main scraping workflow with filtering.

        This method coordinates the scraping process for both match data and
        odds (if requested). It applies the configured filters to select matches,
        then uses specialized scrapers to collect the data in batches.

        Parameters
        ----------
        headless : bool, optional
            Whether to run the browser in headless mode. Set to False for
            debugging or visual verification. Defaults to True.
        batch_size : int, optional
            Number of records to process in each batch. Smaller batches use
            less memory but may be slower. Defaults to 100.
        scrape_odds : bool, optional
            Whether to also scrape betting odds for matches. Defaults to False.

        Returns:
        -------
        Dict[str, Dict[str, int]]
            Dictionary containing results for both match data and odds:
            {
                'matches': {'sport1': count1, 'sport2': count2, ...},
                'odds': {'sport1': count1, 'sport2': count2, ...}
            }
            Empty dictionaries if no matches are found or errors occur.
        """
        results = {"matches": {}, "odds": {}}

        try:
            # Process match data
            matches = self._get_filtered_matches()
            if not matches:
                logger.info("No matches found matching the current filters")
            else:
                match_count = len(matches)
                sport_count = len(set(m["sport_name"] for m in matches))
                logger.info(
                    f"Found {match_count} matches to scrape across {sport_count} sports"
                )

                try:
                    results["matches"] = self.data_scraper.scrape(
                        headless=headless, batch_size=batch_size, matches=matches
                    )
                    logger.info(
                        f"Successfully completed match data scraping for "
                        f"{sum(results['matches'].values())} matches"
                    )
                except Exception as e:
                    logger.error(f"Failed to scrape match data: {str(e)}")

            # Process odds if requested
            if scrape_odds:
                odds_matches = self._get_filtered_matches(odds=True)
                if not odds_matches:
                    logger.info("No matches found requiring odds collection")
                else:
                    odds_count = len(odds_matches)
                    sport_count = len(set(m["sport_name"] for m in odds_matches))
                    logger.info(
                        f"Found {odds_count} matches requiring odds collection "
                        f"across {sport_count} sports"
                    )

                    try:
                        match_ids = [m["match_id"] for m in odds_matches]
                        results["odds"] = self.odds_scraper.scrape(
                            headless=headless,
                            batch_size=batch_size,
                            match_ids=match_ids,
                        )
                        logger.info(
                            f"Successfully completed odds scraping for "
                            f"{sum(results['odds'].values())} matches"
                        )
                    except Exception as e:
                        logger.error(f"Failed to scrape odds data: {str(e)}")

            return results

        except Exception as e:
            logger.error(f"Critical error during scraping process: {str(e)}")
            return results

    def get_available_filters(self) -> Dict[str, Set[str]]:
        """Get available filter values from the database.

        Returns:
        -------
        Dict[str, Set[str]]
            Dictionary mapping filter types to available values
        """
        # Use UNION ALL for better performance than multiple DISTINCT operations
        query = """
            SELECT 'sports' as type, s.name as value
            FROM sports s
            WHERE EXISTS (SELECT 1 FROM match_ids m WHERE m.sport_id = s.id)
            UNION ALL
            SELECT 'leagues', m.league
            FROM (SELECT DISTINCT league FROM match_ids) m
            UNION ALL
            SELECT 'seasons', m.season
            FROM (SELECT DISTINCT season FROM match_ids) m
            UNION ALL
            SELECT 'countries', m.country
            FROM (SELECT DISTINCT country FROM match_ids) m
        """

        available_filters: Dict[str, Set[str]] = {
            "sports": set(),
            "leagues": set(),
            "seasons": set(),
            "countries": set(),
        }

        with self.db_manager.get_cursor() as cursor:
            cursor.execute(query)
            # Process results in chunks for memory efficiency
            while True:
                rows = cursor.fetchmany(1000)
                if not rows:
                    break
                for filter_type, value in rows:
                    if value is not None:  # Skip NULL values
                        available_filters[filter_type].add(value)

        return available_filters


if __name__ == "__main__":
    # Example usage with type-safe filter definition
    filters = {
        "sports": ["handball"],
        "leagues": ["Kvindeligaen Women"],
        "seasons": ["2023/2024"],
        "countries": ["Denmark"],
    }

    scraper = FlexibleScraper(filters=filters)
    results = scraper.scrape(headless=True, batch_size=5, scrape_odds=True)
