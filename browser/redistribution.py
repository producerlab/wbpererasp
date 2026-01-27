"""
Модуль перемещения остатков через браузер ЛК Wildberries.

Функционал:
- Открытие страницы перемещения
- Ввод параметров (артикул, склады, количество)
- Выполнение перемещения
- Обработка ошибок и лимитов
"""

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from playwright.async_api import BrowserContext, Page, TimeoutError as PlaywrightTimeout

from .browser_service import BrowserService, get_browser_service
from utils.encryption import decrypt_token

logger = logging.getLogger(__name__)


class RedistributionStatus(Enum):
    """Статусы перемещения"""
    SUCCESS = "success"                  # Успешно создано
    NO_QUOTA = "no_quota"                # Нет квоты на складе
    INVALID_ARTICLE = "invalid_article"  # Артикул не найден
    INVALID_QUANTITY = "invalid_quantity"  # Недостаточно остатков
    SESSION_EXPIRED = "session_expired"  # Сессия истекла
    BLOCKED = "blocked"                  # Аккаунт заблокирован
    ERROR = "error"                      # Другая ошибка


@dataclass
class RedistributionResult:
    """Результат перемещения"""
    status: RedistributionStatus
    message: str
    supply_id: Optional[str] = None  # ID созданной заявки в WB
    screenshot: Optional[bytes] = None  # Скриншот для отладки


