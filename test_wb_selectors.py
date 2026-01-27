"""
Тестовый скрипт для поиска селекторов на странице авторизации WB.

Использование:
    python test_wb_selectors.py

Скрипт откроет браузер (НЕ headless), загрузит страницу авторизации WB,
сделает скриншот и выведет HTML код формы авторизации.
"""

import asyncio
from playwright.async_api import async_playwright


async def test_selectors():
    """Тестирует доступ к странице авторизации и ищет селекторы"""

    async with async_playwright() as p:
        print("🚀 Запуск браузера...")

        # Запускаем браузер в НЕ headless режиме (видимый)
        browser = await p.chromium.launch(
            headless=False,  # Видимый браузер
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox'
            ]
        )

        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )

        page = await context.new_page()

        # Скрываем webdriver
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => false
            });
        """)

        print("📄 Открываю страницу авторизации WB...")
        await page.goto(
            "https://seller-auth.wildberries.ru/ru/",
            wait_until='networkidle',
            timeout=30000
        )

        print("⏳ Жду загрузки страницы (5 секунд)...")
        await asyncio.sleep(5)

        print("📸 Делаю скриншот...")
        await page.screenshot(path='wb_auth_page.png')
        print("✅ Скриншот сохранен: wb_auth_page.png")

        print("\n" + "="*60)
        print("🔍 ПОИСК СЕЛЕКТОРОВ")
        print("="*60 + "\n")

        # Ищем поле ввода телефона
        print("1️⃣ Ищу поле ввода телефона...")

        phone_selectors = [
            'input[type="tel"]',
            'input[name="phone"]',
            'input[name="phoneNumber"]',
            'input[placeholder*="телефон"]',
            'input[placeholder*="phone"]',
            'input[data-testid*="phone"]',
            'input.phone-input',
            'input#phone',
            '.input-phone input',
        ]

        phone_input = None
        for selector in phone_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    html = await element.evaluate('el => el.outerHTML')
                    print(f"   ✅ Найдено по селектору: {selector}")
                    print(f"      HTML: {html[:200]}...")
                    phone_input = selector
                    break
            except:
                pass

        if not phone_input:
            print("   ❌ Поле телефона не найдено автоматически")
            print("   📝 Получаю весь HTML body...")
            body_html = await page.evaluate('() => document.body.innerHTML')
            with open('wb_auth_body.html', 'w', encoding='utf-8') as f:
                f.write(body_html)
            print("   ✅ HTML сохранен в: wb_auth_body.html")

        # Ищем кнопку отправки
        print("\n2️⃣ Ищу кнопку отправки/получения кода...")

        button_selectors = [
            'button[type="submit"]',
            'button:has-text("Получить код")',
            'button:has-text("Войти")',
            'button:has-text("Отправить")',
            'button.submit-btn',
            'button[data-testid*="submit"]',
        ]

        submit_button = None
        for selector in button_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    html = await element.evaluate('el => el.outerHTML')
                    text = await element.evaluate('el => el.textContent')
                    print(f"   ✅ Найдена кнопка: {selector}")
                    print(f"      Текст: {text}")
                    print(f"      HTML: {html[:200]}...")
                    submit_button = selector
                    break
            except:
                pass

        if not submit_button:
            print("   ❌ Кнопка не найдена автоматически")

        print("\n" + "="*60)
        print("📋 РЕЗЮМЕ")
        print("="*60)
        print(f"Поле телефона: {phone_input or '❌ Не найдено'}")
        print(f"Кнопка отправки: {submit_button or '❌ Не найдено'}")
        print("\nФайлы созданы:")
        print("  • wb_auth_page.png - скриншот страницы")
        print("  • wb_auth_body.html - HTML код страницы (если нужно)")
        print("\n💡 Откройте wb_auth_body.html и найдите input для телефона вручную")
        print("="*60)

        # Ждем 10 секунд перед закрытием (чтобы успеть посмотреть)
        print("\n⏳ Браузер закроется через 10 секунд...")
        await asyncio.sleep(10)

        await browser.close()
        print("✅ Готово!")


if __name__ == "__main__":
    asyncio.run(test_selectors())
