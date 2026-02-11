// ===== Script principal de la aplicación PWA del carrito =====

// 配置（API 基址）
// CHANGE: 云端部署用 config.js 的 api_base_url（如 Render）；本地打开页面时优先用本机 5000，无需改 config
function _getApiBase() {
    if (typeof window === 'undefined' || !window.location || !window.location.origin) return 'http://127.0.0.1:5000/api';
    var origin = window.location.origin;
    var path = (window.location.pathname || '');
    // 本机打开（127.0.0.1 / localhost）时一律用本地 API，方便本地调试
    if (origin.indexOf('127.0.0.1') !== -1 || origin.indexOf('localhost') !== -1) return 'http://127.0.0.1:5000/api';
    // 已配置云端 API 时使用（部署到 Render 后云端页面用此地址，无需再开本机 .bat）
    if (typeof window !== 'undefined' && window.PWA_CONFIG && window.PWA_CONFIG.api_base_url) {
        var url = String(window.PWA_CONFIG.api_base_url).replace(/\/$/, '');
        if (url) return url;
    }
    // CHANGE: 纯静态站（pages.dev/github.io）不再回退到 127.0.0.1，否则云端访问会 ERR_CONNECTION_REFUSED；请在本机 config.js 填写 api_base_url 后同步
    if (path.indexOf('/pwa_cart') !== -1) return origin + '/pwa_cart/api';
    return origin + '/api';
}
const CONFIG = {
    get API_BASE_URL() { return _getApiBase(); },
    DEFAULT_USER_ID: 1,
    SHIPPING_COST: 8.00
};
// CHANGE: 相对路径产品图用 API 所在域名，云端部署时图片从后端（如 Render）加载
function _getImageBase() {
    if (typeof window === 'undefined' || !window.location) return '';
    var api = CONFIG.API_BASE_URL;
    if (!api || api.indexOf('127.0.0.1') !== -1 || api.indexOf('localhost') !== -1) return window.location.origin;
    try { return new URL(api).origin; } catch (e) { return window.location.origin; }
}
// CHANGE: 当 API 返回 /api/images/xxx 且页面在 Pages 上时，用当前站点 base 拼出 Pages 图片 URL（后端未设 PAGES_IMAGE_BASE_URL 时的前端回退）
function _resolveImageSrc(imagePath) {
    if (!imagePath || typeof imagePath !== 'string') return '';
    var raw = imagePath.trim();
    if (raw.startsWith('http://') || raw.startsWith('https://')) {
        // 若 URL 里误含 Windows 路径（如 .../Cristy/D%3A%5CCristy%5C...），只保留最后一个文件名再拼回
        var lastSlash = raw.lastIndexOf('/');
        if (lastSlash !== -1) {
            var after = raw.slice(lastSlash + 1);
            if (after.indexOf('%3A') !== -1 || after.indexOf('%5C') !== -1 || (after.indexOf('Cristy') !== -1 && after.indexOf('Procesado') !== -1)) {
                try {
                    var decoded = decodeURIComponent(after);
                    var fn = decoded.replace(/\\/g, '/').split('/').pop() || decoded;
                    var base = raw.slice(0, lastSlash + 1);
                    return base + encodeURIComponent(fn);
                } catch (e) { /* ignore */ }
            }
        }
        return raw;
    }
    if (raw.startsWith('/api/images/')) {
        var filename = raw.replace('/api/images/', '').split('?')[0].trim();
        if (filename && window.location && (window.location.origin || '').indexOf('127.0.0.1') === -1 && (window.location.origin || '').indexOf('localhost') === -1) {
            var pathname = (window.location.pathname || '').replace(/\/$/, '');
            var base = pathname.indexOf('/pwa_cart') !== -1 ? (window.location.origin + pathname.replace(/\/pwa_cart.*$/, '/pwa_cart')) : (window.location.origin + '/pwa_cart');
            return base + '/Ya%20Subio/Cristy/' + encodeURIComponent(filename);
        }
    }
    return _getImageBase() + (raw.startsWith('/') ? raw : '/' + raw);
}

// 应用状态
// CHANGE: 默认视图改为 ultimo（自家产品）
const AppState = {
    products: [],
    cart: [],
    currentView: 'ultimo',  // CHANGE: 默认显示 ULTIMO（自家产品）
    userId: CONFIG.DEFAULT_USER_ID,
    lastOrderId: null,  // 最后创建的订单ID，用于显示转账信息
    lastOrderSummary: null,  // CHANGE: 最后订单的摘要信息（subtotal, shipping, total）
    lastOrderCart: null,  // CHANGE: 最后提交订单时的购物车状态，用于重新提交
    user: null,  // CHANGE: 用户信息
    token: null  // CHANGE: JWT token
};

// CHANGE: 用户认证相关函数
function getStoredAuth() {
    try {
        const token = localStorage.getItem('auth_token');
        const user = localStorage.getItem('auth_user');
        if (token && user) {
            return { token, user: JSON.parse(user) };
        }
    } catch (e) {
        console.error('获取存储的认证信息失败:', e);
    }
    return null;
}

function saveAuth(token, user) {
    try {
        localStorage.setItem('auth_token', token);
        localStorage.setItem('auth_user', JSON.stringify(user));
        AppState.token = token;
        AppState.user = user;
        AppState.userId = user.user_id;
    } catch (e) {
        console.error('保存认证信息失败:', e);
    }
}

function clearAuth() {
    try {
        localStorage.removeItem('auth_token');
        localStorage.removeItem('auth_user');
        AppState.token = null;
        AppState.user = null;
        AppState.userId = CONFIG.DEFAULT_USER_ID;
    } catch (e) {
        console.error('清除认证信息失败:', e);
    }
}

function updateUserUI() {
    const userInfo = document.getElementById('userInfo');
    const loginBtn = document.getElementById('loginBtn');
    
    // CHANGE: 添加调试日志
    console.log('🔍 [updateUserUI] AppState.user:', AppState.user, 'AppState.token:', AppState.token ? '存在' : '不存在');
    console.log('🔍 [updateUserUI] loginBtn元素:', loginBtn ? '找到' : '未找到');
    
    if (AppState.user && AppState.token) {
        // 显示用户信息
        if (userInfo) {
            userInfo.classList.remove('hidden');
            const userNameEl = document.getElementById('userName');
            const userAvatarEl = document.getElementById('userAvatar');
            if (userNameEl) {
                userNameEl.textContent = AppState.user.name || AppState.user.email || 'Usuario';
            }
            if (userAvatarEl && AppState.user.avatar_url) {
                userAvatarEl.src = AppState.user.avatar_url;
                userAvatarEl.style.display = 'block';
            } else if (userAvatarEl) {
                userAvatarEl.style.display = 'none';
            }
        }
        if (loginBtn) {
            loginBtn.classList.add('hidden');
            console.log('✅ [updateUserUI] 隐藏登录按钮（用户已登录）');
        }
    } else {
        // CHANGE: 显示登录按钮（用户未登录）
        if (userInfo) {
            userInfo.classList.add('hidden');
        }
        if (loginBtn) {
            loginBtn.classList.remove('hidden');
            console.log('✅ [updateUserUI] 显示登录按钮（用户未登录）');
        } else {
            console.warn('⚠️ [updateUserUI] 登录按钮元素不存在');
        }
    }
}

// ===== API调用函数 =====

async function apiRequest(endpoint, options = {}) {
    const url = `${CONFIG.API_BASE_URL}${endpoint}`;
    const method = options.method || 'GET';
    
    console.log(`📡 [API] ${method} ${url}`);
    if (options.body) {
        console.log('📤 请求体:', options.body);
    }
    
    try {
        // 构建请求头：只有 POST/PUT/PATCH 请求才需要 Content-Type
        const headers = {
            ...options.headers
        };
        
        // CHANGE: 添加认证token到请求头
        if (AppState.token) {
            headers['Authorization'] = `Bearer ${AppState.token}`;
        }
        
        // 只有非 GET 请求且有 body 时才设置 Content-Type
        if (method !== 'GET' && options.body) {
            headers['Content-Type'] = 'application/json';
        }
        
        const response = await fetch(url, {
            headers: headers,
            ...options
        });
        
        console.log(`📥 [API] 响应状态: ${response.status} ${response.statusText}`);
        
        const responseText = await response.text();
        console.log('📥 响应内容:', responseText.substring(0, 200)); // 只显示前200个字符
        
        // 检查响应是否是 HTML（错误页面）
        if (responseText.trim().startsWith('<!DOCTYPE') || responseText.trim().startsWith('<!doctype')) {
            console.error('❌ 服务器返回了HTML错误页面而不是JSON');
            throw new Error(`服务器错误: ${response.status} - 收到HTML响应而不是JSON`);
        }
        
        let data;
        try {
            data = JSON.parse(responseText);
        } catch (e) {
            console.error('❌ JSON解析失败:', e);
            console.error('响应内容（前500字符）:', responseText.substring(0, 500));
            throw new Error(`响应不是有效的JSON: ${response.status} ${response.statusText}`);
        }
        
        if (!response.ok) {
            console.error('❌ API错误响应:', data);
            throw new Error(`API错误: ${response.status} - ${data.error || data.message || responseText.substring(0, 100)}`);
        }
        
        console.log('✅ [API] 请求成功:', data);
        return data;
    } catch (error) {
        console.error('❌ [API] 请求失败:', error);
        console.error('错误详情:', {
            message: error.message,
            stack: error.stack,
            endpoint: url,
            method: method
        });
        showToast('Error de red, por favor intente más tarde', 'error');
        throw error;
    }
}

// Obtener lista de productos
// CHANGE: 支持 supplier 参数，用于区分自家产品和其他供应商产品；带超时避免一直 Cargando
// 无 supplier 时默认 'Cristy'（ULTIMO 页），避免后端无 supplier 时返回空列表
async function fetchProducts(supplier = null) {
    const LOAD_TIMEOUT_MS = 30000;  // ULTIMO(Cristy) 匹配 DB 可能需数秒
    const effectiveSupplier = supplier != null && supplier !== '' ? supplier : 'Cristy';
    try {
        console.log('📦 [fetchProducts] 开始获取产品列表...', `supplier=${effectiveSupplier}`);
        let url = '/products?limit=500';
        if (effectiveSupplier) {
            url += `&supplier=${encodeURIComponent(effectiveSupplier)}`;
        }
        url += '&_=' + (Date.now ? Date.now() : 0);  // 避免缓存导致产品代码/价格全部相同
        const timeoutPromise = new Promise((_, reject) => {
            setTimeout(() => reject(new Error('Tiempo de espera agotado. Compruebe la conexión o intente más tarde.')), LOAD_TIMEOUT_MS);
        });
        const result = await Promise.race([apiRequest(url), timeoutPromise]);
        console.log('📦 [fetchProducts] API响应:', result);
        
        if (result && result.success) {
            var newProducts = Array.isArray(result.data) ? result.data.slice() : [];
            console.log('✅ [fetchProducts] 成功加载 ' + newProducts.length + ' 个产品 supplier=' + effectiveSupplier);
            // CHANGE: 仅当当前视图与本次请求一致时才更新列表，避免 others 晚返回覆盖 ULTIMO 的 Cristy 列表
            var viewMatch = (effectiveSupplier === 'Cristy' && AppState.currentView === 'ultimo') || (effectiveSupplier === 'others' && AppState.currentView === 'products');
            if (AppState._hashProductForView && effectiveSupplier === 'others' && AppState.currentView === 'products') {
                var hp = AppState._hashProductForView.product;
                if (hp && !newProducts.some(function(px) { return String(px.id) === String(hp.id); })) {
                    newProducts.push(hp);
                }
                AppState._hashProductForView = null;
                AppState.products = newProducts;
                renderProducts();
                requestAnimationFrame(function() {
                    requestAnimationFrame(function() { applyProductHashAnchor(); });
                });
            } else if (viewMatch) {
                AppState.products = newProducts;
                if (AppState.products.length === 0) {
                    console.warn('⚠️ [fetchProducts] 警告: API返回成功，但产品列表为空');
                    console.info('💡 [fetchProducts] 排查: 若后端在 Render，请检查云端 DB 是否有数据、Cristy 目录/ULTIMO 条件；可在控制台试 fetchProducts("") 或 fetchProducts("others") 看是否全空');
                }
                renderProducts();
            }
        } else {
            console.error('❌ [fetchProducts] API返回错误:', result?.error || '未知错误');
            console.error('❌ [fetchProducts] 完整响应:', result);
            AppState.products = [];
            renderProducts(); // 显示空状态
            showToast('Error al cargar productos', 'error');
        }
    } catch (error) {
        console.error('❌ [fetchProducts] 异常:', error);
        console.error('❌ [fetchProducts] 错误堆栈:', error.stack);
        AppState.products = [];
        AppState._lastProductsError = error;
        renderProducts(); // 显示空状态（会判断 404 并提示启动 API 服务器）
        if (error && error.message && error.message.indexOf('404') !== -1) {
            showToast('Inicie el servidor API del carrito PWA (puerto 5000)', 'error');
        }
    }
}

// Obtener carrito
async function fetchCart() {
    try {
        // 确保 userId 有效
        const userId = AppState.userId || CONFIG.DEFAULT_USER_ID;
        if (!userId || userId <= 0) {
            console.error('❌ userId无效:', userId);
            AppState.cart = [];
            updateCartUI();
            return;
        }
        
        const result = await apiRequest(`/cart?user_id=${userId}`);
        if (result && result.success) {
            AppState.cart = result.data || [];
            console.log(`🛒 Carrito actualizado: ${AppState.cart.length} artículos`);
            updateCartUI();
        } else {
            console.error('Error al obtener carrito:', result?.error || '未知错误');
            AppState.cart = [];
            updateCartUI();
        }
    } catch (error) {
        console.error('Error al obtener carrito:', error);
        // 购物车为空是可以接受的，继续显示页面
        AppState.cart = [];
        updateCartUI();
    }
}

