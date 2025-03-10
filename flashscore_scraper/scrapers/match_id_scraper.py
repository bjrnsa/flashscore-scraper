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
        SELECT match_id FROM match_ids
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
            config = FlashscoreConfig(sports=raw_config.get("sports", {}))

            # Convert validated config to required format
            sport_leagues = {}
            for sport_name, sport_data in config.sports.items():
                leagues = [
                    (
                        league.name,
                        league.country,
                        str(league.url),  # Convert HttpUrl to string
                        league.seasons,
                    )
                    for league in sport_data.leagues
                ]
                sport_leagues[sport_name.lower()] = leagues
            return sport_leagues

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

    def _extract_ids(self, browser: BrowserManager, url: str) -> Set[str]:
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
        if not url:
            raise ValueError("URL cannot be empty")

        parsed_url = urlparse(url)
        if not parsed_url.scheme or not parsed_url.netloc:
            raise ValueError(
                f"Invalid URL format: {url}. URL must include scheme (http/https) and domain."
            )

        match_ids = set()

        try:
            with browser.get_driver(url) as driver:
                # Load all available matches
                self._load_all_matches(driver)

                # Parse the page and extract match IDs
                soup = BeautifulSoup(driver.page_source, "html.parser")
                matches = soup.find_all("div", class_="event__match--withRowLink")

                if not matches:
                    logger.warning(f"No matches found at URL: {url}")
                    return match_ids

                # Extract and validate match IDs
                for element in matches:
                    if not isinstance(element, Tag) or "id" not in element.attrs:
                        logger.debug("Skipping invalid match element")
                        continue

                    match_id = str(element.attrs["id"]).split("_")[-1]
                    if match_id.isalnum():
                        match_ids.add(match_id)
                    else:
                        logger.debug(f"Skipping invalid match ID: {match_id}")

                logger.info(f"Found {len(match_ids)} unique match IDs")
                return match_ids

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
                "SELECT match_id FROM match_ids WHERE sport_id = ?", (sport_id,)
            )
            return {row[0] for row in cursor.fetchall()}

    def _extract_fixture_ids(self, browser: BrowserManager, url: str) -> Set[str]:
        """Extract unique match IDs from a league season fixtures page.

        Similar to _extract_ids but for fixtures URL pattern.
        """
        # Modify URL to target fixtures instead of results
        fixtures_url = url.replace("/results/", "/fixtures/")

        # Use existing extraction logic but for fixtures page
        return self._extract_ids(browser, fixtures_url)

    def _store_fixture(self, fixture_data: dict) -> bool:
        """Store fixture data in the fixtures table.

        Parameters
        ----------
        fixture_data : dict
            Dictionary containing fixture information

        Returns:
        -------
        bool
            True if storage was successful, False otherwise
        """
        query = """
            INSERT INTO fixtures
            (flashscore_id, sport_id, country, league, season,
             match_info, datetime, home_team, away_team)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(flashscore_id) DO UPDATE SET
            datetime = excluded.datetime,
            match_info = excluded.match_info
        """
        try:
            with self.db_manager.get_cursor() as cursor:
                cursor.execute(
                    query,
                    (
                        fixture_data["flashscore_id"],
                        fixture_data["sport_id"],
                        fixture_data["country"],
                        fixture_data["league"],
                        fixture_data["season"],
                        fixture_data["match_info"],
                        fixture_data["datetime"],
                        fixture_data["home_team"],
                        fixture_data["away_team"],
                    ),
                )
            return True
        except Exception as e:
            logger.error(f"Failed to store fixture: {str(e)}")
            return False

    def _cleanup_completed_fixtures(self) -> None:
        """Remove fixtures that have been completed and added to match_data."""
        query = """
            DELETE FROM fixtures
            WHERE flashscore_id IN (
                SELECT flashscore_id
                FROM match_data
            )
        """
        try:
            with self.db_manager.get_cursor() as cursor:
                cursor.execute(query)
        except Exception as e:
            logger.error(f"Failed to cleanup completed fixtures: {str(e)}")

    def scrape(self, headless: bool = True) -> Dict[str, Dict[str, int]]:
        """Main scraping workflow for collecting match IDs and fixtures from Flashscore.

        Parameters
        ----------
        headless : bool, optional
            Whether to run the browser in headless mode, by default True

        Returns:
        -------
        Dict[str, Dict[str, int]]
            Dictionary mapping sport names to counts of new results and fixtures
            Example: {'football': {'results': 150, 'fixtures': 75}}

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
            total_sports = len(sport_leagues)
            processed_sports = 0

            for sport_name, leagues in sport_leagues.items():
                try:
                    processed_sports += 1
                    logger.info(
                        f"Processing sport {processed_sports}/{total_sports}: {sport_name}"
                    )

                    sport_id = self.db_manager.register_sport(sport_name)
                    existing_ids = self._get_existing_ids(sport_id)
                    sport_results = {"results": 0, "fixtures": 0}
                    failed_leagues = []

                    for league, country, base_url, seasons in tqdm(
                        leagues, desc=f"Processing {sport_name} leagues"
                    ):
                        current_season = max(seasons)  # Get current season

                        # Process historical results
                        for season in seasons:
                            try:
                                season_suffix = f"-{season - 1}-{season}/results/"
                                season_str = f"{season - 1}/{season}"
                                url = f"{base_url}{season_suffix}"

                                logger.debug(
                                    f"Scraping results for {league} {season_str}"
                                )
                                league_ids = self._extract_ids(browser, url)
                                new_ids = league_ids - existing_ids

                                if new_ids:
                                    query = """
                                        INSERT INTO match_ids
                                        (match_id, sport_id, source, country, league, season)
                                        VALUES (?, ?, ?, ?, ?, ?)
                                    """
                                    records = [
                                        (
                                            id_,
                                            sport_id,
                                            "flashscore",
                                            country,
                                            league,
                                            season_str,
                                        )
                                        for id_ in new_ids
                                    ]

                                    if not self.execute_query(query, records):
                                        logger.error(
                                            f"Failed to store match IDs for {league} {season_str}"
                                        )
                                        failed_leagues.append(
                                            (league, season_str, "results")
                                        )
                                        continue

                                    sport_results["results"] += len(new_ids)
                                    logger.info(
                                        f"Found {len(new_ids)} new matches in {league} {season_str}"
                                    )

                            except Exception as e:
                                logger.error(
                                    f"Failed to scrape results for {league} {season_str}: {str(e)}"
                                )
                                failed_leagues.append((league, season_str, "results"))
                                continue

                        # Process fixtures for current season only
                        try:
                            fixture_suffix = (
                                f"-{current_season - 1}-{current_season}/fixtures/"
                            )
                            fixture_url = f"{base_url}{fixture_suffix}"

                            logger.debug(
                                f"Scraping fixtures for {league} {current_season - 1}/{current_season}"
                            )
                            fixture_ids = self._extract_fixture_ids(
                                browser, fixture_url
                            )
                            new_fixture_ids = fixture_ids - existing_ids

                            if new_fixture_ids:
                                # First add to match_ids table
                                query = """
                                    INSERT INTO match_ids
                                    (match_id, sport_id, source, country, league, season)
                                    VALUES (?, ?, ?, ?, ?, ?)
                                """
                                fixture_records = [
                                    (
                                        id_,
                                        sport_id,
                                        "flashscore",
                                        country,
                                        league,
                                        f"{current_season - 1}/{current_season}",
                                    )
                                    for id_ in new_fixture_ids
                                ]

                                if not self.execute_query(query, fixture_records):
                                    logger.error(
                                        f"Failed to store fixture IDs for {league}"
                                    )
                                    failed_leagues.append(
                                        (league, str(current_season), "fixtures")
                                    )
                                    continue

                                sport_results["fixtures"] += len(new_fixture_ids)
                                logger.info(
                                    f"Found {len(new_fixture_ids)} new fixtures in {league}"
                                )

                        except Exception as e:
                            logger.error(
                                f"Failed to scrape fixtures for {league}: {str(e)}"
                            )
                            failed_leagues.append(
                                (league, str(current_season), "fixtures")
                            )
                            continue

                    # Cleanup completed fixtures
                    self._cleanup_completed_fixtures()
                    results[sport_name] = sport_results

                    if failed_leagues:
                        logger.warning(f"Failed scraping attempts for {sport_name}:")
                        for league, season, type_ in failed_leagues:
                            logger.warning(f"- {league} {season} ({type_})")

                except Exception as e:
                    logger.error(f"Failed to process sport {sport_name}: {str(e)}")
                    results[sport_name] = {"results": 0, "fixtures": 0}
                    continue

            logger.info("Match ID and fixture scraping completed")
            return results

        except Exception as e:
            error_msg = f"Critical error during scraping: {str(e)}"
            logger.error(error_msg)
            raise ScraperException(error_msg) from e


if __name__ == "__main__":
    scraper = MatchIDScraper()
    results = scraper.scrape(headless=False)
    for sport, counts in results.items():
        print(
            f"Scraped {counts['results']} results and {counts['fixtures']} fixtures for {sport}"
        )
