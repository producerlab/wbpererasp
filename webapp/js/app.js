// Telegram WebApp API
const tg = window.Telegram?.WebApp || {
    expand: () => {},
    initData: '',
    showAlert: (msg) => alert(msg)
};
tg.expand();

// API base URL
const API_BASE = window.location.origin;

// Demo mode detection
const DEMO_MODE = !window.location.pathname.includes('/webapp/') ||
                  window.location.protocol === 'file:' ||
                  window.location.hostname === 'localhost';

// Состояние приложения
let state = {
    currentTab: 'current',
    suppliers: [],
    warehouses: [],
    selectedSupplier: null,
    productData: null,
    sourceStocks: [],
    currentRequests: [],
    archiveRequests: []
};

// Demo data for testing UI
const DEMO_DATA = {
    suppliers: [
        { id: 1, name: 'ИП Иванов И.И.', is_default: true },
        { id: 2, name: 'ООО "Торговый Дом"', is_default: false }
    ],
    warehouses: [
        { id: 507, name: 'Коледино', region: 'Московская область' },
        { id: 117501, name: 'Казань', region: 'Татарстан' },
        { id: 130744, name: 'Краснодар', region: 'Краснодарский край' },
        { id: 208277, name: 'Санкт-Петербург', region: 'Ленинградская область' }
    ],
    currentRequests: [
        {
            id: 1,
            supplier_id: 1,
            supplier_name: 'ИП Иванов И.И.',
            nm_id: 12345678,
            product_name: 'Футболка мужская хлопок',
            source_warehouse_id: 507,
            source_warehouse_name: 'Коледино',
            target_warehouse_id: 117501,
            target_warehouse_name: 'Казань',
            quantity: 150,
            status: 'searching',
            created_at: new Date().toISOString()
        },
        {
            id: 2,
            supplier_id: 1,
            supplier_name: 'ИП Иванов И.И.',
            nm_id: 87654321,
            product_name: 'Джинсы женские slim',
            source_warehouse_id: 507,
            source_warehouse_name: 'Коледино',
            target_warehouse_id: 130744,
            target_warehouse_name: 'Краснодар',
            quantity: 75,
            status: 'pending',
            created_at: new Date(Date.now() - 86400000).toISOString()
        }
    ],
    archiveRequests: [
        {
            id: 3,
            supplier_id: 2,
            supplier_name: 'ООО "Торговый Дом"',
            nm_id: 11223344,
            product_name: 'Куртка зимняя',
            source_warehouse_id: 117501,
            source_warehouse_name: 'Казань',
            target_warehouse_id: 507,
            target_warehouse_name: 'Коледино',
            quantity: 30,
            status: 'completed',
            created_at: new Date(Date.now() - 172800000).toISOString(),
            completed_at: new Date(Date.now() - 86400000).toISOString()
        }
    ]
};

// Утилиты
function showLoader() {
    document.getElementById('loader').classList.remove('hidden');
}

function hideLoader() {
    document.getElementById('loader').classList.add('hidden');
}

function showError(message) {
    if (DEMO_MODE) {
        console.warn('Demo mode:', message);
    } else {
        tg.showAlert(message);
    }
}

async function apiRequest(url, options = {}) {
    const headers = {
        'Content-Type': 'application/json',
        'X-Telegram-Init-Data': tg.initData,
        ...options.headers
    };

    const response = await fetch(`${API_BASE}${url}`, {
        ...options,
        headers
    });

    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || 'Request failed');
    }

    return response.json();
}