// ===== Modal de selección de cantidad =====
let currentProductForModal = null;

function showQuantityModal(productId) {
    console.log('📱 showQuantityModal llamado con productId:', productId);
    
    // Buscar información del producto
    const product = AppState.products.find(p => String(p.id) === String(productId));
    if (!product) {
        console.error('❌ Producto no existe:', productId);
        console.error('📦 Productos disponibles:', AppState.products.map(p => p.id));
        showToast('Error: Producto no existe', 'error');
        return;
    }
    
    console.log('✅ Producto encontrado:', product);
    currentProductForModal = product;
    console.log('✅ currentProductForModal establecido:', currentProductForModal);
    
    // Actualizar contenido del modal
    const modal = document.getElementById('quantityModal');
    if (!modal) {
        console.error('❌ Modal no encontrado');
        return;
    }
    
    const productNameEl = document.getElementById('modalProductName');
    const productPriceEl = document.getElementById('modalProductPrice');
    const quantityInput = document.getElementById('quantityInput');
    const totalPriceEl = document.getElementById('modalTotalPrice');
    
    if (!productNameEl || !productPriceEl || !quantityInput || !totalPriceEl) {
        console.error('❌ Elementos del modal no encontrados');
        return;
    }
    
    productNameEl.textContent = product.name;
    // CHANGE: 初始显示根据数量1计算的价格
    const initialPrice = calculatePriceByQuantity(product, 1);
    productPriceEl.textContent = `$${initialPrice.toFixed(2)}`;
    quantityInput.value = 1;
    updateModalTotalPrice();
    
    // Mostrar modal
    modal.classList.remove('hidden');
    modal.focus(); // 使模态框可接收键盘事件
    
    // Enfocar en el campo de entrada de cantidad
    setTimeout(() => {
        quantityInput.focus();
        quantityInput.select();
    }, 100);
}

function hideQuantityModal() {
    const modal = document.getElementById('quantityModal');
    modal.classList.add('hidden');
    currentProductForModal = null;
}

function updateModalTotalPrice() {
    if (!currentProductForModal) return;
    
    const quantityInput = document.getElementById('quantityInput');
    const totalPriceEl = document.getElementById('modalTotalPrice');
    const productPriceEl = document.getElementById('modalProductPrice');
    
    // CHANGE: 允许输入框为空，如果为空则使用1作为默认值计算价格
    const inputValue = quantityInput.value.trim();
    let quantity = parseInt(inputValue);
    
    // 如果为空或无效，使用1作为默认值（但不更新输入框，允许用户继续输入）
    if (isNaN(quantity) || inputValue === '') {
        quantity = 1;
    }
    
    // CHANGE: 根据数量计算单价
    const unitPrice = calculatePriceByQuantity(currentProductForModal, quantity);
    const total = unitPrice * quantity;
    
    productPriceEl.textContent = `$${unitPrice.toFixed(2)}`;
    totalPriceEl.textContent = `Total: $${total.toFixed(2)}`;
}

function confirmAddToCart() {
    console.log('✅ confirmAddToCart llamado');
    console.log('📦 currentProductForModal:', currentProductForModal);
    
    if (!currentProductForModal) {
        console.error('❌ currentProductForModal es null');
        console.error('📦 AppState.products:', AppState.products);
        showToast('Error: Producto no disponible', 'error');
        hideQuantityModal();
        return;
    }
    
    const quantityInput = document.getElementById('quantityInput');
    if (!quantityInput) {
        console.error('❌ quantityInput no encontrado');
        showToast('Error: Campo de cantidad no encontrado', 'error');
        return;
    }
    
    // CHANGE: 验证数量，如果为空或无效，使用1
    const inputValue = quantityInput.value.trim();
    let quantity = parseInt(inputValue);
    
    if (isNaN(quantity) || inputValue === '') {
        quantity = 1;
        quantityInput.value = quantity;
    }
    
    console.log('📊 Cantidad seleccionada:', quantity);
    
    if (quantity < 1) {
        quantity = 1;
        quantityInput.value = quantity;
        showToast('La cantidad debe ser mayor que 0', 'error');
        return;
    }
    
    if (quantity > 999) {
        quantity = 999;
        quantityInput.value = quantity;
    }
    
    if (quantity > 999) {
        showToast('La cantidad no puede exceder 999', 'error');
        return;
    }
    
    // Guardar productoID antes de cerrar modal
    const productId = currentProductForModal.id;
    console.log('🆔 ID del producto guardado:', productId);
    
    if (!productId) {
        console.error('❌ ID del producto no disponible');
        console.error('📦 currentProductForModal completo:', currentProductForModal);
        showToast('Error: ID del producto no disponible', 'error');
        return;
    }
    
    // CHANGE: 与购物车页一致的单价（按数量层级），传给后端直接采用，保证其他位置“只读结果就一致”
    const unitPrice = calculatePriceByQuantity(currentProductForModal, quantity);
    console.log('🛒 按数量层级单价:', unitPrice, 'quantity=', quantity);
    
    // Cerrar modal (esto establecerá currentProductForModal = null)
    hideQuantityModal();
    
    // Añadir al carrito，传入单价以便后端照存、不重算
    console.log('🛒 Añadiendo al carrito: productId=', productId, 'quantity=', quantity, 'price=', unitPrice);
    addToCart(productId, quantity, unitPrice);
}

// Inicializar eventos del modal de selección de cantidad
function initQuantityModal() {
    const modal = document.getElementById('quantityModal');
    const closeBtn = document.getElementById('modalCloseBtn');
    const cancelBtn = document.getElementById('modalCancelBtn');
    const confirmBtn = document.getElementById('modalConfirmBtn');
    const decreaseBtn = document.getElementById('decreaseBtn');
    const increaseBtn = document.getElementById('increaseBtn');
    const quantityInput = document.getElementById('quantityInput');
    
    // Botón de cerrar
    closeBtn.addEventListener('click', hideQuantityModal);
    cancelBtn.addEventListener('click', hideQuantityModal);
    
    // Botón de confirmar
    confirmBtn.addEventListener('click', confirmAddToCart);
    
    // Cerrar al hacer clic en el fondo
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            hideQuantityModal();
        }
    });
    
    // Cerrar con tecla ESC
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
            hideQuantityModal();
        }
    });
    
    // 数量增减按钮
    decreaseBtn.addEventListener('click', () => {
        const current = parseInt(quantityInput.value) || 1;
        if (current > 1) {
            quantityInput.value = current - 1;
            updateModalTotalPrice();
        }
    });
    
    increaseBtn.addEventListener('click', () => {
        const current = parseInt(quantityInput.value) || 1;
        if (current < 999) {
            quantityInput.value = current + 1;
            updateModalTotalPrice();
        }
    });
    
    // Cambio en el campo de entrada de cantidad
    // CHANGE: 允许用户删除数字并输入新数字，只在输入有效数字时更新价格
    quantityInput.addEventListener('input', () => {
        const inputValue = quantityInput.value.trim();
        
        // 如果输入框为空，允许保持为空（不强制设置为1）
        if (inputValue === '') {
            return; // 允许用户删除所有内容
        }
        
        // 尝试解析为数字
        let value = parseInt(inputValue);
        
        // 如果解析失败，不更新值（允许用户继续输入）
        if (isNaN(value)) {
            return;
        }
        
        // 限制范围
        if (value < 1) {
            value = 1;
            quantityInput.value = value;
        } else if (value > 999) {
            value = 999;
            quantityInput.value = value;
        }
        
        // 更新总价（只在有有效数字时）
        updateModalTotalPrice();
    });
    
    // CHANGE: 当输入框失去焦点时，验证并设置默认值
    quantityInput.addEventListener('blur', () => {
        const inputValue = quantityInput.value.trim();
        let value = parseInt(inputValue);
        
        // 如果为空或无效，设置为1
        if (isNaN(value) || inputValue === '') {
            value = 1;
            quantityInput.value = value;
        } else {
            // 限制范围
            if (value < 1) value = 1;
            if (value > 999) value = 999;
            quantityInput.value = value;
        }
        
        updateModalTotalPrice();
    });
    
    // CHANGE: 移动端键盘打开时，滚动弹窗使确认按钮可见，避免被键盘遮挡
    if ('ontouchstart' in window) {
        function scrollModalFooterIntoView() {
            const footer = modal.querySelector('.modal-footer');
            if (footer && !modal.classList.contains('hidden')) {
                footer.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            }
        }
        quantityInput.addEventListener('focus', function() {
            setTimeout(scrollModalFooterIntoView, 400);
        });
        if (window.visualViewport) {
            window.visualViewport.addEventListener('resize', function() {
                if (!modal.classList.contains('hidden') && document.activeElement === quantityInput) {
                    setTimeout(scrollModalFooterIntoView, 100);
                }
            });
        }
    }
}

// Añadir al carrito。unitPrice 可选：与购物车页一致的单价值，后端直接采用以保证各处“只读结果一致”
async function addToCart(productId, quantity = 1, unitPrice = null) {
    try {
        console.log('='.repeat(50));
        console.log(`🛒 [ADD_TO_CART] Iniciando adición de producto al carrito`);
        console.log(`   ID del Producto: ${productId}`);
        console.log(`   Cantidad: ${quantity}`);
        console.log(`   Precio (tier): ${unitPrice != null ? unitPrice : '(backend)'}`);
        console.log(`   用户ID: ${AppState.userId}`);
        console.log(`   API地址: ${CONFIG.API_BASE_URL}/cart/add`);
        
        if (!productId) {
            console.error('❌ ID del producto vacío!');
            showToast('Error: ID del producto inválido', 'error');
            return;
        }
        
        const requestBody = {
            user_id: AppState.userId,
            product_id: productId,
            quantity: quantity
        };
        if (unitPrice != null && typeof unitPrice === 'number' && unitPrice > 0) {
            requestBody.price = unitPrice;
        }
        
        console.log('📤 发送请求:', JSON.stringify(requestBody, null, 2));
        
        const result = await apiRequest('/cart/add', {
            method: 'POST',
            body: JSON.stringify(requestBody)
        });
        
        console.log('📥 API响应:', JSON.stringify(result, null, 2));
        
        if (result.success) {
            console.log('✅ API respondió exitosamente, actualizando carrito...');
            // Actualizar carrito inmediatamente
            await fetchCart();
            showToast('Producto agregado al carrito', 'success');
            console.log('✅ Producto añadido exitosamente al carrito');
        } else {
            console.error('❌ API respondió con error:', result.error);
            showToast(result.error || 'Error al agregar producto', 'error');
        }
        console.log('='.repeat(50));
    } catch (error) {
        console.error('='.repeat(50));
        console.error('❌ [ADD_TO_CART] 异常:', error);
        console.error('错误堆栈:', error.stack);
        console.error('='.repeat(50));
        showToast('Error de conexión al agregar producto', 'error');
    }
}

// Actualizar cantidad de productos en el carrito。unitPrice 可选，与 add 一致保证后端照存
async function updateCartItem(productId, quantity, unitPrice = null) {
    try {
        const body = {
            user_id: AppState.userId,
            product_id: productId,
            quantity: quantity
        };
        if (unitPrice != null && typeof unitPrice === 'number' && unitPrice > 0) {
            body.price = unitPrice;
        }
        const result = await apiRequest('/cart/update', {
            method: 'POST',
            body: JSON.stringify(body)
        });
        
        if (result.success) {
            await fetchCart();
        }
    } catch (error) {
        console.error('Error al actualizar carrito:', error);
    }
}

// Eliminar producto del carrito
async function removeFromCart(productId) {
    try {
        const result = await apiRequest('/cart/remove', {
            method: 'POST',
            body: JSON.stringify({
                user_id: AppState.userId,
                product_id: productId
            })
        });
        
        if (result.success) {
            await fetchCart();
            showToast('Producto eliminado del carrito', 'success');
        }
    } catch (error) {
        console.error('Error al eliminar producto:', error);
    }
}

// Vaciar carrito
async function clearCart(silent = false) {
    // CHANGE: 用户点击 Vaciar Carrito 已表示确认，不再弹出 confirm 避免卡顿（与 editOrder 一致）
    try {
        const result = await apiRequest('/cart/clear', {
            method: 'POST',
            body: JSON.stringify({
                user_id: AppState.userId
            })
        });
        
        if (result.success) {
            await fetchCart();
            if (!silent) {
                showToast('Carrito vaciado', 'success');
            }
        }
    } catch (error) {
        console.error('Error al vaciar carrito:', error);
    }
}

// CHANGE: 保存客户信息到localStorage
function saveCustomerInfo(customerInfo) {
    try {
        localStorage.setItem('customer_info', JSON.stringify(customerInfo));
        console.log('✅ 客户信息已保存到localStorage');
    } catch (error) {
        console.error('❌ 保存客户信息失败:', error);
    }
}

// CHANGE: 从localStorage加载客户信息
function loadCustomerInfo() {
    try {
        const savedInfo = localStorage.getItem('customer_info');
        if (savedInfo) {
            return JSON.parse(savedInfo);
        }
    } catch (error) {
        console.error('❌ 加载客户信息失败:', error);
    }
    return null;
}

