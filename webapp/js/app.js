// Telegram WebApp API
const tg = window.Telegram.WebApp;
tg.expand();

// API base URL
const API_BASE = window.location.origin;

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

// Утилиты
function showLoader() {
    document.getElementById('loader').classList.remove('hidden');
}

function hideLoader() {
    document.getElementById('loader').classList.add('hidden');
}

function showError(message) {
    tg.showAlert(message);
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
    try {
        showLoader();

        // Загружаем поставщиков
        state.suppliers = await apiRequest('/api/suppliers');

        // Загружаем склады
        state.warehouses = await apiRequest('/api/warehouses');

        // Загружаем заявки
        await loadRequests();

        // Заполняем dropdown поставщиков
        populateSuppliers();

        // Устанавливаем обработчики
        setupEventListeners();

        hideLoader();
    } catch (error) {
        hideLoader();
        showError('Ошибка загрузки данных: ' + error.message);
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

    const statusClass = `status-${request.status}`;
    const statusText = {
        pending: 'Ожидание',
        searching: 'Поиск слотов',
        completed: 'Выполнено',
        cancelled: 'Отменено'
    }[request.status] || request.status;

    const createdDate = new Date(request.created_at).toLocaleDateString('ru-RU');
    const completedDate = request.completed_at ?
        ' → ' + new Date(request.completed_at).toLocaleDateString('ru-RU') : '';

    card.innerHTML = `
        <div class="request-header">
            <div class="request-supplier">${request.supplier_name}</div>
            <div class="request-date">${createdDate}${completedDate}</div>
        </div>
        <div class="request-route">
            <div class="warehouse-name">${request.source_warehouse_name || 'Склад ' + request.source_warehouse_id}</div>
            <div class="route-arrow">→</div>
            <div class="warehouse-name">${request.target_warehouse_name || 'Склад ' + request.target_warehouse_id}</div>
        </div>
        <div class="request-info">
            <div>Арт: ${request.nm_id}</div>
            <div>Кол-во: ${request.quantity}</div>
        </div>
        ${type === 'current' ? `
            <div class="request-actions">
                <button class="btn-action btn-edit" onclick="editRequest(${request.id})">✏️ Изменить</button>
                <button class="btn-action btn-delete" onclick="deleteRequest(${request.id})">🗑 Удалить</button>
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

// Редактирование заявки
async function editRequest(requestId) {
    const request = [...state.currentRequests, ...state.archiveRequests]
        .find(r => r.id === requestId);

    if (!request) return;

    const newQuantity = prompt('Новое количество:', request.quantity);
    if (!newQuantity) return;

    try {
        showLoader();
        await apiRequest(`/api/requests/${requestId}`, {
            method: 'PATCH',
            body: JSON.stringify({ quantity: parseInt(newQuantity) })
        });

        hideLoader();
        await loadRequests();
        tg.showAlert('Заявка обновлена');
    } catch (error) {
        hideLoader();
        showError('Ошибка обновления: ' + error.message);
    }
}

// Удаление заявки
async function deleteRequest(requestId) {
    if (!confirm('Удалить заявку?')) return;

    try {
        showLoader();
        await apiRequest(`/api/requests/${requestId}`, {
            method: 'DELETE'
        });

        hideLoader();
        await loadRequests();
        tg.showAlert('Заявка удалена');
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