// Инициализация
async function init() {
    // Всегда устанавливаем обработчики событий
    setupEventListeners();

    try {
        showLoader();

        if (DEMO_MODE) {
            // Demo mode - use mock data
            console.log('🎨 Running in DEMO MODE');
            state.suppliers = DEMO_DATA.suppliers;
            state.warehouses = DEMO_DATA.warehouses;
            state.currentRequests = DEMO_DATA.currentRequests;
            state.archiveRequests = DEMO_DATA.archiveRequests;
        } else {
            // Production mode - load from API
            state.suppliers = await apiRequest('/api/suppliers');
            state.warehouses = await apiRequest('/api/warehouses');
            await loadRequests();
        }

        // Заполняем dropdown поставщиков
        populateSuppliers();

        // Обновляем счётчики и рендерим
        document.getElementById('current-count').textContent = state.currentRequests.length;
        document.getElementById('archive-count').textContent = state.archiveRequests.length;
        renderRequests();

        hideLoader();
    } catch (error) {
        hideLoader();
        console.error('Init error:', error);

        // В случае ошибки - показываем demo данные
        if (!DEMO_MODE) {
            showError('Ошибка загрузки данных: ' + error.message);
        }
    }
}

// Заполнение dropdown поставщиков
function populateSuppliers() {
    const select = document.getElementById('supplier');
    select.innerHTML = '<option value="">Выберите поставщика...</option>';

    state.suppliers.forEach(supplier => {
        const option = document.createElement('option');
        option.value = supplier.id;
        option.textContent = supplier.name;
        if (supplier.is_default) {
            option.selected = true;
        }
        select.appendChild(option);
    });
}

// Обработчики событий
function setupEventListeners() {
    // Табы
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            const tabName = tab.dataset.tab;
            switchTab(tabName);
        });
    });

    // Кнопка создания заявки
    document.getElementById('btn-create').addEventListener('click', () => {
        openCreateModal();
    });

    // Закрытие модалки
    document.getElementById('btn-close-modal').addEventListener('click', () => {
        closeCreateModal();
    });

    // Поиск товара по артикулу
    let searchTimeout;
    document.getElementById('nm-id').addEventListener('input', (e) => {
        clearTimeout(searchTimeout);
        const value = e.target.value.trim();

        if (value.length >= 3) {
            searchTimeout = setTimeout(() => searchProduct(value), 500);
        } else {
            hideProductFields();
        }
    });

    // Выбор склада-источника
    document.getElementById('source-warehouse').addEventListener('change', (e) => {
        const selectedId = parseInt(e.target.value);
        if (selectedId) {
            showQuantityField(selectedId);
            loadTargetWarehouses(selectedId);
        }
    });

    // Отправка формы
    document.getElementById('form-create').addEventListener('submit', (e) => {
        e.preventDefault();
        createRequest();
    });
}

// Переключение табов
function switchTab(tabName) {
    state.currentTab = tabName;

    // Обновляем активный таб
    document.querySelectorAll('.tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.tab === tabName);
    });

    // Показываем нужный список
    document.getElementById('current-list').classList.toggle('hidden', tabName !== 'current');
    document.getElementById('archive-list').classList.toggle('hidden', tabName !== 'archive');
}

// Загрузка заявок
async function loadRequests() {
    try {
        // Текущие (pending, searching)
        const pending = await apiRequest('/api/requests?status=pending');
        const searching = await apiRequest('/api/requests?status=searching');
        state.currentRequests = [...pending, ...searching];

        // Архив (completed, cancelled)
        const completed = await apiRequest('/api/requests?status=completed');
        const cancelled = await apiRequest('/api/requests?status=cancelled');
        state.archiveRequests = [...completed, ...cancelled];

        // Обновляем счётчики
        document.getElementById('current-count').textContent = state.currentRequests.length;
        document.getElementById('archive-count').textContent = state.archiveRequests.length;

        // Рендерим списки
        renderRequests();
    } catch (error) {
        showError('Ошибка загрузки заявок: ' + error.message);
    }
}

// Рендеринг списка заявок
function renderRequests() {
    renderRequestsList('current', state.currentRequests);
    renderRequestsList('archive', state.archiveRequests);
}

function renderRequestsList(type, requests) {
    const listId = type === 'current' ? 'current-list' : 'archive-list';
    const emptyId = type === 'current' ? 'current-empty' : 'archive-empty';
    const list = document.getElementById(listId);
    const empty = document.getElementById(emptyId);

    // Очищаем список (кроме empty state)
    Array.from(list.children).forEach(child => {
        if (child.id !== emptyId) {
            child.remove();
        }
    });

    if (requests.length === 0) {
        empty.classList.remove('hidden');
        return;
    }

    empty.classList.add('hidden');

    requests.forEach(request => {
        const card = createRequestCard(request, type);
        list.appendChild(card);
    });
}