// CHANGE: 显示客户信息表单（用于修改后重新提交订单）
function showCustomerInfoModalForResubmit() {
    const modal = document.getElementById('customerInfoModal');
    if (!modal) {
        console.error('❌ 客户信息模态框未找到');
        return;
    }
    
    // CHANGE: 设置重新提交模式标志
    modal.dataset.editMode = 'resubmit';
    
    // CHANGE: 加载已保存的客户信息
    const savedInfo = loadCustomerInfo();
    const form = document.getElementById('customerInfoForm');
    const modalTitle = modal.querySelector('.modal-header h3');
    const modalDescription = modal.querySelector('.modal-body p');
    const submitBtn = document.getElementById('customerInfoSubmitBtn');
    
    if (form) {
        if (savedInfo) {
            // 填充已保存的信息
            document.getElementById('cedula').value = savedInfo.cedula || '';
            document.getElementById('nombres').value = savedInfo.nombres || '';
            document.getElementById('direccion').value = savedInfo.direccion || '';
            document.getElementById('provincia').value = savedInfo.provincia || '';
            document.getElementById('ciudad').value = savedInfo.ciudad || '';
            document.getElementById('whatsapp').value = savedInfo.whatsapp || '';
            document.getElementById('email').value = savedInfo.email || '';
            console.log('✅ 已加载保存的客户信息');
        } else {
            // 清空表单
            form.reset();
        }
    }
    
    // CHANGE: 更新标题和按钮文本为重新提交模式
    if (modalTitle) {
        modalTitle.textContent = '✏️ Editar Datos y Reenviar Pedido';
    }
    if (modalDescription) {
        modalDescription.textContent = 'Modifique sus datos personales. Después de guardar, el pedido se reenviará automáticamente con los nuevos datos.';
    }
    if (submitBtn) {
        submitBtn.textContent = 'Guardar y Reenviar Pedido';
    }
    
    // 显示模态框
    modal.classList.remove('hidden');
    
    // 聚焦第一个输入框
    setTimeout(() => {
        const firstInput = document.getElementById('cedula');
        if (firstInput) {
            firstInput.focus();
        }
    }, 100);
}

// CHANGE: 显示客户信息表单（支持编辑模式）
function showCustomerInfoModal(isEditMode = false) {
    const modal = document.getElementById('customerInfoModal');
    if (!modal) {
        console.error('❌ 客户信息模态框未找到');
        return;
    }
    
    // CHANGE: 设置编辑模式标志
    modal.dataset.editMode = isEditMode ? 'true' : 'false';
    
    // CHANGE: 加载已保存的客户信息
    const savedInfo = loadCustomerInfo();
    const form = document.getElementById('customerInfoForm');
    const modalTitle = modal.querySelector('.modal-header h3');
    const modalDescription = modal.querySelector('.modal-body p');
    const submitBtn = document.getElementById('customerInfoSubmitBtn');
    
    if (form) {
        if (savedInfo) {
            // 填充已保存的信息
            document.getElementById('cedula').value = savedInfo.cedula || '';
            document.getElementById('nombres').value = savedInfo.nombres || '';
            document.getElementById('direccion').value = savedInfo.direccion || '';
            document.getElementById('provincia').value = savedInfo.provincia || '';
            document.getElementById('ciudad').value = savedInfo.ciudad || '';
            document.getElementById('whatsapp').value = savedInfo.whatsapp || '';
            document.getElementById('email').value = savedInfo.email || '';
            console.log('✅ 已加载保存的客户信息');
        } else {
            // 清空表单
            form.reset();
        }
    }
    
    // CHANGE: 根据模式更新标题和按钮文本
    if (isEditMode) {
        if (modalTitle) {
            modalTitle.textContent = '✏️ Editar Datos del Cliente';
        }
        if (modalDescription) {
            modalDescription.textContent = 'Modifique sus datos personales. Los cambios se guardarán automáticamente.';
        }
        if (submitBtn) {
            submitBtn.textContent = 'Guardar Cambios';
        }
    } else {
        if (modalTitle) {
            modalTitle.textContent = '📋 Datos para el pedido';
        }
        if (modalDescription) {
            modalDescription.textContent = 'Por favor, complete los siguientes datos para realizar su pedido:';
        }
        if (submitBtn) {
            submitBtn.textContent = 'Confirmar Pedido';
        }
    }
    
    // 显示模态框
    modal.classList.remove('hidden');
    
    // 聚焦第一个输入框
    setTimeout(() => {
        const firstInput = document.getElementById('cedula');
        if (firstInput) {
            firstInput.focus();
        }
    }, 100);
}

// 隐藏客户信息表单
function hideCustomerInfoModal() {
    const modal = document.getElementById('customerInfoModal');
    if (modal) {
        modal.classList.add('hidden');
    }
}

// CHANGE: 提交订单（带客户信息）或仅保存信息（编辑模式）或重新提交订单
async function submitOrderWithCustomerInfo() {
    const modal = document.getElementById('customerInfoModal');
    const isEditMode = modal && modal.dataset.editMode === 'true';
    const isResubmitMode = modal && modal.dataset.editMode === 'resubmit';
    
    const form = document.getElementById('customerInfoForm');
    if (!form) {
        showToast('Error: formulario no encontrado', 'error');
        return;
    }
    
    // 验证表单
    if (!form.checkValidity()) {
        form.reportValidity();
        return;
    }
    
    // 收集客户信息
    const customerInfo = {
        cedula: document.getElementById('cedula').value.trim(),
        nombres: document.getElementById('nombres').value.trim(),
        direccion: document.getElementById('direccion').value.trim(),
        provincia: document.getElementById('provincia').value.trim(),
        ciudad: document.getElementById('ciudad').value.trim(),
        whatsapp: document.getElementById('whatsapp').value.trim(),
        email: document.getElementById('email').value.trim() || ''
    };
    
    // 验证必填字段
    if (!customerInfo.cedula || !customerInfo.nombres || !customerInfo.direccion || 
        !customerInfo.provincia || !customerInfo.ciudad || !customerInfo.whatsapp) {
        showToast('Por favor, complete todos los campos obligatorios', 'error');
        return;
    }
    
    // CHANGE: 保存客户信息到localStorage
    saveCustomerInfo(customerInfo);
    
    // CHANGE: 如果是编辑模式，只保存信息不提交订单
    if (isEditMode) {
        showToast('✅ Datos guardados correctamente', 'success');
        hideCustomerInfoModal();
        // CHANGE: 刷新转账信息页面以显示更新的客户信息
        if (document.getElementById('paymentSection') && !document.getElementById('paymentSection').classList.contains('hidden')) {
            fetchBankInfo();
        }
        return;
    }
    
    // CHANGE: 如果是重新提交模式，恢复购物车并重新提交订单
    if (isResubmitMode) {
        hideCustomerInfoModal();
        
        // 检查是否有保存的购物车状态
        if (!AppState.lastOrderCart || AppState.lastOrderCart.length === 0) {
            showToast('❌ No se puede reenviar el pedido: el carrito anterior no está disponible', 'error');
            return;
        }
        
        // 恢复购物车状态
        AppState.cart = JSON.parse(JSON.stringify(AppState.lastOrderCart));
        
        // 显示加载提示
        showToast('Reenviando pedido con los nuevos datos...', 'info');
        
        // 计算订单摘要
        const subtotal = AppState.cart.reduce((sum, item) => {
            const product = AppState.products.find(p => String(p.id) === String(item.product_id));
            const unitPrice = calculatePriceByQuantity(product || item, item.quantity);
            return sum + (unitPrice * item.quantity);
        }, 0);
        const shipping = CONFIG.SHIPPING_COST;
        const total = subtotal + shipping;
        
        try {
            // CHANGE: 发送 CARRITO 计算的小计/总计，保证 PEDIDOS 与 CARRITO 一致
            const result = await apiRequest('/checkout', {
                method: 'POST',
                body: JSON.stringify({
                    user_id: AppState.userId,
                    customer_info: customerInfo,
                    subtotal: subtotal,
                    total: total
                })
            });
            
            if (result.success) {
                showToast(`¡Pedido reenviado! Nuevo número de pedido: ${result.data.order_id}`, 'success');
                await fetchCart();
                // 更新订单信息
                AppState.lastOrderId = result.data.order_id;
                AppState.lastOrderSummary = {
                    subtotal: subtotal,
                    shipping: shipping,
                    total: total
                };
                // 更新购物车状态
                AppState.lastOrderCart = JSON.parse(JSON.stringify(AppState.cart));
                // 刷新转账信息页面
                fetchBankInfo();
            } else {
                showToast('Error al reenviar el pedido', 'error');
            }
        } catch (error) {
            console.error('重新提交订单失败:', error);
            showToast('Error al reenviar el pedido', 'error');
        }
        return;
    }
    
    // 隐藏模态框
    hideCustomerInfoModal();
    
    // CHANGE: 在提交订单前计算订单摘要（避免购物车被清空后无法计算）
    const subtotal = AppState.cart.reduce((sum, item) => {
        const product = AppState.products.find(p => String(p.id) === String(item.product_id));
        const unitPrice = calculatePriceByQuantity(product || item, item.quantity);
        return sum + (unitPrice * item.quantity);
    }, 0);
    const shipping = CONFIG.SHIPPING_COST;
    const total = subtotal + shipping;
    
    // 显示加载提示
    showToast('Procesando pedido...', 'info');
    
    try {
        // CHANGE: 发送 CARRITO 计算的小计/总计，保证 PEDIDOS 与 CARRITO 一致
        const result = await apiRequest('/checkout', {
            method: 'POST',
            body: JSON.stringify({
                user_id: AppState.userId,
                customer_info: customerInfo,
                subtotal: subtotal,
                total: total
            })
        });
        
        if (result.success) {
            showToast(`¡Pedido realizado! Número de pedido: ${result.data.order_id}`, 'success');
            await fetchCart();
            // CHANGE: 保存订单ID、订单摘要和购物车状态，用于显示转账信息和重新提交
            AppState.lastOrderId = result.data.order_id;
            AppState.lastOrderSummary = {
                subtotal: subtotal,
                shipping: shipping,
                total: total
            };
            // CHANGE: 保存购物车状态，用于重新提交订单
            AppState.lastOrderCart = JSON.parse(JSON.stringify(AppState.cart));
            // 显示转账信息视图
            switchView('payment');
        }
    } catch (error) {
        console.error('提交订单失败:', error);
        showToast('Error al realizar el pedido', 'error');
    }
}

// 提交订单（原函数，现在显示客户信息表单）
async function checkout() {
    if (AppState.cart.length === 0) {
        showToast('El carrito está vacío', 'error');
        return;
    }
    
    // 显示客户信息表单
    showCustomerInfoModal();
}

// 获取订单列表
async function fetchOrders() {
    try {
        const result = await apiRequest(`/orders?user_id=${AppState.userId}`);
        if (result.success) {
            renderOrders(result.data || []);
        } else {
            showToast('Error al cargar pedidos', 'error');
        }
    } catch (error) {
        console.error('获取订单列表失败:', error);
        showToast('Error al cargar pedidos', 'error');
    }
}

