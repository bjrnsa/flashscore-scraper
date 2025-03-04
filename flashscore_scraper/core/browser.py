"""Browser management module for Flashscore scraping."""

from contextlib import contextmanager
from typing import Generator

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.webdriver import WebDriver


class BrowserManager:
    """Manages browser instances for web scraping with connection pooling."""

    def __init__(self, headless: bool = True, max_retries: int = 3):
        """Initialize the BrowserManager.

        Parameters
        ----------
        headless : bool, optional
            Whether to run the browser in headless mode, by default True
        max_retries : int, optional
            Maximum number of retry attempts for browser operations, by default 3
        """
        self.options = Options()
        if headless:
            self.options.add_argument("--headless")
        self.options.add_argument("--no-sandbox")
        self.options.add_argument("--disable-dev-shm-usage")
        self.options.add_argument("--disable-gpu")
        self.options.add_argument("--window-size=1920,1080")
        self.options.add_argument("--disable-extensions")
        self.options.add_argument("--dns-prefetch-disable")

        self.max_retries = max_retries
        self._driver = None

    def _create_driver(self) -> WebDriver:
        """Create a new WebDriver instance with optimized settings.

        Returns:
        -------
        WebDriver
            Configured Chrome WebDriver instance
        """
        driver = webdriver.Chrome(options=self.options)
        driver.set_page_load_timeout(30)
        return driver

    def _get_driver(self) -> WebDriver:
        """Get or create a WebDriver instance.

        Returns:
        -------
        WebDriver
            Active WebDriver instance
        """
        if not self._driver:
            self._driver = self._create_driver()
        return self._driver

    @contextmanager
    def get_driver(self, url: str | None = None) -> Generator[WebDriver, None, None]:
        """Create and manage a browser instance with retry logic.

        Parameters
        ----------
        url : str, optional
            URL to navigate to, by default None

        Yields:
        ------
        Generator[WebDriver, None, None]
            A configured Chrome WebDriver instance
        """
        retry_count = 0
        last_exception = None

        while retry_count < self.max_retries:
            try:
                driver = self._get_driver()
                if url:
                    driver.get(url)
                yield driver
                break
            except Exception as e:
                last_exception = e
                retry_count += 1
                if self._driver:
                    try:
                        self._driver.quit()
                    except:
                        pass
                    self._driver = None
                if retry_count == self.max_retries:
                    raise last_exception

    def close(self) -> None:
        """Close the browser and cleanup resources."""
        if self._driver:
            try:
                self._driver.quit()
            except:
                pass
            self._driver = None
