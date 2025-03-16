"""Base scraper module providing common functionality."""

import logging
import time
from pathlib import Path
from typing import List, Optional, Tuple, TypeVar

from bs4 import BeautifulSoup
from pydantic import BaseModel
from ratelimit import limits, sleep_and_retry
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from flashscore_scraper.core.browser import BrowserManager
from flashscore_scraper.core.database import DatabaseManager

# Configure logging
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# Type variable for generic model
T = TypeVar("T", bound=BaseModel)


class BaseScraper:
    """Base class for all Flashscore scrapers."""

    # Common constants
    BASE_URL = "https://www.flashscore.com"
    DEFAULT_BATCH_SIZE = 100
    TIMEOUT = 3
    CLICK_DELAY = 0.1

    # Rate limiting constants
    MAX_REQUESTS_PER_MINUTE = 120
    ONE_MINUTE = 60
    MAX_RETRIES = 3

    # Common CSS selectors and class names
    TOURNAMENT_HEADER_CLASS = "tournamentHeader__country"
    EVENT_MORE_CLASS = "event__more--static"
    LOADING_INDICATOR_CLASS = "loadingIndicator"

    def __init__(
        self,
        db_path: Path | str = "database/database.db",
    ):
        """Initialize the base scraper.

        Parameters
        ----------
        config_path : Optional[Path], optional
            Path to configuration file, by default None
        db_path : Path | str, optional
            Path to database file, by default "database/database.db"
        """
        self.db_path = Path(db_path)
        self.db_manager = self.get_database()

    def get_browser(self, headless: bool = True) -> BrowserManager:
        """Create a new browser manager instance.

        Parameters
        ----------
        headless : bool, optional
            Whether to run in headless mode, by default True

        Returns:
        -------
        BrowserManager
            Configured browser manager instance
        """
        return BrowserManager(headless=headless)

    def get_database(self) -> DatabaseManager:
        """Create a new database manager instance.

        Returns:
        -------
        DatabaseManager
            Configured database manager instance
        """
        return DatabaseManager(self.db_path)

    @sleep_and_retry
    @limits(calls=MAX_REQUESTS_PER_MINUTE, period=ONE_MINUTE)
    def _wait_for_element(
        self, driver: WebDriver, by: str, value: str, timeout: Optional[int] = None
    ) -> None:
        """Wait for element with rate limiting.

        Parameters
        ----------
        driver : WebDriver
            The Selenium WebDriver instance
        by : str
            The method to locate the element (e.g. "class name", "id", "xpath")
        value : str
            The locator value
        timeout : Optional[int], optional
            Custom timeout value, by default None
        """
        WebDriverWait(driver, timeout or self.TIMEOUT).until(
            EC.presence_of_element_located(
                (getattr(By, by.upper().replace(" ", "_")), value)
            )
        )

    def _scrape_page(self, driver: WebDriver, wait_for_class: str) -> BeautifulSoup:
        """Load a page and return parsed content.

        Parameters
        ----------
        driver : WebDriver
            The Selenium WebDriver instance
        wait_for_class : str
            Class name to wait for before parsing

        Returns:
        -------
        BeautifulSoup
            Parsed page content
        """
        self._wait_for_element(driver, By.CLASS_NAME, wait_for_class)
        return BeautifulSoup(driver.page_source, "html.parser")

    # Storage operations should be implemented by each scraper
    def execute_query(self, query: str, params: Optional[List[Tuple]] = None) -> bool:
        """Execute a database query.

        Parameters
        ----------
        query : str
            SQL query to execute
        params : Optional[List[Tuple]], optional
            Query parameters for executemany, by default None

        Returns:
        -------
        bool
            True if successful, False otherwise
        """
        try:
            with self.db_manager.get_cursor() as cursor:
                if params:
                    cursor.executemany(query, params)
                else:
                    cursor.execute(query)
            return True
        except Exception as e:
            logger.error(f"Query execution failed: {str(e)}")
            return False

    def _class_filter(
        self, classes: str, include: List[str], exclude: Optional[List[str]] = None
    ) -> bool:
        """Filter HTML classes.

        Parameters
        ----------
        classes : str
            Space-separated class string
        include : List[str]
            Classes that must be present
        exclude : Optional[List[str]], optional
            Classes that must not be present, by default None

        Returns:
        -------
        bool
            True if criteria met, False otherwise
        """
        if not classes:
            return False
        class_list = classes.split()
        if all(cls in class_list for cls in include):
            if exclude:
                return all(cls not in class_list for cls in exclude)
            return True
        return False

    @sleep_and_retry
    @limits(calls=MAX_REQUESTS_PER_MINUTE, period=ONE_MINUTE)
    def _scrape_page_with_retry(
        self, driver: WebDriver, wait_class: str, timeout: Optional[int] = None
    ) -> BeautifulSoup:
        """Load a page and return parsed content with retry logic.

        Parameters
        ----------
        driver : WebDriver
            The Selenium WebDriver instance
        wait_class : str
            Class name to wait for before parsing
        timeout : Optional[int], optional
            Custom timeout value, by default None

        Returns:
        -------
        BeautifulSoup
            Parsed page content

        Raises:
        ------
        TimeoutException
            If element cannot be found after retries
        """
        retry_count = 0
        while retry_count < self.MAX_RETRIES:
            try:
                self._wait_for_element(driver, "class name", wait_class, timeout)
                return BeautifulSoup(driver.page_source, "html.parser")
            except TimeoutException:
                retry_count += 1
                if retry_count == self.MAX_RETRIES:
                    raise
                time.sleep(retry_count)

        # This line should never be reached due to the raise in the loop,
        # but satisfies the type checker
        raise TimeoutException(f"Failed to find element with class {wait_class}")

    def _scroll_to_bottom(self, driver: WebDriver) -> None:
        """Scroll to page bottom to load more content.

        Parameters
        ----------
        driver : WebDriver
            The Selenium WebDriver instance
        """
        last_height = driver.execute_script("return document.body.scrollHeight")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

        try:
            WebDriverWait(driver, self.TIMEOUT).until(
                lambda d: d.execute_script("return document.body.scrollHeight")
                > last_height
            )
        except TimeoutException:
            pass  # No new content loaded

    @sleep_and_retry
    @limits(calls=MAX_REQUESTS_PER_MINUTE, period=ONE_MINUTE)
    def _load_more_content(self, driver: WebDriver) -> bool:
        """Click 'load more' button with rate limiting.

        Parameters
        ----------
        driver : WebDriver
            The Selenium WebDriver instance

        Returns:
        -------
        bool
            True if more content was loaded, False if at the end or on error
        """
        retry_count = 0
        while retry_count < self.MAX_RETRIES:
            try:
                self._scroll_to_bottom(driver)
                button = WebDriverWait(driver, self.TIMEOUT).until(
                    EC.element_to_be_clickable((By.CLASS_NAME, self.EVENT_MORE_CLASS))
                )
                time.sleep(self.CLICK_DELAY)
                button.click()
                return True
            except (TimeoutException, ElementClickInterceptedException):
                retry_count += 1
                if retry_count == self.MAX_RETRIES:
                    return False
                time.sleep(retry_count)
            except WebDriverException:
                return False

        # This line is reached if we break out of the loop
        return False