// 渲染订单列表
function renderOrders(orders) {
    const ordersList = document.getElementById('ordersList');
    
    if (orders.length === 0) {
        ordersList.innerHTML = `
            <div style="text-align: center; padding: 3rem 1rem; color: var(--text-light);">
                <div style="font-size: 4rem; margin-bottom: 1rem;">📋</div>
                <h3 style="font-size: 1.2rem; margin-bottom: 0.5rem;">No hay pedidos</h3>
                <p>Comience a comprar para ver sus pedidos aquí</p>
            </div>
        `;
        return;
    }
    
    ordersList.innerHTML = orders.map(order => {
        const date = new Date(order.created_at);
        const formattedDate = date.toLocaleDateString('es-ES', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
        
        const statusText = {
            'pending': '⏳ Pendiente',
            'confirmed': '✅ Confirmado',
            'processing': '🔄 Procesando',
            'shipped': '🚚 Enviado',
            'completed': '✅ Completado',
            'cancelled': '❌ Cancelado'
        }[order.status] || order.status;
        
        // 转义 order.id 以防止 XSS
        const safeOrderId = String(order.id).replace(/'/g, "\\'").replace(/"/g, '&quot;');
        
        // CHANGE: API已经返回包含运费的正确总价，直接使用
        const displayTotal = order.total_amount;
        
        return `
            <div class="order-card">
                <div class="order-card-content" onclick="viewOrderDetail('${safeOrderId}')">
                    <div class="order-header">
                        <div class="order-id">Pedido: ${order.id}</div>
                        <div class="order-status">${statusText}</div>
                    </div>
                    <div class="order-info">
                        <div class="order-total">Total: $${displayTotal.toFixed(2)}</div>
                        <div class="order-date">${formattedDate}</div>
                    </div>
                </div>
                ${order.status === 'pending' ? `
                    <div class="order-actions-bar">
                        <button class="btn btn-secondary btn-edit-order" onclick="event.stopPropagation(); editOrder('${safeOrderId}')" title="Editar pedido">
                            ✏️ Editar Pedido
                        </button>
                    </div>
                ` : ''}
            </div>
        `;
    }).join('');
}

// 编辑订单 - 将订单商品添加到购物车
async function editOrder(orderId) {
    try {
        // 先获取订单详情
        const result = await apiRequest(`/orders/${orderId}?user_id=${AppState.userId}`);
        if (!result.success) {
            showToast('Error al cargar el pedido', 'error');
            return;
        }
        
        const order = result.data;
        
        // 检查订单状态
        if (order.status !== 'pending') {
            showToast('Solo se pueden editar pedidos pendientes', 'error');
            return;
        }
        
        // CHANGE: 用户点击 EDITAR PEDIDO 已表示确认，不再弹出 confirm 避免卡顿（浏览器安全机制）
        // 清空当前购物车（静默模式，不显示确认对话框）
        await clearCart(true);
        
        // 将订单中的商品添加到购物车
        let addedCount = 0;
        let failedCount = 0;
        
        for (const item of order.items) {
            try {
                // 直接调用 API，不显示 toast
                const result = await apiRequest('/cart/add', {
                    method: 'POST',
                    body: JSON.stringify({
                        user_id: AppState.userId,
                        product_id: item.product_id,
                        quantity: item.quantity
                    })
                });
                
                if (result.success) {
                    addedCount++;
                } else {
                    failedCount++;
                    console.error(`Error al agregar producto ${item.product_id}:`, result.error);
                }
            } catch (error) {
                failedCount++;
                console.error(`Error al agregar producto ${item.product_id}:`, error);
            }
        }
        
        // 更新购物车UI
        await fetchCart();
        
        if (addedCount > 0) {
            if (failedCount > 0) {
                showToast(`Pedido cargado: ${addedCount} producto(s) agregado(s), ${failedCount} fallido(s).`, 'success');
            } else {
                showToast(`Pedido cargado al carrito. ${addedCount} producto(s) agregado(s).`, 'success');
            }
            // 切换到购物车视图
            switchView('cart');
        } else {
            showToast('Error al cargar productos del pedido', 'error');
        }
    } catch (error) {
        console.error('编辑订单失败:', error);
        showToast('Error al editar el pedido', 'error');
    }
}

// 查看订单详情
async function viewOrderDetail(orderId) {
    try {
        const result = await apiRequest(`/orders/${orderId}?user_id=${AppState.userId}`);
        if (result.success) {
            renderOrderDetail(result.data);
            switchView('order-detail');
        } else {
            showToast('Error al cargar el pedido', 'error');
        }
    } catch (error) {
        console.error('获取订单详情失败:', error);
        showToast('Error al cargar el pedido', 'error');
    }
}

// 渲染订单详情
function renderOrderDetail(order) {
    const orderDetailContent = document.getElementById('orderDetailContent');
    
    const date = new Date(order.created_at);
    const formattedDate = date.toLocaleDateString('es-ES', {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
    
    const statusText = {
        'pending': '⏳ Pendiente',
        'confirmed': '✅ Confirmado',
        'processing': '🔄 Procesando',
        'shipped': '🚚 Enviado',
        'completed': '✅ Completado',
        'cancelled': '❌ Cancelado'
    }[order.status] || order.status;
    
    const itemsHtml = order.items.map(item => `
        <div class="order-item">
            <div class="order-item-info">
                <div class="order-item-name">${item.name}</div>
                <div class="order-item-details">
                    <span>Cantidad: ${item.quantity}</span>
                    <span>Precio: $${item.price.toFixed(2)}</span>
                </div>
            </div>
            <div class="order-item-subtotal">$${(item.subtotal).toFixed(2)}</div>
        </div>
    `).join('');
    
    // CHANGE: 计算商品小计和运费
    const subtotal = order.items.reduce((sum, item) => sum + (item.subtotal || 0), 0);
    const shipping = CONFIG.SHIPPING_COST;
    // CHANGE: 总是使用 subtotal + shipping 作为总价，修复旧订单计算错误
    // 旧订单的 total_amount 可能不包含运费，所以直接计算正确的总价
    const total = subtotal + shipping;
    
    orderDetailContent.innerHTML = `
        <div class="order-detail-card">
            <div class="order-detail-header">
                <h3>Pedido: ${order.order_id}</h3>
                <div class="order-status-badge">${statusText}</div>
            </div>
            <div class="order-detail-date">Fecha: ${formattedDate}</div>
            <div class="order-items-list">
                <h4>Productos:</h4>
                ${itemsHtml}
            </div>
            <div class="order-detail-summary">
                <div class="summary-row">
                    <span>Subtotal:</span>
                    <span>$${subtotal.toFixed(2)}</span>
                </div>
                <div class="summary-row">
                    <span>Envío:</span>
                    <span style="font-weight: 500;">$${shipping.toFixed(2)}</span>
                </div>
                <div class="summary-row total">
                    <span>Total:</span>
                    <span>$${total.toFixed(2)}</span>
                </div>
            </div>
            ${order.status === 'pending' ? `
                <div class="order-actions">
                    <button class="btn btn-primary" onclick="viewPaymentInfo('${order.order_id}')">
                        Ver Información de Transferencia
                    </button>
                </div>
            ` : ''}
        </div>
    `;
}

// 获取转账信息
async function fetchBankInfo() {
    try {
        const result = await apiRequest('/payment/bank-info');
        if (result.success) {
            // CHANGE: 调试日志 - 确认Telegram链接
            console.log('📱 接收到的Telegram链接:', result.data.customer_service?.telegram);
            renderBankInfo(result.data);
        } else {
            showToast('Error al cargar información de transferencia', 'error');
        }
    } catch (error) {
        console.error('获取转账信息失败:', error);
        showToast('Error al cargar información de transferencia', 'error');
    }
}

// 获取银行logo路径
function getBankLogoPath(bankName) {
    // 银行名称到logo文件的映射
    const bankLogoMap = {
        'Banco Pichincha': 'banco-pichincha.png',
        'Banco del Pacífico': 'banco-del-pacifico.png',
        'Banco Guayaquil': 'banco-guayaquil.png',
        'Produbanco (Grupo Promerica)': 'produbanco.png'
    };
    
    const logoFileName = bankLogoMap[bankName] || 'default-bank.png';
    return `assets/bank-logos/${logoFileName}`;
}

// 渲染转账信息
function renderBankInfo(bankInfo) {
    const paymentContent = document.getElementById('paymentContent');
    
    const banksHtml = bankInfo.banks.map(bank => {
        const logoPath = getBankLogoPath(bank.name);
        return `
        <div class="bank-card">
            <div class="bank-name">
                <img src="${logoPath}" alt="${bank.name}" class="bank-logo" onerror="this.style.display='none';">
            </div>
            <div class="bank-details">
                <div class="bank-detail-row">
                    <span class="bank-label">Tipo:</span>
                    <span class="bank-value">${bank.type}</span>
                </div>
                <div class="bank-detail-row">
                    <span class="bank-label">Número:</span>
                    <span class="bank-value">${bank.number}</span>
                </div>
                <div class="bank-detail-row">
                    <span class="bank-label">Nombre:</span>
                    <span class="bank-value">${bank.account_name}</span>
                </div>
                <div class="bank-detail-row">
                    <span class="bank-label">I.C.:</span>
                    <span class="bank-value">${bank.id_number}</span>
                </div>
            </div>
        </div>
    `;
    }).join('');
    
    // CHANGE: 加载客户信息用于显示
    const customerInfo = loadCustomerInfo();
    
    // CHANGE: 在转账信息页面顶部显示客户资料
    const customerInfoHtml = customerInfo ? `
        <div class="customer-info-card" style="background: white; border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
            <h3 style="margin-top: 0; margin-bottom: 1rem; color: var(--text-color, #333); font-size: 1.2rem; font-weight: 600;">👤 Datos del Cliente</h3>
            <div class="customer-info-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid #eee;">
                <span style="color: var(--text-color, #666); font-weight: 500;">Cédula/RUC:</span>
                <span style="font-weight: 500;">${customerInfo.cedula || 'N/A'}</span>
            </div>
            <div class="customer-info-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid #eee;">
                <span style="color: var(--text-color, #666); font-weight: 500;">Nombres:</span>
                <span style="font-weight: 500;">${customerInfo.nombres || 'N/A'}</span>
            </div>
            <div class="customer-info-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid #eee;">
                <span style="color: var(--text-color, #666); font-weight: 500;">Dirección:</span>
                <span style="font-weight: 500; text-align: right; max-width: 60%;">${customerInfo.direccion || 'N/A'}</span>
            </div>
            <div class="customer-info-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid #eee;">
                <span style="color: var(--text-color, #666); font-weight: 500;">Provincia:</span>
                <span style="font-weight: 500;">${customerInfo.provincia || 'N/A'}</span>
            </div>
            <div class="customer-info-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid #eee;">
                <span style="color: var(--text-color, #666); font-weight: 500;">Ciudad:</span>
                <span style="font-weight: 500;">${customerInfo.ciudad || 'N/A'}</span>
            </div>
            <div class="customer-info-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid #eee;">
                <span style="color: var(--text-color, #666); font-weight: 500;">WhatsApp:</span>
                <span style="font-weight: 500;">${customerInfo.whatsapp || 'N/A'}</span>
            </div>
            ${customerInfo.email ? `
            <div class="customer-info-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0;">
                <span style="color: var(--text-color, #666); font-weight: 500;">E-Mail:</span>
                <span style="font-weight: 500;">${customerInfo.email}</span>
            </div>
            ` : ''}
        </div>
    ` : '';
    
    // CHANGE: 在转账信息页面顶部显示订单摘要（账单）
    const orderSummaryHtml = AppState.lastOrderSummary ? `
        <div class="order-summary-card" style="background: white; border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
            <h3 style="margin-top: 0; margin-bottom: 1rem; color: var(--text-color, #333); font-size: 1.2rem; font-weight: 600;">📋 Resumen del Pedido</h3>
            <div class="summary-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid #eee;">
                <span style="color: var(--text-color, #666);">Subtotal:</span>
                <span style="font-weight: 500;">$${AppState.lastOrderSummary.subtotal.toFixed(2)}</span>
            </div>
            <div class="summary-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid #eee;">
                <span style="color: var(--text-color, #666);">Envío:</span>
                <span style="font-weight: 500;">$${AppState.lastOrderSummary.shipping.toFixed(2)}</span>
            </div>
            <div class="summary-row total" style="display: flex; justify-content: space-between; padding: 0.75rem 0; margin-top: 0.5rem; border-top: 2px solid var(--primary-color, #4CAF50);">
                <span style="font-size: 1.1rem; font-weight: 600; color: var(--text-color, #333);">Total:</span>
                <span style="font-size: 1.2rem; font-weight: 700; color: var(--primary-color, #4CAF50);">$${AppState.lastOrderSummary.total.toFixed(2)}</span>
            </div>
        </div>
    ` : '';
    
    paymentContent.innerHTML = `
        ${customerInfoHtml}
        ${orderSummaryHtml}
        <div class="payment-info-card">
            <div class="payment-message">
                <p>${bankInfo.message}</p>
            </div>
            <div class="banks-list">
                ${banksHtml}
            </div>
            <div class="payment-footer" style="display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; margin-top: 1.5rem; flex-wrap: wrap;">
                ${AppState.lastOrderId ? `
                    <div class="order-reference" style="flex: 1; min-width: 200px;">
                        <p style="margin-bottom: 0.5rem;"><strong>Número de Pedido:</strong> ${AppState.lastOrderId}</p>
                        <p style="margin: 0 0 0.75rem 0; color: var(--text-light, #666); font-size: 0.9rem;">Por favor, incluya este número al realizar la transferencia.</p>
                        <button onclick="copyOrderNumber('${AppState.lastOrderId}')" class="btn btn-primary" style="width: 100%; max-width: 300px; padding: 0.6rem 1rem; font-size: 0.9rem; display: flex; align-items: center; justify-content: center; gap: 0.5rem;">
                            <span>📋</span>
                            <span>COPIAR NUMERO DE PEDIDO</span>
                        </button>
                    </div>
                ` : '<div style="flex: 1;"></div>'}
                <div class="customer-service" style="flex-shrink: 0;">
                    <h4 style="margin-bottom: 0.5rem; margin-top: 0;">Servicio al Cliente:</h4>
                    <div class="service-links" style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
                        <a href="${bankInfo.customer_service.whatsapp}" target="_blank" class="btn btn-secondary" style="white-space: nowrap;">
                            <img src="assets/social-logos/whatsapp.png" alt="WhatsApp" class="social-logo" onerror="this.style.display='none'; this.nextSibling.style.display='inline';">
                            <span class="social-fallback" style="display: none;">📱</span> WhatsApp
                        </a>
                        <a href="${bankInfo.customer_service.telegram}" target="_blank" class="btn btn-secondary" style="white-space: nowrap;" onclick="console.log('🔗 Telegram链接:', '${bankInfo.customer_service.telegram}');">
                            <img src="assets/social-logos/telegram.png" alt="Telegram" class="social-logo" onerror="this.style.display='none'; this.nextSibling.style.display='inline';">
                            <span class="social-fallback" style="display: none;">💬</span> Telegram
                        </a>
                    </div>
                </div>
            </div>
        </div>
    `;
}

// 查看转账信息（从订单详情）
function viewPaymentInfo(orderId) {
    AppState.lastOrderId = orderId;
    switchView('payment');
}

// 复制订单号到剪贴板
function copyOrderNumber(orderId) {
    if (!orderId) {
        console.error('❌ 订单号为空');
        return;
    }
    
    // 使用 Clipboard API 复制订单号
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(orderId).then(() => {
            // 显示成功提示
            showNotification('✅ N.º de pedido copiado: ' + orderId, 'success');
        }).catch(err => {
            console.error('❌ 复制失败:', err);
            // 回退方案：使用传统方法
            fallbackCopyToClipboard(orderId);
        });
    } else {
        // 回退方案：使用传统方法
        fallbackCopyToClipboard(orderId);
    }
}

// 回退复制方法（兼容旧浏览器）
function fallbackCopyToClipboard(text) {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.left = '-999999px';
    textArea.style.top = '-999999px';
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    
    try {
        const successful = document.execCommand('copy');
        if (successful) {
            showNotification('✅ N.º de pedido copiado: ' + text, 'success');
        } else {
            showNotification('❌ No se pudo copiar. Copie el número manualmente.', 'error');
        }
    } catch (err) {
        console.error('❌ 复制失败:', err);
        showNotification('❌ No se pudo copiar. Copie el número manualmente.', 'error');
    } finally {
        document.body.removeChild(textArea);
    }
}

// 显示通知（如果不存在则创建简单的通知函数）
function showNotification(message, type = 'info') {
    // 检查是否已有通知系统
    if (typeof window.showToast === 'function') {
        window.showToast(message, type);
        return;
    }
    
    // 简单的通知实现
    const notification = document.createElement('div');
    notification.textContent = message;
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 1rem 1.5rem;
        background: ${type === 'success' ? '#4CAF50' : type === 'error' ? '#f44336' : '#2196F3'};
        color: white;
        border-radius: 4px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.2);
        z-index: 10000;
        font-size: 0.9rem;
        max-width: 300px;
        animation: slideIn 0.3s ease-out;
    `;
    
    // 添加动画样式（如果不存在）
    if (!document.getElementById('notification-styles')) {
        const style = document.createElement('style');
        style.id = 'notification-styles';
        style.textContent = `
            @keyframes slideIn {
                from {
                    transform: translateX(100%);
                    opacity: 0;
                }
                to {
                    transform: translateX(0);
                    opacity: 1;
                }
            }
            @keyframes slideOut {
                from {
                    transform: translateX(0);
                    opacity: 1;
                }
                to {
                    transform: translateX(100%);
                    opacity: 0;
                }
            }
        `;
        document.head.appendChild(style);
    }
    
    document.body.appendChild(notification);
    
    // 3秒后自动移除
    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease-out';
        setTimeout(() => {
            if (notification.parentNode) {
                notification.parentNode.removeChild(notification);
            }
        }, 300);
    }, 3000);
}

// 导出函数到全局作用域
window.viewOrderDetail = viewOrderDetail;
window.viewPaymentInfo = viewPaymentInfo;
window.editOrder = editOrder;
window.copyOrderNumber = copyOrderNumber;

// ===== UI渲染函数 =====

// Renderizar lista de productos
// CHANGE: 网页展示用产品代码，去掉 ._AI / ._Al 等后缀，便于阅读
function displayProductCode(code) {
    if (code == null || code === '') return '';
    var s = String(code).trim();
    return s.replace(/\._A[Ii]\s*$/i, '').trim() || s;
}

function renderProducts() {
    const grid = document.getElementById('productsGrid');
    
    if (!grid) {
        console.error('❌ [renderProducts] 找不到 productsGrid 元素');
        return;
    }
    
    console.log(`🎨 [renderProducts] 开始渲染，产品数量: ${AppState.products.length}`);
    // CHANGE: 只要数据库有资料就显示产品；无图或图失败用占位图
    const productsToRender = AppState.products.filter(function(p) { return p && (p.id || p.name); });
    if (productsToRender.length === 0) {
        console.warn('⚠️ [renderProducts] 无产品，显示空状态');
        var err = AppState._lastProductsError;
        var is404 = err && err.message && String(err.message).indexOf('404') !== -1;
        var hintHtml = is404
            ? '<p style="color: var(--text-light); font-size: 1rem; margin-top: 0.5rem;">Inicie el servidor API del carrito PWA (puerto 5000).</p>'
            : '<p style="color: var(--text-light); font-size: 1.1rem;">Pronto agregaremos nuevos productos</p>';
        grid.innerHTML = `
            <div class="empty-state" style="grid-column: 1 / -1; text-align: center; padding: 4rem 2rem;">
                <div style="font-size: 5rem; margin-bottom: 1.5rem; opacity: 0.6; animation: bounce 2s ease-in-out infinite;">📦</div>
                <h3 style="font-size: 1.5rem; color: var(--text-color); margin-bottom: 0.5rem; font-weight: 600;">No hay productos disponibles</h3>
                ${hintHtml}
                <button class="btn btn-primary" onclick="location.reload()" style="margin-top: 1rem;">🔄 Recargar</button>
            </div>
        `;
        return;
    }
    
    var placeholderSvg = typeof PRODUCT_PLACEHOLDER_SVG !== 'undefined' ? PRODUCT_PLACEHOLDER_SVG : '';
    grid.innerHTML = productsToRender.map((product, index) => {
        // CHANGE: 确保每条卡片用当前项数据，避免闭包/缓存导致全部显示同一产品
        const p = product && typeof product === 'object' ? product : {};
        const safeProductId = String(p.id != null ? p.id : '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
        // CHANGE: product_code 供 Telegram/WhatsApp 链接 #/product/2202._AI 直达匹配
        const productCode = (p.product_code != null && p.product_code !== '') ? String(p.product_code) : safeProductId;
        const safeProductCode = productCode.replace(/'/g, "\\'").replace(/"/g, '&quot;');
        // CHANGE: 默认批量价，无批量价用批发价；不显示 Precio Unidad 标签
        const displayPrice = (p.bulk_price && p.bulk_price > 0)
            ? p.bulk_price
            : (p.wholesale_price && p.wholesale_price > 0
                ? p.wholesale_price
                : (p.price || 0));
        const priceLabel = (p.bulk_price && p.bulk_price > 0)
            ? 'Precio Bulto'
            : (p.wholesale_price && p.wholesale_price > 0
                ? 'Precio Mayoreo'
                : '');
        
        // CHANGE: 有图用 API URL 或前端回退拼 Pages URL，无图用占位图；图加载失败时 handleImageError 换占位图不隐藏卡片
        const rawPath = p.image_path || '';
        const hasImage = rawPath && String(rawPath).trim() && !rawPath.includes('data:image');
        const imageSrc = hasImage ? _resolveImageSrc(rawPath) : (placeholderSvg || '');
        const safeImagePath = (rawPath || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
        const safeImageSrc = (imageSrc || '').replace(/"/g, '&quot;').replace(/'/g, "\\'");
        return `
        <div class="product-card" data-product-id="${safeProductId}" data-product-code="${safeProductCode}" data-image-path="${safeImagePath || ''}">
            <div class="product-image-wrapper">
                <img src="${safeImageSrc}" 
                     alt="${(p.name || '').replace(/"/g, '&quot;')}" 
                     class="product-image"
                     data-image-src="${safeImageSrc}"
                     onclick="showImageModal('${safeImageSrc}')"
                     style="cursor: pointer;"
                     onerror="handleImageError(this);">
            </div>
            <div class="product-info">
                <div class="product-code">${(displayProductCode(p.product_code || p.id || '') || '').replace(/"/g, '&quot;')}</div>
                <div class="product-name">${(p.name || p.product_code || p.id || '').replace(/"/g, '&quot;')}</div>
                <div class="product-price">
                    ${priceLabel ? `<div class="price-label">${priceLabel}:</div>` : ''}
                    <div class="price-amount">${displayPrice > 0 ? '$' + displayPrice.toFixed(2) : 'Consultar precio'}</div>
                </div>
                <div class="product-actions">
                    <button class="btn btn-primary add-to-cart-btn" data-product-id="${safeProductId}">
                        Agregar al Carrito
                    </button>
                </div>
            </div>
        </div>
    `;
    }).join('');
    
    console.log(`✅ [renderProducts] 成功渲染 ${productsToRender.length} 个产品（共 ${AppState.products.length} 个）`);
    
    // Vincular eventos de clic de botones (usar delegación de eventos, más confiable)
    const addToCartButtons = document.querySelectorAll('.add-to-cart-btn');
    console.log(`🔗 [renderProducts] 找到 ${addToCartButtons.length} 个"添加到购物车"按钮`);
    addToCartButtons.forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            const productId = this.getAttribute('data-product-id');
            console.log('🖱️ Botón clickeado, ID del Producto:', productId);
            if (productId) {
                // 显示数量选择模态框
                showQuantityModal(productId);
            } else {
                console.error('❌ ID del producto inválido:', productId);
            }
        });
    });
    
    console.log(`✅ Renderizados ${AppState.products.length} productos, vinculados ${addToCartButtons.length} botones`);

    // CHANGE: Telegram/WhatsApp 链接 #/product/2202._AI 或 #/product/18bf4405 直达：渲染后尝试滚动到该产品
    var anchorResult = applyProductHashAnchor();
    if (anchorResult && !anchorResult.applied && anchorResult.segment) {
        fetchSingleProductForHash(anchorResult.segment);
    }
}

// CHANGE: 解析 location.hash 中的 #/product/<id|code>，滚动到对应产品卡片并高亮；未找到时返回 { applied: false, segment } 以便请求单产品（Telegram 展示码直达）
function applyProductHashAnchor() {
    var hash = (typeof location !== 'undefined' && location.hash) ? location.hash.trim() : '';
    if (!hash) return null;
    var segment = '';
    if (hash.indexOf('#/product/') === 0) {
        segment = hash.replace('#/product/', '').replace(/^\/+|\/+$/g, '').trim();
    } else if (hash.indexOf('#/pwa_cart/products/') === 0) {
        segment = hash.replace('#/pwa_cart/products/', '').replace(/^\/+|\/+$/g, '').trim();
    } else if (hash.indexOf('#/products/') === 0) {
        segment = hash.replace('#/products/', '').replace(/^\/+|\/+$/g, '').trim();
    }
    if (!segment) return null;
    function norm(s) {
        if (!s) return '';
        var t = s.trim().toLowerCase();
        return t.replace(/\._al$/i, '._ai');
    }
    var grid = document.getElementById('productsGrid');
    if (!grid) return { applied: false, segment: segment };
    var cards = grid.querySelectorAll('.product-card[data-product-id], .product-card[data-product-code]');
    for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var id = (card.getAttribute('data-product-id') || '').trim();
        var code = (card.getAttribute('data-product-code') || '').trim();
        if (id === segment || code === segment || norm(id) === norm(segment) || norm(code) === norm(segment)) {
            card.classList.add('product-card-highlight');
            // CHANGE: 红色霓虹高亮保持到用户点击任意位置再取消
            if (window._highlightClickOff) {
                document.removeEventListener('click', window._highlightClickOff);
                document.removeEventListener('touchstart', window._highlightClickOff);
            }
            window._highlightClickOff = function() {
                card.classList.remove('product-card-highlight');
                document.removeEventListener('click', window._highlightClickOff);
                document.removeEventListener('touchstart', window._highlightClickOff);
                window._highlightClickOff = null;
            };
            document.addEventListener('click', window._highlightClickOff, true);
            document.addEventListener('touchstart', window._highlightClickOff, true);
            var scrollCard = card;
            requestAnimationFrame(function() {
                requestAnimationFrame(function() {
                    scrollCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
                });
            });
            return { applied: true, segment: segment };
        }
    }
    return { applied: false, segment: segment };
}

// CHANGE: hash 直达时请求单产品；若为供应商产品则切到 PRODUCTOS 页并合并列表
async function fetchSingleProductForHash(segment) {
    if (!segment) return;
    try {
        var result = await apiRequest('/products/' + encodeURIComponent(segment));
        if (result && result.success && result.data) {
            var p = result.data;
            var prov = (p.codigo_proveedor || '').trim().toLowerCase();
            var isSupplier = prov && prov !== 'cristy';
            if (isSupplier) {
                AppState._hashProductForView = { product: p, segment: segment };
                switchView('products');
            } else {
                var exists = AppState.products.some(function (px) { return String(px.id) === String(p.id); });
                if (!exists) {
                    AppState.products.push(p);
                    renderProducts();
                }
            }
        }
    } catch (e) {
        console.warn('[fetchSingleProductForHash] 无法加载产品:', segment, e);
    }
}

// CHANGE: 进入购物车视图前，按 cart 中的 product_id 补全 AppState.products，避免列表因 products 未包含购物车商品而空白
async function ensureCartProductsInState() {
    if (!AppState.cart || AppState.cart.length === 0) return;
    const idsToLoad = [];
    for (const item of AppState.cart) {
        const pid = item.product_id;
        const p = AppState.products.find(px => String(px.id) === String(pid));
        const hasValidImage = p && p.image_path && String(p.image_path).trim() && !(p.image_path.includes && p.image_path.includes('data:image'));
        if (!p || !hasValidImage) {
            if (idsToLoad.indexOf(String(pid)) === -1) idsToLoad.push(String(pid));
        }
    }
    for (const productId of idsToLoad) {
        try {
            const result = await apiRequest('/products/' + encodeURIComponent(productId));
            if (result && result.success && result.data) {
                const existing = AppState.products.find(px => String(px.id) === String(result.data.id));
                if (!existing) {
                    AppState.products.push(result.data);
                }
            }
        } catch (e) {
            console.warn('⚠️ [ensureCartProductsInState] 拉取商品失败:', productId, e);
        }
    }
}

// Renderizar carrito
function renderCart() {
    const cartItems = document.getElementById('cartItems');
    
    if (AppState.cart.length === 0) {
        cartItems.innerHTML = `
            <div class="empty-cart">
                <div class="empty-cart-icon">🛒</div>
                <h3 style="font-size: 1.5rem; margin-bottom: 0.5rem; color: var(--text-color); font-weight: 600;">Tu carrito está vacío</h3>
                <p style="margin-bottom: 2rem; color: var(--text-light);">Agrega productos para comenzar a comprar</p>
                <button class="btn btn-primary" onclick="switchView('products')" style="position: relative; z-index: 1;">Ir a Comprar</button>
            </div>
        `;
        return;
    }
    
    // NOTE: 冇图不显示、不能缺图空着；仅渲染有 image_path 的项，图失败时 handleImageError 隐藏该行
    // CHANGE: 使用 String 比较，避免 API 返回的 product_id 为字符串而 products[].id 为数字导致匹配失败
    const cartWithImage = AppState.cart.filter(item => {
        const p = AppState.products.find(px => String(px.id) === String(item.product_id));
        return p && p.image_path && String(p.image_path).trim() && !p.image_path.includes('data:image');
    });
    // CHANGE: 若补全后仍无带图项，降级显示全部 cart 项（用占位图），避免“有数量、有小计但列表空白”
    const itemsToRender = cartWithImage.length > 0 ? cartWithImage : AppState.cart;
    const placeholderSvg = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect fill="#f0f0f0" width="100" height="100"/><text x="50" y="55" text-anchor="middle" fill="#999" font-size="12">Sin imagen</text></svg>');
    cartItems.innerHTML = itemsToRender.map(item => {
        const product = AppState.products.find(p => String(p.id) === String(item.product_id)) || {
            name: item.name || 'Producto desconocido',
            price: item.price || 0,
            image_path: ''
        };
        
        const safeProductId = String(item.product_id).replace(/'/g, "\\'").replace(/"/g, '&quot;');
        const hasValidImg = product.image_path && String(product.image_path).trim() && !product.image_path.includes('data:image');
        const cartImgSrc = hasValidImg ? _resolveImageSrc(product.image_path) : placeholderSvg;
        const safeCartImgSrc = (cartImgSrc || '').replace(/"/g, '&quot;').replace(/'/g, "\\'");
        return `
            <div class="cart-item">
                <div class="cart-item-image-wrapper">
                    <img src="${safeCartImgSrc}" 
                         alt="${product.name}" 
                         class="cart-item-image"
                         onclick="showImageModal('${safeCartImgSrc}')"
                         style="cursor: pointer;"
                         onerror="handleImageError(this);">
                </div>
                <div class="cart-item-info">
                    <div class="cart-item-name">${product.name}</div>
                    <div class="cart-item-price">$${calculatePriceByQuantity(product || item, item.quantity).toFixed(2)} × ${item.quantity}</div>
                </div>
                <div class="cart-item-controls">
                    <div class="quantity-control">
                        <button class="quantity-btn" onclick="updateQuantity('${safeProductId}', ${item.quantity - 1})">-</button>
                        <span class="quantity-value">${item.quantity}</span>
                        <button class="quantity-btn" onclick="updateQuantity('${safeProductId}', ${item.quantity + 1})">+</button>
                    </div>
                    <button class="remove-btn" onclick="removeFromCart('${safeProductId}')" style="position: relative; z-index: 20;" title="Eliminar producto del carrito">🗑️ ELIMINAR</button>
                </div>
            </div>
        `;
    }).join('');
    
    // 更新总价
    updateCartTotal();
}

// 按数量取价：1-2 单价，3-11 批发价，12+ 批量价（无批量价则用批发价）
// 情况1 三价: 1-2 unidad, 3-11 mayor, 12+ bulto（无 bulto 用 mayor）
// 情况2 两价(unidad+bulto): 1-11 unidad, 12+ bulto
// 情况3 一价: 所有数量用该价
function calculatePriceByQuantity(product, quantity) {
    if (!product) return 0;
    const price = Number(product.price) || 0;
    const wholesalePrice = Number(product.wholesale_price) || 0;
    const bulkPrice = Number(product.bulk_price) || 0;
    const hasUnidad = price > 0;
    const hasMayor = wholesalePrice > 0;
    const hasBulto = bulkPrice > 0;
    const tierCount = (hasUnidad ? 1 : 0) + (hasMayor ? 1 : 0) + (hasBulto ? 1 : 0);
    if (tierCount === 0) return 0;
    if (tierCount === 1) return price || wholesalePrice || bulkPrice;
    const scenarioSkipMayor = tierCount === 2 && hasUnidad && hasBulto && !hasMayor;
    // 1-2 件必须用单价；price 为 API 约定的 precio_unidad，避免旧逻辑默认批发价
    if (quantity <= 2) return (price > 0 ? price : (wholesalePrice || bulkPrice));
    if (scenarioSkipMayor && quantity <= 11) return price;
    if (quantity <= 11) return wholesalePrice || bulkPrice || price;
    if (scenarioSkipMayor) return bulkPrice || price;
    return bulkPrice || wholesalePrice || price;
}

// Actualizar precio total del carrito
function updateCartTotal() {
    const subtotal = AppState.cart.reduce((sum, item) => {
        const product = AppState.products.find(p => String(p.id) === String(item.product_id));
        const unitPrice = calculatePriceByQuantity(product || item, item.quantity);
        return sum + (unitPrice * item.quantity);
    }, 0);
    
    const total = subtotal + CONFIG.SHIPPING_COST;
    
    document.getElementById('subtotal').textContent = `$${subtotal.toFixed(2)}`;
    document.getElementById('shipping').textContent = `$${CONFIG.SHIPPING_COST.toFixed(2)}`;
    document.getElementById('total').textContent = `$${total.toFixed(2)}`;
}

// Actualizar UI del carrito
function updateCartUI() {
    const cartCount = AppState.cart.reduce((sum, item) => sum + (item.quantity || 0), 0);
    
    console.log(`🛒 Actualizando UI del carrito: ${AppState.cart.length} artículos, cantidad total: ${cartCount}`);
    console.log('Contenido del carrito:', AppState.cart);
    
    document.getElementById('cartCount').textContent = cartCount;
    const bottomNavCount = document.getElementById('bottomNavCartCount');
    if (bottomNavCount) {
        bottomNavCount.textContent = cartCount;
        // Si la cantidad es 0, ocultar insignia
        if (cartCount === 0) {
            bottomNavCount.style.display = 'none';
        } else {
            bottomNavCount.style.display = 'flex';
        }
    }
    
    // Actualizar insignia del botón del carrito superior
    const topCartCount = document.getElementById('cartCount');
    if (topCartCount) {
        if (cartCount === 0) {
            topCartCount.style.display = 'none';
        } else {
            topCartCount.style.display = 'flex';
        }
    }
    
    if (AppState.currentView === 'cart') {
        renderCart();
    }
}

// Cambiar vista
// CHANGE: 支持 ultimo 视图（显示自家产品）和 products 视图（显示其他供应商产品）
function switchView(view) {
    AppState.currentView = view;
    
    const productsSection = document.getElementById('productsSection');
    const cartSection = document.getElementById('cartSection');
    const ordersSection = document.getElementById('ordersSection');
    const orderDetailSection = document.getElementById('orderDetailSection');
    const paymentSection = document.getElementById('paymentSection');
    const navItems = document.querySelectorAll('.nav-item');
    
    // 隐藏所有视图
    productsSection.classList.add('hidden');
    cartSection.classList.add('hidden');
    ordersSection.classList.add('hidden');
    orderDetailSection.classList.add('hidden');
    paymentSection.classList.add('hidden');
    
    // 显示对应视图
    if (view === 'ultimo') {
        // CHANGE: ULTIMO 视图 - 显示 D:\Ya Subio\Cristy 内的产品图（supplier=Cristy）
        productsSection.classList.remove('hidden');
        fetchProducts('Cristy');
    } else if (view === 'products') {
        productsSection.classList.remove('hidden');
        // CHANGE: 若为 hash 直达的供应商产品，先显示该产品再拉 others 列表并合并
        if (AppState._hashProductForView && AppState._hashProductForView.product) {
            AppState.products = [AppState._hashProductForView.product];
            renderProducts();
            fetchProducts('others');
        } else {
            AppState.products = [];
            renderProducts();
            fetchProducts('others');
        }
    } else if (view === 'cart') {
        cartSection.classList.remove('hidden');
        // CHANGE: 先按购物车商品补全 products 再渲染，避免列表空白（数量有、小计有但无商品行）
        (async function () {
            await ensureCartProductsInState();
            renderCart();
        })();
    } else if (view === 'orders') {
        ordersSection.classList.remove('hidden');
        fetchOrders();
    } else if (view === 'order-detail') {
        orderDetailSection.classList.remove('hidden');
    } else if (view === 'payment') {
        paymentSection.classList.remove('hidden');
        fetchBankInfo();
    }
    
    // 更新导航状态
    navItems.forEach(item => {
        if (item.dataset.view === view) {
            item.classList.add('active');
        } else {
            item.classList.remove('active');
        }
    });
}

// Actualizar cantidad de producto（传单价以便后端照存，与页面一致）
function updateQuantity(productId, newQuantity) {
    if (newQuantity <= 0) {
        removeFromCart(productId);
    } else {
        const product = AppState.products.find(p => String(p.id) === String(productId));
        const unitPrice = product ? calculatePriceByQuantity(product, newQuantity) : null;
        updateCartItem(productId, newQuantity, unitPrice);
    }
}

// 显示提示消息
function showToast(message, type = 'info') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast show ${type}`;
    
    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}

// Buscar productos（CHANGE: 调用 API 带 search 参数，服务端按 name/description/codigo 过滤；仅展示有图产品）
async function searchProducts(query) {
    var q = (query || '').trim();
    var grid = document.getElementById('productsGrid');
    if (!grid) return;
    if (!q) {
        renderProducts();
        return;
    }
    grid.innerHTML = '<div class="loading">Buscando...</div>';
    // CHANGE: 搜索时不传 supplier，让 API 在 ULTIMO+PRODUCTOS 两页并集中搜索
    var url = '/products?limit=500&search=' + encodeURIComponent(q);
    try {
        var result = await apiRequest(url);
        // CHANGE: 仅当当前输入仍为该关键词时更新列表，避免旧响应覆盖
        var currentInput = (document.getElementById('searchInput') && document.getElementById('searchInput').value) ? document.getElementById('searchInput').value.trim() : '';
        if (currentInput !== q) { return; }
        grid = document.getElementById('productsGrid');
        if (!grid) return;
        if (result && result.success && result.data && result.data.length > 0) {
            var filtered = result.data;
            grid.innerHTML = filtered.map(function(product) {
                var safeProductId = String(product.id).replace(/'/g, "\\'").replace(/"/g, '&quot;');
                var productCode = (product.product_code != null && product.product_code !== '') ? String(product.product_code).replace(/'/g, "\\'").replace(/"/g, '&quot;') : safeProductId;
                var safeImagePath = product.image_path ? product.image_path.replace(/'/g, "\\'").replace(/"/g, '&quot;') : '';
                var searchImgSrc = product.image_path ? _resolveImageSrc(product.image_path) : '';
                var safeSearchImgSrc = (searchImgSrc || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
                var displayPrice = (product.bulk_price && product.bulk_price > 0) ? product.bulk_price : (product.wholesale_price && product.wholesale_price > 0 ? product.wholesale_price : (product.price || 0));
                var priceLabel = (product.bulk_price && product.bulk_price > 0) ? 'Precio Bulto' : (product.wholesale_price && product.wholesale_price > 0 ? 'Precio Mayoreo' : '');
                var priceText = displayPrice > 0 ? '$' + displayPrice.toFixed(2) : 'Consultar precio';
                var labelHtml = priceLabel ? '<div class="price-label">' + priceLabel + ':</div>' : '';
                return '<div class="product-card" data-product-id="' + safeProductId + '" data-product-code="' + productCode + '" data-image-path="' + (safeImagePath || '') + '">' +
                    '<div class="product-image-wrapper">' +
                    '<img src="' + searchImgSrc.replace(/"/g, '&quot;') + '" alt="' + (product.name || '').replace(/"/g, '&quot;') + '" class="product-image" data-image-src="' + safeSearchImgSrc + '" onclick="showImageModal(\'' + safeSearchImgSrc + '\')" style="cursor: pointer;" onerror="handleImageError(this);">' +
                    '</div><div class="product-info">' +
                    '<div class="product-code">' + (displayProductCode(product.product_code || product.id || '') || '').replace(/"/g, '&quot;') + '</div>' +
                    '<div class="product-name">' + (product.name || '') + '</div>' +
                    '<div class="product-price">' + labelHtml + '<div class="price-amount">' + priceText + '</div></div>' +
                    '<div class="product-actions"><button class="btn btn-primary add-to-cart-btn" data-product-id="' + safeProductId + '">Agregar al Carrito</button></div>' +
                    '</div></div>';
            }).join('');
            var addToCartButtons = document.querySelectorAll('.add-to-cart-btn');
            addToCartButtons.forEach(function(btn) {
                btn.addEventListener('click', function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    var productId = this.getAttribute('data-product-id');
                    if (productId) { showQuantityModal(productId); }
                });
            });
            applyProductHashAnchor();
        } else {
            grid.innerHTML = '<div class="loading">No se encontraron productos coincidentes</div>';
        }
    } catch (err) {
        console.error('搜索请求失败:', err);
        grid = document.getElementById('productsGrid');
        if (grid) { grid.innerHTML = '<div class="loading">Error de búsqueda. Intente de nuevo.</div>'; }
    }
}

// ===== 事件监听 =====

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', async () => {
    console.log('🚀 [INIT] 页面加载完成，开始初始化...');
    // CHANGE: 初始化 Lucide 图标（新一代 SVG 图标）
    if (typeof lucide !== 'undefined' && lucide.createIcons) {
        lucide.createIcons();
    }
    console.log('   用户ID:', AppState.userId);
    console.log('   API地址:', CONFIG.API_BASE_URL);
    
    // Inicializar modal de selección de cantidad
    initQuantityModal();
    
    // CHANGE: 初始化图片大图模态框
    initImageModal();
    
    // CHANGE: 初始化客户信息表单模态框
    initCustomerInfoModal();
    
    // 检查关键函数是否存在
    console.log('🔍 检查关键函数:');
    console.log('   addToCart:', typeof addToCart);
    console.log('   fetchCart:', typeof fetchCart);
    console.log('   renderProducts:', typeof renderProducts);
    
    // CHANGE: 先拉产品（默认 ULTIMO = Cristy 目录），再注册 Service Worker
    console.log('📦 [INIT] Iniciando carga de productos...');
    console.log('📦 [INIT] AppState.userId:', AppState.userId);
    console.log('📦 [INIT] CONFIG.API_BASE_URL:', CONFIG.API_BASE_URL);
    fetchProducts('Cristy').catch(error => {
        console.error('❌ [INIT] 加载产品失败:', error);
        console.error('❌ [INIT] 错误详情:', {
            message: error.message,
            stack: error.stack
        });
        // 显示错误状态，但不阻止页面继续加载
        const productsGrid = document.getElementById('productsGrid');
        if (productsGrid) {
            productsGrid.innerHTML = `
                <div class="empty-state" style="grid-column: 1 / -1; text-align: center; padding: 4rem 2rem;">
                    <div style="font-size: 5rem; margin-bottom: 1.5rem; opacity: 0.6;">⚠️</div>
                    <h3 style="font-size: 1.5rem; color: var(--text-color); margin-bottom: 0.5rem; font-weight: 600;">No se pueden cargar los productos</h3>
                    <p style="color: var(--text-light); font-size: 1.1rem;">${error.message || 'Compruebe la conexión o intente más tarde'}</p>
                    <button class="btn btn-primary" onclick="fetchProducts().then(() => console.log('OK')).catch(e => console.error(e))" style="margin-top: 1rem;">🔄 Recargar</button>
                </div>
            `;
        }
    });
    
    // 注册 Service Worker（不阻塞产品加载）
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('./service-worker.js')
            .then(reg => console.log('✅ Service Worker注册成功:', reg))
            .catch(err => console.error('❌ Service Worker注册失败:', err));
    }
    
    // Cargar carrito (不阻塞页面加载，静默失败)
    console.log('🛒 Iniciando carga del carrito...');
    fetchCart().catch(error => {
        // 购物车加载失败不影响产品显示，静默处理
        console.warn('⚠️ 加载购物车失败（不影响产品显示）:', error.message);
        AppState.cart = [];
        updateCartUI();
    });
    
    // 绑定事件
    const cartButton = document.getElementById('cartButton');
    if (cartButton) {
        cartButton.addEventListener('click', () => {
            console.log('🖱️ Botón del carrito clickeado');
            switchView('cart');
        });
    } else {
        console.error('❌ 找不到cartButton元素');
    }
    
    const backButton = document.getElementById('backButton');
    if (backButton) {
        backButton.addEventListener('click', () => {
            console.log('🖱️ Botón de regreso clickeado');
            switchView('products');
        });
    }
    
    const clearCartButton = document.getElementById('clearCartButton');
    if (clearCartButton) {
        clearCartButton.addEventListener('click', clearCart);
    }
    
    const checkoutButton = document.getElementById('checkoutButton');
    if (checkoutButton) {
        checkoutButton.addEventListener('click', checkout);
    }
    
    // CHANGE: 购物车页面编辑客户信息按钮
    const editCustomerInfoButton = document.getElementById('editCustomerInfoButton');
    if (editCustomerInfoButton) {
        editCustomerInfoButton.addEventListener('click', () => {
            console.log('✏️ 编辑客户信息按钮点击（购物车页面）');
            showCustomerInfoModal(true); // 传入true表示编辑模式
        });
    }
    
    // CHANGE: 订单页面编辑客户信息按钮
    const editCustomerInfoButtonOrders = document.getElementById('editCustomerInfoButtonOrders');
    if (editCustomerInfoButtonOrders) {
        editCustomerInfoButtonOrders.addEventListener('click', () => {
            console.log('✏️ 编辑客户信息按钮点击（订单页面）');
            showCustomerInfoModal(true); // 传入true表示编辑模式
        });
    }
    
    // CHANGE: 转账信息页面编辑客户信息按钮（修改后重新提交订单）
    const editCustomerInfoButtonPayment = document.getElementById('editCustomerInfoButtonPayment');
    if (editCustomerInfoButtonPayment) {
        editCustomerInfoButtonPayment.addEventListener('click', () => {
            console.log('✏️ 编辑客户信息按钮点击（转账信息页面）');
            showCustomerInfoModalForResubmit(); // 特殊模式：修改后重新提交订单
        });
    }
    
    // 订单列表返回按钮
    const ordersBackButton = document.getElementById('ordersBackButton');
    if (ordersBackButton) {
        ordersBackButton.addEventListener('click', () => {
            switchView('products');
        });
    }
    
    // 订单详情返回按钮
    const orderDetailBackButton = document.getElementById('orderDetailBackButton');
    if (orderDetailBackButton) {
        orderDetailBackButton.addEventListener('click', () => {
            switchView('orders');
        });
    }
    
    // 转账信息返回按钮
    const paymentBackButton = document.getElementById('paymentBackButton');
    if (paymentBackButton) {
        paymentBackButton.addEventListener('click', () => {
            // 如果是从订单详情来的，返回订单详情；否则返回订单列表
            if (AppState.lastOrderId) {
                viewOrderDetail(AppState.lastOrderId);
            } else {
                switchView('orders');
            }
        });
    }
    
    // CHANGE: 底部导航 - 事件委托 + 防抖。BUSCAR 独立处理，避免与其他分支干扰
    var bottomNav = document.querySelector('.bottom-nav');
    if (bottomNav) {
        var _lastNavAction = { view: null, ts: 0 };
        var DEBOUNCE_MS = 400;
        // BUSCAR 专用：直接跳转搜索并聚焦，独立分支避免与其他导航干扰
        function goToSearch(e) {
            var item = e.target.closest('.nav-item[data-view="search"]');
            if (!item || !bottomNav.contains(item)) return false;
            e.preventDefault();
            e.stopPropagation();
            var productsSection = document.getElementById('productsSection');
            var searchInp = document.getElementById('searchInput');
            if (!productsSection || !searchInp) return true;
            // 显示产品区（若被隐藏，如从 Carrito/Pedidos 切换）
            if (productsSection.classList.contains('hidden')) {
                switchView('products');
            }
            // 清空搜索框并恢复默认产品列表，方便用户直接输入关键词
            searchInp.value = '';
            if (typeof renderProducts === 'function') renderProducts();
            // 双帧延迟聚焦，确保布局/渲染完成
            requestAnimationFrame(function () {
                requestAnimationFrame(function () {
                    searchInp.focus();
                    searchInp.scrollIntoView({ behavior: 'smooth', block: 'center' });
                });
            });
            console.log('🔍 BUSCAR: 跳转搜索并聚焦');
            return true;
        }
        function handleNavAction(e) {
            // 优先处理 BUSCAR，独立分支
            if (goToSearch(e)) return;
            var item = e.target.closest('.nav-item');
            if (!item || !bottomNav.contains(item)) return;
            if (item.classList.contains('nav-link')) return;
            e.preventDefault();
            e.stopPropagation();
            var view = item.dataset.view;
            if (!view) return;
            var now = Date.now();
            if (_lastNavAction.view === view && now - _lastNavAction.ts < DEBOUNCE_MS) return;
            _lastNavAction = { view: view, ts: now };
            console.log('🖱️ Botón de navegación clickeado:', view);
            switchView(view);
        }
        bottomNav.addEventListener('click', handleNavAction, true);
        bottomNav.addEventListener('pointerup', handleNavAction, true);
        bottomNav.addEventListener('touchend', handleNavAction, { passive: false, capture: true });
        // CHANGE: 为所有底部导航按钮添加透明覆盖层，确保整块可点击且显示手指，避免 toast/其他元素遮挡
        bottomNav.querySelectorAll('.nav-item').forEach(function (btn) {
            if (!btn.querySelector('.nav-hit-overlay')) {
                var overlay = document.createElement('span');
                overlay.className = 'nav-hit-overlay';
                overlay.setAttribute('aria-hidden', 'true');
                overlay.style.cssText = 'position:absolute;inset:0;cursor:pointer;pointer-events:auto;z-index:1;';
                btn.style.position = 'relative';
                btn.insertBefore(overlay, btn.firstChild);
            }
        });
    }
    
    // 搜索功能：防抖 + 支持 Enter 触发，确保有反应
    var searchDebounceTimer = null;
    var searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.addEventListener('input', function(e) {
            var val = (e.target.value || '').trim();
            if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
            if (!val) {
                renderProducts();
                return;
            }
            searchDebounceTimer = setTimeout(function() {
                searchDebounceTimer = null;
                console.log('🔍 搜索:', val);
                searchProducts(val);
            }, 350);
        });
        searchInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
                searchDebounceTimer = null;
                var val = (e.target.value || '').trim();
                if (val) { searchProducts(val); } else { renderProducts(); }
            }
        });
    }
    
    // Usar delegación de eventos para manejar todos los botones de añadir al carrito (más confiable)
    document.addEventListener('click', function(e) {
        if (e.target.classList.contains('add-to-cart-btn') || e.target.closest('.add-to-cart-btn')) {
            const btn = e.target.classList.contains('add-to-cart-btn') ? e.target : e.target.closest('.add-to-cart-btn');
            const productId = btn.getAttribute('data-product-id');
            console.log('🖱️ [Delegación de eventos] Botón de añadir al carrito clickeado, ID del Producto:', productId);
            if (productId) {
                e.preventDefault();
                e.stopPropagation();
                // 显示数量选择模态框
                showQuantityModal(productId);
            }
        }
    });
    
    // CHANGE: 初始化认证相关功能
    initAuth();
    
    // CHANGE: hash 变化时（如点击链接）重新执行直达逻辑
    window.addEventListener('hashchange', function() {
        if (document.getElementById('productsSection') && !document.getElementById('productsSection').classList.contains('hidden')) {
            var r = applyProductHashAnchor();
            if (r && !r.applied && r.segment) fetchSingleProductForHash(r.segment);
        }
    });
    
    // CHANGE: 在初始化完成后，再次确保登录按钮可见（如果没有认证信息）
    // 使用setTimeout确保所有初始化完成
    setTimeout(() => {
        const loginBtn = document.getElementById('loginBtn');
        const userInfo = document.getElementById('userInfo');
        if (loginBtn) {
            if (!AppState.user || !AppState.token) {
                loginBtn.classList.remove('hidden');
                console.log('✅ [INIT完成] 最终确保登录按钮可见');
            }
        }
        if (userInfo && (!AppState.user || !AppState.token)) {
            userInfo.classList.add('hidden');
        }
    }, 500);
    
    console.log('✅ [INIT] Inicialización completada');
});

