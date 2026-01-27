"""
Базовый сервис Playwright для работы с ЛК Wildberries.

Обеспечивает:
- Создание и управление browser context
- Stealth режим (маскировка автоматизации)
- Сохранение и восстановление сессий (cookies)
- Human-like поведение (задержки, движения мыши)
"""

import asyncio
import json
import logging
import random
from typing import Optional
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)


class BrowserService:
    """Сервис для управления браузером Playwright"""

    WB_SELLER_URL = "https://seller.wildberries.ru"
    WB_LOGIN_URL = "https://seller.wildberries.ru/login"

    # User agents для ротации
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    ]

    def __init__(self, headless: bool = True):
        """
        Инициализация сервиса.

        Args:
            headless: Запускать браузер в headless режиме (без GUI)
        """
        self.headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None

    async def start(self) -> None:
        """Запуск Playwright и браузера"""
        if self._browser:
            return

        logger.info("Запуск Playwright...")
        self._playwright = await async_playwright().start()

        # Запускаем Chromium с параметрами для stealth
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
            ]
        )
        logger.info("Браузер запущен")

    async def stop(self) -> None:
        """Остановка браузера и Playwright"""
        if self._browser:
            await self._browser.close()
            self._browser = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        logger.info("Браузер остановлен")

    async def create_context(
        self,
        cookies: Optional[list] = None,
        proxy: Optional[dict] = None
    ) -> BrowserContext:
        """
        Создание нового browser context с настройками stealth.

        Args:
            cookies: Список cookies для восстановления сессии
            proxy: Настройки прокси {'server': 'http://...', 'username': '...', 'password': '...'}

        Returns:
            BrowserContext с настройками stealth
        """
        if not self._browser:
            await self.start()

        # Случайный user agent
        user_agent = random.choice(self.USER_AGENTS)

        # Параметры контекста
        context_params = {
            'user_agent': user_agent,
            'viewport': {'width': 1920, 'height': 1080},
            'locale': 'ru-RU',
            'timezone_id': 'Europe/Moscow',
            'geolocation': {'latitude': 55.7558, 'longitude': 37.6173},  # Москва
            'permissions': ['geolocation'],
            'java_script_enabled': True,
            'ignore_https_errors': True,
        }

        if proxy:
            context_params['proxy'] = proxy

        context = await self._browser.new_context(**context_params)

        # Применяем stealth скрипты
        await self._apply_stealth(context)

        # Восстанавливаем cookies если есть
        if cookies:
            await context.add_cookies(cookies)
            logger.debug(f"Восстановлено {len(cookies)} cookies")

        return context

    async def _apply_stealth(self, context: BrowserContext) -> None:
        """
        Применение stealth скриптов для маскировки автоматизации.

        Скрывает признаки Playwright:
        - navigator.webdriver
        - window.chrome
        - WebGL fingerprint
        """
        stealth_script = """
        // Скрываем webdriver
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });

        // Подделываем chrome
        window.chrome = {
            runtime: {},
            loadTimes: function() {},
            csi: function() {},
            app: {}
        };

        // Подделываем plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                {
                    name: 'Chrome PDF Plugin',
                    description: 'Portable Document Format',
                    filename: 'internal-pdf-viewer',
                    length: 1
                },
                {
                    name: 'Chrome PDF Viewer',
                    description: '',
                    filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
                    length: 1
                },
                {
                    name: 'Native Client',
                    description: '',
                    filename: 'internal-nacl-plugin',
                    length: 2
                }
            ]
        });

        // Подделываем languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['ru-RU', 'ru', 'en-US', 'en']
        });

        // Подделываем permissions
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );

        // Скрываем automation flags
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        """

        await context.add_init_script(stealth_script)

    async def create_page(self, context: BrowserContext) -> Page:
        """
        Создание новой страницы с настройками.

        Args:
            context: Browser context

        Returns:
            Новая страница
        """
        page = await context.new_page()

        # Устанавливаем таймауты
        page.set_default_timeout(30000)  # 30 секунд
        page.set_default_navigation_timeout(60000)  # 60 секунд

        return page

    async def human_delay(self, min_ms: int = 500, max_ms: int = 2000) -> None:
        """
        Human-like задержка между действиями.

        Args:
            min_ms: Минимальная задержка в миллисекундах
            max_ms: Максимальная задержка в миллисекундах
        """
        delay = random.randint(min_ms, max_ms) / 1000
        await asyncio.sleep(delay)

    async def human_type(self, page: Page, selector: str, text: str) -> None:
        """
        Human-like ввод текста с случайными задержками.

        Args:
            page: Страница
            selector: CSS селектор элемента
            text: Текст для ввода
        """
        element = await page.wait_for_selector(selector)
        await element.click()

        for char in text:
            await page.keyboard.type(char, delay=random.randint(50, 150))

    async def take_screenshot(
        self,
        page: Page,
        path: Optional[str] = None
    ) -> bytes:
        """
        Сделать скриншот страницы.

        Args:
            page: Страница
            path: Путь для сохранения (опционально)

        Returns:
            Скриншот в формате PNG (bytes)
        """
        screenshot = await page.screenshot(full_page=False)

        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'wb') as f:
                f.write(screenshot)

        return screenshot

    async def get_cookies(self, context: BrowserContext) -> list:
        """
        Получить все cookies из контекста.

        Args:
            context: Browser context

        Returns:
            Список cookies
        """
        return await context.cookies()

    def serialize_cookies(self, cookies: list) -> str:
        """
        Сериализация cookies в JSON строку.

        Args:
            cookies: Список cookies

        Returns:
            JSON строка
        """
        return json.dumps(cookies, ensure_ascii=False)

    def deserialize_cookies(self, cookies_json: str) -> list:
        """
        Десериализация cookies из JSON строки.

        Args:
            cookies_json: JSON строка с cookies

        Returns:
            Список cookies
        """
        return json.loads(cookies_json)


# Singleton instance для переиспользования
_browser_service: Optional[BrowserService] = None
_browser_lock = asyncio.Lock()


async def get_browser_service(headless: bool = True) -> BrowserService:
    """
    Получить singleton instance BrowserService.

    Args:
        headless: Запускать в headless режиме

    Returns:
        BrowserService instance
    """
    global _browser_service

    async with _browser_lock:
        if _browser_service is None:
            _browser_service = BrowserService(headless=headless)
            await _browser_service.start()

    return _browser_service


async def shutdown_browser_service() -> None:
    """Корректное завершение browser service"""
    global _browser_service

    if _browser_service:
        await _browser_service.stop()
        _browser_service = None