function createRequestCard(request, type) {
    const card = document.createElement('div');
    card.className = 'request-card';
    card.dataset.requestId = request.id;

    const statusText = {
        pending: 'Ожидание',
        searching: 'Поиск',
        completed: 'Готово',
        cancelled: 'Отмена'
    }[request.status] || request.status;

    const createdDate = new Date(request.created_at).toLocaleDateString('ru-RU', {
        day: 'numeric',
        month: 'short'
    });

    // Product name or article
    const productName = request.product_name || `Артикул ${request.nm_id}`;

    // Truncate warehouse names if too long
    const sourceName = request.source_warehouse_name || `Склад ${request.source_warehouse_id}`;
    const targetName = request.target_warehouse_name || `Склад ${request.target_warehouse_id}`;

    card.innerHTML = `
        <div class="request-card-header">
            <div class="request-product">
                <div class="request-product-name">${productName}</div>
                <div class="request-article">${request.nm_id}</div>
            </div>
            <div class="status-badge status-${request.status}">
                <span class="status-dot"></span>
                ${statusText}
            </div>
        </div>

        <div class="request-card-body">
            <div class="request-route">
                <div class="warehouse-block">
                    <div class="warehouse-label">Откуда</div>
                    <div class="warehouse-name">${sourceName}</div>
                </div>
                <div class="route-arrow">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M5 12h14M12 5l7 7-7 7"/>
                    </svg>
                </div>
                <div class="warehouse-block">
                    <div class="warehouse-label">Куда</div>
                    <div class="warehouse-name">${targetName}</div>
                </div>
            </div>
        </div>

        <div class="request-card-footer">
            <!-- Количество - большое и по центру -->
            <div class="request-quantity-display" data-request-id="${request.id}">
                <span class="request-quantity">${request.quantity}</span>
                <span class="request-quantity-label">шт</span>
            </div>

            <!-- Режим редактирования (скрыт по умолчанию) -->
            <div class="request-edit-mode hidden" data-request-id="${request.id}">
                <input type="number"
                       class="request-edit-input"
                       value="${request.quantity}"
                       min="1"
                       data-original="${request.quantity}">
                <button class="btn-save" onclick="saveQuantity(${request.id})">Сохранить</button>
                <button class="btn-cancel-edit" onclick="cancelEdit(${request.id})">Отмена</button>
            </div>

            <!-- Дата и поставщик -->
            <div class="request-meta-row">
                <span class="request-date">${createdDate}</span>
                <span class="request-supplier">${request.supplier_name}</span>
            </div>
        </div>

        ${type === 'current' ? `
            <div class="request-actions" data-request-id="${request.id}">
                <button class="btn-action" onclick="startEdit(${request.id})">Изменить</button>
                <button class="btn-action btn-delete" onclick="deleteRequest(${request.id})">Удалить</button>
            </div>
        ` : ''}
    `;

    return card;
}

// Открытие модалки создания
function openCreateModal() {
    document.getElementById('modal-create').classList.remove('hidden');
    resetCreateForm();
}

// Закрытие модалки
function closeCreateModal() {
    document.getElementById('modal-create').classList.add('hidden');
    resetCreateForm();
}

// Сброс формы
function resetCreateForm() {
    document.getElementById('form-create').reset();
    hideProductFields();
}

function hideProductFields() {
    document.getElementById('product-info').classList.add('hidden');
    document.getElementById('source-group').classList.add('hidden');
    document.getElementById('quantity-group').classList.add('hidden');
    document.getElementById('target-group').classList.add('hidden');
}