// CHANGE: 认证相关函数
function initAuth() {
    // CHANGE: 确保登录按钮在页面加载时可见（如果没有认证信息）
    // 先更新UI，确保登录按钮显示
    updateUserUI();
    
    // 检查是否有存储的认证信息
    const stored = getStoredAuth();
    if (stored) {
        AppState.token = stored.token;
        AppState.user = stored.user;
        AppState.userId = stored.user.user_id;
        // 验证token是否有效（异步，会再次调用updateUserUI）
        verifyToken(stored.token);
    } else {
        // CHANGE: 如果没有存储的认证信息，确保登录按钮显示
        // updateUserUI()已经在上面调用了，但为了确保，再次调用
        updateUserUI();
    }
    
    // CHANGE: 登录按钮 - 确保始终可见（如果没有认证信息）
    const loginBtn = document.getElementById('loginBtn');
    if (loginBtn) {
        loginBtn.addEventListener('click', () => {
            showAuthModal('login');
        });
        // CHANGE: 强制显示登录按钮（如果没有认证信息）
        if (!AppState.user || !AppState.token) {
            loginBtn.classList.remove('hidden');
            console.log('✅ [initAuth] 显示登录按钮（无认证信息）');
        } else {
            loginBtn.classList.add('hidden');
            console.log('✅ [initAuth] 隐藏登录按钮（已登录）');
        }
    } else {
        console.warn('⚠️ [initAuth] 未找到登录按钮元素 (id=loginBtn)');
        // CHANGE: 尝试延迟查找（DOM可能还没完全加载）
        setTimeout(() => {
            const retryLoginBtn = document.getElementById('loginBtn');
            if (retryLoginBtn) {
                retryLoginBtn.addEventListener('click', () => {
                    showAuthModal('login');
                });
                if (!AppState.user || !AppState.token) {
                    retryLoginBtn.classList.remove('hidden');
                    console.log('✅ [initAuth延迟] 显示登录按钮');
                }
            } else {
                console.error('❌ [initAuth延迟] 仍然未找到登录按钮元素');
            }
        }, 500);
    }
    
    // 登出按钮
    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', () => {
            logout();
        });
    }
    
    // 初始化认证模态框
    initAuthModal();
    
    // CHANGE: 若 URL 含 #/reset?token=xxx，自动显示重置密码表单
    var resetToken = _getResetTokenFromUrl();
    if (resetToken) {
        showAuthModal('reset', resetToken);
    }
}

