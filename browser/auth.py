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
    CAPTCHA_REQUIRED = "captcha"         # Требуется ввод captcha
    SUCCESS = "success"                  # Успешная авторизация
    FAILED = "failed"                    # Ошибка авторизации
    BLOCKED = "blocked"                  # Аккаунт заблокирован
    INVALID_CODE = "invalid_code"        # Неверный код
    CODE_EXPIRED = "code_expired"        # Код истёк
    TOO_MANY_ATTEMPTS = "too_many"       # Слишком много попыток
    WAITING_NEW_CODE = "waiting_new_code"  # Ожидание возможности запросить новый код
    NEW_CODE_SENT = "new_code_sent"      # Новый код запрошен


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
    captcha_screenshot: Optional[bytes] = None  # Скриншот captcha для отправки пользователю
    keep_alive_task: Optional[asyncio.Task] = None  # Background task для поддержания сессии


class WBAuthService:
    """Сервис авторизации в ЛК Wildberries"""

    WB_SELLER_URL = "https://seller.wildberries.ru"
    # Порядок оптимизирован: начинаем с самого вероятного URL
    WB_LOGIN_URLS = [
        "https://seller-auth.wildberries.ru/",  # Основной URL авторизации (первый)
        "https://seller.wildberries.ru/login",  # Альтернативный
        "https://seller.wildberries.ru"          # Fallback
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
            'button:has-text("Получить код")',
            'button:has-text("Войти")',
            'button:has-text("Далее")',
            'button:has-text("Продолжить")',
            'button[type="submit"]',
            'button[class*="submit"]',
            'button[class*="Submit"]',
            '[data-testid*="submit"]',
            # НЕ используем 'form button' - слишком широко, может найти кнопку dropdown
        ]),
        # Селекторы для поля кода
        # WB может использовать:
        # 1. Одно поле с maxlength=6
        # 2. 6 отдельных полей с maxlength=1
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
        # Селекторы для отдельных полей кода (6 штук по 1 цифре)
        'code_digit_inputs': ', '.join([
            'input.InputCell-PB5beCCt55',  # WB использует этот класс
            'input[class*="InputCell"]',   # Вариация класса
            'input[maxlength="1"][type="tel"]',
            'input[maxlength="1"][type="text"]',
            'input[maxlength="1"][inputmode="numeric"]',
            '[class*="code"] input[maxlength="1"]',
            '[class*="sms"] input[maxlength="1"]',
            '[class*="otp"] input',
            '[class*="verification"] input[maxlength="1"]',
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
        self._code_digit_inputs: Optional[list] = None  # 6 отдельных полей для цифр кода

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

    def has_session(self, user_id: int) -> bool:
        """Проверить, есть ли активная сессия для пользователя"""
        session = self._sessions.get(user_id)
        if session is None:
            return False
        # Сессия активна в нескольких состояниях
        active_statuses = {
            AuthStatus.PENDING_CODE,
            AuthStatus.WAITING_NEW_CODE,
            AuthStatus.NEW_CODE_SENT,
        }
        return session.status in active_statuses

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

            # Создаём сессию с увеличенным timeout (5 минут вместо 30 секунд)
            browser = await self._get_browser()
            context = await browser.create_context()

            # Устанавливаем долгий timeout для страницы (5 минут)
            # Это нужно, чтобы сессия не истекала пока пользователь вводит SMS код
            page = await browser.create_page(context)
            page.set_default_timeout(300000)  # 5 минут в миллисекундах

            # Перехватываем console errors для диагностики
            console_errors = []

            def handle_console(msg):
                """Обработчик console messages"""
                if msg.type in ['error', 'warning']:
                    error_text = f"[{msg.type.upper()}] {msg.text}"
                    console_errors.append(error_text)
                    logger.warning(f"Browser console {msg.type}: {msg.text}")

            page.on('console', handle_console)

            # Инжектируем скрипт для сохранения console errors
            await page.add_init_script('''
                window.__console_errors__ = [];
                const originalError = console.error;
                console.error = function(...args) {
                    window.__console_errors__.push(args.join(' '));
                    originalError.apply(console, args);
                };
            ''')

            session = AuthSession(
                user_id=user_id,
                phone=normalized_phone,
                status=AuthStatus.PENDING_PHONE,
                context=context,
                page=page
            )
            self._sessions[user_id] = session

            # Запускаем keep-alive task для поддержания сессии
            # Будет пинговать страницу каждые 30 секунд, пока ждём SMS код
            keep_alive_task = asyncio.create_task(self._keep_session_alive(user_id, page))
            # Сохраняем task в сессию для последующей отмены
            session.keep_alive_task = keep_alive_task

            # Пробуем разные URL для авторизации (WB может менять структуру)
            phone_input = None

            for login_url in self.WB_LOGIN_URLS:
                logger.info(f"Пробуем URL: {login_url}")
                try:
                    await page.goto(login_url, wait_until='domcontentloaded', timeout=30000)
                    await browser.human_delay(1000, 1500)  # Ускорено: 2000-3000ms → 1000-1500ms

                    # Логируем текущий URL (возможен редирект)
                    current_url = page.url
                    logger.info(f"Текущий URL после загрузки: {current_url}")

                    # Минимальная имитация поведения (одно движение мыши)
                    await browser.random_mouse_movements(page, count=1)  # Сокращено: 3 → 1
                    await browser.human_delay(300, 500)  # Ускорено: 500-1000ms → 300-500ms

                    # Проверяем наличие captcha сразу после загрузки
                    if await self._detect_captcha(page):
                        logger.warning("Captcha обнаружена сразу после загрузки!")
                        screenshot = await browser.take_screenshot(page)
                        session.status = AuthStatus.CAPTCHA_REQUIRED
                        session.captcha_screenshot = screenshot
                        session.error_message = "Wildberries показал капчу. Попробуйте позже или с другого IP."
                        return session

                    # Короткая пауза для загрузки антибот-скриптов
                    await browser.human_delay(500, 800)  # Ускорено: 1000-2000ms → 500-800ms

                    # Проверяем и принимаем cookie баннер если появился
                    await self._accept_cookie_banner(page)

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

            # Вводим номер телефона (без +7, т.к. на странице WB уже есть префикс +7)
            phone_digits = normalized_phone.replace('+7', '').replace('+', '')
            logger.info(f"Будем вводить: {phone_digits[:3]}*** ({len(phone_digits)} цифр)")

            # Прокручиваем к полю ввода чтобы оно было видимо
            try:
                await phone_input.scroll_into_view_if_needed()
                await browser.human_delay(200, 300)  # Ускорено: 300-500ms → 200-300ms
            except Exception as e:
                logger.debug(f"scroll_into_view_if_needed не удался: {e}")

            # Убрано избыточное движение мыши для ускорения

            # Проверяем начальное значение поля
            initial_value = await phone_input.input_value()
            logger.info(f"Начальное значение в поле: '{initial_value}'")

            # WB имеет сложный компонент: слева флаг+dropdown, справа поле ввода
            # Нужно кликнуть СПРАВА (в область цифр), не слева (где флаг)

            # Получаем координаты поля
            box = await phone_input.bounding_box()
            if box:
                # Кликаем в ПРАВУЮ часть поля (70% от левого края) - там область ввода цифр
                click_x = box['x'] + box['width'] * 0.7
                click_y = box['y'] + box['height'] / 2
                logger.info(f"Поле: x={box['x']}, width={box['width']}, кликаем в x={click_x}")

                # Сначала кликаем вне поля чтобы закрыть возможный dropdown
                await page.mouse.click(box['x'] + box['width'] + 50, box['y'])
                await browser.human_delay(200, 300)

                # Теперь кликаем в правую часть поля (область ввода цифр)
                await page.mouse.click(click_x, click_y)
                await browser.human_delay(300, 500)

                # Проверяем, открылся ли dropdown (есть ли список стран)
                dropdown_visible = await page.query_selector('[class*="dropdown"]:visible, [class*="select-list"]:visible, [role="listbox"]:visible')
                if dropdown_visible:
                    logger.info("Dropdown открылся, закрываем через Escape")
                    await page.keyboard.press('Escape')
                    await browser.human_delay(200, 300)
                    # Кликаем снова в правую часть
                    await page.mouse.click(click_x, click_y)
                    await browser.human_delay(200, 300)

                # Очищаем поле перед вводом (на случай если там что-то было)
                await page.keyboard.press('Control+a')
                await browser.human_delay(50, 100)
                await page.keyboard.press('Backspace')
                await browser.human_delay(100, 200)

                # Вводим номер (оптимизированная скорость)
                logger.info("Начинаем ввод номера...")
                for i, digit in enumerate(phone_digits):
                    await page.keyboard.type(digit, delay=60)  # Ускорено: 120ms → 60ms
                    await browser.human_delay(30, 60)  # Ускорено: 50-100ms → 30-60ms
                    # Каждые 3 цифры делаем короткую паузу
                    if (i + 1) % 3 == 0:
                        await browser.human_delay(80, 120)  # Ускорено: 150-250ms → 80-120ms

                logger.info("Номер введён")

                # Ждём пока WB отформатирует номер
                await browser.human_delay(300, 500)  # Ускорено: 500-800ms → 300-500ms
            else:
                # Fallback если не получили координаты
                logger.warning("Не удалось получить координаты поля, пробуем fill()")
                await phone_input.fill(phone_digits)

            await browser.human_delay(300, 500)  # Ускорено: 500-1000ms → 300-500ms

            # Проверяем что номер введён (диагностика)
            input_value = await phone_input.input_value()
            logger.info(f"Значение в поле после ввода: '{input_value}'")

            # ВАЖНО: НЕ нажимаем Escape здесь - это может очистить поле!
            # Вместо этого просто кликаем вне поля чтобы убрать фокус с input
            box = await phone_input.bounding_box()
            if box:
                # Кликаем ниже поля (вне dropdown области)
                await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] + 30)
                await browser.human_delay(200, 300)

            # Перепроверяем значение после клика вне поля
            input_value_after = await phone_input.input_value()
            logger.info(f"Значение в поле после клика вне: '{input_value_after}'")

            if not input_value_after or len(input_value_after.replace(' ', '').replace('-', '')) < 10:
                logger.error(f"Номер был очищен или не полный! Было: '{input_value}', стало: '{input_value_after}'")
                session.status = AuthStatus.FAILED
                session.error_message = "Номер телефона не сохранился в поле. Попробуйте ещё раз."
                return session

            # Пробуем отправить форму несколькими способами
            submitted = False

            # Сначала закрываем любые открытые dropdown'ы
            await page.keyboard.press('Escape')
            await browser.human_delay(200, 300)

            # Способ 1: JavaScript click по кнопке (надёжнее чем mouse click)
            submit_button = await self._find_submit_button(page)
            if submit_button:
                button_text = await submit_button.inner_text()
                logger.info(f"Найдена кнопка для отправки: '{button_text}'")

                # Используем JavaScript click - он обходит проблемы с перекрытием элементов
                try:
                    await submit_button.evaluate('el => el.click()')
                    logger.info("Выполнен JavaScript click по кнопке")
                    await browser.human_delay(2000, 3000)  # Ускорено: 3000-4000ms → 2000-3000ms

                    code_input = await self._find_code_input(page)
                    if code_input:
                        submitted = True
                        logger.info("Форма отправлена через JS click")
                except Exception as e:
                    logger.warning(f"JS click не сработал: {e}")

            # Способ 2: Координатный клик по кнопке
            if not submitted and submit_button:
                logger.info("JS click не сработал, пробуем координатный клик")
                button_box = await submit_button.bounding_box()
                if button_box:
                    btn_x = button_box['x'] + button_box['width'] / 2
                    btn_y = button_box['y'] + button_box['height'] / 2
                    logger.info(f"Кликаем кнопку по координатам: x={btn_x}, y={btn_y}")

                    # Сначала убедимся что dropdown закрыт
                    await page.keyboard.press('Escape')
                    await browser.human_delay(100, 200)

                    await page.mouse.click(btn_x, btn_y)
                    await browser.human_delay(2000, 3000)  # Ускорено: 3000-4000ms → 2000-3000ms

                    code_input = await self._find_code_input(page)
                    if code_input:
                        submitted = True
                        logger.info("Форма отправлена через координатный клик")

            # Способ 3: Enter в поле ввода
            if not submitted:
                logger.info("Клик по кнопке не сработал, пробуем Enter")
                # Кликаем в поле ввода чтобы дать ему фокус
                if box:
                    await page.mouse.click(box['x'] + box['width'] * 0.7, box['y'] + box['height'] / 2)
                    await browser.human_delay(200, 300)

                await page.keyboard.press('Enter')
                await browser.human_delay(2000, 3000)  # Ускорено: 3000-4000ms → 2000-3000ms

                code_input = await self._find_code_input(page)
                if code_input:
                    submitted = True
                    logger.info("Форма отправлена через Enter")

            # Способ 4: JavaScript submit формы
            if not submitted:
                logger.info("Пробуем JavaScript submit формы")
                try:
                    await page.evaluate('''
                        const form = document.querySelector('form');
                        if (form) {
                            form.submit();
                            return true;
                        }
                        return false;
                    ''')
                    await browser.human_delay(2000, 3000)  # Ускорено: 3000-4000ms → 2000-3000ms

                    code_input = await self._find_code_input(page)
                    if code_input:
                        submitted = True
                        logger.info("Форма отправлена через JS form.submit()")
                except Exception as e:
                    logger.warning(f"JS form submit не сработал: {e}")

            # ДИАГНОСТИКА: Проверяем состояние страницы после submit
            body_text = await page.inner_text('body')
            logger.info(f"=== ДИАГНОСТИКА ПОСЛЕ SUBMIT ===")
            logger.info(f"URL: {page.url}")
            logger.info(f"Текст страницы (первые 400 символов): {body_text[:400]}")

            # Проверяем console errors
            console_errors = []
            try:
                # Получаем console logs через evaluate
                console_logs = await page.evaluate('''() => {
                    return window.__console_errors__ || [];
                }''')
                if console_logs:
                    logger.warning(f"Console errors: {console_logs}")
                    console_errors = console_logs
            except Exception as e:
                logger.debug(f"Не удалось получить console errors: {e}")

            # Проверяем наличие сообщения об ошибке или предупреждения (скрытые элементы)
            try:
                error_elements = await page.query_selector_all('[class*="error"], [class*="warning"], [class*="alert"]')
                for elem in error_elements:
                    is_visible = await elem.is_visible()
                    text = await elem.inner_text() if is_visible else await elem.text_content()
                    if text and text.strip():
                        logger.warning(f"Найден элемент с ошибкой/предупреждением (visible={is_visible}): {text.strip()[:200]}")
            except Exception as e:
                logger.debug(f"Не удалось проверить error elements: {e}")

            # Проверяем, есть ли конкретное сообщение об отправке SMS
            sms_sent_indicators = [
                'код отправлен',
                'смс отправлен',
                'sms sent',
                'введите код',
                'запросить заново',
            ]
            sms_sent = any(indicator in body_text.lower() for indicator in sms_sent_indicators)
            logger.info(f"Индикаторы отправки SMS на странице: {sms_sent}")

            # Проверяем наличие таймера "Запросить заново через X секунд"
            import re
            timer_match = re.search(r'запросить.*?через\s+(\d+)\s*(секунд|минут)', body_text, re.IGNORECASE)
            if timer_match:
                time_value = timer_match.group(1)
                time_unit = timer_match.group(2)
                logger.info(f"✅ Найден таймер повторной отправки: через {time_value} {time_unit}")
                logger.info("Это указывает, что WB действительно инициировал отправку SMS")
            else:
                logger.warning("⚠️ НЕ НАЙДЕН таймер 'Запросить заново через X секунд'")
                logger.warning("Возможно, WB не отправил SMS, а только показал форму!")

            if not sms_sent and not timer_match:
                logger.error("❌ КРИТИЧНО: Нет никаких признаков отправки SMS!")
                logger.error("Полный текст страницы для анализа:")
                logger.error(body_text[:1000])  # Первая 1000 символов

            # Проверяем, есть ли поле телефона и что в нём
            phone_field_after = await self._find_phone_input(page)
            if phone_field_after:
                value_after_submit = await phone_field_after.input_value()
                logger.info(f"Значение в поле телефона ПОСЛЕ SUBMIT: '{value_after_submit}'")
                if not value_after_submit or len(value_after_submit.replace(' ', '').replace('-', '')) < 10:
                    logger.error("!!! ПРОБЛЕМА: Номер был очищен после клика на кнопку!")

            # Сохраняем скриншот в файл для визуальной диагностики
            try:
                screenshot = await browser.take_screenshot(page)
                if screenshot:
                    import os
                    screenshot_path = "/tmp/wb_auth_debug.png"
                    with open(screenshot_path, "wb") as f:
                        f.write(screenshot)
                    logger.info(f"Скриншот сохранён: {screenshot_path}")
            except Exception as e:
                logger.warning(f"Не удалось сохранить скриншот: {e}")

            # Проверяем ошибки на странице
            error = await self._check_error(page)
            if error:
                session.status = AuthStatus.FAILED
                session.error_message = error
                logger.error(f"Ошибка авторизации: {error}")
                return session

            # Проверяем появление поля для кода (оптимизированное ожидание)
            # WB может долго обрабатывать запрос
            code_input = None
            max_attempts = 3  # Уменьшено: 5 → 3
            for attempt in range(max_attempts):
                logger.info(f"Ищем поле кода, попытка {attempt + 1}/{max_attempts}...")
                code_input = await self._find_code_input(page)
                if code_input:
                    break
                # Ждём между попытками (ускорено)
                await browser.human_delay(1000, 1500)  # Ускорено: 2000-3000ms → 1000-1500ms

            if code_input:
                session.status = AuthStatus.PENDING_CODE
                logger.info(f"SMS отправлено на {normalized_phone[:5]}***")
            else:
                # Последняя попытка - ждём ещё немного
                logger.info("Поле кода не найдено после всех попыток, ждём ещё 2 секунды...")
                await browser.human_delay(2000, 3000)  # Ускорено: 5000-6000ms → 2000-3000ms
                code_input = await self._find_code_input(page)

                if code_input:
                    session.status = AuthStatus.PENDING_CODE
                    logger.info(f"SMS отправлено на {normalized_phone[:5]}*** (после дополнительного ожидания)")
                else:
                    # Проверяем, не появилась ли captcha после отправки формы
                    if await self._detect_captcha(page):
                        logger.warning("Captcha появилась после отправки формы!")
                        screenshot = await browser.take_screenshot(page)
                        session.status = AuthStatus.CAPTCHA_REQUIRED
                        session.captcha_screenshot = screenshot
                        session.error_message = "Wildberries требует ввод капчи. Попробуйте позже или с другого IP."
                    else:
                        session.status = AuthStatus.FAILED
                        session.error_message = "Не появилось поле для ввода кода. Возможно, WB показал ошибку или форма не отправилась."
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

        # Можно отправлять код если ждём первый код или после запроса нового
        if session.status not in {AuthStatus.PENDING_CODE, AuthStatus.NEW_CODE_SENT}:
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
            # Сначала проверяем и принимаем cookie баннер если он есть
            await self._accept_cookie_banner(page)

            # Находим поля для кода
            code_input = await self._find_code_input(page)
            if not code_input:
                session.status = AuthStatus.FAILED
                session.error_message = "Поле ввода кода не найдено"
                return session

            # Проверяем, есть ли 6 отдельных полей для цифр (новый UI WB)
            digit_inputs = getattr(self, '_code_digit_inputs', None)

            if digit_inputs and len(digit_inputs) >= len(code):
                # Новый UI: вводим каждую цифру в отдельное поле
                logger.info(f"Ввод кода в {len(digit_inputs)} отдельных полей")

                for i, digit in enumerate(code):
                    if i < len(digit_inputs):
                        inp = digit_inputs[i]
                        try:
                            # Кликаем в поле
                            await inp.click()
                            await browser.human_delay(50, 100)
                            # Очищаем если что-то есть
                            await inp.fill('')
                            await browser.human_delay(30, 60)
                            # Вводим цифру
                            await inp.type(digit, delay=80)
                            await browser.human_delay(100, 200)
                            logger.debug(f"Введена цифра {i+1}: {digit}")
                        except Exception as e:
                            logger.warning(f"Ошибка ввода цифры {i+1}: {e}")
                            # Пробуем альтернативный способ - через keyboard
                            await page.keyboard.type(digit, delay=100)
                            await browser.human_delay(100, 200)

                logger.info("Все цифры кода введены")
            else:
                # Старый UI: одно поле для всего кода
                logger.info("Ввод кода в одно поле (классический UI)")

                # Очищаем поле и вводим код
                await code_input.fill('')
                await browser.human_delay(300, 500)

                for digit in code:
                    await page.keyboard.type(digit, delay=100)
                    await browser.human_delay(50, 150)

            await browser.human_delay(1000, 2000)

            # WB может автоматически отправить форму после ввода 6 цифр
            # Проверяем, не изменился ли уже URL или контент
            await browser.human_delay(500, 1000)

            # Если ещё на той же странице - пробуем нажать кнопку
            current_url = page.url
            if '/login' in current_url or 'auth' in current_url.lower():
                submit_button = await self._find_submit_button(page)
                if submit_button:
                    try:
                        await submit_button.click()
                        logger.info("Нажата кнопка подтверждения")
                    except Exception as e:
                        logger.debug(f"Не удалось нажать кнопку: {e}")

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

    async def request_new_code(self, user_id: int, max_wait_seconds: int = 70) -> AuthSession:
        """
        Дождаться появления кнопки "Запросить код" и нажать на неё.

        WB показывает таймер ~1 минута после неверного кода,
        затем появляется кнопка для запроса нового.

        Args:
            user_id: Telegram user ID
            max_wait_seconds: Максимальное время ожидания

        Returns:
            AuthSession с обновлённым статусом
        """
        session = self._sessions.get(user_id)
        if not session:
            raise ValueError(f"Сессия не найдена для user {user_id}")

        browser = await self._get_browser()
        page = session.page

        # Селекторы для кнопки запроса нового кода
        new_code_selectors = [
            'button:has-text("Запросить код")',
            'button:has-text("запросить код")',
            'button:has-text("Запросить заново")',
            'button:has-text("Отправить код")',
            'button:has-text("Получить код")',
            'a:has-text("Запросить код")',
            'span:has-text("Запросить код")',
            '[class*="resend"]:not([disabled])',
            '[class*="retry"]:not([disabled])',
        ]

        logger.info(f"[REQUEST_NEW_CODE] Ожидание кнопки запроса нового кода (до {max_wait_seconds} сек)...")
        session.status = AuthStatus.WAITING_NEW_CODE

        start_time = asyncio.get_event_loop().time()
        button_found = None

        while (asyncio.get_event_loop().time() - start_time) < max_wait_seconds:
            # Проверяем каждый селектор
            for selector in new_code_selectors:
                try:
                    button = await page.query_selector(selector)
                    if button and await button.is_visible():
                        # Проверяем что кнопка не disabled
                        is_disabled = await button.get_attribute('disabled')
                        if not is_disabled:
                            button_text = await button.inner_text()
                            logger.info(f"[REQUEST_NEW_CODE] Найдена кнопка: '{button_text}'")
                            button_found = button
                            break
                except Exception as e:
                    logger.debug(f"Ошибка при поиске кнопки {selector}: {e}")
                    continue

            if button_found:
                break

            # Также проверяем по тексту на странице
            try:
                body_text = await page.inner_text('body')
                # Если таймер ещё идёт, продолжаем ждать
                if 'запросить заново через' in body_text.lower():
                    # Извлекаем оставшееся время для логирования
                    import re
                    match = re.search(r'через\s+(\d+)\s*(?:сек|секунд)', body_text, re.IGNORECASE)
                    if match:
                        remaining = match.group(1)
                        logger.debug(f"[REQUEST_NEW_CODE] Таймер: осталось {remaining} сек")
            except Exception:
                pass

            # Ждём 2 секунды перед следующей проверкой
            await asyncio.sleep(2)

        if not button_found:
            logger.warning("[REQUEST_NEW_CODE] Кнопка запроса нового кода не появилась")
            session.status = AuthStatus.FAILED
            session.error_message = "Не удалось дождаться кнопки запроса нового кода"
            return session

        # Нажимаем кнопку
        try:
            await button_found.click()
            logger.info("[REQUEST_NEW_CODE] Кнопка нажата")
            await browser.human_delay(2000, 3000)

            # Проверяем, появилось ли поле для ввода нового кода
            code_input = await self._find_code_input(page)
            if code_input:
                session.status = AuthStatus.NEW_CODE_SENT
                logger.info("[REQUEST_NEW_CODE] Новый код запрошен успешно")
            else:
                # Проверяем ошибки
                error = await self._check_error(page)
                if error:
                    session.status = AuthStatus.FAILED
                    session.error_message = error
                else:
                    # Возможно код уже отправлен, но поле не обнаружено
                    session.status = AuthStatus.NEW_CODE_SENT
                    logger.info("[REQUEST_NEW_CODE] Код запрошен (поле не найдено, но ошибок нет)")

        except Exception as e:
            logger.error(f"[REQUEST_NEW_CODE] Ошибка при нажатии кнопки: {e}")
            session.status = AuthStatus.FAILED
            session.error_message = f"Ошибка при запросе нового кода: {e}"

        return session

    async def _find_phone_input(self, page: Page) -> Optional[Any]:
        """
        Найти поле ввода телефона (цифры номера, НЕ dropdown выбора страны).

        WB имеет сложный компонент: dropdown выбора страны (+7, +374, ...)
        и отдельное поле для ввода цифр номера.
        """
        # Ждём загрузки страницы
        await page.wait_for_load_state('domcontentloaded')

        # Стратегия 1: Ищем поле по placeholder с цифрами/форматом номера
        placeholder_selectors = [
            'input[placeholder*="000"]',           # Placeholder "000 000 0000" или подобный
            'input[placeholder*="___"]',           # Placeholder с подчёркиваниями
            'input[placeholder*="9"]',             # Placeholder начинающийся с 9
            'input[placeholder*="номер" i]',       # "Введите номер"
            'input[placeholder*="phone" i]',       # "phone"
        ]

        # Оптимизация: пробуем все селекторы параллельно
        try:
            elements = await asyncio.gather(
                *[page.query_selector(selector) for selector in placeholder_selectors],
                return_exceptions=True
            )

            for selector, element in zip(placeholder_selectors, elements):
                if isinstance(element, Exception) or not element:
                    continue
                try:
                    if await element.is_visible():
                        placeholder = await element.get_attribute('placeholder') or ''
                        logger.info(f"Найдено поле телефона по placeholder: '{placeholder}' через {selector}")
                        return element
                except Exception as e:
                    logger.debug(f"Селектор {selector} не сработал: {e}")
        except Exception as e:
            logger.debug(f"Стратегия 1 не сработала: {e}")

        # Стратегия 2: Ищем все input[type="tel"] и берём последний (обычно это поле цифр)
        try:
            tel_inputs = await page.query_selector_all('input[type="tel"]')
            logger.info(f"Найдено {len(tel_inputs)} элементов input[type='tel']")

            # Оптимизация: собираем атрибуты всех tel inputs параллельно
            if tel_inputs:
                all_attrs = await asyncio.gather(
                    *[asyncio.gather(
                        inp.get_attribute('placeholder'),
                        inp.get_attribute('name'),
                        inp.get_attribute('class'),
                        return_exceptions=True
                    ) for inp in tel_inputs],
                    return_exceptions=True
                )

                for i, attrs in enumerate(all_attrs):
                    if isinstance(attrs, Exception):
                        continue
                    placeholder, name, class_attr = attrs
                    placeholder = '' if isinstance(placeholder, Exception) else (placeholder or '')
                    name = '' if isinstance(name, Exception) else (name or '')
                    class_attr = '' if isinstance(class_attr, Exception) else (class_attr or '')
                    logger.info(f"  tel input [{i}]: placeholder='{placeholder}', name='{name}', class='{class_attr[:50]}'")

                # Если есть несколько tel inputs, берём последний (обычно это поле для цифр)
                # Первый часто является частью dropdown выбора страны
                if len(tel_inputs) >= 2:
                    element = tel_inputs[-1]  # Последний input
                    if await element.is_visible():
                        logger.info("Используем последний input[type='tel'] (поле для цифр)")
                        return element
                elif len(tel_inputs) == 1:
                    # Только один tel input - используем его, но сначала кликаем мимо dropdown
                    element = tel_inputs[0]
                    if await element.is_visible():
                        logger.info("Найден единственный input[type='tel']")
                        return element
        except Exception as e:
            logger.warning(f"Ошибка при поиске input[type='tel']: {e}")

        # Стратегия 3: Ищем input рядом с текстом "+7" или в форме
        try:
            # Ищем форму авторизации и внутри неё input
            form_selectors = [
                'form input[type="tel"]',
                'form input[type="text"]',
                '[class*="auth"] input',
                '[class*="login"] input',
                '[class*="phone-input"] input',
            ]

            # Оптимизация: собираем все элементы из всех селекторов параллельно
            all_selector_results = await asyncio.gather(
                *[page.query_selector_all(selector) for selector in form_selectors],
                return_exceptions=True
            )

            # Объединяем все элементы
            all_elements = []
            for result in all_selector_results:
                if not isinstance(result, Exception) and result:
                    all_elements.extend(result)

            # Проверяем элементы параллельно
            if all_elements:
                checks = await asyncio.gather(
                    *[asyncio.gather(el.is_visible(), el.bounding_box(), return_exceptions=True)
                      for el in all_elements],
                    return_exceptions=True
                )

                for i, (element, check_results) in enumerate(zip(all_elements, checks)):
                    if isinstance(check_results, Exception):
                        continue
                    is_visible, box = check_results
                    if not isinstance(is_visible, Exception) and is_visible and \
                       not isinstance(box, Exception) and box and box['width'] > 100:
                        logger.info(f"Найдено поле телефона в форме, width={box['width']}")
                        return element
        except Exception as e:
            logger.warning(f"Ошибка при поиске в форме: {e}")

        # Стратегия 4: Последняя попытка - любой видимый input подходящего типа
        try:
            inputs = await page.query_selector_all('input')
            logger.info(f"Найдено {len(inputs)} input элементов всего")

            # Оптимизация: проверяем все inputs параллельно
            if inputs:
                checks = await asyncio.gather(
                    *[asyncio.gather(
                        inp.is_visible(),
                        inp.get_attribute('type'),
                        inp.bounding_box(),
                        return_exceptions=True
                    ) for inp in inputs],
                    return_exceptions=True
                )

                for inp, check_results in zip(inputs, checks):
                    if isinstance(check_results, Exception):
                        continue

                    is_visible, input_type, box = check_results

                    if isinstance(is_visible, Exception) or not is_visible:
                        continue
                    if isinstance(input_type, Exception):
                        input_type = 'text'
                    else:
                        input_type = input_type or 'text'

                    if input_type not in ['tel', 'text', 'number']:
                        continue

                    # Проверяем размер - поле для номера должно быть достаточно широким
                    if not isinstance(box, Exception) and box and box['width'] > 100:
                        logger.info(f"Используем input type={input_type}, width={box['width']} как поле телефона")
                        return inp
        except Exception as e:
            logger.error(f"Ошибка при поиске input: {e}")

        return None

    async def _accept_cookie_banner(self, page: Page) -> bool:
        """
        Обнаруживает и принимает cookie notice от WB.

        WB показывает баннер "Этот сайт использует cookie файлы..."
        с кнопкой "Принимаю", который может перекрывать форму ввода кода.

        Returns:
            True если баннер найден и принят, False если не найден
        """
        try:
            logger.info("🍪 Проверяем наличие cookie consent баннера...")

            # Селекторы для кнопки "Принимаю" на cookie баннере
            cookie_button_selectors = [
                'button:has-text("Принимаю")',
                'button:has-text("Принять")',
                'button:has-text("Accept")',
                '[class*="cookie"] button:has-text("Принимаю")',
                '[class*="consent"] button:has-text("Принимаю")',
                '[data-test*="cookie"] button',
                '[data-test*="consent"] button',
                'button[class*="accept"]',
            ]

            # Проверяем все селекторы параллельно
            button_results = await asyncio.gather(
                *[page.query_selector(selector) for selector in cookie_button_selectors],
                return_exceptions=True
            )

            cookie_button = None
            for result in button_results:
                if not isinstance(result, Exception) and result:
                    cookie_button = result
                    break

            if cookie_button:
                # Проверяем, видима ли кнопка
                is_visible = await cookie_button.is_visible()
                if is_visible:
                    logger.info("✅ Найден cookie consent баннер - принимаю...")
                    await cookie_button.click()
                    await asyncio.sleep(0.5)  # Даём время баннеру исчезнуть
                    logger.info("✅ Cookie consent принят")
                    return True
                else:
                    logger.debug("Cookie кнопка найдена, но не видима")
            else:
                logger.debug("Cookie consent баннер не найден (это нормально)")

            return False

        except Exception as e:
            logger.warning(f"Ошибка при проверке cookie баннера: {e}")
            return False

    async def _find_code_input(self, page: Page) -> Optional[Any]:
        """
        Найти поле ввода SMS кода.

        WB может использовать:
        1. Одно поле с maxlength=6 (старый вариант)
        2. 6 отдельных полей с maxlength=1 (новый вариант)

        Returns:
            Элемент для ввода (первое поле если их 6) или None
        """
        try:
            # Сначала проверяем и принимаем cookie баннер если он есть
            # (он может появиться на любом этапе)
            await self._accept_cookie_banner(page)

            # Проверим текст страницы - есть ли сообщение об отправке кода
            body_text = await page.inner_text('body')
            body_lower = body_text.lower()

            # ВАЖНО: Сначала проверяем наличие текста про КОД!
            # WB - это SPA, и оба текста (про телефон и про код) могут быть в DOM одновременно.
            # Если есть текст про код - значит мы на странице кода, независимо от других текстов.
            has_code_text = any(phrase in body_lower for phrase in [
                'введите код', 'enter code', 'код из sms', 'код подтверждения',
                'отправили код', 'отправлен код', 'мы отправили', 'sms с кодом',
                'запросить заново через'  # Это тоже признак страницы кода
            ])
            logger.info(f"Текст страницы содержит упоминание кода: {has_code_text}")

            if has_code_text:
                # Мы на странице ввода кода - продолжаем поиск полей
                logger.info("Обнаружен текст про код - мы на странице ввода кода")
            else:
                # Нет текста про код - проверяем, может мы всё ещё на странице телефона
                still_on_phone_page = any(phrase in body_lower for phrase in [
                    'введите номер телефона',
                    'enter phone number',
                    'введите номер, чтобы войти',
                ])

                if still_on_phone_page:
                    logger.warning("Мы всё ещё на странице ввода телефона - форма не отправилась!")
                    return None

                logger.warning("На странице нет текста о вводе кода - возможно форма не отправилась")
                logger.info(f"Текст страницы: {body_text[:300]}")
                return None

            # === СТРАТЕГИЯ 1: Ищем 6 отдельных полей для цифр (новый UI WB) ===
            logger.info("Стратегия 1: Ищем 6 отдельных полей для цифр...")

            # Сначала ищем по классу InputCell (WB использует этот класс без maxlength)
            digit_inputs = await page.query_selector_all('input.InputCell-PB5beCCt55')
            if not digit_inputs:
                # Fallback: ищем input[maxlength="1"]
                digit_inputs = await page.query_selector_all('input[maxlength="1"]')
            logger.info(f"Найдено {len(digit_inputs)} полей для цифр")

            if len(digit_inputs) >= 4:  # Минимум 4 поля для кода (некоторые сайты используют 4)
                # Проверяем что это действительно поля для кода
                # Оптимизация: кэшируем результаты проверок в одном проходе
                valid_digit_inputs = []
                for inp in digit_inputs:
                    try:
                        # Одновременно получаем видимость и box за один раз
                        is_visible, box = await asyncio.gather(
                            inp.is_visible(),
                            inp.bounding_box(),
                            return_exceptions=True
                        )

                        # Проверяем результаты
                        if isinstance(is_visible, Exception) or not is_visible:
                            continue
                        if isinstance(box, Exception) or not box:
                            continue

                        # Поле для цифры обычно квадратное или почти квадратное
                        if not (20 < box['width'] < 100 and 20 < box['height'] < 100):
                            continue

                        # Получаем атрибуты параллельно
                        input_type, inputmode = await asyncio.gather(
                            inp.get_attribute('type'),
                            inp.get_attribute('inputmode'),
                            return_exceptions=True
                        )

                        input_type = '' if isinstance(input_type, Exception) else (input_type or '')
                        inputmode = '' if isinstance(inputmode, Exception) else (inputmode or '')

                        # Должно быть числовым
                        if input_type in ['tel', 'text', 'number'] or inputmode == 'numeric':
                            valid_digit_inputs.append(inp)
                    except Exception as e:
                        logger.debug(f"Ошибка проверки digit input: {e}")
                        continue

                logger.info(f"Валидных полей для цифр: {len(valid_digit_inputs)}")

                if len(valid_digit_inputs) >= 4:
                    # Сохраняем все поля для метода submit_code
                    self._code_digit_inputs = valid_digit_inputs
                    logger.info(f"Найдено {len(valid_digit_inputs)} полей для ввода цифр кода (новый UI)")
                    # Возвращаем первое поле
                    return valid_digit_inputs[0]

            # === СТРАТЕГИЯ 2: Ищем поля с селекторами для digit inputs ===
            logger.info("Стратегия 2: Ищем по селекторам digit inputs...")
            try:
                digit_selector = self.SELECTORS['code_digit_inputs']
                digit_elements = await page.query_selector_all(digit_selector)

                # Оптимизация: проверяем видимость параллельно для всех элементов
                if digit_elements:
                    visibility_checks = await asyncio.gather(
                        *[el.is_visible() for el in digit_elements],
                        return_exceptions=True
                    )
                    visible_digits = [
                        el for el, is_vis in zip(digit_elements, visibility_checks)
                        if not isinstance(is_vis, Exception) and is_vis
                    ]

                    logger.info(f"Найдено {len(visible_digits)} видимых элементов по digit selectors")

                    if len(visible_digits) >= 4:
                        self._code_digit_inputs = visible_digits
                        logger.info(f"Найдено {len(visible_digits)} полей по digit selectors")
                        return visible_digits[0]
            except Exception as e:
                logger.debug(f"Стратегия 2 не сработала: {e}")

            # === СТРАТЕГИЯ 3: Классический подход - одно поле с maxlength=6 ===
            logger.info("Стратегия 3: Ищем классическое поле с maxlength=6...")
            self._code_digit_inputs = None  # Сбрасываем если были

            try:
                element = await page.wait_for_selector(
                    self.SELECTORS['code_input'],
                    timeout=5000,
                    state='visible'
                )

                if element:
                    # Диагностика: что за элемент нашли
                    placeholder = await element.get_attribute('placeholder') or ''
                    maxlength = await element.get_attribute('maxlength') or ''
                    input_type = await element.get_attribute('type') or ''
                    value = await element.input_value()
                    logger.info(f"Найдено поле кода: type={input_type}, maxlength={maxlength}, placeholder='{placeholder}', value='{value}'")

                    # Валидация: это ДОЛЖНО быть поле для кода, а не что-то другое
                    is_valid_code_field = (
                        maxlength == '6' or
                        (input_type == 'tel' and not value) or
                        'код' in placeholder.lower() or
                        'code' in placeholder.lower()
                    )

                    if not is_valid_code_field:
                        logger.warning(f"Найденный элемент не похож на поле кода (type={input_type}, maxlength={maxlength})")
                    elif value and len(value) > 6:
                        logger.warning(f"Поле содержит значение '{value}' - это поле телефона, не кода!")
                    else:
                        return element
            except PlaywrightTimeout:
                logger.debug("Timeout при поиске классического поля кода")

            # === СТРАТЕГИЯ 4: Fallback - ищем любые видимые input рядом с текстом "код" ===
            logger.info("Стратегия 4: Fallback - ищем input рядом с текстом про код...")
            try:
                # Находим все видимые input и проверяем их родителей на наличие текста про код
                all_inputs = await page.query_selector_all('input:visible')
                for inp in all_inputs:
                    try:
                        maxlength = await inp.get_attribute('maxlength') or ''
                        if maxlength in ['1', '6']:
                            # Проверяем родительский контейнер
                            parent_text = await inp.evaluate('el => el.closest("div, form, section")?.innerText || ""')
                            if any(kw in parent_text.lower() for kw in ['код', 'code', 'sms', 'смс']):
                                logger.info(f"Найдено поле через fallback (maxlength={maxlength})")
                                return inp
                    except Exception:
                        continue
            except Exception as e:
                logger.debug(f"Стратегия 4 не сработала: {e}")

            logger.warning("Не удалось найти поле для ввода кода ни одной стратегией")

            # ДИАГНОСТИКА: Выводим все input элементы на странице (только в debug режиме)
            if logger.level <= logging.DEBUG:
                try:
                    all_inputs = await page.query_selector_all('input')
                    logger.info(f"=== ДИАГНОСТИКА: Все input элементы на странице ({len(all_inputs)}) ===")

                    # Оптимизация: собираем атрибуты всех элементов параллельно
                    if all_inputs:
                        all_attrs = await asyncio.gather(
                            *[asyncio.gather(
                                inp.get_attribute('type'),
                                inp.get_attribute('name'),
                                inp.get_attribute('id'),
                                inp.get_attribute('class'),
                                inp.get_attribute('placeholder'),
                                inp.get_attribute('maxlength'),
                                inp.input_value(),
                                inp.is_visible(),
                                return_exceptions=True
                            ) for inp in all_inputs],
                            return_exceptions=True
                        )

                        for i, attrs in enumerate(all_attrs):
                            try:
                                if isinstance(attrs, Exception):
                                    logger.debug(f"  Input[{i}]: Ошибка при чтении атрибутов: {attrs}")
                                    continue

                                input_type, name, id_attr, class_attr, placeholder, maxlength, value, is_visible = attrs

                                # Обрабатываем возможные исключения в отдельных атрибутах
                                input_type = '' if isinstance(input_type, Exception) else (input_type or '')
                                name = '' if isinstance(name, Exception) else (name or '')
                                id_attr = '' if isinstance(id_attr, Exception) else (id_attr or '')
                                class_attr = '' if isinstance(class_attr, Exception) else (class_attr or '')
                                placeholder = '' if isinstance(placeholder, Exception) else (placeholder or '')
                                maxlength = '' if isinstance(maxlength, Exception) else (maxlength or '')
                                value = '' if isinstance(value, Exception) else (value or '')
                                is_visible = False if isinstance(is_visible, Exception) else is_visible

                                logger.info(
                                    f"  Input[{i}]: type='{input_type}', name='{name}', id='{id_attr}', "
                                    f"class='{class_attr[:50]}', placeholder='{placeholder}', "
                                    f"maxlength='{maxlength}', value='{value[:20]}', visible={is_visible}"
                                )
                            except Exception as e:
                                logger.debug(f"  Input[{i}]: Ошибка при обработке атрибутов: {e}")
                except Exception as e:
                    logger.error(f"Ошибка при диагностике input элементов: {e}")

            return None

        except PlaywrightTimeout:
            logger.warning("Timeout при поиске поля кода - поле не появилось")
            return None

    async def _find_submit_button(self, page: Page) -> Optional[Any]:
        """Найти кнопку отправки формы"""
        try:
            # Сначала ищем кнопку с конкретным текстом (наиболее надёжно)
            specific_buttons = [
                'button:has-text("Получить код")',
                'button:has-text("Войти")',
                'button:has-text("Далее")',
                'button:has-text("Продолжить")',
            ]

            # Оптимизация: проверяем все селекторы одновременно с коротким таймаутом
            button_checks = await asyncio.gather(
                *[page.query_selector(selector) for selector in specific_buttons],
                return_exceptions=True
            )

            for selector, button in zip(specific_buttons, button_checks):
                if isinstance(button, Exception) or not button:
                    continue
                try:
                    # Проверяем видимость, текст и размер параллельно
                    is_visible, text, box = await asyncio.gather(
                        button.is_visible(),
                        button.inner_text(),
                        button.bounding_box(),
                        return_exceptions=True
                    )

                    if isinstance(is_visible, Exception) or not is_visible:
                        continue

                    text = '' if isinstance(text, Exception) else text
                    logger.info(f"Найдена кнопка по селектору {selector}: '{text}'")

                    # Проверяем что кнопка достаточно большая (не часть dropdown)
                    if not isinstance(box, Exception) and box and box['width'] > 50 and box['height'] > 20:
                        return button
                    else:
                        logger.warning(f"Кнопка слишком маленькая (width={box['width'] if box and not isinstance(box, Exception) else 0}), пропускаем")
                except Exception as e:
                    logger.debug(f"Ошибка при проверке кнопки {selector}: {e}")
                    continue

            # Fallback: ищем button[type="submit"]
            try:
                button = await page.query_selector('button[type="submit"]')
                if button and await button.is_visible():
                    text = await button.inner_text()
                    logger.info(f"Найдена кнопка type=submit: '{text}'")
                    return button
            except Exception:
                pass

            logger.warning("Не найдена кнопка отправки формы")
            return None
        except Exception as e:
            logger.error(f"Ошибка при поиске кнопки: {e}")
            return None

    async def _check_error(self, page: Page) -> Optional[str]:
        """Проверить наличие ошибки на странице"""
        try:
            # Сначала ищем конкретные сообщения об ошибках WB
            body_text = await page.inner_text('body')

            # Rate limit errors
            rate_limit_patterns = [
                'запрос кода возможен через',
                'слишком много попыток',
                'повторите попытку через',
                'too many requests',
                'try again in',
            ]

            for pattern in rate_limit_patterns:
                if pattern.lower() in body_text.lower():
                    # Ищем полное сообщение с таймером
                    import re
                    # Паттерн: "Запрос кода возможен через X минут Y секунд"
                    match = re.search(r'(запрос кода возможен через.*?секунд)', body_text, re.IGNORECASE)
                    if match:
                        return f"Rate limit: {match.group(1)}"
                    match = re.search(r'(повторите.*?через.*?\d+.*?(?:минут|секунд|час))', body_text, re.IGNORECASE)
                    if match:
                        return f"Rate limit: {match.group(1)}"
                    return "Rate limit: слишком много попыток. Подождите несколько минут."

            # Общие ошибки
            error_patterns = [
                'неверный номер',
                'некорректный номер',
                'номер не найден',
                'ошибка авторизации',
                'доступ заблокирован',
                'аккаунт заблокирован',
            ]

            for pattern in error_patterns:
                if pattern.lower() in body_text.lower():
                    # Пытаемся найти контекст
                    idx = body_text.lower().find(pattern.lower())
                    if idx >= 0:
                        # Берём 100 символов вокруг
                        start = max(0, idx - 20)
                        end = min(len(body_text), idx + 80)
                        context = body_text[start:end].strip()
                        # Убираем лишние пробелы и переносы
                        context = ' '.join(context.split())
                        return context

            # Fallback: ищем элементы с классом error
            error_element = await page.query_selector(self.SELECTORS['error_message'])
            if error_element:
                text = await error_element.inner_text()
                # Фильтруем слишком короткие или бессмысленные сообщения
                if text and len(text) > 5 and not text.startswith('+'):
                    return text.strip()

        except Exception as e:
            logger.debug(f"Ошибка при проверке ошибок на странице: {e}")

        return None

    async def _detect_captcha(self, page: Page) -> bool:
        """
        Определить наличие captcha на странице.

        Проверяет:
        - reCAPTCHA
        - hCaptcha
        - Yandex SmartCaptcha
        - Кастомные image captcha
        """
        captcha_selectors = [
            # reCAPTCHA
            'iframe[src*="recaptcha"]',
            'iframe[title*="reCAPTCHA"]',
            '[class*="recaptcha"]',
            '#g-recaptcha',
            '.g-recaptcha',

            # hCaptcha
            'iframe[src*="hcaptcha"]',
            '[class*="hcaptcha"]',
            '.h-captcha',

            # Yandex SmartCaptcha
            '[class*="smartcaptcha"]',
            'iframe[src*="smartcaptcha"]',

            # Cloudflare Turnstile
            'iframe[src*="turnstile"]',
            '[class*="turnstile"]',

            # Generic captcha indicators
            '[class*="captcha" i]',
            '[id*="captcha" i]',
            'img[src*="captcha" i]',
            'input[name*="captcha" i]',
        ]

        for selector in captcha_selectors:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    logger.warning(f"Обнаружена captcha: {selector}")
                    return True
            except Exception:
                continue

        # Проверяем текст страницы на упоминание captcha
        try:
            body_text = await page.inner_text('body')
            captcha_keywords = [
                'введите код с картинки',
                'подтвердите, что вы не робот',
                'проверка безопасности',
                'я не робот',
                'verify you are human',
                'captcha',
                'капча',
            ]
            for keyword in captcha_keywords:
                if keyword.lower() in body_text.lower():
                    logger.warning(f"Обнаружен текст captcha: '{keyword}'")
                    return True
        except Exception:
            pass

        return False

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

    async def _keep_session_alive(self, user_id: int, page: Page) -> None:
        """
        Background task для поддержания сессии активной.
        Периодически пингует страницу, чтобы она не истекла.
        """
        try:
            while True:
                # Проверяем, существует ли ещё сессия
                if user_id not in self._sessions:
                    logger.debug(f"Keep-alive stopped: session removed for user {user_id}")
                    break

                # Ждём 30 секунд между пингами
                await asyncio.sleep(30)

                # Пингуем страницу - просто вызываем evaluate, чтобы убедиться что страница жива
                try:
                    await page.evaluate('() => document.title')
                    logger.debug(f"Keep-alive ping successful for user {user_id}")
                except Exception as e:
                    logger.warning(f"Keep-alive ping failed for user {user_id}: {e}")
                    # Если страница мертва - прекращаем пинги
                    break
        except asyncio.CancelledError:
            logger.debug(f"Keep-alive task cancelled for user {user_id}")
        except Exception as e:
            logger.error(f"Keep-alive task error for user {user_id}: {e}")

    async def get_session(self, user_id: int) -> Optional[AuthSession]:
        """Получить текущую сессию пользователя"""
        return self._sessions.get(user_id)

    async def close_session(self, user_id: int) -> None:
        """Закрыть сессию авторизации"""
        session = self._sessions.get(user_id)
        if session:
            # Останавливаем keep-alive task если он существует
            if session.keep_alive_task and not session.keep_alive_task.done():
                session.keep_alive_task.cancel()
                try:
                    await session.keep_alive_task
                except asyncio.CancelledError:
                    pass

            # Закрываем browser context
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