// Поиск товара
async function searchProduct(nmId) {
    const supplierId = document.getElementById('supplier').value;
    if (!supplierId) {
        showError('Выберите поставщика');
        return;
    }

    try {
        showLoader();
        const data = await apiRequest(`/api/products/search?q=${nmId}&supplier_id=${supplierId}`);

        if (data.found) {
            state.productData = data;
            state.sourceStocks = data.warehouses || [];

            // Показываем название
            document.getElementById('product-name').textContent =
                data.product_name || `Артикул ${data.nm_id}`;
            document.getElementById('product-info').classList.remove('hidden');

            // Заполняем склады-источники
            populateSourceWarehouses();
        } else {
            showError('Товар не найден');
            hideProductFields();
        }

        hideLoader();
    } catch (error) {
        hideLoader();
        showError('Ошибка поиска: ' + error.message);
    }
}

// Заполнение складов-источников
function populateSourceWarehouses() {
    const select = document.getElementById('source-warehouse');
    select.innerHTML = '<option value="">Выберите склад</option>';

    state.sourceStocks.forEach(stock => {
        if (stock.quantity > 0) {
            const option = document.createElement('option');
            option.value = stock.warehouse_id;
            option.textContent = `${stock.warehouse_name || 'Склад ' + stock.warehouse_id} (${stock.quantity} шт)`;
            option.dataset.quantity = stock.available;
            select.appendChild(option);
        }
    });

    document.getElementById('source-group').classList.remove('hidden');
}

// Показ поля количества
function showQuantityField(sourceWarehouseId) {
    const select = document.getElementById('source-warehouse');
    const selectedOption = select.options[select.selectedIndex];
    const maxQuantity = parseInt(selectedOption.dataset.quantity || 0);

    document.getElementById('max-quantity').textContent = maxQuantity;
    document.getElementById('quantity').max = maxQuantity;
    document.getElementById('quantity-group').classList.remove('hidden');
}

// Загрузка складов-назначений
function loadTargetWarehouses(sourceWarehouseId) {
    const select = document.getElementById('target-warehouse');
    select.innerHTML = '<option value="">Выберите склад</option>';

    state.warehouses.forEach(warehouse => {
        if (warehouse.id !== sourceWarehouseId) {
            const option = document.createElement('option');
            option.value = warehouse.id;
            option.textContent = warehouse.name + (warehouse.region ? ` (${warehouse.region})` : '');
            select.appendChild(option);
        }
    });

    document.getElementById('target-group').classList.remove('hidden');
}

// Создание заявки
async function createRequest() {
    const supplierId = parseInt(document.getElementById('supplier').value);
    const nmId = parseInt(document.getElementById('nm-id').value);
    const sourceWarehouseId = parseInt(document.getElementById('source-warehouse').value);
    const targetWarehouseId = parseInt(document.getElementById('target-warehouse').value);
    const quantity = parseInt(document.getElementById('quantity').value);

    // Получаем названия
    const sourceSelect = document.getElementById('source-warehouse');
    const sourceOption = sourceSelect.options[sourceSelect.selectedIndex];
    const sourceName = sourceOption.textContent.split('(')[0].trim();

    const targetSelect = document.getElementById('target-warehouse');
    const targetOption = targetSelect.options[targetSelect.selectedIndex];
    const targetName = targetOption.textContent.split('(')[0].trim();

    const requestData = {
        supplier_id: supplierId,
        nm_id: nmId,
        product_name: state.productData?.product_name || '',
        source_warehouse_id: sourceWarehouseId,
        source_warehouse_name: sourceName,
        target_warehouse_id: targetWarehouseId,
        target_warehouse_name: targetName,
        quantity: quantity
    };

    try {
        showLoader();
        await apiRequest('/api/requests', {
            method: 'POST',
            body: JSON.stringify(requestData)
        });

        hideLoader();
        closeCreateModal();

        // Обновляем списки
        await loadRequests();

        tg.showAlert('Заявка создана!');
    } catch (error) {
        hideLoader();
        showError('Ошибка создания заявки: ' + error.message);
    }
}

