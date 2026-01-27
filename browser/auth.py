"""
Модуль авторизации в ЛК Wildberries через SMS.

Flow авторизации:
1. Открываем страницу seller.wildberries.ru
2. Вводим номер телефона
3. WB отправляет SMS с кодом
4. Пользователь вводит код через Telegram бота
5. Браузер вводит код
6. Получаем сессию (cookies)
7. Сохраняем cookies в БД
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Any

from playwright.async_api import BrowserContext, Page, TimeoutError as PlaywrightTimeout

from .browser_service import BrowserService, get_browser_service

logger = logging.getLogger(__name__)


class AuthStatus(Enum):
    """Статусы авторизации"""
    PENDING_PHONE = "pending_phone"      # Ожидание ввода номера
    PENDING_CODE = "pending_code"        # Ожидание SMS кода
    SUCCESS = "success"                  # Успешная авторизация
    FAILED = "failed"                    # Ошибка авторизации
    BLOCKED = "blocked"                  # Аккаунт заблокирован
    INVALID_CODE = "invalid_code"        # Неверный код
    CODE_EXPIRED = "code_expired"        # Код истёк
    TOO_MANY_ATTEMPTS = "too_many"       # Слишком много попыток


@dataclass
class AuthSession:
    """Сессия авторизации"""
    user_id: int                         # Telegram user ID
    phone: str                           # Номер телефона
    status: AuthStatus                   # Текущий статус
    context: Optional[BrowserContext]    # Browser context
    page: Optional[Page]                 # Текущая страница
    cookies: Optional[list] = None       # Cookies после успешной авторизации
    error_message: Optional[str] = None  # Сообщение об ошибке
    supplier_name: Optional[str] = None  # Название поставщика из ЛК


class WBAuthService:
    """Сервис авторизации в ЛК Wildberries"""

    WB_SELLER_URL = "https://seller.wildberries.ru"
    WB_LOGIN_URLS = [
        "https://seller.wildberries.ru/login",
        "https://seller-auth.wildberries.ru/",
        "https://seller.wildberries.ru"
    ]

    # Селекторы элементов на странице авторизации WB
    # WB часто меняет структуру, поэтому используем множественные селекторы
    SELECTORS = {
        'phone_input': ', '.join([
            'input[type="tel"]',
            'input[name="phone"]',
            'input[placeholder*="телефон"]',
            'input[placeholder*="Телефон"]',
            'input[class*="phone"]',
            'input[class*="Phone"]',
            'input[autocomplete="tel"]',
            '[data-testid*="phone"] input',
            'form input[type="text"]',  # Fallback - первый input в форме
        ]),
        'submit_button': ', '.join([
            'button[type="submit"]',
            'button[class*="submit"]',
            'button[class*="Submit"]',
            'button:has-text("Получить код")',
            'button:has-text("Войти")',
            'button:has-text("Далее")',
            'button:has-text("Продолжить")',
            '[data-testid*="submit"]',
            'form button',
        ]),
        'code_input': ', '.join([
            'input[type="tel"][maxlength="6"]',
            'input[type="text"][maxlength="6"]',
            'input[name="code"]',
            'input[placeholder*="код"]',
            'input[placeholder*="Код"]',
            'input[class*="code"]',
            'input[class*="Code"]',
            'input[autocomplete="one-time-code"]',
            '[data-testid*="code"] input',
        ]),
        'code_submit': ', '.join([
            'button[type="submit"]',
            'button:has-text("Войти")',
            'button:has-text("Подтвердить")',
            'button:has-text("Далее")',
        ]),
        'error_message': '[class*="error"], [class*="Error"], [role="alert"], [class*="warning"]',
        'supplier_name': '[class*="supplier"], [class*="company"], [class*="header"] span, h1, h2',
    }

    def __init__(self):
        self._sessions: dict[int, AuthSession] = {}  # user_id -> AuthSession
        self._browser_service: Optional[BrowserService] = None

    async def _get_browser(self) -> BrowserService:
        """Получить browser service"""
        if not self._browser_service:
            self._browser_service = await get_browser_service(headless=True)
        return self._browser_service

    def normalize_phone(self, phone: str) -> str:
        """
        Нормализация номера телефона.

        Args:
            phone: Номер в любом формате

        Returns:
            Номер в формате +7XXXXXXXXXX
        """
        # Убираем всё кроме цифр
        digits = re.sub(r'\D', '', phone)

        # Если начинается с 8, меняем на 7
        if digits.startswith('8') and len(digits) == 11:
            digits = '7' + digits[1:]

        # Если 10 цифр, добавляем 7
        if len(digits) == 10:
            digits = '7' + digits

        # Проверяем длину
        if len(digits) != 11:
            raise ValueError(f"Некорректный номер телефона: {phone}")

        return '+' + digits

    async def start_auth(self, user_id: int, phone: str) -> AuthSession:
        """
        Начать процесс авторизации.

        Args:
            user_id: Telegram user ID
            phone: Номер телефона

        Returns:
            AuthSession с статусом PENDING_CODE или FAILED
        """
        try:
            # Нормализуем номер
            normalized_phone = self.normalize_phone(phone)
            logger.info(f"Начало авторизации для user {user_id}, phone {normalized_phone[:5]}***")

            # Создаём сессию
            browser = await self._get_browser()
            context = await browser.create_context()
            page = await browser.create_page(context)

            session = AuthSession(
                user_id=user_id,
                phone=normalized_phone,
                status=AuthStatus.PENDING_PHONE,
                context=context,
                page=page
            )
            self._sessions[user_id] = session

            # Пробуем разные URL для авторизации (WB может менять структуру)
            phone_input = None

            for login_url in self.WB_LOGIN_URLS:
                logger.info(f"Пробуем URL: {login_url}")
                try:
                    await page.goto(login_url, wait_until='domcontentloaded', timeout=30000)
                    await browser.human_delay(2000, 3000)

                    # Логируем текущий URL (возможен редирект)
                    current_url = page.url
                    logger.info(f"Текущий URL после загрузки: {current_url}")

                    # Ждём дополнительно если есть защита от ботов
                    await browser.human_delay(1000, 2000)

                    # Ищем поле ввода телефона
                    phone_input = await self._find_phone_input(page)
                    if phone_input:
                        logger.info(f"Нашли поле телефона на {current_url}")
                        break

                except Exception as e:
                    logger.warning(f"Ошибка при загрузке {login_url}: {e}")
                    continue
            if not phone_input:
                # Сохраняем скриншот для диагностики
                screenshot = await browser.take_screenshot(page)
                if screenshot:
                    logger.error(f"Страница при ошибке сохранена (screenshot available)")

                # Получаем HTML для диагностики
                page_content = await page.content()
                logger.error(f"Page URL: {page.url}")
                logger.error(f"Page title: {await page.title()}")
                logger.error(f"HTML length: {len(page_content)} chars")

                session.status = AuthStatus.FAILED
                session.error_message = "Не найдено поле ввода телефона. Возможно, WB изменил страницу авторизации."
                logger.error(session.error_message)
                return session

            # Вводим номер телефона
            await browser.human_type(page, self.SELECTORS['phone_input'], normalized_phone)
            await browser.human_delay(500, 1000)

            # Нажимаем кнопку отправки
            submit_button = await self._find_submit_button(page)
            if submit_button:
                await submit_button.click()
                await browser.human_delay(2000, 3000)

            # Проверяем ошибки
            error = await self._check_error(page)
            if error:
                session.status = AuthStatus.FAILED
                session.error_message = error
                logger.error(f"Ошибка авторизации: {error}")
                return session

            # Проверяем появление поля для кода
            code_input = await self._find_code_input(page)
            if code_input:
                session.status = AuthStatus.PENDING_CODE
                logger.info(f"SMS отправлено на {normalized_phone[:5]}***")
            else:
                session.status = AuthStatus.FAILED
                session.error_message = "Не удалось отправить SMS. Попробуйте позже."
                logger.error(session.error_message)

            return session

        except PlaywrightTimeout as e:
            logger.error(f"Timeout при авторизации: {e}")
            if user_id in self._sessions:
                self._sessions[user_id].status = AuthStatus.FAILED
                self._sessions[user_id].error_message = "Превышено время ожидания"
            raise
        except Exception as e:
            logger.error(f"Ошибка авторизации: {e}")
            if user_id in self._sessions:
                self._sessions[user_id].status = AuthStatus.FAILED
                self._sessions[user_id].error_message = str(e)
            raise

    async def submit_code(self, user_id: int, code: str) -> AuthSession:
        """
        Отправить SMS код для завершения авторизации.

        Args:
            user_id: Telegram user ID
            code: SMS код (6 цифр)

        Returns:
            AuthSession с обновлённым статусом
        """
        session = self._sessions.get(user_id)
        if not session:
            raise ValueError(f"Сессия не найдена для user {user_id}")

        if session.status != AuthStatus.PENDING_CODE:
            raise ValueError(f"Неверный статус сессии: {session.status}")

        # Валидация кода
        code = code.strip()
        if not code.isdigit() or len(code) != 6:
            session.status = AuthStatus.INVALID_CODE
            session.error_message = "Код должен содержать 6 цифр"
            return session

        browser = await self._get_browser()
        page = session.page

        try:
            # Вводим код
            code_input = await self._find_code_input(page)
            if not code_input:
                session.status = AuthStatus.FAILED
                session.error_message = "Поле ввода кода не найдено"
                return session

            # Очищаем поле и вводим код
            await code_input.fill('')
            await browser.human_delay(300, 500)

            for digit in code:
                await page.keyboard.type(digit, delay=100)
                await browser.human_delay(50, 150)

            await browser.human_delay(1000, 2000)

            # Нажимаем подтверждение
            submit_button = await self._find_submit_button(page)
            if submit_button:
                await submit_button.click()

            # Ждём результат
            await browser.human_delay(3000, 5000)

            # Проверяем ошибки
            error = await self._check_error(page)
            if error:
                if "неверный" in error.lower() or "invalid" in error.lower():
                    session.status = AuthStatus.INVALID_CODE
                elif "истёк" in error.lower() or "expired" in error.lower():
                    session.status = AuthStatus.CODE_EXPIRED
                elif "много" in error.lower() or "attempts" in error.lower():
                    session.status = AuthStatus.TOO_MANY_ATTEMPTS
                else:
                    session.status = AuthStatus.FAILED
                session.error_message = error
                return session

            # Проверяем успешную авторизацию (должен быть редирект на ЛК)
            if await self._check_logged_in(page):
                session.status = AuthStatus.SUCCESS
                session.cookies = await session.context.cookies()
                session.supplier_name = await self._get_supplier_name(page)
                logger.info(f"Успешная авторизация для user {user_id}")
            else:
                session.status = AuthStatus.FAILED
                session.error_message = "Не удалось войти в аккаунт"

            return session

        except PlaywrightTimeout:
            session.status = AuthStatus.FAILED
            session.error_message = "Превышено время ожидания"
            return session
        except Exception as e:
            session.status = AuthStatus.FAILED
            session.error_message = str(e)
            logger.error(f"Ошибка при вводе кода: {e}")
            return session

    async def _find_phone_input(self, page: Page) -> Optional[Any]:
        """Найти поле ввода телефона"""
        # Сначала пробуем комбинированный селектор
        try:
            element = await page.wait_for_selector(
                self.SELECTORS['phone_input'],
                timeout=10000,
                state='visible'
            )
            if element:
                logger.info("Найдено поле телефона через комбинированный селектор")
                return element
        except PlaywrightTimeout:
            logger.warning("Комбинированный селектор не сработал, пробуем по одному")

        # Пробуем каждый селектор отдельно
        individual_selectors = [
            'input[type="tel"]',
            'input[autocomplete="tel"]',
            'input[name="phone"]',
            'input[placeholder*="телефон" i]',
            'input[class*="phone" i]',
            '#phone',
            '[data-testid*="phone"] input',
        ]

        for selector in individual_selectors:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    logger.info(f"Найдено поле телефона через: {selector}")
                    return element
            except Exception as e:
                logger.debug(f"Селектор {selector} не сработал: {e}")

        # Последняя попытка - найти любой видимый input
        try:
            inputs = await page.query_selector_all('input:visible')
            logger.info(f"Найдено {len(inputs)} видимых input элементов")
            for inp in inputs:
                input_type = await inp.get_attribute('type') or 'text'
                if input_type in ['tel', 'text', 'number']:
                    logger.info(f"Используем input type={input_type} как поле телефона")
                    return inp
        except Exception as e:
            logger.error(f"Ошибка при поиске input: {e}")

        return None

    async def _find_code_input(self, page: Page) -> Optional[Any]:
        """Найти поле ввода SMS кода"""
        try:
            return await page.wait_for_selector(
                self.SELECTORS['code_input'],
                timeout=10000,
                state='visible'
            )
        except PlaywrightTimeout:
            return None

    async def _find_submit_button(self, page: Page) -> Optional[Any]:
        """Найти кнопку отправки"""
        try:
            return await page.wait_for_selector(
                self.SELECTORS['submit_button'],
                timeout=5000,
                state='visible'
            )
        except PlaywrightTimeout:
            return None

    async def _check_error(self, page: Page) -> Optional[str]:
        """Проверить наличие ошибки на странице"""
        try:
            error_element = await page.query_selector(self.SELECTORS['error_message'])
            if error_element:
                return await error_element.inner_text()
        except Exception:
            pass
        return None

    async def _check_logged_in(self, page: Page) -> bool:
        """Проверить успешную авторизацию"""
        current_url = page.url

        # Если URL изменился с login, значит вошли
        if '/login' not in current_url and 'seller.wildberries.ru' in current_url:
            return True

        # Проверяем наличие элементов ЛК
        try:
            # Ищем элементы, характерные для авторизованного состояния
            await page.wait_for_selector(
                '[class*="menu"], [class*="sidebar"], [class*="dashboard"]',
                timeout=5000
            )
            return True
        except PlaywrightTimeout:
            return False

    async def _get_supplier_name(self, page: Page) -> Optional[str]:
        """Получить название поставщика из ЛК"""
        try:
            # Пытаемся найти имя поставщика в разных местах
            selectors = [
                '[class*="supplier-name"]',
                '[class*="company-name"]',
                '[class*="header"] [class*="name"]',
                '[class*="profile"] span',
            ]

            for selector in selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        text = await element.inner_text()
                        if text and len(text) > 2:
                            return text.strip()
                except Exception:
                    continue

        except Exception as e:
            logger.debug(f"Не удалось получить имя поставщика: {e}")

        return None

    async def get_session(self, user_id: int) -> Optional[AuthSession]:
        """Получить текущую сессию пользователя"""
        return self._sessions.get(user_id)

    async def close_session(self, user_id: int) -> None:
        """Закрыть сессию авторизации"""
        session = self._sessions.get(user_id)
        if session:
            if session.context:
                await session.context.close()
            del self._sessions[user_id]
            logger.debug(f"Сессия закрыта для user {user_id}")

    async def take_screenshot(self, user_id: int) -> Optional[bytes]:
        """
        Сделать скриншот текущей страницы сессии.

        Args:
            user_id: Telegram user ID

        Returns:
            Скриншот в PNG или None
        """
        session = self._sessions.get(user_id)
        if session and session.page:
            browser = await self._get_browser()
            return await browser.take_screenshot(session.page)
        return None


# Singleton instance
_auth_service: Optional[WBAuthService] = None


def get_auth_service() -> WBAuthService:
    """Получить singleton instance WBAuthService"""
    global _auth_service
    if _auth_service is None:
        _auth_service = WBAuthService()
    return _auth_service
