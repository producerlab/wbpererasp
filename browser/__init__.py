"""
Browser automation модуль для работы с ЛК Wildberries.

Использует Playwright для эмуляции браузера.
"""

from .browser_service import BrowserService
from .auth import WBAuthService

__all__ = ['BrowserService', 'WBAuthService']