class WBRedistributionService:
    """Сервис перемещения остатков"""

    # URL страницы перемещения в ЛК
    REDISTRIBUTION_URL = "https://seller.wildberries.ru/supplies-management/redistribution"
    STOCKS_URL = "https://seller.wildberries.ru/analytics/warehouse-remains"

    # Селекторы элементов
    SELECTORS = {
        # Поиск артикула
        'article_input': 'input[placeholder*="артикул"], input[placeholder*="nmId"], input[name="article"]',
        'article_search': 'button:has-text("Найти"), button:has-text("Поиск")',
        'article_result': '[class*="product"], [class*="item"], [class*="article"]',

        # Выбор складов
        'source_select': 'select[name="source"], [class*="source"] select',
        'target_select': 'select[name="target"], [class*="target"] select',

        # Количество
        'quantity_input': 'input[type="number"], input[name="quantity"]',

        # Подтверждение
        'submit_button': 'button[type="submit"], button:has-text("Переместить"), button:has-text("Создать")',
        'confirm_button': 'button:has-text("Подтвердить"), button:has-text("Да")',

        # Сообщения
        'success_message': '[class*="success"], [class*="Success"]',
        'error_message': '[class*="error"], [class*="Error"], [role="alert"]',
        'quota_message': ':text("лимит"), :text("квота"), :text("недоступ")',
    }

    def __init__(self):
        self._browser_service: Optional[BrowserService] = None

    async def _get_browser(self) -> BrowserService:
        """Получить browser service"""
        if not self._browser_service:
            self._browser_service = await get_browser_service(headless=True)
        return self._browser_service

    async def execute_redistribution(
        self,
        cookies_encrypted: str,
        nm_id: int,
        source_warehouse_id: int,
        target_warehouse_id: int,
        quantity: int
    ) -> RedistributionResult:
        """
        Выполнить перемещение остатков.

        Args:
            cookies_encrypted: Зашифрованные cookies сессии
            nm_id: Артикул товара
            source_warehouse_id: ID склада-источника
            target_warehouse_id: ID склада-назначения
            quantity: Количество

        Returns:
            RedistributionResult с результатом
        """
        browser = await self._get_browser()
        context: Optional[BrowserContext] = None
        page: Optional[Page] = None

        try:
            # Расшифровываем и парсим cookies
            cookies_json = decrypt_token(cookies_encrypted)
            cookies = browser.deserialize_cookies(cookies_json)

            # Создаём контекст с сессией
            context = await browser.create_context(cookies=cookies)
            page = await browser.create_page(context)

            # Открываем страницу перемещения
            logger.info(f"Opening redistribution page for nm_id={nm_id}")
            await page.goto(self.REDISTRIBUTION_URL, wait_until='networkidle')
            await browser.human_delay(2000, 3000)

            # Проверяем авторизацию
            if '/login' in page.url:
                logger.warning("Session expired - redirected to login")
                return RedistributionResult(
                    status=RedistributionStatus.SESSION_EXPIRED,
                    message="Сессия истекла. Необходима повторная авторизация.",
                    screenshot=await browser.take_screenshot(page)
                )

            # Ищем артикул
            result = await self._search_article(page, browser, nm_id)
            if result:
                return result

            # Выбираем склады
            result = await self._select_warehouses(
                page, browser, source_warehouse_id, target_warehouse_id
            )
            if result:
                return result

            # Вводим количество
            result = await self._enter_quantity(page, browser, quantity)
            if result:
                return result

            # Подтверждаем перемещение
            return await self._submit_redistribution(page, browser)

        except PlaywrightTimeout as e:
            logger.error(f"Timeout during redistribution: {e}")
            screenshot = await browser.take_screenshot(page) if page else None
            return RedistributionResult(
                status=RedistributionStatus.ERROR,
                message="Превышено время ожидания. Попробуйте позже.",
                screenshot=screenshot
            )

        except Exception as e:
            logger.error(f"Error during redistribution: {e}", exc_info=True)
            screenshot = await browser.take_screenshot(page) if page else None
            return RedistributionResult(
                status=RedistributionStatus.ERROR,
                message=f"Ошибка: {str(e)}",
                screenshot=screenshot
            )

        finally:
            if context:
                await context.close()

    async def _search_article(
        self,
        page: Page,
        browser: BrowserService,
        nm_id: int
    ) -> Optional[RedistributionResult]:
        """
        Поиск артикула на странице.

        Returns:
            None если успешно, RedistributionResult если ошибка
        """
        try:
            # Ищем поле ввода артикула
            article_input = await page.wait_for_selector(
                self.SELECTORS['article_input'],
                timeout=10000
            )

            if not article_input:
                return RedistributionResult(
                    status=RedistributionStatus.ERROR,
                    message="Поле ввода артикула не найдено",
                    screenshot=await browser.take_screenshot(page)
                )

            # Вводим артикул
            await browser.human_type(page, self.SELECTORS['article_input'], str(nm_id))
            await browser.human_delay(500, 1000)

            # Нажимаем поиск (если есть кнопка)
            try:
                search_btn = await page.query_selector(self.SELECTORS['article_search'])
                if search_btn:
                    await search_btn.click()
                    await browser.human_delay(2000, 3000)
            except Exception:
                pass

            # Проверяем результат
            error = await self._check_error(page)
            if error and ('не найден' in error.lower() or 'not found' in error.lower()):
                return RedistributionResult(
                    status=RedistributionStatus.INVALID_ARTICLE,
                    message=f"Артикул {nm_id} не найден",
                    screenshot=await browser.take_screenshot(page)
                )

            return None

        except PlaywrightTimeout:
            return RedistributionResult(
                status=RedistributionStatus.ERROR,
                message="Не удалось найти поле ввода артикула",
                screenshot=await browser.take_screenshot(page)
            )

    async def _select_warehouses(
        self,
        page: Page,
        browser: BrowserService,
        source_id: int,
        target_id: int
    ) -> Optional[RedistributionResult]:
        """
        Выбор складов.

        Returns:
            None если успешно, RedistributionResult если ошибка
        """
        try:
            # Выбираем склад-источник
            source_select = await page.query_selector(self.SELECTORS['source_select'])
            if source_select:
                await source_select.select_option(value=str(source_id))
                await browser.human_delay(500, 1000)

            # Выбираем склад-назначение
            target_select = await page.query_selector(self.SELECTORS['target_select'])
            if target_select:
                await target_select.select_option(value=str(target_id))
                await browser.human_delay(500, 1000)

            # Проверяем ошибки (например, нет квоты)
            error = await self._check_error(page)
            if error:
                if 'лимит' in error.lower() or 'квот' in error.lower():
                    return RedistributionResult(
                        status=RedistributionStatus.NO_QUOTA,
                        message=f"Нет квоты на выбранном направлении: {error}",
                        screenshot=await browser.take_screenshot(page)
                    )

            return None

        except Exception as e:
            logger.error(f"Error selecting warehouses: {e}")
            return None  # Продолжаем, возможно селекторы другие

    async def _enter_quantity(
        self,
        page: Page,
        browser: BrowserService,
        quantity: int
    ) -> Optional[RedistributionResult]:
        """
        Ввод количества.

        Returns:
            None если успешно, RedistributionResult если ошибка
        """
        try:
            quantity_input = await page.query_selector(self.SELECTORS['quantity_input'])
            if quantity_input:
                await quantity_input.fill(str(quantity))
                await browser.human_delay(300, 500)

            # Проверяем ошибки
            error = await self._check_error(page)
            if error and ('недостаточно' in error.lower() or 'превышает' in error.lower()):
                return RedistributionResult(
                    status=RedistributionStatus.INVALID_QUANTITY,
                    message=f"Недостаточно остатков: {error}",
                    screenshot=await browser.take_screenshot(page)
                )

            return None

        except Exception as e:
            logger.error(f"Error entering quantity: {e}")
            return None

    async def _submit_redistribution(
        self,
        page: Page,
        browser: BrowserService
    ) -> RedistributionResult:
        """
        Подтверждение перемещения.

        Returns:
            RedistributionResult с результатом
        """
        try:
            # Нажимаем кнопку отправки
            submit_btn = await page.query_selector(self.SELECTORS['submit_button'])
            if submit_btn:
                await submit_btn.click()
                await browser.human_delay(2000, 3000)

            # Подтверждаем если есть модальное окно
            try:
                confirm_btn = await page.wait_for_selector(
                    self.SELECTORS['confirm_button'],
                    timeout=3000
                )
                if confirm_btn:
                    await confirm_btn.click()
                    await browser.human_delay(2000, 3000)
            except PlaywrightTimeout:
                pass

            # Проверяем результат
            await browser.human_delay(2000, 3000)

            # Ищем сообщение об успехе
            success = await page.query_selector(self.SELECTORS['success_message'])
            if success:
                success_text = await success.inner_text()
                # Пытаемся извлечь ID заявки
                supply_id = self._extract_supply_id(success_text)
                return RedistributionResult(
                    status=RedistributionStatus.SUCCESS,
                    message="Заявка на перемещение создана",
                    supply_id=supply_id,
                    screenshot=await browser.take_screenshot(page)
                )

            # Проверяем ошибки
            error = await self._check_error(page)
            if error:
                if 'лимит' in error.lower() or 'квот' in error.lower():
                    return RedistributionResult(
                        status=RedistributionStatus.NO_QUOTA,
                        message=error,
                        screenshot=await browser.take_screenshot(page)
                    )
                else:
                    return RedistributionResult(
                        status=RedistributionStatus.ERROR,
                        message=error,
                        screenshot=await browser.take_screenshot(page)
                    )

            # Не понятно что произошло
            return RedistributionResult(
                status=RedistributionStatus.ERROR,
                message="Не удалось определить результат операции",
                screenshot=await browser.take_screenshot(page)
            )

        except Exception as e:
            logger.error(f"Error submitting redistribution: {e}")
            return RedistributionResult(
                status=RedistributionStatus.ERROR,
                message=f"Ошибка при подтверждении: {str(e)}",
                screenshot=await browser.take_screenshot(page) if page else None
            )

    async def _check_error(self, page: Page) -> Optional[str]:
        """Проверить наличие ошибки на странице"""
        try:
            error_element = await page.query_selector(self.SELECTORS['error_message'])
            if error_element:
                return await error_element.inner_text()
        except Exception:
            pass
        return None

    def _extract_supply_id(self, text: str) -> Optional[str]:
        """Извлечь ID заявки из текста"""
        import re
        # Ищем числовой ID или UUID
        match = re.search(r'(?:ID|№|номер)[\s:]*([A-Za-z0-9-]+)', text, re.IGNORECASE)
        if match:
            return match.group(1)
        # Просто число
        match = re.search(r'\d{6,}', text)
        if match:
            return match.group(0)
        return None

    async def check_quota(
        self,
        cookies_encrypted: str,
        warehouse_id: int
    ) -> Optional[int]:
        """
        Проверить квоту на складе.

        Args:
            cookies_encrypted: Зашифрованные cookies
            warehouse_id: ID склада

        Returns:
            Доступная квота или None если не удалось определить
        """
        browser = await self._get_browser()
        context: Optional[BrowserContext] = None

        try:
            cookies_json = decrypt_token(cookies_encrypted)
            cookies = browser.deserialize_cookies(cookies_json)

            context = await browser.create_context(cookies=cookies)
            page = await browser.create_page(context)

            await page.goto(self.REDISTRIBUTION_URL, wait_until='networkidle')
            await browser.human_delay(1000, 2000)

            # Парсим квоты со страницы
            # Это зависит от структуры страницы WB
            # TODO: Реализовать парсинг квот

            return None

        except Exception as e:
            logger.error(f"Error checking quota: {e}")
            return None

        finally:
            if context:
                await context.close()


# Singleton instance
_redistribution_service: Optional[WBRedistributionService] = None


def get_redistribution_service() -> WBRedistributionService:
    """Получить singleton instance WBRedistributionService"""
    global _redistribution_service
    if _redistribution_service is None:
        _redistribution_service = WBRedistributionService()
    return _redistribution_service
