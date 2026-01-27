-- WB Redistribution Bot - PostgreSQL Schema
-- Инициализация базы данных для Railway

-- ========================================
-- Пользователи
-- ========================================
CREATE TABLE IF NOT EXISTS users (
    telegram_id BIGINT PRIMARY KEY,
    username VARCHAR(255),
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ========================================
-- WB API Токены
-- ========================================
CREATE TABLE IF NOT EXISTS wb_api_tokens (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    name VARCHAR(255) DEFAULT 'Основной',
    encrypted_token TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used TIMESTAMP
);

-- ========================================
-- Поставщики (Мультиаккаунт)
-- ========================================
CREATE TABLE IF NOT EXISTS suppliers (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    token_id INTEGER NOT NULL REFERENCES wb_api_tokens(id) ON DELETE CASCADE,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ========================================
-- Заявки на перемещение
-- ========================================
CREATE TABLE IF NOT EXISTS redistribution_requests (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    nm_id BIGINT NOT NULL,
    product_name TEXT,
    source_warehouse_id INTEGER NOT NULL,
    source_warehouse_name VARCHAR(255),
    target_warehouse_id INTEGER NOT NULL,
    target_warehouse_name VARCHAR(255),
    quantity INTEGER NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    supply_id VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

-- ========================================
-- Индексы для производительности
-- ========================================
CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);
CREATE INDEX IF NOT EXISTS idx_tokens_user_id ON wb_api_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_suppliers_user_id ON suppliers(user_id);
CREATE INDEX IF NOT EXISTS idx_requests_user_id ON redistribution_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_requests_status ON redistribution_requests(status);