function initAuthModal() {
    const modal = document.getElementById('authModal');
    const closeBtn = document.getElementById('authModalCloseBtn');
    const loginForm = document.getElementById('loginForm');
    const registerForm = document.getElementById('registerForm');
    const forgotForm = document.getElementById('forgotForm');
    const resetForm = document.getElementById('resetForm');
    const switchToRegister = document.getElementById('switchToRegister');
    const switchToLogin = document.getElementById('switchToLogin');
    const switchToForgot = document.getElementById('switchToForgot');
    const switchToLoginFromForgot = document.getElementById('switchToLoginFromForgot');
    const switchToLoginFromReset = document.getElementById('switchToLoginFromReset');
    const loginFormElement = document.getElementById('loginFormElement');
    const registerFormElement = document.getElementById('registerFormElement');
    const forgotFormElement = document.getElementById('forgotFormElement');
    const resetFormElement = document.getElementById('resetFormElement');
    
    if (!modal) return;
    
    // 关闭按钮
    if (closeBtn) {
        closeBtn.addEventListener('click', hideAuthModal);
    }
    
    // 点击背景关闭
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            hideAuthModal();
        }
    });
    
    // ESC键关闭
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
            hideAuthModal();
        }
    });
    
    // 切换表单
    if (switchToRegister) {
        switchToRegister.addEventListener('click', () => {
            showAuthModal('register');
        });
    }
    if (switchToLogin) {
        switchToLogin.addEventListener('click', () => {
            showAuthModal('login');
        });
    }
    if (switchToForgot) {
        switchToForgot.addEventListener('click', () => {
            showAuthModal('forgot');
        });
    }
    if (switchToLoginFromForgot) {
        switchToLoginFromForgot.addEventListener('click', () => {
            showAuthModal('login');
        });
    }
    if (switchToLoginFromReset) {
        switchToLoginFromReset.addEventListener('click', () => {
            showAuthModal('login');
        });
    }
    
    // 登录表单提交
    if (loginFormElement) {
        loginFormElement.addEventListener('submit', async (e) => {
            e.preventDefault();
            const email = document.getElementById('loginEmail').value;
            const password = document.getElementById('loginPassword').value;
            await handleLogin(email, password);
        });
    }
    
    // 注册表单提交
    if (registerFormElement) {
        registerFormElement.addEventListener('submit', async (e) => {
            e.preventDefault();
            const name = document.getElementById('registerName').value;
            const email = document.getElementById('registerEmail').value;
            const password = document.getElementById('registerPassword').value;
            const passwordConfirm = document.getElementById('registerPasswordConfirm').value;
            
            if (password !== passwordConfirm) {
                showToast('Las contraseñas no coinciden', 'error');
                return;
            }
            
            await handleRegister(name, email, password);
        });
    }
    
    // CHANGE: 忘记密码表单提交
    if (forgotFormElement) {
        forgotFormElement.addEventListener('submit', async (e) => {
            e.preventDefault();
            const email = document.getElementById('forgotEmail').value;
            await handleForgotPassword(email);
        });
    }
    
    // CHANGE: 重置密码表单提交
    if (resetFormElement) {
        resetFormElement.addEventListener('submit', async (e) => {
            e.preventDefault();
            const password = document.getElementById('resetPassword').value;
            const passwordConfirm = document.getElementById('resetPasswordConfirm').value;
            if (password !== passwordConfirm) {
                showToast('Las contraseñas no coinciden', 'error');
                return;
            }
            const token = _getResetTokenFromUrl();
            if (!token) {
                showToast('Enlace inválido o expirado', 'error');
                return;
            }
            await handleResetPassword(token, password);
        });
    }
    
}

