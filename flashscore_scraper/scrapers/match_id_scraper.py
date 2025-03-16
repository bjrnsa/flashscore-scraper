"""Module for scraping match IDs from FlashScore."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple
from urllib.parse import urlparse

import yaml
from bs4 import BeautifulSoup, Tag
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.webdriver import WebDriver
from tqdm import tqdm

from flashscore_scraper.core.browser import BrowserManager
from flashscore_scraper.exceptions import ScraperException
from flashscore_scraper.models.base import FlashscoreConfig
from flashscore_scraper.scrapers.base import BaseScraper

# Configure logging - only show warnings and errors by default
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


class MatchIDScraper(BaseScraper):
    """Scrapes match IDs from FlashScore and stores them in a database."""

    # No need to redefine constants as they're inherited from BaseScraper

    def __init__(
        self,
        config_path: Path | str = "config/flashscore_urls.yaml",
        db_path: Path | str = "database/database.db",
    ):
        """Initialize the MatchIDScraper with configuration and database paths.

        Parameters
        ----------
        config_path : Path | str
            Path to the configuration file containing URLs for leagues and seasons.
            Must be a valid YAML file with the expected structure.
        db_path : Path | str
            Path to the SQLite database file where match IDs will be stored.
            Parent directories will be created if they don't exist.

        Raises:
        ------
        FileNotFoundError
            If the config file does not exist at the specified path.
        """
        self.config_path = Path(config_path)
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Config file not found at: {config_path}. Please ensure the file exists."
            )

        super().__init__(db_path)
        self.db_manager = self.get_database()
        # Create database directory if it doesn't exist
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    def _load_yaml_config(self) -> Dict[str, Any]:
        """Load and validate YAML configuration file.

        Returns:
        -------
        Dict[str, Any]
            Parsed configuration data

        Raises:
        ------
        ValueError
            If config file is missing or invalid
        """
        if not self.config_path or not isinstance(self.config_path, Path):
            raise ValueError("Valid config path is required")

        try:
            with open(self.config_path) as f:
                return yaml.safe_load(f)
        except (yaml.YAMLError, IOError) as e:
            logger.error(f"Failed to load config: {str(e)}")
            raise ValueError(f"Config file error: {str(e)}") from e

    def _check_existing_matches(
        self, sport_id: int, league: str, season: int
    ) -> Set[str]:
        """Get existing match IDs for a specific league and season.

        Parameters
        ----------
        sport_id : int
            The ID of the sport.
        league : str
            The name of the league.
        season : int
            The season year.

        Returns:
        -------
        Set[str]
            Set of existing match IDs.
        """
        query = """
        SELECT flashscore_id FROM flashscore_ids
        WHERE sport_id = ? AND league = ? AND season = ?
        """
        with self.db_manager.get_cursor() as cursor:
            cursor.execute(query, (sport_id, league, str(season)))
            return {row[0] for row in cursor.fetchall()}

    def _load_config(self) -> Dict[str, List[Tuple[str, str, str, List[int]]]]:
        """Load and validate league URLs and metadata from YAML configuration.

        Returns:
        -------
        Dict[str, List[Tuple[str, str, str, List[int]]]]
            Dictionary mapping sport names to lists of (league_name, country, url, seasons) tuples

        Raises:
        ------
        ValueError
            If the configuration file is invalid or contains invalid values.
        """
        if not isinstance(self.config_path, Path):
            raise ValueError("Config path must be a Path object")

        try:
            raw_config = yaml.safe_load(self.config_path.read_text())
            config = FlashscoreConfig(sport_ids=raw_config.get("sport_ids", {}))

            return {
                sport_name.lower(): [
                    (
                        league.name,
                        league.country,
                        str(league.url),
                        league.seasons,
                        league.url_pattern,
                    )
                    for league in sport_data.leagues
                ]
                for sport_name, sport_data in config.sport_ids.items()
            }

        except (yaml.YAMLError, ValueError) as e:
            logger.error(f"Configuration error: {str(e)}")
            raise ValueError(str(e)) from e
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            raise ValueError(str(e)) from e

    def _load_all_matches(self, driver: WebDriver) -> None:
        """Continuously load matches until no more are available.

        Parameters
        ----------
        driver : WebDriver
            The Selenium WebDriver instance used for browser automation.
            The driver should already be at the correct page URL.
        """
        while self._load_more_content(driver):
            pass  # Continue loading until no more content is available

    def _extract_ids(
        self, browser: BrowserManager, url: str, is_fixture: bool = False
    ) -> Set[str]:
        """Extract unique match IDs from a league season page.

        This method navigates to the provided URL, loads all available matches
        by scrolling and clicking 'load more' buttons, then extracts unique
        match IDs from the loaded content.

        Parameters
        ----------
        browser : BrowserManager
            The BrowserManager instance used for browser automation.
        url : str
            The URL of the league season page to scrape. Must be a valid
            Flashscore URL containing match results.
        is_fixture : bool, optional
            Whether the URL is for upcoming_fixtures, by default False

        Returns:
        -------
        Set[str]
            Set of unique match IDs found on the page. Each ID is an alphanumeric
            string that uniquely identifies a match in the Flashscore system.
            Returns an empty set if no matches are found.

        Raises:
        ------
        ScraperException
            If there are issues loading the page or extracting match IDs,
            such as network errors or page structure changes.
        ValueError
            If the URL is empty or malformed (missing scheme or netloc).
            Example: "Invalid URL format: http://" (missing netloc)
        """
        if not url or not urlparse(url).scheme or not urlparse(url).netloc:
            raise ValueError(f"Invalid URL format: {url}")

        flashscore_ids = set()
        try:
            with browser.get_driver(url) as driver:
                # Load all available matches
                self._load_all_matches(driver)

                # Parse the page and extract match IDs
                soup = BeautifulSoup(driver.page_source, "html.parser")
                matches = soup.find_all("div", class_="event__match--withRowLink")

                if not matches:
                    logger.warning(f"No matches found at URL: {url}")
                    return flashscore_ids

                # Extract and validate match IDs
                for element in matches:
                    if not isinstance(element, Tag) or "id" not in element.attrs:
                        logger.debug("Skipping invalid match element")
                        continue

                    flashscore_id = str(element.attrs["id"]).split("_")[-1]
                    if flashscore_id.isalnum():
                        flashscore_ids.add(flashscore_id)
                    else:
                        logger.debug(f"Skipping invalid match ID: {flashscore_id}")

                logger.info(
                    f"Found {len(flashscore_ids)} unique {'fixture' if is_fixture else 'match'} IDs"
                )
                return flashscore_ids

        except TimeoutException as e:
            error_msg = f"Timed out while loading matches from {url}"
            logger.error(error_msg)
            raise ScraperException(error_msg) from e
        except Exception as e:
            error_msg = f"Failed to extract match IDs from {url}: {str(e)}"
            logger.error(error_msg)
            raise ScraperException(error_msg) from e

    def _get_existing_ids(self, sport_id: int) -> Set[str]:
        """Retrieve already stored match IDs for a specific sport from database.

        Parameters
        ----------
        sport_id : int
            ID of the sport to get existing match IDs for

        Returns:
        -------
        Set[str]
            Set of existing match IDs for the sport
        """
        with self.db_manager.get_cursor() as cursor:
            cursor.execute(
                "SELECT flashscore_id FROM flashscore_ids WHERE sport_id = ?",
                (sport_id,),
            )
            return {row[0] for row in cursor.fetchall()}

    def _extract_fixture_ids(self, browser: BrowserManager, url: str) -> Set[str]:
        """Extract unique match IDs from a league season fixtures page.

        Similar to _extract_ids but for fixtures URL pattern.
        """
        fixtures_url = url.replace("/results/", "/fixtures/")
        return self._extract_ids(browser, fixtures_url, is_fixture=True)

    def _store_flashscore_ids(
        self, records: List[Tuple], is_fixture: bool = False
    ) -> bool:
        """Store match IDs in the flashscore_ids table.

        Parameters
        ----------
        records : List[Tuple]
            List of tuples containing match ID, sport ID, country, league, and season
        is_fixture : bool, optional
            Whether the records are for upcoming_fixtures, by default False

        Returns:
        -------
        bool
            True if storage was successful, False otherwise
        """
        query = """
            INSERT INTO flashscore_ids
            (flashscore_id, sport_id,  country, league, season)
            VALUES (?, ?, ?, ?, ?)
        """
        return bool(self.execute_query(query, records))

    def _cleanup_completed_upcoming_fixtures(self) -> None:
        """Remove upcoming_fixtures that have been completed and added to match_data."""
        try:
            with self.db_manager.get_cursor() as cursor:
                cursor.execute(
                    "DELETE FROM upcoming_fixtures WHERE flashscore_id IN (SELECT flashscore_id FROM match_data)"
                )
        except Exception as e:
            logger.error(f"Failed to cleanup completed upcoming_fixtures: {str(e)}")

    def _is_season_already_scraped(
        self, sport_id: int, league: str, season: str
    ) -> bool:
        """Check if a season has been completely scraped before.

        Parameters
        ----------
        sport_id : int
            The ID of the sport
        league : str
            The name of the league
        season : str
            The season identifier (e.g., "2023/2024")

        Returns:
        -------
        bool
            True if the season has been completely scraped, False otherwise
        """
        query = """
            SELECT is_complete
            FROM scraped_seasons
            WHERE sport_id = ? AND league = ? AND season = ?
        """
        with self.db_manager.get_cursor() as cursor:
            cursor.execute(query, (sport_id, league, season))
            result = cursor.fetchone()
            return bool(result and result[0])

    def _mark_season_as_scraped(self, sport_id: int, league: str, season: str) -> None:
        """Mark a season as completely scraped.

        Parameters
        ----------
        sport_id : int
            The ID of the sport
        league : str
            The name of the league
        season : str
            The season identifier (e.g., "2023/2024")
        """
        query = """
            INSERT INTO scraped_seasons (sport_id, league, season, is_complete)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(sport_id, league, season)
            DO UPDATE SET is_complete = 1, last_scraped = CURRENT_TIMESTAMP
        """
        with self.db_manager.get_cursor() as cursor:
            cursor.execute(query, (sport_id, league, season))

    def scrape(self, headless: bool = True) -> Dict[str, Dict[str, int]]:
        """Main scraping workflow for collecting match IDs and upcoming_fixtures from Flashscore.

        Parameters
        ----------
        headless : bool, optional
            Whether to run the browser in headless mode, by default True

        Returns:
        -------
        Dict[str, Dict[str, int]]
            Dictionary mapping sport names to counts of new results and upcoming_fixtures
            Example: {'football': {'results': 150, 'upcoming_fixtures': 75}}

        Raises:
        ------
        ScraperException
            If there are persistent issues with scraping or database operations
        """
        try:
            sport_leagues = self._load_config()
            browser = self.get_browser(headless)
            results = {}

            logger.info("Starting match ID and fixture scraping process")
            total_sport_ids = len(sport_leagues)
            processed_sport_ids = 0

            for sport_name, leagues in sport_leagues.items():
                try:
                    processed_sport_ids += 1
                    logger.info(
                        f"Processing sport {processed_sport_ids}/{total_sport_ids}: {sport_name}"
                    )

                    sport_id = self.db_manager.register_sport(sport_name)
                    existing_ids = self._get_existing_ids(sport_id)
                    sport_results = {"results": 0, "upcoming_fixtures": 0}
                    failed_leagues = []

                    for league, country, base_url, seasons, url_pattern in tqdm(
                        leagues, desc=f"Processing {sport_name} leagues"
                    ):
                        current_season = max(seasons)  # Get current season
                        for season in seasons:
                            season_str = f"{season - 1}/{season}"

                            # Skip if historical season is already scraped
                            if (
                                season < current_season
                                and self._is_season_already_scraped(
                                    sport_id, league, season
                                )
                            ):
                                logger.info(
                                    f"Skipping {league} {season_str} - already fully scraped"
                                )
                                continue
                            previous_season = season - 1
                            url = url_pattern.format(
                                base_url=base_url,
                                previous_season=previous_season,
                                season=season,
                            )
                            try:
                                logger.debug(
                                    f"Scraping results for {league} {season_str}"
                                )
                                new_ids = self._extract_ids(browser, url) - existing_ids

                                if new_ids:
                                    records = [
                                        (
                                            flashscore_id,
                                            sport_id,
                                            country,
                                            league,
                                            season,
                                        )
                                        for flashscore_id in new_ids
                                    ]

                                    if self._store_flashscore_ids(records):
                                        sport_results["results"] += len(new_ids)
                                        logger.info(
                                            f"Found {len(new_ids)} new matches in {league} {season_str}"
                                        )
                                        # Mark season as scraped only if we found and stored matches
                                        if season < current_season:
                                            self._mark_season_as_scraped(
                                                sport_id, league, season
                                            )
                                    else:
                                        failed_leagues.append(
                                            (league, season, "results")
                                        )

                            except Exception as e:
                                logger.error(
                                    f"Failed to scrape results for {league} {season_str}: {str(e)}"
                                )
                                failed_leagues.append((league, season_str, "results"))
                                continue

                        # Process fixtures for current season
                        try:
                            previous_season = current_season - 1
                            fixture_url = url_pattern.format(
                                base_url=base_url,
                                previous_season=previous_season,
                                season=current_season,
                            )

                            logger.debug(
                                f"Scraping fixtures for {league} {current_season}"
                            )
                            new_fixture_ids = (
                                self._extract_fixture_ids(browser, fixture_url)
                                - existing_ids
                            )

                            if new_fixture_ids:
                                fixture_records = [
                                    (
                                        id_,
                                        sport_id,
                                        country,
                                        league,
                                        current_season,
                                    )
                                    for id_ in new_fixture_ids
                                ]

                                if self._store_flashscore_ids(
                                    fixture_records, is_fixture=True
                                ):
                                    sport_results["upcoming_fixtures"] += len(
                                        new_fixture_ids
                                    )
                                    logger.info(
                                        f"Found {len(new_fixture_ids)} new fixtures in {league}"
                                    )
                                else:
                                    failed_leagues.append(
                                        (league, str(current_season), "fixtures")
                                    )

                        except Exception as e:
                            logger.error(
                                f"Failed to scrape fixtures for {league}: {str(e)}"
                            )
                            failed_leagues.append(
                                (league, str(current_season), "fixtures")
                            )
                            continue

                    # Cleanup completed upcoming_fixtures
                    self._cleanup_completed_upcoming_fixtures()
                    results[sport_name] = sport_results

                    if failed_leagues:
                        logger.warning(f"Failed scraping attempts for {sport_name}:")
                        for league, season, type_ in failed_leagues:
                            logger.warning(f"- {league} {season} ({type_})")

                except Exception as e:
                    logger.error(f"Failed to process sport {sport_name}: {str(e)}")
                    results[sport_name] = {"results": 0, "upcoming_fixtures": 0}
                    continue

            logger.info("Match ID and fixture scraping completed")
            return results

        except Exception as e:
            error_msg = f"Critical error during scraping: {str(e)}"
            logger.error(error_msg)
            raise ScraperException(error_msg) from e


if __name__ == "__main__":
    scraper = MatchIDScraper()
    results = scraper.scrape(headless=True)
    for sport, counts in results.items():
        print(
            f"Scraped {counts['results']} results and {counts['upcoming_fixtures']} upcoming_fixtures for {sport}"
        )
