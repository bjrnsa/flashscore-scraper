"""Core module for Flashscore scraping."""

from .browser import BrowserManager
from .database import DatabaseManager

__all__ = ["BrowserManager", "DatabaseManager"]