// Inline редактирование - начать
function startEdit(requestId) {
    const card = document.querySelector(`.request-card[data-request-id="${requestId}"]`);
    if (!card) return;

    // Скрываем display, показываем edit mode
    const display = card.querySelector(`.request-quantity-display[data-request-id="${requestId}"]`);
    const editMode = card.querySelector(`.request-edit-mode[data-request-id="${requestId}"]`);
    const actions = card.querySelector(`.request-actions[data-request-id="${requestId}"]`);

    if (display) display.classList.add('hidden');
    if (editMode) {
        editMode.classList.remove('hidden');
        // Фокус на input
        const input = editMode.querySelector('.request-edit-input');
        if (input) {
            input.focus();
            input.select();
        }
    }
    if (actions) actions.classList.add('hidden');
}

// Inline редактирование - сохранить
async function saveQuantity(requestId) {
    const card = document.querySelector(`.request-card[data-request-id="${requestId}"]`);
    if (!card) return;

    const editMode = card.querySelector(`.request-edit-mode[data-request-id="${requestId}"]`);
    const input = editMode?.querySelector('.request-edit-input');
    const newQuantity = parseInt(input?.value || 0);

    if (!newQuantity || newQuantity < 1) {
        showError('Введите корректное количество');
        return;
    }

    try {
        if (!DEMO_MODE) {
            showLoader();
            await apiRequest(`/api/requests/${requestId}`, {
                method: 'PATCH',
                body: JSON.stringify({ quantity: newQuantity })
            });
            hideLoader();
            await loadRequests();
        } else {
            // Demo mode - обновляем локально
            const request = state.currentRequests.find(r => r.id === requestId);
            if (request) {
                request.quantity = newQuantity;
            }
            // Обновляем UI
            const display = card.querySelector(`.request-quantity-display[data-request-id="${requestId}"]`);
            const quantitySpan = display?.querySelector('.request-quantity');
            if (quantitySpan) quantitySpan.textContent = newQuantity;

            // Выходим из режима редактирования
            cancelEdit(requestId);

            console.log(`Demo: Updated quantity to ${newQuantity}`);
        }
    } catch (error) {
        hideLoader();
        showError('Ошибка обновления: ' + error.message);
    }
}

// Inline редактирование - отмена
function cancelEdit(requestId) {
    const card = document.querySelector(`.request-card[data-request-id="${requestId}"]`);
    if (!card) return;

    const display = card.querySelector(`.request-quantity-display[data-request-id="${requestId}"]`);
    const editMode = card.querySelector(`.request-edit-mode[data-request-id="${requestId}"]`);
    const actions = card.querySelector(`.request-actions[data-request-id="${requestId}"]`);

    // Восстанавливаем оригинальное значение
    const input = editMode?.querySelector('.request-edit-input');
    if (input) {
        input.value = input.dataset.original;
    }

    // Показываем display, скрываем edit mode
    if (display) display.classList.remove('hidden');
    if (editMode) editMode.classList.add('hidden');
    if (actions) actions.classList.remove('hidden');
}

// Старый метод - оставляем для совместимости
async function editRequest(requestId) {
    startEdit(requestId);
}

// Удаление заявки
async function deleteRequest(requestId) {
    if (!confirm('Удалить заявку?')) return;

    try {
        if (!DEMO_MODE) {
            showLoader();
            await apiRequest(`/api/requests/${requestId}`, {
                method: 'DELETE'
            });
            hideLoader();
            await loadRequests();
        } else {
            // Demo mode - удаляем локально
            state.currentRequests = state.currentRequests.filter(r => r.id !== requestId);
            state.archiveRequests = state.archiveRequests.filter(r => r.id !== requestId);

            // Обновляем счётчики
            document.getElementById('current-count').textContent = state.currentRequests.length;
            document.getElementById('archive-count').textContent = state.archiveRequests.length;

            // Перерендериваем
            renderRequests();
            console.log(`Demo: Deleted request ${requestId}`);
        }
    } catch (error) {
        hideLoader();
        showError('Ошибка удаления: ' + error.message);
    }
}

// Запускаем приложение
init();

// Экспортируем функции для HTML onclick
window.editRequest = editRequest;
window.deleteRequest = deleteRequest;
window.startEdit = startEdit;
window.saveQuantity = saveQuantity;
window.cancelEdit = cancelEdit;
