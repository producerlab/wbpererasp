"""
Браузерная автоматизация для работы с личным кабинетом Wildberries.

Использует Playwright для:
- SMS авторизации
- Создания поставок (перераспределения)
- Получения данных, недоступных через API
"""

import asyncio
import logging
import json
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)


class WBBrowserClient:
    """
    Клиент для работы с личным кабинетом WB через браузер.

    Использование:
        client = WBBrowserClient()

        # Шаг 1: Запросить SMS код
        await client.request_sms_code("+79991234567")

        # Шаг 2: Войти с кодом из SMS
        cookies = await client.login_with_sms_code("+79991234567", "123456")

        # Шаг 3: Использовать сохраненные cookies
        client = WBBrowserClient(cookies=cookies)
        await client.create_supply(...)
    """

    SELLER_URL = "https://seller.wildberries.ru"
    LOGIN_URL = "https://seller-auth.wildberries.ru/ru/?redirect_url=https%3A%2F%2Fseller.wildberries.ru%2F&fromSellerLanding"

    def __init__(self, cookies: Optional[List[Dict]] = None, headless: bool = True):
        """
        Args:
            cookies: Сохраненные cookies для авторизации
            headless: Запускать браузер в headless режиме
        """
        self.cookies = cookies
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._playwright = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self):
        """Запускает браузер"""
        logger.info("Starting browser...")
        self._playwright = await async_playwright().start()

        # Запускаем chromium с антибот защитой
        self.browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox'
            ]
        )

        # Создаем контекст с cookies если есть
        context_options = {
            'viewport': {'width': 1920, 'height': 1080},
            'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

        self.context = await self.browser.new_context(**context_options)

        # Добавляем cookies если есть
        if self.cookies:
            await self.context.add_cookies(self.cookies)

        self.page = await self.context.new_page()

        # Скрываем webdriver
        await self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => false
            });
        """)

        logger.info("Browser started successfully")

    async def close(self):
        """Закрывает браузер"""
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser closed")

    async def request_sms_code(self, phone: str) -> bool:
        """
        Запрашивает SMS код для авторизации.

        Args:
            phone: Номер телефона в формате +79991234567

        Returns:
            True если SMS запрошен успешно
        """
        if not self.page:
            await self.start()

        try:
            logger.info(f"Requesting SMS code for phone: {phone}")

            # Открываем страницу авторизации
            await self.page.goto(self.LOGIN_URL, wait_until='networkidle', timeout=30000)

            # Ждем поле ввода телефона
            # ВАЖНО: Селекторы нужно будет уточнить на реальном сайте!
            phone_input = await self.page.wait_for_selector(
                'input[type="tel"], input[name="phone"], input[placeholder*="телефон"]',
                timeout=10000
            )

            if not phone_input:
                logger.error("Phone input field not found")
                return False

            # Очищаем и вводим телефон
            await phone_input.fill(phone)
            logger.info(f"Phone entered: {phone}")

            # Ищем кнопку "Получить код" / "Войти"
            submit_button = await self.page.wait_for_selector(
                'button[type="submit"], button:has-text("Получить код"), button:has-text("Войти")',
                timeout=5000
            )

            if not submit_button:
                logger.error("Submit button not found")
                return False

            # Кликаем
            await submit_button.click()
            logger.info("Submit button clicked, waiting for SMS code input...")

            # Ждем появления поля для SMS кода
            sms_input = await self.page.wait_for_selector(
                'input[type="text"][maxlength="6"], input[name="code"], input[placeholder*="код"]',
                timeout=10000
            )

            if sms_input:
                logger.info("SMS code input appeared - SMS sent successfully")
                return True
            else:
                logger.error("SMS code input did not appear")
                return False

        except PlaywrightTimeout as e:
            logger.error(f"Timeout while requesting SMS code: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to request SMS code: {e}", exc_info=True)
            return False

    async def login_with_sms_code(self, phone: str, sms_code: str) -> Optional[List[Dict]]:
        """
        Авторизуется с SMS кодом и возвращает cookies.

        Args:
            phone: Номер телефона
            sms_code: Код из SMS

        Returns:
            Список cookies для сохранения в БД
        """
        if not self.page:
            await self.start()

        try:
            logger.info(f"Logging in with SMS code for phone: {phone}")

            # Если мы еще не на странице с полем SMS кода - запрашиваем его
            current_url = self.page.url
            if 'seller.wildberries.ru' not in current_url:
                success = await self.request_sms_code(phone)
                if not success:
                    logger.error("Failed to request SMS code before login")
                    return None

            # Ждем поле для SMS кода
            sms_input = await self.page.wait_for_selector(
                'input[type="text"][maxlength="6"], input[name="code"], input[placeholder*="код"]',
                timeout=10000
            )

            if not sms_input:
                logger.error("SMS code input not found")
                return None

            # Вводим код
            await sms_input.fill(sms_code)
            logger.info(f"SMS code entered: {sms_code}")

            # Ищем кнопку подтверждения
            confirm_button = await self.page.wait_for_selector(
                'button[type="submit"], button:has-text("Войти"), button:has-text("Подтвердить")',
                timeout=5000
            )

            if confirm_button:
                await confirm_button.click()
                logger.info("Confirm button clicked")

            # Ждем успешной авторизации (редирект в личный кабинет)
            try:
                await self.page.wait_for_url(
                    "**/main**",  # Обычно после логина редирект на /main
                    timeout=15000
                )
                logger.info("Successfully logged in - redirected to main page")
            except PlaywrightTimeout:
                # Альтернативный способ проверки - ждем исчезновения формы логина
                await self.page.wait_for_selector(
                    'input[name="code"]',
                    state='hidden',
                    timeout=10000
                )
                logger.info("Login form disappeared - assuming successful login")

            # Получаем cookies
            cookies = await self.context.cookies()
            logger.info(f"Cookies retrieved: {len(cookies)} cookies")

            return cookies

        except PlaywrightTimeout as e:
            logger.error(f"Timeout while logging in with SMS code: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to login with SMS code: {e}", exc_info=True)
            return None

    async def check_session_valid(self) -> bool:
        """
        Проверяет валидность сохраненной сессии.

        Returns:
            True если сессия валидна
        """
        if not self.cookies:
            return False

        if not self.page:
            await self.start()

        try:
            # Пытаемся открыть главную страницу
            await self.page.goto(f"{self.SELLER_URL}/main", wait_until='networkidle', timeout=15000)

            # Проверяем, не редиректнуло ли на логин
            if 'login' in self.page.url.lower():
                logger.info("Session invalid - redirected to login")
                return False

            # Проверяем наличие элементов личного кабинета
            is_logged_in = await self.page.locator('text="Поставки"').count() > 0
            logger.info(f"Session valid: {is_logged_in}")
            return is_logged_in

        except Exception as e:
            logger.error(f"Failed to check session validity: {e}")
            return False

    async def get_supplier_info(self) -> Optional[Dict[str, Any]]:
        """
        Получает информацию о поставщике из личного кабинета.

        Returns:
            Словарь с информацией о поставщике
        """
        if not self.page:
            await self.start()

        try:
            await self.page.goto(f"{self.SELLER_URL}/main", wait_until='networkidle')

            # Ищем имя поставщика на странице
            # ВАЖНО: Селекторы нужно уточнить!
            supplier_name_element = await self.page.query_selector(
                '.supplier-name, [class*="supplier"], [data-testid="supplier-name"]'
            )

            supplier_name = "Мой магазин"  # Дефолтное значение
            if supplier_name_element:
                supplier_name = await supplier_name_element.inner_text()

            return {
                "name": supplier_name.strip(),
                "url": self.page.url
            }

        except Exception as e:
            logger.error(f"Failed to get supplier info: {e}")
            return None

    async def create_supply(
        self,
        nm_id: int,
        quantity: int,
        warehouse_from: int,
        warehouse_to: int
    ) -> bool:
        """
        Создает поставку (перераспределение) через браузер.

        Args:
            nm_id: Артикул WB
            quantity: Количество
            warehouse_from: ID склада-источника
            warehouse_to: ID склада-назначения

        Returns:
            True если поставка создана успешно
        """
        if not self.page:
            await self.start()

        try:
            logger.info(f"Creating supply: {nm_id} x{quantity} from {warehouse_from} to {warehouse_to}")

            # Открываем страницу создания поставки
            await self.page.goto(
                f"{self.SELLER_URL}/supplies-management/all-supplies",
                wait_until='networkidle',
                timeout=30000
            )

            # ВАЖНО: Дальнейшие селекторы нужно уточнить на реальном сайте!
            # Это примерная логика, которую нужно адаптировать

            # Кликаем "Создать поставку"
            create_button = await self.page.wait_for_selector(
                'button:has-text("Создать"), button:has-text("Новая поставка")',
                timeout=10000
            )
            await create_button.click()

            # Заполняем форму
            # ... (здесь нужна детальная логика работы с формой WB)

            logger.info("Supply created successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to create supply: {e}", exc_info=True)
            return False


class SessionExpiredError(Exception):
    """Исключение при истечении сессии"""
    pass