function showAuthModal(mode = 'login', token) {
    const modal = document.getElementById('authModal');
    const loginForm = document.getElementById('loginForm');
    const registerForm = document.getElementById('registerForm');
    const forgotForm = document.getElementById('forgotForm');
    const resetForm = document.getElementById('resetForm');
    const authModalTitle = document.getElementById('authModalTitle');
    const forgotSuccess = document.getElementById('forgotSuccess');
    
    if (!modal) return;
    
    var allForms = [loginForm, registerForm, forgotForm, resetForm];
    allForms.forEach(function(f) { if (f) f.classList.add('hidden'); });
    
    if (mode === 'login') {
        if (loginForm) loginForm.classList.remove('hidden');
        if (authModalTitle) authModalTitle.textContent = 'Iniciar Sesión';
    } else if (mode === 'register') {
        if (registerForm) registerForm.classList.remove('hidden');
        if (authModalTitle) authModalTitle.textContent = 'Registrarse';
    } else if (mode === 'forgot') {
        if (forgotForm) forgotForm.classList.remove('hidden');
        if (forgotSuccess) forgotSuccess.classList.add('hidden');
        if (authModalTitle) authModalTitle.textContent = 'Recuperar contraseña';
    } else if (mode === 'reset' && token) {
        if (resetForm) resetForm.classList.remove('hidden');
        if (authModalTitle) authModalTitle.textContent = 'Restablecer contraseña';
        var resetTokenInput = document.getElementById('resetTokenHidden');
        if (!resetTokenInput) {
            var inp = document.createElement('input');
            inp.type = 'hidden';
            inp.id = 'resetTokenHidden';
            inp.value = token;
            var form = document.getElementById('resetFormElement');
            if (form) form.appendChild(inp);
        } else {
            resetTokenInput.value = token;
        }
    }
    
    modal.classList.remove('hidden');
}

