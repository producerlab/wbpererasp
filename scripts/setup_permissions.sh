#!/bin/bash
#
# Скрипт для установки правильных file permissions
# для критичных файлов WB Redistribution Bot
#

set -e  # Остановить при ошибке

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo ""
echo "=========================================="
echo "  🔒 Setup File Permissions"
echo "  WB Redistribution Bot"
echo "=========================================="
echo ""

# Определить корневую директорию проекта
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "📁 Рабочая директория: $PROJECT_ROOT"
echo ""

# Массив критичных файлов и их требуемых permissions
declare -A FILES=(
    [".env"]="600"
    ["bot_data.db"]="600"
)

# Счетчики
FIXED=0
SKIPPED=0
ERRORS=0

# Обработать каждый файл
for FILE in "${!FILES[@]}"; do
    REQUIRED_MODE="${FILES[$FILE]}"

    if [ ! -f "$FILE" ]; then
        echo -e "${YELLOW}⏭️  $FILE - не существует, пропуск${NC}"
        ((SKIPPED++))
        continue
    fi

    # Получить текущие permissions
    CURRENT_MODE=$(stat -f "%A" "$FILE" 2>/dev/null || stat -c "%a" "$FILE" 2>/dev/null)

    if [ "$CURRENT_MODE" = "$REQUIRED_MODE" ]; then
        echo -e "${GREEN}✅ $FILE - уже корректные права ($CURRENT_MODE)${NC}"
    else
        echo -e "${YELLOW}⚠️  $FILE - неправильные права: $CURRENT_MODE (должно быть $REQUIRED_MODE)${NC}"
        echo "   Исправляю..."

        # Установить правильные permissions
        if chmod "$REQUIRED_MODE" "$FILE"; then
            echo -e "${GREEN}   ✅ Исправлено!${NC}"
            ((FIXED++))
        else
            echo -e "${RED}   ❌ Ошибка при установке permissions${NC}"
            ((ERRORS++))
        fi
    fi
done

# Дополнительно: установить владельца если скрипт запущен с sudo
if [ "$EUID" -eq 0 ]; then
    echo ""
    echo "ℹ️  Скрипт запущен с правами root"

    if [ -n "$SUDO_USER" ]; then
        echo "   Устанавливаю владельца файлов: $SUDO_USER"

        for FILE in "${!FILES[@]}"; do
            if [ -f "$FILE" ]; then
                chown "$SUDO_USER:$SUDO_USER" "$FILE" 2>/dev/null || true
            fi
        done

        echo -e "${GREEN}   ✅ Владелец установлен${NC}"
    fi
fi

# Итоговый отчет
echo ""
echo "=========================================="
echo "  📊 Итоговый отчет"
echo "=========================================="
echo ""
echo "Исправлено: $FIXED"
echo "Пропущено: $SKIPPED"
echo "Ошибок: $ERRORS"
echo ""

if [ $ERRORS -gt 0 ]; then
    echo -e "${RED}❌ Обнаружены ошибки!${NC}"
    echo "Проверьте вывод выше для деталей."
    exit 1
elif [ $FIXED -gt 0 ]; then
    echo -e "${GREEN}✅ Permissions успешно исправлены!${NC}"
else
    echo -e "${GREEN}✅ Все permissions уже корректны!${NC}"
fi

echo ""
echo "💡 Для дополнительной безопасности:"
echo "   - Убедитесь что .env файл не коммитится в git"
echo "   - В production используйте secrets manager"
echo "   - Регулярно ротируйте encryption key"
echo ""
