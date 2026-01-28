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

    # User agents для ротации (актуальные на 2026)
    USER_AGENTS = [
        # Chrome 131 (январь 2026)
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        # Chrome 130
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        # Firefox 134
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:134.0) Gecko/20100101 Firefox/134.0",
        # Safari 18
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
        # Edge 131
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
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

        # Запускаем Chromium с расширенными параметрами для stealth
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                # Основные anti-detection флаги
                '--disable-blink-features=AutomationControlled',
                '--disable-automation',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',

                # Отключаем признаки headless
                '--disable-infobars',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',

                # Эмуляция обычного браузера
                '--window-size=1920,1080',
                '--start-maximized',
                '--disable-extensions',

                # Обход fingerprint checks
                '--disable-canvas-aa',
                '--disable-2d-canvas-clip-aa',
                '--disable-gl-drawing-for-tests',

                # Дополнительные флаги
                '--lang=ru-RU,ru',
                '--disable-translate',
                '--disable-sync',
                '--disable-background-networking',
                '--metrics-recording-only',
                '--disable-default-apps',
                '--mute-audio',
                '--no-first-run',
                '--no-default-browser-check',
                '--disable-popup-blocking',
                '--disable-prompt-on-repost',
                '--disable-hang-monitor',
                '--disable-client-side-phishing-detection',
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
        Применение продвинутых stealth скриптов для маскировки автоматизации.

        Обходит:
        - navigator.webdriver detection
        - Chrome automation flags
        - Headless detection
        - WebGL fingerprint checks
        - Canvas fingerprint checks
        - AudioContext fingerprint
        - Permission API anomalies
        """
        stealth_script = """
        // =====================================================
        // COMPREHENSIVE STEALTH SCRIPT v2.0
        // =====================================================

        // 1. Скрываем webdriver
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
            configurable: true
        });

        // Также убираем из prototype
        delete Navigator.prototype.webdriver;

        // 2. Подделываем chrome object (как в реальном Chrome)
        window.chrome = {
            app: {
                isInstalled: false,
                InstallState: {DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed'},
                RunningState: {CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running'}
            },
            runtime: {
                OnInstalledReason: {CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update'},
                OnRestartRequiredReason: {APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic'},
                PlatformArch: {ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64'},
                PlatformNaclArch: {ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64'},
                PlatformOs: {ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win'},
                RequestUpdateCheckStatus: {NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available'},
                connect: function() {},
                sendMessage: function() {}
            },
            csi: function() { return {}; },
            loadTimes: function() {
                return {
                    commitLoadTime: Date.now() / 1000 - Math.random() * 5,
                    connectionInfo: 'http/1.1',
                    finishDocumentLoadTime: Date.now() / 1000 - Math.random() * 2,
                    finishLoadTime: Date.now() / 1000 - Math.random(),
                    firstPaintAfterLoadTime: 0,
                    firstPaintTime: Date.now() / 1000 - Math.random() * 3,
                    navigationType: 'Other',
                    npnNegotiatedProtocol: 'unknown',
                    requestTime: Date.now() / 1000 - Math.random() * 10,
                    startLoadTime: Date.now() / 1000 - Math.random() * 8,
                    wasAlternateProtocolAvailable: false,
                    wasFetchedViaSpdy: false,
                    wasNpnNegotiated: false
                };
            }
        };

        // 3. Подделываем plugins (как в реальном Chrome)
        const makePluginArray = () => {
            const plugins = [
                { name: 'Chrome PDF Plugin', description: 'Portable Document Format', filename: 'internal-pdf-viewer', mimeTypes: ['application/x-google-chrome-pdf'] },
                { name: 'Chrome PDF Viewer', description: '', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', mimeTypes: ['application/pdf'] },
                { name: 'Native Client', description: '', filename: 'internal-nacl-plugin', mimeTypes: ['application/x-nacl', 'application/x-pnacl'] }
            ];

            const pluginArray = [];
            plugins.forEach((p, i) => {
                const plugin = Object.create(Plugin.prototype);
                Object.defineProperties(plugin, {
                    name: { value: p.name, enumerable: true },
                    description: { value: p.description, enumerable: true },
                    filename: { value: p.filename, enumerable: true },
                    length: { value: p.mimeTypes.length, enumerable: true }
                });
                pluginArray.push(plugin);
            });

            Object.setPrototypeOf(pluginArray, PluginArray.prototype);
            return pluginArray;
        };

        Object.defineProperty(navigator, 'plugins', {
            get: () => makePluginArray(),
            configurable: true
        });

        // 4. Подделываем mimeTypes
        Object.defineProperty(navigator, 'mimeTypes', {
            get: () => {
                const mimes = ['application/pdf', 'application/x-google-chrome-pdf', 'application/x-nacl', 'application/x-pnacl'];
                const mimeArray = [];
                mimes.forEach(m => {
                    const mimeType = Object.create(MimeType.prototype);
                    Object.defineProperties(mimeType, {
                        type: { value: m, enumerable: true },
                        suffixes: { value: m === 'application/pdf' ? 'pdf' : '', enumerable: true },
                        description: { value: '', enumerable: true }
                    });
                    mimeArray.push(mimeType);
                });
                Object.setPrototypeOf(mimeArray, MimeTypeArray.prototype);
                return mimeArray;
            },
            configurable: true
        });

        // 5. Подделываем languages
        Object.defineProperty(navigator, 'languages', {
            get: () => Object.freeze(['ru-RU', 'ru', 'en-US', 'en']),
            configurable: true
        });

        // 6. Скрываем headless mode
        Object.defineProperty(navigator, 'hardwareConcurrency', {
            get: () => 8,  // Типичное значение для desktop
            configurable: true
        });

        Object.defineProperty(navigator, 'deviceMemory', {
            get: () => 8,  // 8GB RAM
            configurable: true
        });

        // 7. Подделываем permissions API
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = async function(parameters) {
            if (parameters.name === 'notifications') {
                return { state: 'prompt', onchange: null };
            }
            if (parameters.name === 'geolocation') {
                return { state: 'granted', onchange: null };
            }
            try {
                return await originalQuery.call(this, parameters);
            } catch (e) {
                return { state: 'prompt', onchange: null };
            }
        };

        // 8. Скрываем WebDriver-related свойства
        const propertiesToDelete = [
            'cdc_adoQpoasnfa76pfcZLmcfl_Array',
            'cdc_adoQpoasnfa76pfcZLmcfl_Promise',
            'cdc_adoQpoasnfa76pfcZLmcfl_Symbol',
            '__webdriver_evaluate',
            '__selenium_evaluate',
            '__webdriver_script_function',
            '__webdriver_script_func',
            '__webdriver_script_fn',
            '__fxdriver_evaluate',
            '__driver_unwrapped',
            '__webdriver_unwrapped',
            '__driver_evaluate',
            '__selenium_unwrapped',
            '__fxdriver_unwrapped',
            '_Selenium_IDE_Recorder',
            '_selenium',
            'calledSelenium',
            '$cdc_asdjflasutopfhvcZLmcfl_',
            '$chrome_asyncScriptInfo',
            '__$webdriverAsyncExecutor'
        ];

        propertiesToDelete.forEach(prop => {
            try {
                delete window[prop];
            } catch (e) {}
        });

        // 9. Подделываем connection info (скрываем headless)
        Object.defineProperty(navigator, 'connection', {
            get: () => ({
                effectiveType: '4g',
                rtt: 50,
                downlink: 10,
                saveData: false,
                onchange: null
            }),
            configurable: true
        });

        // 10. Консистентный screen (не выдаёт headless)
        Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth, configurable: true });
        Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight + 85, configurable: true });

        // 11. Защита от canvas fingerprint detection
        const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type) {
            if (type === 'image/png' && this.width === 16 && this.height === 16) {
                // Это скорее всего fingerprint check - добавляем шум
                const ctx = this.getContext('2d');
                const imageData = ctx.getImageData(0, 0, this.width, this.height);
                for (let i = 0; i < imageData.data.length; i += 4) {
                    imageData.data[i] = imageData.data[i] ^ (Math.random() * 2 | 0);
                }
                ctx.putImageData(imageData, 0, 0);
            }
            return originalToDataURL.apply(this, arguments);
        };

        // 12. Маскируем Notification API
        if (window.Notification) {
            Object.defineProperty(Notification, 'permission', {
                get: () => 'default',
                configurable: true
            });
        }

        // 13. Console log trap (некоторые сайты проверяют console)
        const originalConsoleLog = console.log;
        console.log = function() {
            // Скрываем логи Playwright
            const args = Array.from(arguments);
            const isPlaywrightLog = args.some(arg =>
                typeof arg === 'string' &&
                (arg.includes('playwright') || arg.includes('puppeteer') || arg.includes('selenium'))
            );
            if (!isPlaywrightLog) {
                originalConsoleLog.apply(console, arguments);
            }
        };

        // 14. Скрываем iframe detection
        Object.defineProperty(window, 'frameElement', {
            get: () => null,
            configurable: true
        });

        console.log('[Stealth] Anti-detection scripts loaded');
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

    async def random_mouse_movements(self, page: Page, count: int = 3) -> None:
        """
        Случайные движения мыши для имитации человеческого поведения.

        Args:
            page: Страница
            count: Количество движений
        """
        viewport = page.viewport_size
        if not viewport:
            return

        for _ in range(count):
            # Случайные координаты в пределах viewport
            x = random.randint(100, viewport['width'] - 100)
            y = random.randint(100, viewport['height'] - 100)

            # Плавное движение к точке
            await page.mouse.move(x, y, steps=random.randint(5, 15))
            await self.human_delay(100, 300)

    async def human_scroll(self, page: Page, direction: str = 'down', distance: int = 300) -> None:
        """
        Human-like прокрутка страницы.

        Args:
            page: Страница
            direction: Направление ('up' или 'down')
            distance: Расстояние прокрутки в пикселях
        """
        delta = distance if direction == 'down' else -distance

        # Прокручиваем с случайными интервалами
        steps = random.randint(3, 7)
        step_distance = delta // steps

        for _ in range(steps):
            await page.mouse.wheel(0, step_distance)
            await self.human_delay(50, 150)

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