function hideAuthModal() {
    const modal = document.getElementById('authModal');
    if (modal) {
        modal.classList.add('hidden');
    }
}

async function handleLogin(email, password) {
    try {
        const result = await apiRequest('/auth/login', {
            method: 'POST',
            body: JSON.stringify({ email, password })
        });
        
        if (result.success && result.data) {
            saveAuth(result.data.token, {
                user_id: result.data.user_id,
                email: result.data.email,
                name: result.data.name,
                avatar_url: result.data.avatar_url
            });
            updateUserUI();
            hideAuthModal();
            showToast('Inicio de sesión exitoso', 'success');
            // 重新加载购物车
            if (typeof loadCart === 'function') {
                loadCart();
            } else {
                renderCart();
            }
        } else {
            showToast(result.error || 'Error al iniciar sesión', 'error');
        }
    } catch (error) {
        console.error('登录失败:', error);
        showToast('Error al iniciar sesión', 'error');
    }
}

async function handleRegister(name, email, password) {
    try {
        const result = await apiRequest('/auth/register', {
            method: 'POST',
            body: JSON.stringify({ name, email, password })
        });
        
        if (result.success && result.data) {
            saveAuth(result.data.token, {
                user_id: result.data.user_id,
                email: result.data.email,
                name: result.data.name,
                avatar_url: result.data.avatar_url
            });
            updateUserUI();
            hideAuthModal();
            showToast('Registro exitoso', 'success');
            // 重新加载购物车
            if (typeof loadCart === 'function') {
                loadCart();
            } else {
                renderCart();
            }
        } else {
            showToast(result.error || 'Error al registrarse', 'error');
        }
    } catch (error) {
        console.error('注册失败:', error);
        showToast('Error al registrarse', 'error');
    }
}

// CHANGE: 忘记密码
function _getResetTokenFromUrl() {
    var hash = (typeof location !== 'undefined' && location.hash) ? location.hash.trim() : '';
    if (hash.indexOf('#/reset') === 0) {
        var q = hash.indexOf('?');
        if (q !== -1) {
            var params = hash.substring(q + 1).split('&');
            for (var i = 0; i < params.length; i++) {
                var p = params[i].split('=');
                if (p[0] === 'token' && p[1]) return decodeURIComponent(p[1]);
            }
        }
    }
    return null;
}

async function handleForgotPassword(email) {
    try {
        const result = await apiRequest('/auth/forgot-password', {
            method: 'POST',
            body: JSON.stringify({ email: email.trim().toLowerCase() })
        });
        
        if (result.success && result.reset_url) {
            var forgotSuccess = document.getElementById('forgotSuccess');
            var forgotResetLink = document.getElementById('forgotResetLink');
            var forgotForm = document.getElementById('forgotFormElement');
            if (forgotSuccess && forgotResetLink) {
                forgotResetLink.href = result.reset_url;
                forgotResetLink.textContent = result.reset_url;
                forgotSuccess.classList.remove('hidden');
                if (forgotForm) forgotForm.classList.add('hidden');
            }
            showToast(result.message || 'Revisa el enlace para restablecer', 'success');
        } else if (result.success) {
            showToast(result.message || 'Si el correo existe, recibirás instrucciones', 'success');
        } else {
            showToast(result.error || 'Error al solicitar recuperación', 'error');
        }
    } catch (error) {
        console.error('忘记密码失败:', error);
        showToast('Error al solicitar recuperación', 'error');
    }
}

async function handleResetPassword(token, password) {
    try {
        const result = await apiRequest('/auth/reset-password', {
            method: 'POST',
            body: JSON.stringify({ token: token, password: password })
        });
        
        if (result.success) {
            showToast('Contraseña restablecida correctamente', 'success');
            hideAuthModal();
            if (typeof history !== 'undefined' && history.replaceState) {
                history.replaceState(null, '', location.pathname + location.search);
            }
        } else {
            showToast(result.error || 'Error al restablecer contraseña', 'error');
        }
    } catch (error) {
        console.error('重置密码失败:', error);
        showToast('Error al restablecer contraseña', 'error');
    }
}


async function verifyToken(token) {
    try {
        const result = await apiRequest('/auth/verify', {
            method: 'POST',
            body: JSON.stringify({ token })
        });
        
        if (result.success && result.data) {
            AppState.user = result.data;
            AppState.userId = result.data.user_id;
            updateUserUI();
        } else {
            // Token无效，清除认证信息
            clearAuth();
            updateUserUI();
        }
    } catch (error) {
        console.error('验证token失败:', error);
        clearAuth();
        updateUserUI();
    }
}

function logout() {
    clearAuth();
    updateUserUI();
    // 清空购物车
    AppState.cart = [];
    renderCart();
    showToast('Sesión cerrada', 'info');
}

// CHANGE: 只要数据库有资料就显示产品；图加载失败用占位图，不隐藏卡片（避免整页空白）
var PRODUCT_PLACEHOLDER_SVG = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200"><rect fill="#f0f0f0" width="200" height="200"/><text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="#999" font-size="14" font-family="sans-serif">Sin imagen</text></svg>');
function handleImageError(imgElement) {
    imgElement.onerror = null;
    if (imgElement.src && imgElement.src.includes('data:image/svg+xml')) {
        return;
    }
    const productCard = imgElement.closest('.product-card');
    const cartItem = imgElement.closest('.cart-item');
    if (productCard) {
        imgElement.src = typeof PRODUCT_PLACEHOLDER_SVG !== 'undefined' ? PRODUCT_PLACEHOLDER_SVG : '';
        imgElement.alt = imgElement.alt || 'Sin imagen';
        console.log('⚠️ 产品图加载失败，已用占位图显示');
    }
    if (cartItem) {
        imgElement.src = typeof PRODUCT_PLACEHOLDER_SVG !== 'undefined' ? PRODUCT_PLACEHOLDER_SVG : '';
        imgElement.alt = imgElement.alt || 'Sin imagen';
        console.log('⚠️ 购物车项图片加载失败，已用占位图');
    }
}

// CHANGE: 图片大图显示功能
function showImageModal(imageSrc) {
    if (!imageSrc || imageSrc.includes('data:image/svg+xml')) {
        return; // 不显示占位图
    }
    
    const modal = document.getElementById('imageModal');
    const img = document.getElementById('imageModalImg');
    
    if (!modal || !img) return;
    
    img.src = imageSrc;
    modal.classList.remove('hidden');
}

function hideImageModal() {
    const modal = document.getElementById('imageModal');
    if (modal) {
        modal.classList.add('hidden');
    }
}

// 初始化图片模态框
function initImageModal() {
    const modal = document.getElementById('imageModal');
    const closeBtn = document.getElementById('imageModalCloseBtn');
    
    if (!modal || !closeBtn) return;
    
    // 关闭按钮
    closeBtn.addEventListener('click', hideImageModal);
    
    // 点击背景关闭
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            hideImageModal();
        }
    });
    
    // ESC键关闭
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
            hideImageModal();
        }
    });
}

// 初始化客户信息表单模态框
function initCustomerInfoModal() {
    const modal = document.getElementById('customerInfoModal');
    const closeBtn = document.getElementById('customerInfoModalCloseBtn');
    const cancelBtn = document.getElementById('customerInfoCancelBtn');
    const submitBtn = document.getElementById('customerInfoSubmitBtn');
    
    if (!modal || !closeBtn || !cancelBtn || !submitBtn) {
        console.error('❌ 客户信息模态框元素未找到');
        return;
    }
    
    // 关闭按钮
    closeBtn.addEventListener('click', hideCustomerInfoModal);
    cancelBtn.addEventListener('click', hideCustomerInfoModal);
    
    // 提交按钮
    submitBtn.addEventListener('click', submitOrderWithCustomerInfo);
    
    // 点击背景关闭
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            hideCustomerInfoModal();
        }
    });
    
    // ESC键关闭
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
            hideCustomerInfoModal();
        }
    });
    
    // 表单回车提交
    const form = document.getElementById('customerInfoForm');
    if (form) {
        form.addEventListener('submit', (e) => {
            e.preventDefault();
            submitOrderWithCustomerInfo();
        });
    }
}

// Hacer funciones disponibles en el ámbito global
window.addToCart = addToCart;
window.removeFromCart = removeFromCart;
window.updateQuantity = updateQuantity;
window.switchView = switchView;
window.fetchProducts = fetchProducts; // 添加这个，方便调试
window.AppState = AppState; // 添加这个，方便调试
window.showImageModal = showImageModal; // CHANGE: 导出图片大图函数

// Depuración: verificar que las funciones estén correctamente expuestas
console.log('🔍 [GLOBAL] 检查全局函数:');
console.log('   window.addToCart:', typeof window.addToCart);
console.log('   window.removeFromCart:', typeof window.removeFromCart);
console.log('   window.updateQuantity:', typeof window.updateQuantity);
console.log('   window.switchView:', typeof window.switchView);
