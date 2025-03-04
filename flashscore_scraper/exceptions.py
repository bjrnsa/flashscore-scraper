"""Custom exceptions for the flashscore_scraper package."""


class ScraperException(Exception):
    """Base exception for all scraper errors."""

    pass


class ParsingException(ScraperException):
    """Exception for data parsing errors."""

    pass


class ValidationException(ScraperException):
    """Exception for data validation errors."""

    pass
