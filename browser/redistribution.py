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
    # Правильный URL - страница остатков на складе с кнопкой "Перераспределить остатки"
    REDISTRIBUTION_URL = "https://seller.wildberries.ru/analytics-reports/warehouse-remains"
    STOCKS_URL = "https://seller.wildberries.ru/analytics-reports/warehouse-remains"

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
        """Получить browser service - всегда создаём новый для избежания проблем с event loop"""
        # Не используем кеширование/singleton из-за проблем с разными event loops
        # (FastAPI vs Playwright могут работать в разных loops)
        # Создаём новый instance напрямую
        service = BrowserService(headless=True)
        await service.start()
        return service

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
            if browser:
                await browser.stop()

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
            except Exception as e:
                logger.warning(f"Кнопка поиска недоступна или ошибка клика: {e}")

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
        except Exception as e:
            logger.debug(f"Не удалось проверить ошибку на странице: {e}")
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
            if browser:
                await browser.stop()

    async def search_product_via_modal(
        self,
        cookies_encrypted: str,
        query: str
    ) -> list:
        """
        Поиск товара через модальное окно "Перераспределить остатки".

        Открывает страницу warehouse-remains, кликает кнопку,
        вводит артикул в autocomplete и получает результаты.

        Args:
            cookies_encrypted: Зашифрованные cookies
            query: Артикул или часть артикула

        Returns:
            Список найденных товаров
        """
        browser = await self._get_browser()
        context: Optional[BrowserContext] = None
        page: Optional[Page] = None

        try:
            cookies_json = decrypt_token(cookies_encrypted)
            cookies = browser.deserialize_cookies(cookies_json)

            context = await browser.create_context(cookies=cookies)
            page = await browser.create_page(context)

            # Открываем страницу остатков
            logger.info(f"Opening {self.STOCKS_URL} for product search")
            await page.goto(self.STOCKS_URL, wait_until='networkidle', timeout=30000)
            await browser.human_delay(1500, 2500)

            # Проверяем авторизацию
            current_url = page.url
            logger.info(f"Current URL after navigation: {current_url}")
            if '/login' in current_url or 'auth' in current_url:
                logger.warning(f"Session expired - redirected to: {current_url}")
                # Сохраняем скриншот для отладки
                screenshot_path = "/tmp/wb_session_expired.png"
                await browser.take_screenshot(page, path=screenshot_path)
                logger.info(f"Session expired screenshot saved to {screenshot_path}")
                return []

            # Кликаем кнопку "Перераспределить остатки"
            redistribute_btn = None
            selectors_to_try = [
                'text=Перераспределить остатки',
                'button:has-text("Перераспределить")',
                '[class*="redistribute"]',
                'a:has-text("Перераспределить")',
            ]

            for selector in selectors_to_try:
                try:
                    redistribute_btn = await page.query_selector(selector)
                    if redistribute_btn:
                        logger.info(f"Found redistribute button: {selector}")
                        break
                except:
                    continue

            if not redistribute_btn:
                logger.warning("Redistribute button not found, trying search on page directly")
                # Сохраняем скриншот для отладки
                screenshot_path = "/tmp/wb_debug_screenshot.png"
                await browser.take_screenshot(page, path=screenshot_path)
                logger.info(f"Screenshot saved to {screenshot_path}")

                # Пробуем найти поле поиска прямо на странице
                search_selectors = [
                    'input[placeholder*="Поиск"]',
                    'input[placeholder*="поиск"]',
                    'input[placeholder*="Артикул"]',
                    'input[placeholder*="артикул"]',
                    'input[placeholder*="nmId"]',
                    'input[type="search"]',
                    '[class*="search"] input',
                    '[class*="Search"] input',
                ]

                for selector in search_selectors:
                    try:
                        search_input = await page.query_selector(selector)
                        if search_input and await search_input.is_visible():
                            logger.info(f"Found search input on page: {selector}")
                            await search_input.click()
                            await browser.human_delay(200, 400)
                            await search_input.fill(query)
                            await browser.human_delay(1500, 2500)

                            # Ждём и проверяем результаты в таблице или autocomplete
                            # Пробуем нажать Enter для поиска
                            await page.keyboard.press('Enter')
                            await browser.human_delay(2000, 3000)

                            # Возвращаем пустой список, данные будут в fallback через get_warehouse_stocks
                            return []
                    except Exception as e:
                        logger.debug(f"Selector {selector} failed: {e}")
                        continue

                logger.error("No search input found on page")
                return []

            await redistribute_btn.click()
            await browser.human_delay(1000, 1500)

            # Ищем поле ввода артикула в модальном окне
            input_selectors = [
                'input[placeholder*="артикул" i]',
                'input[placeholder*="Артикул"]',
                'input[placeholder*="nmId"]',
                '[class*="modal"] input',
                '[role="dialog"] input',
                'input[type="text"]',
            ]

            input_field = None
            for selector in input_selectors:
                try:
                    input_field = await page.query_selector(selector)
                    if input_field:
                        # Проверяем что поле видимо
                        is_visible = await input_field.is_visible()
                        if is_visible:
                            logger.info(f"Found input field: {selector}")
                            break
                        input_field = None
                except:
                    continue

            if not input_field:
                logger.error("Article input field not found in modal")
                return []

            # Вводим запрос
            await input_field.click()
            await browser.human_delay(200, 400)
            await input_field.fill(query)
            await browser.human_delay(1500, 2500)  # Ждем autocomplete

            # Парсим результаты autocomplete
            results = []
            suggestion_selectors = [
                '[class*="option"]',
                '[class*="suggestion"]',
                '[class*="autocomplete"] li',
                '[role="option"]',
                '[class*="dropdown"] [class*="item"]',
                '[class*="listbox"] > div',
            ]

            for selector in suggestion_selectors:
                try:
                    suggestions = await page.query_selector_all(selector)
                    if suggestions:
                        logger.info(f"Found {len(suggestions)} suggestions with {selector}")
                        for suggestion in suggestions:
                            try:
                                text = await suggestion.inner_text()
                                text = text.strip()
                                if text and text != query:
                                    # Пробуем извлечь nmId
                                    parts = text.split()
                                    if parts:
                                        try:
                                            nm_id = int(parts[0])
                                            name = ' '.join(parts[1:]) if len(parts) > 1 else ''
                                            results.append({
                                                'nmId': nm_id,
                                                'name': name,
                                                'text': text
                                            })
                                        except ValueError:
                                            # Первая часть не число
                                            results.append({
                                                'text': text
                                            })
                            except:
                                continue
                        break
                except:
                    continue

            logger.info(f"Search returned {len(results)} results for '{query}'")
            return results

        except PlaywrightTimeout as e:
            logger.error(f"Timeout during product search: {e}")
            return []

        except Exception as e:
            logger.error(f"Error searching product: {e}", exc_info=True)
            return []

        finally:
            if context:
                await context.close()
            if browser:
                await browser.stop()

    async def get_warehouse_stocks(
        self,
        cookies_encrypted: str
    ) -> list:
        """
        Получить все остатки из таблицы на странице warehouse-remains.

        Args:
            cookies_encrypted: Зашифрованные cookies

        Returns:
            Список товаров с остатками
        """
        browser = await self._get_browser()
        context: Optional[BrowserContext] = None
        page: Optional[Page] = None

        try:
            cookies_json = decrypt_token(cookies_encrypted)
            cookies = browser.deserialize_cookies(cookies_json)

            context = await browser.create_context(cookies=cookies)
            page = await browser.create_page(context)

            # Перехватываем API ответы
            captured_data = []

            async def capture_response(response):
                url = response.url
                # Перехватываем все JSON API от wildberries
                if response.status == 200 and 'wildberries' in url:
                    content_type = response.headers.get('content-type', '')
                    if 'json' in content_type or '/api/' in url or '/ns/' in url:
                        try:
                            data = await response.json()
                            captured_data.append({'url': url, 'data': data})
                            # Логируем больше информации
                            data_info = f"list[{len(data)}]" if isinstance(data, list) else f"dict keys: {list(data.keys())[:5]}" if isinstance(data, dict) else type(data).__name__
                            logger.info(f"📡 Captured API: {url[:100]} -> {data_info}")
                        except Exception as e:
                            pass  # Не все ответы JSON

            page.on('response', capture_response)

            # Открываем страницу с увеличенным timeout
            logger.info(f"Opening {self.STOCKS_URL}")
            await page.goto(self.STOCKS_URL, wait_until='domcontentloaded', timeout=60000)

            # Проверяем URL после загрузки
            current_url = page.url
            logger.info(f"Current URL after navigation: {current_url}")

            # Проверяем редирект на логин
            if '/login' in current_url or '/auth' in current_url or 'passport' in current_url:
                logger.error(f"Session expired - redirected to login: {current_url}")
                return []

            # Ждём загрузки данных - даём больше времени
            await browser.human_delay(3000, 4000)

            # Пробуем прокрутить страницу чтобы триггернуть загрузку данных
            try:
                await page.evaluate('window.scrollTo(0, 500)')
                await browser.human_delay(2000, 3000)
            except:
                pass

            # Ещё немного ждём
            await browser.human_delay(3000, 4000)

            logger.info(f"After delay, captured {len(captured_data)} APIs")

            # Логируем что перехватили
            logger.info(f"Total captured APIs: {len(captured_data)}")
            for item in captured_data:
                logger.info(f"API URL: {item['url'][:120]}")
                data = item['data']
                if isinstance(data, dict):
                    logger.info(f"  Keys: {list(data.keys())[:10]}")
                elif isinstance(data, list):
                    logger.info(f"  List with {len(data)} items")
                    if data and isinstance(data[0], dict):
                        logger.info(f"  First item keys: {list(data[0].keys())[:10]}")

            # Пробуем извлечь данные из API ответов
            # Приоритет: balances > remains > stocks
            for item in captured_data:
                url = item['url'].lower()
                data = item['data']

                # Пропускаем если это не данные об остатках
                if 'balances' not in url and 'remains' not in url and 'stocks' not in url:
                    continue

                logger.info(f"🔍 Checking balances/remains/stocks URL: {url[:80]}")

                if isinstance(data, list) and len(data) > 0:
                    logger.info(f"✅ Found stock data in list from {url[:60]}")
                    return data
                elif isinstance(data, dict):
                    for key in ['data', 'items', 'result', 'rows', 'content', 'report', 'balances']:
                        if key in data:
                            val = data[key]
                            if isinstance(val, list):
                                logger.info(f"  Key '{key}' contains list with {len(val)} items")
                                if len(val) > 0:
                                    logger.info(f"✅ Found stock data in '{key}' from {url[:60]}")
                                    return val
                            elif isinstance(val, dict):
                                logger.info(f"  Key '{key}' contains dict with keys: {list(val.keys())[:5]}")

            # Fallback: любые данные с nmId
            for item in captured_data:
                data = item['data']
                if isinstance(data, list) and len(data) > 0:
                    if isinstance(data[0], dict) and ('nmId' in data[0] or 'nm_id' in data[0] or 'nmID' in data[0]):
                        logger.info(f"✅ Found nmId data from {item['url'][:60]}")
                        return data

            # Если API не перехватили - парсим таблицу
            logger.info("No stock data in captured APIs, parsing table directly...")
            return await self._parse_stocks_table(page)

        except Exception as e:
            logger.error(f"Error getting warehouse stocks: {e}")
            return []

        finally:
            if context:
                await context.close()
            # Останавливаем браузер, чтобы избежать утечки ресурсов
            if browser:
                await browser.stop()

    async def _parse_stocks_table(self, page: Page) -> list:
        """Парсит таблицу остатков со страницы"""
        results = []

        try:
            # Сохраняем скриншот для отладки
            screenshot_path = "/tmp/wb_table_debug.png"
            await page.screenshot(path=screenshot_path)
            logger.info(f"Table page screenshot saved to {screenshot_path}")

            # Ждем таблицу с увеличенным таймаутом
            await page.wait_for_selector('table', timeout=30000)

            # Получаем заголовки
            headers = []
            header_cells = await page.query_selector_all('table thead th')
            for cell in header_cells:
                text = await cell.inner_text()
                headers.append(text.strip().lower())

            logger.info(f"Table headers: {headers}")

            # Получаем строки
            rows = await page.query_selector_all('table tbody tr')
            logger.info(f"Found {len(rows)} rows")

            for row in rows:
                try:
                    cells = await row.query_selector_all('td')
                    if len(cells) >= 3:
                        item = {}
                        for i, cell in enumerate(cells):
                            text = await cell.inner_text()
                            text = text.strip()

                            if i < len(headers):
                                header = headers[i]
                                if 'бренд' in header:
                                    item['brand'] = text
                                elif 'предмет' in header:
                                    item['subject'] = text
                                elif 'артикул wb' in header or 'nmid' in header.lower():
                                    try:
                                        item['nmId'] = int(text)
                                    except:
                                        pass
                                elif 'объем' in header or 'объём' in header:
                                    try:
                                        item['volume'] = float(text.replace(',', '.'))
                                    except:
                                        pass
                                elif 'всего' in header and 'склад' in header:
                                    try:
                                        item['totalQuantity'] = int(text)
                                    except:
                                        pass

                        if item.get('nmId'):
                            results.append(item)

                except Exception as e:
                    logger.debug(f"Error parsing row: {e}")
                    continue

            logger.info(f"Parsed {len(results)} items from table")

        except Exception as e:
            logger.error(f"Error parsing stocks table: {e}")

        return results


# Singleton instance
_redistribution_service: Optional[WBRedistributionService] = None


def get_redistribution_service() -> WBRedistributionService:
    """Получить singleton instance WBRedistributionService"""
    global _redistribution_service
    if _redistribution_service is None:
        _redistribution_service = WBRedistributionService()
    return _redistribution_service
