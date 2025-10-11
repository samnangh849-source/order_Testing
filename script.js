// --- Global State ---
let currentUser = null;
let originalAdminUser = null; // For "Login As" feature
let appData = {};
let allOrdersData = []; // This will now be mainly sourced from adminDataCache for admins
let selectedTeam = null;
let order = { page: null, telegramValue: null, customer: {}, products: [], shipping: {}, payment: {}, telegram: {}, subtotal: 0, grandTotal: 0, note: '' };
let currentPage = 'loginPage';
let productFormCounter = 0;
let html5QrCode = null;
let currentScannerFormId = null;
let isUploading = false;
let currentAdminView = 'dashboard';
let dataRefreshInterval = null; // General purpose interval
let adminDataCache = { orders: null, reports: null }; // NEW: Cache for admin-specific heavy data
let lastLogTimestamp = null; // NEW: To track changes for cache invalidation
let adminCacheStatusInterval = null; // NEW: Interval to check for cache updates


// --- DOM Elements ---
const pages = {
    loginPage: document.getElementById('loginPage'),
    roleSelectionPage: document.getElementById('roleSelectionPage'),
    teamSelectionPage: null,
    selectionPage: null, customerPage: null, productsPage: null, reviewPage: null, shippingPage: null, finalConfirmationPage: null
};
const mainContainer = document.getElementById('main-container');
const appContainer = document.getElementById('app-container');
const loginForm = document.getElementById('login-form');
const usernameInput = document.getElementById('username');
const passwordInput = document.getElementById('password');
const loginError = document.getElementById('login-error');
const loginButton = document.getElementById('login-button');
const flareLight = document.querySelector('.flare-light');
const connectionStatus = document.getElementById('connection-status');
const statusSpinner = document.getElementById('status-spinner');
const statusText = document.getElementById('status-text');
const passwordToggle = document.getElementById('password-toggle');
const eyeOpen = document.getElementById('eye-open');
const eyeClosed = document.getElementById('eye-closed');
const scannerContainer = document.getElementById('barcode-scanner-container');
const closeScannerBtn = document.getElementById('close-scanner-btn');
const imagePreviewModal = document.getElementById('image-preview-modal');
const previewImage = document.getElementById('preview-image');
const dataLoader = document.getElementById('data-loader');
// Profile elements
const appHeader = document.getElementById('app-header');
const profileMenuButton = document.getElementById('profile-menu-button');
const profileDropdown = document.getElementById('profile-dropdown');
const editProfileBtn = document.getElementById('edit-profile-btn');
const logoutBtn = document.getElementById('logout-btn');
const refreshDataBtn = document.getElementById('refresh-data-btn');
const switchAccountBtn = document.getElementById('switch-account-btn');
const backToRoleSelectBtn = document.getElementById('back-to-role-select-btn');
const editProfileModal = document.getElementById('edit-profile-modal');
const closeProfileModalBtn = document.getElementById('close-profile-modal-btn');
const cancelEditProfileBtn = document.getElementById('cancel-edit-profile-btn');
const editProfileForm = document.getElementById('edit-profile-form');


// --- Event Listeners ---
document.addEventListener('DOMContentLoaded', initialize);
document.addEventListener('mousemove', (e) => {
    flareLight.style.setProperty('--x', `${e.clientX}px`);
    flareLight.style.setProperty('--y', `${e.clientY}px`);
});
loginForm.addEventListener('submit', handleLogin);
passwordToggle.addEventListener('click', togglePasswordVisibility);
closeScannerBtn.addEventListener('click', stopBarcodeScanner);
// Role Selection Listeners
document.getElementById('select-role-admin').addEventListener('click', () => loadAdminDashboard());
document.getElementById('select-role-user').addEventListener('click', handleUserRoleSelection);
// Profile Listeners
profileMenuButton.addEventListener('click', () => profileDropdown.classList.toggle('hidden'));
logoutBtn.addEventListener('click', logout);
editProfileBtn.addEventListener('click', openEditProfileModal);
switchAccountBtn.addEventListener('click', (e) => { e.preventDefault(); showAdminView('loginAs'); });
backToRoleSelectBtn.addEventListener('click', handleBackToRoleSelection);
refreshDataBtn.addEventListener('click', handleRefreshData);
closeProfileModalBtn.addEventListener('click', closeEditProfileModal);
cancelEditProfileBtn.addEventListener('click', closeEditProfileModal);
editProfileForm.addEventListener('submit', handleProfileUpdateSubmit);
// Hide dropdown if clicked outside
document.addEventListener('click', (e) => {
    const profileMenuContainer = document.getElementById('profile-menu-container');
    if (profileMenuContainer && !profileMenuContainer.contains(e.target)) {
        profileDropdown.classList.add('hidden');
    }
});

// REMOVED: The 'beforeunload' event listener has been removed to prevent logout prompts on refresh.

// --- Initialization & Session Management ---
async function initialize() {
    // Inject Refresh Modal HTML into the body
    const refreshModalHtml = `
        <div id="refresh-confirm-modal" class="hidden fixed inset-0 bg-black bg-opacity-80 flex items-center justify-center z-50 p-4">
            <div class="page-card w-full max-w-sm text-center">
                <h2 class="text-xl font-bold text-white mb-4">ផ្ទុកទំព័រឡើងវិញ</h2>
                <p class="text-gray-300 mb-6">តើអ្នកចង់ទាញយកទិន្នន័យចុងក្រោយពី Server ដែរឬទេ?</p>
                <div class="flex justify-center space-x-4">
                    <button id="confirm-refresh-no" class="btn btn-secondary">ប្រើទិន្នន័យចាស់</button>
                    <button id="confirm-refresh-yes" class="btn btn-primary">ទាញទិន្នន័យថ្មី</button>
                </div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', refreshModalHtml);

    const sessionDataString = localStorage.getItem('orderAppSession');
    if (sessionDataString) {
        // Session exists, show confirmation modal instead of directly loading
        document.getElementById('refresh-confirm-modal').classList.remove('hidden');

        document.getElementById('confirm-refresh-yes').onclick = () => {
            document.getElementById('refresh-confirm-modal').classList.add('hidden');
            checkSession(true); // Force refresh
        };
        document.getElementById('confirm-refresh-no').onclick = () => {
            document.getElementById('refresh-confirm-modal').classList.add('hidden');
            checkSession(false); // Use cache
        };
    } else {
        // No session, proceed to login verification
        await verifyWebAppUrl();
    }
}


async function checkSession(forceRefresh = false) {
    // Check for admin impersonation session first
    const originalAdminSessionString = localStorage.getItem('originalAdminSession');
    if (originalAdminSessionString) {
        originalAdminUser = JSON.parse(originalAdminSessionString).user;
        showImpersonationBanner();
    }

    const sessionDataString = localStorage.getItem('orderAppSession');
    if (sessionDataString) {
        const sessionData = JSON.parse(sessionDataString);
        const now = new Date().getTime();
        const sevenDaysInMillis = 7 * 24 * 60 * 60 * 1000;

        if (!sessionData.timestamp || (now - sessionData.timestamp > sevenDaysInMillis)) {
            logout(null, true); 
            await verifyWebAppUrl();
        } else {
            currentUser = sessionData.user;
            pages.loginPage.classList.add('hidden');
            mainContainer.classList.remove('items-center', 'justify-center'); 
            
            await fetchData(forceRefresh);
            
            // If this is an impersonated session, skip role selection
            if (originalAdminUser) {
                navigateToUserView();
            } 
            // If it's a real admin, check for hybrid role
            else if (currentUser.IsSystemAdmin) {
                 await initializeAdminDataCache(); // Initialize admin data cache
                 const teams = (currentUser.Team || '').split(',').map(t => t.trim()).filter(Boolean);
                 if (teams.length > 0) {
                     showPage('roleSelectionPage');
                 } else {
                     loadAdminDashboard();
                 }
            } 
            // Regular user navigation
            else {
                navigateToUserView();
            }
            updateProfileDisplay();
        }
    } else {
        await verifyWebAppUrl();
    }
}

function navigateToUserView() {
    const teams = (currentUser.Team || '').split(',').map(t => t.trim()).filter(Boolean);
    if (teams.length > 1) {
         buildTeamSelectionUI(teams);
         showPage('teamSelectionPage');
    } else if (teams.length === 1) {
        selectedTeam = teams[0];
        setupPageSelection();
        showPage('selectionPage');
    } else {
        // User with no teams, show an appropriate message
        appContainer.innerHTML = `<div class="page-card text-center text-yellow-400">គណនីរបស់អ្នកមិនមានក្រុមទេ។ សូមទាក់ទងអ្នកគ្រប់គ្រង។</div>`;
        appContainer.classList.remove('hidden');
    }
}

function handleUserRoleSelection() {
    // This is called when a hybrid admin chooses to act as a user
    pages.roleSelectionPage.classList.add('hidden');
    navigateToUserView();
}

async function verifyWebAppUrl() {
    loginButton.disabled = true;
    statusSpinner.classList.remove('hidden');
    statusText.textContent = 'កំពុងពិនិត្យការតភ្ជាប់...';
    connectionStatus.className = 'text-sm rounded-lg p-3 mb-6 flex items-center justify-center space-x-2 bg-yellow-900 text-yellow-300';

    if (typeof WEB_APP_URL === 'undefined' || WEB_APP_URL.includes("YOUR_GOOGLE_APPS_SCRIPT_WEB_APP_URL_HERE") || WEB_APP_URL.length < 50) {
        setConnectionStatus(false, 'URL មិនទាន់បានកំណត់រចនាសម្ព័ន្ធ។ សូមពិនិត្យ Deployment របស់អ្នក។');
        return;
    }

    try {
        const response = await fetch(`${WEB_APP_URL}?action=ping`);
        if (!response.ok) throw new Error(`Server responded with status: ${response.status}`);
        const data = await response.json();
        if (data.status === 'success' && data.message === 'pong') {
            setConnectionStatus(true, 'ការតភ្ជាប់ជោគជ័យ');
        } else {
            throw new Error('Invalid response from server.');
        }
    } catch (error) {
        console.error("Connection verification failed:", error);
        setConnectionStatus(false, 'URL មិនត្រឹមត្រូវ ឬមិនដំណើរការ។ សូមពិនិត្យ Deployment របស់អ្នក។');
    }
}

function setConnectionStatus(isSuccess, message) {
    statusSpinner.classList.add('hidden');
    statusText.textContent = message;
    const iconContainer = connectionStatus.querySelector('svg');
    if (iconContainer) iconContainer.remove();

    if (isSuccess) {
        connectionStatus.className = 'text-sm rounded-lg p-3 mb-6 flex items-center justify-center space-x-2 bg-green-900 text-green-300';
        statusText.insertAdjacentHTML('beforebegin', '<svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd" /></svg>');
        loginButton.disabled = false;
    } else {
        connectionStatus.className = 'text-sm rounded-lg p-3 mb-6 flex items-center justify-center space-x-2 bg-red-900 text-red-300';
        statusText.insertAdjacentHTML('beforebegin', '<svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd" /></svg>');
        loginButton.disabled = true;
    }
}

// --- Navigation ---
function showPage(pageId) {
    Object.values(pages).forEach(page => {
        if(page) page.classList.add('hidden');
    });
    if (pages[pageId]) {
        pages[pageId].classList.remove('hidden');
        currentPage = pageId;
        updateProgressIndicator(pageId);
    } else {
        // Handle admin pages which are not in the `pages` object
        currentPage = `admin-${pageId}`;
    }
}

function updateProgressIndicator(pageId) {
    const progressIndicator = document.getElementById('progress-indicator');
    if (!progressIndicator) return;

    const pageToStepMap = { 'customerPage': 'customer', 'productsPage': 'products', 'reviewPage': 'review', 'shippingPage': 'shipping', 'finalConfirmationPage': 'final' };
    const currentStepKey = pageToStepMap[pageId];

    if (!currentStepKey) {
        progressIndicator.classList.add('hidden');
        return;
    }
    progressIndicator.classList.remove('hidden');

    const stepOrder = ['customer', 'products', 'review', 'shipping', 'final'];
    const currentStepIndex = stepOrder.indexOf(currentStepKey);

    stepOrder.forEach((stepKey, index) => {
        const stepEl = document.getElementById(`step-${stepKey}`);
        if (!stepEl) return;
        const circle = stepEl.querySelector('.step-circle');
        const label = stepEl.querySelector('.step-label');
        const connector = document.getElementById(`connector-${index}`);

        circle.className = 'step-circle w-8 h-8 rounded-full flex items-center justify-center font-bold transition-all duration-300';
        label.className = 'step-label text-center text-xs mt-2 transition-all duration-300 font-medium hidden sm:block';
        if (connector) connector.className = 'step-connector flex-1 h-1 transition-all duration-300';

        if (index < currentStepIndex) {
            circle.classList.add('bg-blue-600', 'text-white');
            if (label) label.classList.add('text-gray-400');
            if (connector) connector.classList.add('bg-blue-600');
        } else if (index === currentStepIndex) {
            circle.classList.add('bg-white', 'border-2', 'border-blue-600', 'text-blue-600', 'scale-110');
            if (label) label.classList.add('text-blue-400', 'font-bold');
            if (connector) connector.classList.add('bg-gray-700');
        } else {
            circle.classList.add('bg-gray-600', 'text-gray-400');
            if (label) label.classList.add('text-gray-500');
            if (connector) connector.classList.add('bg-gray-700');
        }
    });
}

function goBack(targetPageId) { showPage(targetPageId); }

// --- Data Fetching & UI Setup ---
async function handleRefreshData(e) {
    if (e) e.preventDefault();
    profileDropdown.classList.add('hidden');
    
    const choice = await showConfirmation(
        'ទាញទិន្នន័យថ្មី',
        'តើអ្នកពិតជាចង់ទាញយកទិន្នន័យចុងក្រោយពី Server មែនទេ? វាអាចនឹងប្រើពេលបន្តិច។'
    );

    if (choice === 'primary') {
        localStorage.removeItem('appDataCache');
        if (currentUser.IsSystemAdmin && !originalAdminUser) {
            // For admin, also clear the specific admin caches
            localStorage.removeItem('adminOrdersCache');
            localStorage.removeItem('adminReportsCache');
            await initializeAdminDataCache(); // Re-fetch admin cache
            await loadAdminDashboard(false); // Reload main admin structure
        } else {
            await fetchData(true);
             // After fetching, re-navigate to the correct user view
            navigateToUserView();
        }
        alert('ទិន្នន័យត្រូវបានទាញយកឡើងវិញដោយជោគជ័យ។');
    }
}
async function fetchData(forceRefresh = false) {
    dataLoader.classList.remove('hidden');
    appContainer.classList.add('hidden');

    const CACHE_KEY = 'appDataCache';
    const CACHE_DURATION = 3600 * 1000; // 1 hour

    try {
        const cachedDataString = localStorage.getItem(CACHE_KEY);
        let needsFetching = true;

        if (cachedDataString && !forceRefresh) {
            const cachedData = JSON.parse(cachedDataString);
            const now = new Date().getTime();
            if (now - cachedData.timestamp < CACHE_DURATION) {
                appData = cachedData.data;
                needsFetching = false;
            }
        }

        if (needsFetching) {
            const [staticResponse, usersResponse] = await Promise.all([
                fetch(`${WEB_APP_URL}?action=getStaticData`),
                fetch(`${WEB_APP_URL}?action=getUsers`)
            ]);

            if (!staticResponse.ok) throw new Error('Could not fetch static data.');
            if (!usersResponse.ok) throw new Error('Could not fetch user data.');

            const staticResult = await staticResponse.json();
            const usersResult = await usersResponse.json();

            if (staticResult.status !== 'success') throw new Error(staticResult.message);
            if (usersResult.status !== 'success') throw new Error(usersResult.message);

            appData = { ...staticResult.data, users: usersResult.data };
            
            localStorage.setItem(CACHE_KEY, JSON.stringify({ data: appData, timestamp: new Date().getTime() }));
        }
        
        buildAppUI();
        populateStaticDropdowns();
        updateProductSuggestions();
        updateColorSuggestions();
        appContainer.classList.remove('hidden');
    } catch (error) {
        console.error("Data Fetching Error:", error);
        loginError.textContent = `Error: ${error.message}`;
        logout();
    } finally {
        dataLoader.classList.add('hidden');
    }
}

// --- Logging ---
async function logUserAction(action, details = {}) {
    try {
        const payload = {
            action: 'writeLog',
            logData: {
                Timestamp: new Date().toISOString(),
                UserName: currentUser ? currentUser.UserName : 'N/A',
                Action: action,
                Details: JSON.stringify(details)
            }
        };
        // Fire-and-forget request
        fetch(WEB_APP_URL, {
            method: 'POST',
            body: JSON.stringify(payload),
            headers: { 'Content-Type': 'text/plain;charset=utf-8' },
            mode: 'no-cors'
        });
    } catch (error) {
        console.error("Failed to write log:", error);
    }
}


// --- Admin Data Caching & Refresh ---
async function initializeAdminDataCache() {
    console.log("Initializing Admin Data Cache...");
    if (adminCacheStatusInterval) clearInterval(adminCacheStatusInterval);

    try {
        const response = await fetch(`${WEB_APP_URL}?action=getLatestLogTimestamp`);
        const result = await response.json();
        if (result.status === 'success') {
            lastLogTimestamp = result.timestamp;
            console.log("Initial log timestamp:", lastLogTimestamp);
        }
    } catch (e) {
        console.error("Could not fetch initial log timestamp:", e);
    }

    const cachedOrders = localStorage.getItem('adminOrdersCache');
    const cachedReports = localStorage.getItem('adminReportsCache');
    if (cachedOrders) adminDataCache.orders = JSON.parse(cachedOrders);
    if (cachedReports) adminDataCache.reports = JSON.parse(cachedReports);

    if (!adminDataCache.orders || !adminDataCache.reports) {
         await silentRefreshAdminCache();
    }
   
    adminCacheStatusInterval = setInterval(checkCacheStatus, 60 * 1000);
}

async function checkCacheStatus() {
    if (document.hidden || !currentUser || !currentUser.IsSystemAdmin) return;
    console.log("Checking for cache updates...");
    try {
        const response = await fetch(`${WEB_APP_URL}?action=getLatestLogTimestamp`);
        const result = await response.json();
        if (result.status === 'success' && result.timestamp !== lastLogTimestamp) {
            console.log("New log entry detected. Refreshing admin cache. Old TS:", lastLogTimestamp, "New TS:", result.timestamp);
            lastLogTimestamp = result.timestamp;
            await silentRefreshAdminCache();
        } else {
             console.log("No new log entries detected. Cache is up-to-date.");
        }
    } catch(e) {
        console.error("Error checking cache status:", e);
    }
}

async function silentRefreshAdminCache() {
    if (document.hidden || !currentUser || !currentUser.IsSystemAdmin) return;
    console.log('Silent Admin Cache Refresh triggered at', new Date().toLocaleTimeString());
    try {
        const [ordersResponse, reportsResponse] = await Promise.all([
            fetch(WEB_APP_URL, {
                method: 'POST',
                body: JSON.stringify({ action: 'adminGetAllOrders' }),
                headers: { 'Content-Type': 'text/plain;charset=utf-8' },
            }),
            fetch(`${WEB_APP_URL}?action=getReportData`)
        ]);

        if (ordersResponse.ok) {
            const ordersResult = await ordersResponse.json();
            if (ordersResult.status === 'success') {
                adminDataCache.orders = ordersResult.data;
                localStorage.setItem('adminOrdersCache', JSON.stringify(ordersResult.data));
                if (currentAdminView === 'orders' && document.getElementById('all-orders-table')) {
                    allOrdersData = adminDataCache.orders;
                    applyOrderFilters();
                }
            }
        }

        if (reportsResponse.ok) {
            const reportsResult = await reportsResponse.json();
            if (reportsResult.status === 'success') {
                adminDataCache.reports = reportsResult.data;
                localStorage.setItem('adminReportsCache', JSON.stringify(reportsResult.data));
                if (currentAdminView === 'reports' && document.getElementById('admin-content')) {
                    renderReportsView();
                }
            }
        }
    } catch (error) {
        console.error("Silent Admin Cache Refresh failed:", error);
    }
}


async function loadAdminDashboard(isSilent = false) {
    if (!isSilent) {
        dataLoader.classList.remove('hidden');
    }
    appContainer.classList.add('hidden');
    try {
        const response = await fetch(`${WEB_APP_URL}?action=getAllSheetDataForAdmin`);
        if (!response.ok) throw new Error('Could not fetch admin data.');
        const result = await response.json();
        if (result.status !== 'success') throw new Error(result.message);
        
        appData.admin = result.data;
        buildAdminUI(result.data);
        
        appContainer.classList.remove('hidden');
    } catch (error) {
        console.error("Failed to load admin dashboard:", error);
        logout(null, false);
        alert("Could not load Admin Dashboard. " + error.message);
    } finally {
        if (!isSilent) {
            dataLoader.classList.add('hidden');
        }
    }
}

function buildAdminUI(allData) {
    appContainer.innerHTML = `
        <div class="flex h-full min-h-screen w-full max-w-7xl mx-auto">
            <!-- Sidebar -->
            <aside class="admin-sidebar w-64 bg-gray-800 text-gray-300 flex-shrink-0 p-4">
                <h2 class="text-xl font-bold text-white mb-6">Admin Panel</h2>
                <nav id="admin-nav" class="admin-sidebar-nav flex flex-col space-y-2">
                    <a href="#" data-view="dashboard" class="flex items-center p-3 rounded-md active">ទិន្នន័យសង្ខេប</a>
                    <a href="#" data-view="orders" class="flex items-center p-3 rounded-md">ប្រតិបត្តិការណ៍ទាំងអស់</a>
                    <a href="#" data-view="reports" class="flex items-center p-3 rounded-md">របាយការណ៍ & ការវិភាគ</a>
                    <a href="#" data-view="loginAs" class="flex items-center p-3 rounded-md">ចូលគណនីផ្សេង</a>
                    <a href="#" data-view="logs" class="flex items-center p-3 rounded-md">កំណត់ត្រាសកម្មភាព</a>
                    <a href="#" data-view="config" class="flex items-center p-3 rounded-md">ការគ្រប់គ្រងប្រព័ន្ធ</a>
                </nav>
            </aside>
            <!-- Main Content -->
            <main id="admin-content" class="flex-1 p-4 sm:p-6 lg:p-8 overflow-y-auto">
                <!-- Content will be injected here -->
            </main>
        </div>
    `;
    document.getElementById('admin-nav').addEventListener('click', (e) => {
        e.preventDefault();
        const link = e.target.closest('a');
        if (link) {
            document.querySelectorAll('#admin-nav a').forEach(el => el.classList.remove('active'));
            link.classList.add('active');
            const view = link.dataset.view;
            showAdminView(view);
        }
    });
    showAdminView('dashboard');
}

function showAdminView(view, isRefresh = false) {
    // Stop the general data refresh interval when navigating admin views
    if(dataRefreshInterval) clearInterval(dataRefreshInterval);

    showPage(view);
    currentAdminView = view;
    const contentArea = document.getElementById('admin-content');
    if (!contentArea) return;

    const data = appData.admin;
    switch (view) {
        case 'dashboard':
            contentArea.innerHTML = createDashboardView(data);
            break;
        case 'config':
            contentArea.innerHTML = createSystemConfigView();
            document.getElementById('config-nav').addEventListener('click', e => {
                e.preventDefault();
                const link = e.target.closest('a');
                if (link) {
                    document.querySelectorAll('#config-nav a').forEach(el => el.classList.remove('bg-gray-700', 'font-semibold'));
                    link.classList.add('bg-gray-700', 'font-semibold');
                    renderConfigTable(link.dataset.sheet);
                }
            });
            renderConfigTable('users');
            break;
        case 'reports':
             renderReportsView(); // Will use cache
            break;
        case 'orders':
             createAllOrdersView(); // Will use cache
            break;
        case 'loginAs':
            contentArea.innerHTML = createLoginAsView();
            break;
        case 'logs':
            createLogsView();
            break;
    }
}

function createDashboardView(data) {
    const stats = [
        { label: 'អ្នកប្រើប្រាស់', value: data.users.length, icon: '👤' },
        { label: 'ក្រុម (Teams)', value: data.teams.length, icon: '👥' },
        { label: 'ផលិតផល', value: data.products.length, icon: '🛍️' },
        { label: 'អ្នកដឹកជញ្ជូន', value: data.drivers.length, icon: '🚚' },
        { label: 'គណនីធនាគារ', value: data.bankAccounts.length, icon: '🏦' }
    ];

    return `
        <h1 class="text-3xl font-bold text-white mb-6">ទិន្នន័យសង្ខេប</h1>
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
            ${stats.map(stat => `
                <div class="page-card flex items-center p-6">
                    <div class="text-4xl mr-6">${stat.icon}</div>
                    <div>
                        <p class="text-4xl font-bold text-white">${stat.value}</p>
                        <p class="text-gray-400">${stat.label}</p>
                    </div>
                </div>
            `).join('')}
        </div>
    `;
}

function createSystemConfigView() {
    const manageableSheets = {
        'users': 'អ្នកប្រើប្រាស់', 'products': 'ផលិតផល', 'teams': 'ក្រុម', 'locations': 'ទីតាំង',
        'shippingMethods': 'វិធីដឹកជញ្ជូន', 'drivers': 'អ្នកដឹកជញ្ជូន', 'bankAccounts': 'គណនីធនាគារ',
        'phoneCarriers': 'ក្រុមហ៊ុនទូរស័ព្ទ', 'telegramTemplates': 'គំរូសារ Telegram',
    };

    return `
        <h1 class="text-3xl font-bold text-white mb-6">ការគ្រប់គ្រងប្រព័ន្ធ</h1>
        <div class="grid grid-cols-1 md:grid-cols-4 gap-6">
            <div class="md:col-span-1">
                <div class="page-card p-4">
                    <nav id="config-nav" class="flex flex-col space-y-1">
                        ${Object.keys(manageableSheets).map((key, index) => `
                            <a href="#" data-sheet="${key}" class="p-3 rounded-md text-gray-300 hover:bg-gray-700 ${index === 0 ? 'bg-gray-700 font-semibold' : ''}">${manageableSheets[key]}</a>
                        `).join('')}
                    </nav>
                </div>
            </div>
            <div id="config-content" class="md:col-span-3">
                <!-- Config table will be injected here -->
            </div>
        </div>
    `;
}

function renderConfigTable(sheetKey) {
    const data = appData.admin[sheetKey];
     const manageableSheets = {
        'users': 'អ្នកប្រើប្រាស់', 'products': 'ផលិតផល', 'teams': 'ក្រុម', 'locations': 'ទីតាំង', 
        'shippingMethods': 'វិធីដឹកជញ្ជូន', 'drivers': 'អ្នកដឹកជញ្ជូន', 'bankAccounts': 'គណនីធនាគារ', 
        'phoneCarriers': 'ក្រុមហ៊ុនទូរស័ព្ទ', 'telegramTemplates': 'គំរូសារ Telegram',
    };

    const headers = data.length > 0 ? Object.keys(data[0]) : [];
    const contentArea = document.getElementById('config-content');

    if (!contentArea) return;

    contentArea.innerHTML = `
        <div class="page-card">
            <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center mb-4 gap-4">
                <h2 class="text-2xl font-bold text-blue-400">${manageableSheets[sheetKey]}</h2>
                <div class="w-full sm:w-auto flex items-center gap-2">
                     <input type="text" id="search-${sheetKey}" placeholder="ស្វែងរក..." class="form-input w-full sm:w-48 text-sm py-2">
                    <button onclick="openAdminEditModal('${sheetKey}')" class="btn btn-secondary text-sm py-2 px-3 whitespace-nowrap">បន្ថែមថ្មី</button>
                </div>
            </div>
            <div class="overflow-x-auto">
                <table id="table-${sheetKey}" class="admin-table">
                    <thead><tr>
                        ${headers.map(h => `<th>${h}</th>`).join('')}
                        <th>Actions</th>
                    </tr></thead>
                    <tbody>
                        ${data.map((row, rowIndex) => `
                            <tr>
                                ${headers.map(h => `
                                    <td>
                                        ${(h.toLowerCase().includes('imageurl') || h.toLowerCase().includes('logourl')) ? 
                                            `<img src="${convertGoogleDriveUrl(row[h])}" class="h-10 w-10 object-contain rounded-md cursor-pointer bg-white p-1" onclick="showImagePreview(this.src)" onerror="this.src='https://placehold.co/100x100/1f2937/4b5563?text=IMG'"/>` : 
                                        ['AllowManualDriver', 'RequireDriverSelection', 'IsSystemAdmin'].includes(h) ?
                                            ( ['true', true].includes(row[h]) ? '<span class="text-green-400 font-bold">TRUE</span>' : '<span class="text-red-400">FALSE</span>' ) :
                                            `<div class="truncate max-w-xs">${row[h]}</div>`
                                        }
                                    </td>
                                `).join('')}
                                <td class="whitespace-nowrap">
                                    <button onclick="openAdminEditModal('${sheetKey}', ${rowIndex})" class="action-btn text-yellow-400 hover:text-yellow-600 p-1">✏️</button>
                                    <button onclick="deleteConfigRow('${sheetKey}', ${rowIndex})" class="action-btn text-red-400 hover:text-red-600 p-1">🗑️</button>
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        </div>
    `;
    document.getElementById(`search-${sheetKey}`).addEventListener('input', (e) => {
        const searchTerm = e.target.value.toLowerCase();
        const tableRows = document.querySelectorAll(`#table-${sheetKey} tbody tr`);
        tableRows.forEach(row => {
            const rowText = row.textContent.toLowerCase();
            row.style.display = rowText.includes(searchTerm) ? '' : 'none';
        });
    });
}

function openAdminEditModal(sheetKey, rowIndex = null) {
    const isNew = rowIndex === null;
    const data = isNew ? {} : appData.admin[sheetKey][rowIndex];
    const headers = appData.admin[sheetKey].length > 0 ? Object.keys(appData.admin[sheetKey][0]) : [];
    const modal = document.getElementById('admin-edit-modal');
    const modalContent = document.getElementById('admin-edit-modal-content');

    let formFields = '';
    headers.forEach(header => {
        const value = data[header] || '';
        const isImageField = header.toLowerCase().includes('imageurl') || header.toLowerCase().includes('logourl');
        const isBooleanField = ['AllowManualDriver', 'RequireDriverSelection', 'IsSystemAdmin'].includes(header);
        const isTemplateField = sheetKey === 'telegramTemplates' && header === 'Template';

        if (isImageField) {
             formFields += `
                <div>
                    <label for="modal-field-${header}" class="block text-sm font-medium text-gray-400 mb-2">${header}</label>
                    <div class="flex items-center space-x-4">
                        <img id="modal-preview-${header}" src="${convertGoogleDriveUrl(value)}" class="h-16 w-16 object-contain rounded-md bg-white p-1" onerror="this.src='https://placehold.co/100x100/1f2937/4b5563?text=IMG'"/>
                        <div class="w-full">
                            <input type="text" id="modal-field-${header}" value="${value}" class="form-input w-full mb-2" placeholder="ឬបិទភ្ជាប់ URL">
                            <input type="file" id="modal-upload-${header}" class="text-sm text-gray-400 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-blue-500 file:text-white hover:file:bg-blue-600">
                        </div>
                    </div>
                </div>
             `;
        } else if (isBooleanField) {
            const isTrue = ['true', true].includes(value);
             formFields += `
                <div>
                    <label class="block text-sm font-medium text-gray-400 mb-2">${header}</label>
                    <div id="modal-field-${header}" data-value="${isTrue}" class="flex rounded-md shadow-sm">
                        <button type="button" onclick="toggleBoolean(this, true)" class="${isTrue ? 'btn-primary' : 'btn-secondary'} rounded-r-none flex-1">TRUE</button>
                        <button type="button" onclick="toggleBoolean(this, false)" class="${!isTrue ? 'btn-primary' : 'btn-secondary'} rounded-l-none flex-1">FALSE</button>
                    </div>
                </div>
            `;
        }
         else if (isTemplateField) {
            formFields += `
                <div>
                    <label for="modal-field-${header}" class="block text-sm font-medium text-gray-400 mb-2">${header}</label>
                    <textarea id="modal-field-${header}" class="form-textarea w-full h-48" style="font-family: monospace;">${value}</textarea>
                </div>
            `;
        }
        else {
             formFields += `
                <div>
                    <label for="modal-field-${header}" class="block text-sm font-medium text-gray-400 mb-2">${header}</label>
                    <input type="text" id="modal-field-${header}" value="${value}" class="form-input w-full">
                </div>
             `;
        }
    });

    modalContent.innerHTML = `
         <div class="flex justify-between items-center mb-6">
            <h2 class="text-2xl font-bold text-white">${isNew ? 'បន្ថែម' : 'កែសម្រួល'}ទិន្នន័យ</h2>
            <button onclick="closeAdminEditModal()" class="text-2xl text-gray-500 hover:text-white">&times;</button>
        </div>
        <div class="space-y-4">${formFields}</div>
        <div class="flex justify-end pt-6 mt-6 border-t border-gray-700">
            <button onclick="closeAdminEditModal()" class="btn btn-secondary mr-4">បោះបង់</button>
            <button onclick="saveAdminEdit('${sheetKey}', ${rowIndex})" class="btn btn-primary">រក្សាទុក</button>
        </div>
    `;
    
    headers.forEach(header => {
        if (header.toLowerCase().includes('imageurl') || header.toLowerCase().includes('logourl')) {
            const uploadInput = document.getElementById(`modal-upload-${header}`);
            const urlInput = document.getElementById(`modal-field-${header}`);
            const previewImg = document.getElementById(`modal-preview-${header}`);

            uploadInput.addEventListener('change', async (e) => {
                const file = e.target.files[0];
                if (!file) return;

                if (isNew) {
                    alert("Please save the new item first before uploading an image.");
                    return;
                }

                const headers = Object.keys(appData.admin[sheetKey][0]);
                const primaryKeyHeader = headers[0];
                const primaryKeyValue = data[primaryKeyHeader]; 
                
                const sheetNameMapping = {
                    'users': 'Users', 'products': 'Products', 'teams': 'TeamsPages', 'shippingMethods': 'ShippingMethods', 
                    'drivers': 'Drivers', 'bankAccounts': 'BankAccounts', 'phoneCarriers': 'PhoneCarriers'
                };
                const actualSheetName = sheetNameMapping[sheetKey];
                const pk = { key: primaryKeyHeader, value: primaryKeyValue };
                
                const newUrl = await uploadFile(file, actualSheetName, pk, header);

                if(newUrl) {
                    urlInput.value = newUrl;
                    previewImg.src = convertGoogleDriveUrl(newUrl);
                    appData.admin[sheetKey][rowIndex][header] = newUrl;
                    await saveSheetData(sheetKey, true); // Silently save the change
                    alert('រូបភាពបាន Upload និងរក្សាទុកដោយស្វ័យប្រវត្តិ។');
                }
            });
             urlInput.addEventListener('input', () => {
                previewImg.src = convertGoogleDriveUrl(urlInput.value);
            });
        }
    });

    modal.classList.remove('hidden');
}

function closeAdminEditModal() {
    document.getElementById('admin-edit-modal').classList.add('hidden');
}

function toggleBoolean(button, value) {
    const container = button.parentElement;
    container.dataset.value = value;
    const buttons = container.querySelectorAll('button');
    if (value) {
        buttons[0].classList.replace('btn-secondary', 'btn-primary');
        buttons[1].classList.replace('btn-primary', 'btn-secondary');
    } else {
        buttons[0].classList.replace('btn-primary', 'btn-secondary');
        buttons[1].classList.replace('btn-secondary', 'btn-primary');
    }
}

async function saveAdminEdit(sheetKey, rowIndex) {
    const isNew = rowIndex === null;
    const headers = appData.admin[sheetKey].length > 0 ? Object.keys(appData.admin[sheetKey][0]) : [];
    const newRowData = {};

    for (const header of headers) {
        const fieldEl = document.getElementById(`modal-field-${header}`);
        if(fieldEl.hasAttribute('data-value')){
             newRowData[header] = fieldEl.dataset.value === 'true';
        } else {
             newRowData[header] = fieldEl.value;
        }
    }

    if (isNew) {
        appData.admin[sheetKey].push(newRowData);
    } else {
        appData.admin[sheetKey][rowIndex] = newRowData;
    }

    await saveSheetData(sheetKey);
    logUserAction('Admin: Save Config', { sheet: sheetKey, isNew: isNew, data: newRowData });
    closeAdminEditModal();
}

async function deleteConfigRow(sheetKey, rowIndex) {
    const choice = await showConfirmation('បញ្ជាក់ការលុប', 'តើអ្នកពិតជាចង់លុបรายการនេះមែនទេ?');
    if (choice === 'primary') {
        const deletedItem = appData.admin[sheetKey].splice(rowIndex, 1);
        await saveSheetData(sheetKey);
        logUserAction('Admin: Delete Config', { sheet: sheetKey, rowIndex: rowIndex, deletedData: deletedItem[0] });
    }
}

async function saveSheetData(sheetKey, isSilent = false) {
    const sheetData = appData.admin[sheetKey];
    if (!sheetData) return;

    const headers = sheetData.length > 0 ? Object.keys(sheetData[0]) : [];
    const dataToSend = [headers, ...sheetData.map(row => headers.map(h => row[h]))];

    const sheetNameMapping = {
        'users': 'Users', 'products': 'Products', 'teams': 'TeamsPages', 'locations': 'Locations',
        'shippingMethods': 'ShippingMethods', 'drivers': 'Drivers', 'bankAccounts': 'BankAccounts',
        'phoneCarriers': 'PhoneCarriers', 'telegramTemplates': 'TelegramTemplates'
    };
    const actualSheetName = sheetNameMapping[sheetKey];

    if (!actualSheetName) {
        if (!isSilent) alert(`Error: No mapping found for ${sheetKey}`);
        return;
    }
    
    if (!isSilent) dataLoader.classList.remove('hidden');

    try {
        const payload = {
            action: 'adminUpdateSheet',
            sheetName: actualSheetName,
            data: dataToSend,
            isSystemAdmin: true,
            adminUser: currentUser.UserName
        };
        const response = await fetch(WEB_APP_URL, {
            method: 'POST',
            body: JSON.stringify(payload),
            headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        });
        const result = await response.json();
        if (result.status !== 'success') throw new Error(result.message);
        
        if (!isSilent) {
            alert(`"${actualSheetName}" បានធ្វើបច្ចុប្បន្នភាពដោយជោគជ័យ!`);
            renderConfigTable(sheetKey);
        }
    } catch (error) {
        console.error('Save Sheet Error:', error);
        if (!isSilent) alert(`មានបញ្ហាក្នុងការរក្សាទុក: ${error.message}`);
    } finally {
        if (!isSilent) dataLoader.classList.add('hidden');
    }
}

async function createAllOrdersView() {
    const contentArea = document.getElementById('admin-content');
    allOrdersData = adminDataCache.orders;

    if (!allOrdersData) {
        contentArea.innerHTML = `<div class="page-card"><div id="data-loader-spinner" class="w-8 h-8 border-2 rounded-full animate-spin mx-auto"></div><p class="text-center mt-2">កំពុងទាញយកប្រតិបត្តិការណ៍...</p></div>`;
        try {
            const response = await fetch(WEB_APP_URL, {
                method: 'POST', body: JSON.stringify({ action: 'adminGetAllOrders' }),
                headers: { 'Content-Type': 'text/plain;charset=utf-8' },
            });
            const result = await response.json();
            if (result.status !== 'success') throw new Error(result.message);
            
            adminDataCache.orders = result.data;
            allOrdersData = result.data;
            localStorage.setItem('adminOrdersCache', JSON.stringify(result.data));
            renderAllOrdersTable();
        } catch (error) {
            contentArea.innerHTML = `<div class="page-card text-red-400">Error loading orders: ${error.message}</div>`;
        }
    } else {
        renderAllOrdersTable();
    }
}


function renderAllOrdersTable() {
    const contentArea = document.getElementById('admin-content');
    const teams = [...new Set(appData.admin.teams.map(t => t.Team))];

    contentArea.innerHTML = `
        <h1 class="text-3xl font-bold text-white mb-6">ប្រតិបត្តិការណ៍ទាំងអស់</h1>
        <div class="page-card">
            <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-4">
                <input type="text" id="orders-search" placeholder="ស្វែងរកតាម ID, ឈ្មោះ, លេខទូរស័ព្ទ..." class="form-input md:col-span-2">
                <select id="orders-team-filter" class="form-select">
                    <option value="">គ្រប់ក្រុម</option>
                    ${teams.map(t => `<option value="${t}">${t}</option>`).join('')}
                </select>
                <input type="date" id="orders-date-filter" class="form-input">
            </div>
            <div class="overflow-x-auto">
                <table id="all-orders-table" class="admin-table">
                    <thead>
                        <tr>
                            <th>Order ID</th><th>Timestamp</th><th>Team</th>
                            <th>Customer Name</th><th>Customer Phone</th><th>Grand Total</th>
                            <th>Payment Status</th><th>Actions</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>
    `;
    applyOrderFilters();
    document.getElementById('orders-search').addEventListener('input', applyOrderFilters);
    document.getElementById('orders-team-filter').addEventListener('change', applyOrderFilters);
    document.getElementById('orders-date-filter').addEventListener('change', applyOrderFilters);
}

function applyOrderFilters() {
    if (!document.getElementById('orders-search')) return;
    const search = document.getElementById('orders-search').value.toLowerCase();
    const team = document.getElementById('orders-team-filter').value;
    const date = document.getElementById('orders-date-filter').value;
    
    const dataToFilter = allOrdersData || [];
    const filteredData = dataToFilter.filter(o => {
        const searchTextMatch = search === '' ||
            (o['Order ID'] && o['Order ID'].toLowerCase().includes(search)) ||
            (o['Customer Name'] && o['Customer Name'].toLowerCase().includes(search)) ||
            (o['Customer Phone'] && o['Customer Phone'].includes(search));

        const teamMatch = team === '' || o.Team === team;
        const dateMatch = date === '' || (o.Timestamp && new Date(o.Timestamp).toLocaleDateString('en-CA') === date);
        return searchTextMatch && teamMatch && dateMatch;
    });
    
    const tableBody = document.querySelector("#all-orders-table tbody");
    if (!tableBody) return;
    tableBody.innerHTML = filteredData.map(o => `
        <tr>
            <td class="font-mono text-sm">${o['Order ID']}</td>
            <td>${o.Timestamp ? new Date(o.Timestamp).toLocaleString() : 'N/A'}</td>
            <td>${o.Team}</td><td>${o['Customer Name']}</td><td>${o['Customer Phone']}</td>
            <td class="font-bold text-blue-400">$${(o['Grand Total'] || 0).toFixed(2)}</td>
            <td>${o['Payment Status']}</td>
            <td class="whitespace-nowrap">
               <button onclick="openAdminOrderDetailView('${o['Order ID']}')" class="action-btn text-blue-400 hover:text-blue-600 p-1" title="មើលលម្អិត & កែសម្រួល">✏️</button>
               <button onclick="deleteOrder('${o['Order ID']}')" class="action-btn text-red-400 hover:text-red-600 p-1" title="លុប">🗑️</button>
            </td>
        </tr>
    `).join('');
}

async function deleteOrder(orderId) {
    const choice = await showConfirmation(
        'បញ្ជាក់ការលុប',
        `តើអ្នកពិតជាចង់លុបប្រតិបត្តិការ ${orderId} មែនទេ? សកម្មភាពនេះមិនអាចមិនអាចត្រឡប់វិញបានទេ។`
    );
    if (choice === 'primary') {
        dataLoader.classList.remove('hidden');
        try {
            const payload = {
                action: 'deleteOrder',
                orderId: orderId,
                adminUser: currentUser.UserName
            };
            const response = await fetch(WEB_APP_URL, {
                method: 'POST', body: JSON.stringify(payload),
                headers: { 'Content-Type': 'text/plain;charset=utf-8' },
            });
            const result = await response.json();
            if (result.status !== 'success') throw new Error(result.message);
            
            // Remove from local data and re-render
            allOrdersData = allOrdersData.filter(o => o['Order ID'] !== orderId);
            adminDataCache.orders = allOrdersData;
            localStorage.setItem('adminOrdersCache', JSON.stringify(allOrdersData));
            applyOrderFilters();
            logUserAction('Admin: Delete Order', { orderId: orderId });
            alert(`Order ${orderId} has been deleted.`);
        } catch (error) {
            alert(`Error deleting order: ${error.message}`);
        } finally {
            dataLoader.classList.add('hidden');
        }
    }
}

async function renderReportsView() {
    const contentArea = document.getElementById('admin-content');
    let data = adminDataCache.reports;

    if (!data) {
        contentArea.innerHTML = `<div class="page-card"><div id="data-loader-spinner" class="w-8 h-8 border-2 rounded-full animate-spin mx-auto"></div><p class="text-center mt-2">កំពុងទាញយកររបាយការណ៍...</p></div>`;
        try {
            const response = await fetch(`${WEB_APP_URL}?action=getReportData`);
            const result = await response.json();
            if (result.status !== 'success') throw new Error(result.message);
            
            adminDataCache.reports = result.data;
            localStorage.setItem('adminReportsCache', JSON.stringify(result.data));
            data = result.data; // Use the freshly fetched data now
        } catch (error) {
            contentArea.innerHTML = `<div class="page-card text-red-400">Error loading reports: ${error.message}</div>`;
            return;
        }
    }

    const months = ["មករា", "កុម្ភៈ", "មីនា", "មេសា", "ឧសភា", "មិថុនា", "កក្កដា", "សីហា", "កញ្ញា", "តុលា", "វិច្ឆិកា", "ធ្នូ"];
    
    let yearlyHtml = Object.keys(data.yearly).sort((a,b) => b-a).map(year => `
        <tr>
            <td>${year}</td>
            <td class="text-green-400">$${data.yearly[year].revenue.toFixed(2)}</td>
            <td class="text-red-400">$${data.yearly[year].expense.toFixed(2)}</td>
            <td class="font-bold text-blue-400">$${data.yearly[year].profit.toFixed(2)}</td>
        </tr>`).join('');
    
    const currentYear = new Date().getFullYear();
    let monthlyHtml = months.map((monthName, index) => {
        const monthKey = `${currentYear}-${String(index + 1).padStart(2, '0')}`;
        const monthData = data.monthly[monthKey];
        return `<tr>
            <td>${monthName}</td>
            <td class="text-green-400">$${monthData ? monthData.revenue.toFixed(2) : '0.00'}</td>
            <td class="text-red-400">$${monthData ? monthData.expense.toFixed(2) : '0.00'}</td>
            <td class="font-bold text-blue-400">$${monthData ? monthData.profit.toFixed(2) : '0.00'}</td>
        </tr>`
    }).join('');

    let companyExpenseHtml = Object.keys(data.byCompany).sort().map(company => `
        <tr>
            <td>${company}</td>
            <td class="text-red-400">$${data.byCompany[company].totalExpense.toFixed(2)}</td>
            <td>${data.byCompany[company].orderCount}</td>
        </tr>`).join('');

    let driverExpenseHtml = Object.keys(data.byDriver).sort().map(driver => `
        <tr>
            <td>${driver}</td>
            <td class="text-red-400">$${data.byDriver[driver].totalExpense.toFixed(2)}</td>
            <td>${data.byDriver[driver].orderCount}</td>
        </tr>`).join('');

    contentArea.innerHTML = `
        <h1 class="text-3xl font-bold text-white mb-6">របាយការណ៍ & ការវិភាគ</h1>
        <div class="space-y-8">
            <div class="page-card"><h2 class="text-xl font-bold text-blue-400 mb-4">ទិន្នន័យប្រចាំឆ្នាំ</h2><div class="overflow-x-auto"><table class="admin-table"><thead><tr><th>ឆ្នាំ</th><th>ចំណូលសរុប</th><th>ចំណាយសរុប</th><th>ប្រាក់ចំណេញ</th></tr></thead><tbody>${yearlyHtml}</tbody></table></div></div>
            <div class="page-card"><h2 class="text-xl font-bold text-blue-400 mb-4">ទិន្នន័យប្រចាំខែ (ឆ្នាំ ${currentYear})</h2><div class="overflow-x-auto"><table class="admin-table"><thead><tr><th>ខែ</th><th>ចំណូលសរុប</th><th>ចំណាយសរុប</th><th>ប្រាក់ចំណេញ</th></tr></thead><tbody>${monthlyHtml}</tbody></table></div></div>
            <div class="page-card"><h2 class="text-xl font-bold text-blue-400 mb-4">ចំណាយតាមក្រុមហ៊ុនដឹកជញ្ជូន</h2><div class="overflow-x-auto"><table class="admin-table"><thead><tr><th>ក្រុមហ៊ុន</th><th>ចំណាយសរុប</th><th>ចំនួនប្រតិបត្តិការ</th></tr></thead><tbody>${companyExpenseHtml}</tbody></table></div></div>
            <div class="page-card"><h2 class="text-xl font-bold text-blue-400 mb-4">ចំណាយតាមអ្នកដឹក</h2><div class="overflow-x-auto"><table class="admin-table"><thead><tr><th>អ្នកដឹក</th><th>ចំណាយសរុប</th><th>ចំនួនប្រតិបត្តិការ</th></tr></thead><tbody>${driverExpenseHtml}</tbody></table></div></div>
        </div>`;
}


// --- NEW Admin Views: Login As & Logs ---
function createLoginAsView() {
    const users = appData.admin.users.filter(u => u.UserName !== currentUser.UserName);
    return `
        <h1 class="text-3xl font-bold text-white mb-6">ចូលប្រើក្នុងនាមជាគណនីផ្សេង</h1>
        <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6">
            ${users.map(user => `
                <div class="page-card text-center p-4 cursor-pointer transition-all duration-200 hover:bg-gray-700 hover:border-blue-500 transform hover:-translate-y-1" onclick="loginAsUser('${user.UserName}')">
                    <img src="${convertGoogleDriveUrl(user.ProfilePictureURL)}" class="w-24 h-24 rounded-full object-cover mx-auto mb-4 border-2 border-gray-600" onerror="this.src='https://placehold.co/100x100/1f2937/4b5563?text=User'">
                    <p class="font-bold text-white truncate">${user.FullName}</p>
                    <p class="text-sm text-gray-400">${user.UserName}</p>
                    <p class="text-xs text-blue-300 mt-1">${user.IsSystemAdmin ? 'System Admin' : (user.Role || 'User')}</p>
                </div>
            `).join('')}
        </div>
    `;
}

async function createLogsView() {
    const contentArea = document.getElementById('admin-content');
    contentArea.innerHTML = `<div class="page-card"><div id="data-loader-spinner" class="w-8 h-8 border-2 rounded-full animate-spin mx-auto"></div><p class="text-center mt-2">កំពុងទាញយក Logs...</p></div>`;
    try {
        const response = await fetch(`${WEB_APP_URL}?action=getLogs`);
        if (!response.ok) throw new Error(`Server responded with status: ${response.status}`);
        const result = await response.json();
        if (result.status !== 'success') throw new Error(result.message);
        renderLogsTable(result.data);
    } catch (error) {
        console.error("Error fetching logs:", error);
        contentArea.innerHTML = `<div class="page-card text-red-400">Error loading logs: ${error.message}</div>`;
    }
}

function renderLogsTable(logsData) {
    const contentArea = document.getElementById('admin-content');
    const users = [...new Set(logsData.map(log => log.UserName))];

    contentArea.innerHTML = `
        <h1 class="text-3xl font-bold text-white mb-6">កំណត់ត្រាសកម្មភាពអ្នកប្រើប្រាស់</h1>
        <div class="page-card">
            <div class="flex flex-col sm:flex-row gap-4 mb-4">
                <input type="text" id="logs-search" placeholder="ស្វែងរកតាមសកម្មភាព, ពត៌មានលំអិត..." class="form-input flex-grow">
                <select id="logs-user-filter" class="form-select sm:w-48">
                    <option value="">អ្នកប្រើប្រាស់ទាំងអស់</option>
                    ${users.map(u => `<option value="${u}">${u}</option>`).join('')}
                </select>
            </div>
            <div class="overflow-x-auto max-h-[60vh]">
                <table id="logs-table" class="admin-table">
                    <thead>
                        <tr>
                            <th>Timestamp</th>
                            <th>UserName</th>
                            <th>Action</th>
                            <th>Details</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${logsData.reverse().map(log => `
                            <tr>
                                <td class="whitespace-nowrap">${new Date(log.Timestamp).toLocaleString()}</td>
                                <td class="font-semibold text-blue-300">${log.UserName}</td>
                                <td class="whitespace-nowrap">${log.Action}</td>
                                <td class="text-xs font-mono max-w-md truncate" title="${log.Details}">${log.Details}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        </div>
    `;

    const applyLogsFilter = () => {
        const search = document.getElementById('logs-search').value.toLowerCase();
        const user = document.getElementById('logs-user-filter').value;
        const tableRows = document.querySelectorAll("#logs-table tbody tr");

        tableRows.forEach(row => {
            const rowUser = row.cells[1].textContent;
            const rowText = row.textContent.toLowerCase();
            const userMatch = user === '' || rowUser === user;
            const searchMatch = search === '' || rowText.includes(search);
            row.style.display = userMatch && searchMatch ? '' : 'none';
        });
    };

    document.getElementById('logs-search').addEventListener('input', applyLogsFilter);
    document.getElementById('logs-user-filter').addEventListener('change', applyLogsFilter);
}


function buildAppUI() {
    appContainer.innerHTML = `
        <div id="teamSelectionPage" class="hidden w-full max-w-4xl mx-auto"><div class="page-card"><h2 class="text-2xl font-bold text-center mb-8 text-white">សូមជ្រើសរើសក្រុមដើម្បីបន្ត</h2><div id="team-selection-buttons" class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 sm:gap-6"></div></div></div>
        <div id="progress-indicator" class="hidden w-full max-w-3xl mx-auto mb-4 sm:mb-8"><div class="flex items-center">
            <div id="step-customer" class="flex flex-col items-center"><div class="step-circle">1</div><div class="step-label">អតិថិជន</div></div>
            <div id="connector-1" class="step-connector flex-1 h-1"></div>
            <div id="step-products" class="flex flex-col items-center"><div class="step-circle">2</div><div class="step-label">ផលិតផល</div></div>
            <div id="connector-2" class="step-connector flex-1 h-1"></div>
            <div id="step-review" class="flex flex-col items-center"><div class="step-circle">3</div><div class="step-label">ត្រួតពិនិត្យ</div></div>
            <div id="connector-3" class="step-connector flex-1 h-1"></div>
            <div id="step-shipping" class="flex flex-col items-center"><div class="step-circle">4</div><div class="step-label">ដឹកជញ្ជូន</div></div>
            <div id="connector-4" class="step-connector flex-1 h-1"></div>
            <div id="step-final" class="flex flex-col items-center"><div class="step-circle">5</div><div class="step-label">ផ្ទៀងផ្ទាត់</div></div>
        </div></div>
        <div id="selectionPage" class="hidden w-full max-w-4xl mx-auto"><div class="page-card"><h2 class="text-2xl font-bold text-center mb-8 text-white">សូមជ្រើសរើស Page</h2><div id="page-selection-buttons" class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 sm:gap-6"></div><button id="back-to-team-btn" class="btn btn-secondary mt-8" onclick="showPage('teamSelectionPage')">ត្រឡប់ទៅជ្រើសរើសក្រុម</button></div></div>
        <div id="customerPage" class="hidden w-full max-w-2xl mx-auto"><div class="page-card"><h2 class="text-2xl font-bold mb-6 text-white">១. ព័ត៌មានអតិថិជន</h2><div class="space-y-4">
            <input type="text" id="customer-name" placeholder="ឈ្មោះអតិថិជន" class="form-input w-full">
            <div class="relative"><input type="tel" id="customer-phone" placeholder="លេខទូរស័ព្ទ" class="form-input w-full pr-12"><img id="phone-carrier-logo" src="" alt="Carrier" class="hidden absolute right-2 top-1/2 -translate-y-1/2 h-8 w-auto object-contain"></div>
            <div class="grid grid-cols-1 sm:grid-cols-3 gap-4"><select id="province" class="form-select w-full"></select><select id="district" class="form-select w-full"></select><select id="sangkat" class="form-select w-full"></select></div>
            <div class="flex items-center"><input type="checkbox" id="add-location-details" class="h-4 w-4 rounded bg-gray-700 border-gray-600 text-blue-600 focus:ring-blue-500"><label for="add-location-details" class="ml-2 text-gray-300">បន្ថែមព័ត៌មានទីតាំងលម្អិត</label></div>
            <input type="text" id="additional-location" placeholder="ផ្ទះលេខ, ផ្លូវ,..." class="form-input w-full hidden">
            <div><label class="block text-sm font-medium text-gray-400 mb-2">ថ្លៃសេវាដឹកជញ្ជូន</label><div class="flex flex-col sm:flex-row sm:space-x-4 space-y-2 sm:space-y-0"><button id="shipping-fee-btn" class="btn btn-primary flex-1">គិតថ្លៃសេវា</button><button id="no-shipping-fee-btn" class="btn btn-secondary flex-1">មិនគិតថ្លៃសេវា</button></div><input type="number" id="shipping-fee-amount" placeholder="តម្លៃសេវាដឹក (ឧ. 1.5)" class="form-input w-full mt-3"></div>
        </div><div class="flex justify-between mt-8"><button class="btn btn-secondary" onclick="goBack('selectionPage')">ត្រឡប់ក្រោយ</button><button class="btn btn-primary" onclick="validateAndGoToProducts()">បន្ទាប់</button></div></div></div>
        <div id="productsPage" class="hidden w-full max-w-2xl mx-auto"><div class="page-card"><h2 class="text-2xl font-bold mb-6 text-white">២. ព័ត៌មានផលិតផល</h2><div id="product-form-container" class="space-y-6"></div><div class="flex flex-col sm:flex-row justify-between mt-8 space-y-2 sm:space-y-0"><button class="btn btn-secondary" onclick="goBack('customerPage')">ត្រឡប់ក្រោយ</button><div class="flex space-x-2 sm:space-x-4"><button id="add-another-product-btn" class="btn btn-secondary flex-1">រក្សាទុក & បន្ថែមថ្មី</button><button id="finish-adding-products-btn" class="btn btn-primary flex-1">រក្សាទុក & បញ្ចប់</button></div></div></div></div>
        <div id="reviewPage" class="hidden w-full max-w-3xl mx-auto"><div class="page-card"><h2 class="text-2xl font-bold mb-6 text-white">៣. ត្រួតពិនិត្យផលិតផល</h2><div id="review-list" class="space-y-4"></div><div class="border-t border-gray-700 mt-6 pt-6 text-right space-y-2"><p class="text-lg">ចំនួនទំនិញសរុប: <span id="review-total-quantity" class="font-bold text-xl text-white">0</span></p><p class="text-lg">តម្លៃទំនិញសរុប (Subtotal): <span id="review-subtotal" class="font-bold text-xl text-blue-400">0.00$</span></p></div><div class="flex justify-between mt-8"><button class="btn btn-secondary" onclick="goBack('productsPage')">ត្រឡប់ក្រោយ</button><button class="btn btn-primary" onclick="goToShipping()">បន្ទាប់</button></div></div></div>
        <div id="shippingPage" class="hidden w-full max-w-2xl mx-auto"><div class="page-card"><h2 class="text-2xl font-bold mb-6 text-white">៤. វិធីសាស្រ្តដឹកជញ្ជូន</h2><div class="space-y-4">
            <div><label for="internal-shipping-method" class="block text-sm font-medium text-gray-400 mb-2">ជ្រើសរើសវិធីសាស្រ្តដឹកជញ្ជូន</label><select id="internal-shipping-method" class="form-select w-full"></select></div>
            <div id="shipping-details-container" class="mt-4"></div>
            <div class="space-y-2 mt-4"><div><label class="block text-sm font-medium text-gray-400 mb-2">តម្លៃសេវាដឹក (ចំណាយ)</label><div class="flex flex-col sm:flex-row sm:space-x-4 space-y-2 sm:space-y-0"><button id="internal-cost-btn" class="btn btn-primary flex-1">គិតថ្លៃចំណាយ</button><button id="no-internal-cost-btn" class="btn btn-secondary flex-1">មិនគិតថ្លៃ</button></div><input type="number" id="internal-shipping-cost" placeholder="0.00" class="form-input w-full mt-3"></div></div>
        </div><div class="flex justify-between mt-8"><button class="btn btn-secondary" onclick="goBack('reviewPage')">ត្រឡប់ក្រោយ</button><button class="btn btn-primary" onclick="goToFinalConfirmation()">បន្ទាប់</button></div></div></div>
        <div id="finalConfirmationPage" class="hidden w-full max-w-3xl mx-auto"><div class="page-card"><h2 class="text-2xl font-bold mb-6 text-white">៥. ផ្ទៀងផ្ទាត់ចុងក្រោយ</h2>
            <div class="mb-6"><h3 class="text-lg font-semibold border-b border-gray-700 pb-2 mb-3 text-blue-400">ព័ត៌មានអតិថិជន</h3><div class="text-gray-300 space-y-1"><p><strong>ឈ្មោះ:</strong> <span id="final-customer-name"></span></p><p><strong>លេខទូរស័ព្ទ:</strong> <span id="final-customer-phone"></span></p><p><strong>ទីតាំង:</strong> <span id="final-customer-location"></span></p><p><strong>អាសយដ្ឋានលម្អិត:</strong> <span id="final-customer-address"></span></p></div></div>
            <div class="mb-6"><h3 class="text-lg font-semibold border-b border-gray-700 pb-2 mb-3 text-blue-400">បញ្ជីផលិតផល</h3><div id="final-products-list" class="space-y-4"></div></div>
            <div class="mb-6"><h3 class="text-lg font-semibold border-b border-gray-700 pb-2 mb-3 text-blue-400">ព័ត៌មានដឹកជញ្ជូន</h3><div class="text-gray-300 space-y-1"><p><strong>ថ្លៃដឹក (គិតពីភ្ញៀវ):</strong> <span id="final-customer-shipping-fee" class="font-bold"></span></p><p><strong>វិធីដឹក:</strong> <span id="final-internal-method"></span></p><p><strong>អ្នកដឹក/Logo:</strong> <span id="final-internal-details"></span></p><p><strong>ចំណាយ:</strong> <span id="final-internal-cost"></span></p></div></div>
            <div class="mb-6"><h3 class="text-lg font-semibold border-b border-gray-700 pb-2 mb-3 text-blue-400">ស្ថានភាពទូទាត់ប្រាក់</h3><div class="flex space-x-6"><label class="flex items-center"><input type="radio" name="payment-status" value="Unpaid" checked class="form-radio h-4 w-4 bg-gray-700 border-gray-600 text-blue-600"> <span class="ml-2">Unpaid</span></label><label class="flex items-center"><input type="radio" name="payment-status" value="Paid" class="form-radio h-4 w-4 bg-gray-700 border-gray-600 text-blue-600"> <span class="ml-2">Paid</span></label></div>
                <div id="payment-details-unpaid" class="mt-2 p-3 bg-red-900/50 border border-red-800 rounded-md text-red-300 font-semibold">ជាឥវ៉ាន់COD</div>
                <div id="payment-details-paid" class="hidden mt-2"><label for="bank-account-select" class="block text-sm font-medium text-gray-400 mb-2">ជ្រើសរើសគណនីធនាគារទទួល:</label><div class="flex items-center space-x-4"><select id="bank-account-select" class="form-select flex-grow"></select><img id="bank-logo-preview" src="" alt="Bank Logo" class="h-10 object-contain hidden bg-white p-1 rounded-md"></div></div>
            </div>
            <div class="mb-6"><h3 class="text-lg font-semibold border-b border-gray-700 pb-2 mb-3 text-blue-400">ចំណាំបន្ថែម</h3><textarea id="final-order-note" class="form-input w-full" rows="3" placeholder="បញ្ចូលចំណាំបន្ថែម (អាចរំលងបាន)..."></textarea></div>
            <div class="border-t border-gray-700 mt-6 pt-6 text-right"><p class="text-xl sm:text-2xl font-bold text-white">សរុបចុងក្រោយ (Grand Total)</p><p class="text-3xl sm:text-4xl font-extrabold text-blue-400" id="final-grand-total">0.00$</p></div>
            <div class="mt-8 border-t border-gray-700 pt-6"><h3 class="text-lg font-semibold mb-3 text-blue-400">ជម្រើស Telegram Bot</h3><div class="flex items-center"><input type="checkbox" id="schedule-telegram" class="h-4 w-4 rounded bg-gray-700 border-gray-600 text-blue-600 focus:ring-blue-500"><label for="schedule-telegram" class="ml-2 text-gray-300">កំណត់ពេលបញ្ជូនសារ</label></div><input type="datetime-local" id="telegram-schedule-time" class="form-input w-full md:w-1/2 mt-3 hidden"></div>
            <div class="flex justify-between mt-8"><button class="btn btn-secondary" onclick="goBack('shippingPage')">កែសម្រួល</button><button id="submit-order-btn" class="btn btn-primary flex items-center"><span id="submit-btn-text">បញ្ជូនទិន្នន័យ</span><div id="loading-spinner" class="hidden w-5 h-5 border-2 border-t-transparent rounded-full animate-spin ml-2"></div></button></div>
        </div></div>
        <div id="submit-feedback" class="hidden fixed inset-0 bg-black bg-opacity-70 flex items-center justify-center z-50 opacity-0 transform scale-95"><div class="text-center text-white">
            <svg id="feedback-success-icon" class="w-16 h-16 text-green-500 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
            <svg id="feedback-error-icon" class="hidden w-16 h-16 text-red-500 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
            <p id="feedback-message" class="text-2xl font-bold"></p>
        </div></div>
    `;
    
    pages.teamSelectionPage = document.getElementById('teamSelectionPage');
    pages.selectionPage = document.getElementById('selectionPage');
    pages.customerPage = document.getElementById('customerPage');
    pages.productsPage = document.getElementById('productsPage');
    pages.reviewPage = document.getElementById('reviewPage');
    pages.shippingPage = document.getElementById('shippingPage');
    pages.finalConfirmationPage = document.getElementById('finalConfirmationPage');

    
    document.getElementById('add-location-details').addEventListener('change', (e) => document.getElementById('additional-location').classList.toggle('hidden', !e.target.checked));
    document.getElementById('shipping-fee-btn').addEventListener('click', () => setShippingFeeMode(true));
    document.getElementById('no-shipping-fee-btn').addEventListener('click', () => setShippingFeeMode(false));
    document.getElementById('customer-phone').addEventListener('input', handlePhoneInput);
    document.getElementById('add-another-product-btn').addEventListener('click', () => addProduct(true));
    document.getElementById('finish-adding-products-btn').addEventListener('click', goToReview);
    document.getElementById('internal-shipping-method').addEventListener('change', renderShippingDetails);
    document.getElementById('internal-cost-btn').addEventListener('click', () => setInternalCostMode(true));
    document.getElementById('no-internal-cost-btn').addEventListener('click', () => setInternalCostMode(false));
    document.querySelectorAll('input[name="payment-status"]').forEach(radio => radio.addEventListener('change', handlePaymentStatusChange));
    document.getElementById('schedule-telegram').addEventListener('change', (e) => {
        const timeInput = document.getElementById('telegram-schedule-time');
        timeInput.classList.toggle('hidden', !e.target.checked);
        if (!e.target.checked) {
          timeInput.value = '';
        }
    });
    document.getElementById('submit-order-btn').addEventListener('click', handleSubmitOrder);
}

// --- Login/Logout & Password Toggle ---
async function handleLogin(e) {
    e.preventDefault();
    loginError.textContent = '';

    const loginBtnText = document.getElementById('login-btn-text');
    const loginSpinner = document.getElementById('login-spinner');
    loginButton.disabled = true;
    loginSpinner.classList.remove('hidden');
    loginBtnText.textContent = 'កំពុងដំណើរការ...';

    const username = usernameInput.value.trim();
    const password = passwordInput.value;

    try {
        const response = await fetch(`${WEB_APP_URL}?action=getUsers`);
        if (!response.ok) throw new Error('Cannot connect to the server.');
        const result = await response.json();
        if (result.status !== 'success') throw new Error(result.message);
        const user = result.data.find(u => u.UserName === username && u.Password == password);

        if (user) {
            currentUser = user; 
            const sessionData = { user: currentUser, timestamp: new Date().getTime() };
            localStorage.setItem('orderAppSession', JSON.stringify(sessionData));
            logUserAction('Login');
            pages.loginPage.classList.add('hidden');
            mainContainer.classList.remove('items-center', 'justify-center'); 
            
            await fetchData(true);

            const teams = (currentUser.Team || '').split(',').map(t => t.trim()).filter(Boolean);
            if (user.IsSystemAdmin) {
                await initializeAdminDataCache(); // Initialize admin data cache
                 if (teams.length > 0) {
                    showPage('roleSelectionPage');
                } else {
                    loadAdminDashboard();
                }
            } else {
                navigateToUserView();
            }
            updateProfileDisplay();
        } else {
            loginError.textContent = 'ឈ្មោះគណនី ឬពាក្យសម្ងាត់មិនត្រឹមត្រូវ';
        }
    } catch(error) {
        loginError.textContent = 'Login failed: ' + error.message;
    } finally {
        loginButton.disabled = false;
        loginSpinner.classList.add('hidden');
        loginBtnText.textContent = 'ចូលប្រើ';
    }
}

function updateProfileDisplay() {
    if (!currentUser) return;
    document.getElementById('user-fullname').textContent = currentUser.FullName || 'N/A';
    document.getElementById('user-role').textContent = currentUser.IsSystemAdmin ? 'System Admin' : (currentUser.Role || 'N/A');
    const avatar = document.getElementById('user-avatar');
    avatar.src = convertGoogleDriveUrl(currentUser.ProfilePictureURL);
    avatar.onerror = () => { avatar.src = 'https://placehold.co/100x100/1f2937/4b5563?text=User'; };
    appHeader.classList.remove('hidden');

    // Hide the old "Switch Account" button in dropdown, it's now in the main admin nav
    switchAccountBtn.classList.add('hidden');

    // Show/hide "Back to Role Select" button for hybrid admins
    const isHybridAdmin = currentUser.IsSystemAdmin && (currentUser.Team || '').split(',').map(t => t.trim()).filter(Boolean).length > 0;
    backToRoleSelectBtn.classList.toggle('hidden', !isHybridAdmin);
}

function togglePasswordVisibility() {
    const isPassword = passwordInput.type === 'password';
    passwordInput.type = isPassword ? 'text' : 'password';
    eyeOpen.classList.toggle('hidden', isPassword);
    eyeClosed.classList.toggle('hidden', !isPassword);
}

function logout(e, sessionExpired = false) {
    if(e) e.preventDefault();
    logUserAction('Logout');
    clearInterval(dataRefreshInterval);
    clearInterval(adminCacheStatusInterval); // Clear admin cache interval
    localStorage.removeItem('orderAppSession');
    localStorage.removeItem('appDataCache'); 
    localStorage.removeItem('originalAdminSession');
    localStorage.removeItem('adminOrdersCache'); // Clear admin cache
    localStorage.removeItem('adminReportsCache'); // Clear admin cache
    currentUser = null;
    originalAdminUser = null;
    selectedTeam = null;
    appContainer.innerHTML = '';
    appContainer.classList.add('hidden');
    appHeader.classList.add('hidden');
    document.getElementById('impersonation-banner').classList.add('hidden');
    document.body.style.paddingTop = '0';
    pages.loginPage.classList.remove('hidden');
    mainContainer.classList.add('items-center', 'justify-center');
    
    if (sessionExpired) {
        loginError.textContent = 'Session បានหมดសុពលភាព, សូមចូលម្តងទៀត។';
    } else {
        loginError.textContent = '';
    }
}

// --- Profile Modal Logic ---
function openEditProfileModal(e) {
    e.preventDefault();
    profileDropdown.classList.add('hidden');
    
    const avatarPreview = document.getElementById('edit-profile-avatar-preview');
    const avatarUrlInput = document.getElementById('edit-profile-picture-url');

    avatarPreview.src = convertGoogleDriveUrl(currentUser.ProfilePictureURL);
    avatarUrlInput.value = currentUser.ProfilePictureURL || '';
    document.getElementById('edit-username').value = currentUser.UserName;
    document.getElementById('edit-fullname').value = currentUser.FullName;
    document.getElementById('edit-password').value = '';
    document.getElementById('edit-confirm-password').value = '';
    document.getElementById('profile-update-error').textContent = '';
    
    avatarUrlInput.addEventListener('input', () => {
        avatarPreview.src = convertGoogleDriveUrl(avatarUrlInput.value);
    });

    document.getElementById('edit-profile-picture-upload').addEventListener('change', async (event) => {
        const file = event.target.files[0];
        if (!file) return;
        const pk = { key: 'UserName', value: currentUser.UserName };
        const newUrl = await uploadFile(file, 'Users', pk, 'ProfilePictureURL');

        if(newUrl) {
            avatarUrlInput.value = newUrl;
            avatarPreview.src = convertGoogleDriveUrl(newUrl);
            currentUser.ProfilePictureURL = newUrl; // Update local state
            await saveSheetData('users', true); // Silently save the change
            alert('រូបភាពបាន Upload និងរក្សាទុកដោយស្វ័យប្រវត្តិ។');
        }
    });

    editProfileModal.classList.remove('hidden');
}

function closeEditProfileModal() {
    editProfileModal.classList.add('hidden');
}

async function handleProfileUpdateSubmit(e) {
    e.preventDefault();
    const errorP = document.getElementById('profile-update-error');
    errorP.textContent = '';
    const newPassword = document.getElementById('edit-password').value;
    const confirmPassword = document.getElementById('edit-confirm-password').value;
    const newFullName = document.getElementById('edit-fullname').value.trim();
    const newProfilePicUrl = document.getElementById('edit-profile-picture-url').value.trim();

    if (!newFullName) {
        errorP.textContent = 'សូមបំពេញឈ្មោះពេញ។';
        return;
    }

    if (newPassword !== confirmPassword) {
        errorP.textContent = 'ពាក្យសម្ងាត់ថ្មីមិនត្រូវគ្នាទេ។';
        return;
    }

    const payload = {
        action: 'updateUserProfile',
        username: currentUser.UserName,
        fullName: newFullName,
        newPassword: newPassword,
        profilePictureURL: newProfilePicUrl,
    };
    
    const saveBtn = document.getElementById('save-profile-btn');
    saveBtn.disabled = true;
    saveBtn.textContent = 'កំពុងរក្សាទុក...';

    try {
        const updateResponse = await fetch(WEB_APP_URL, {
            method: 'POST', body: JSON.stringify(payload),
            headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        });
        const updateResult = await updateResponse.json();
        if (updateResult.status !== 'success') throw new Error(updateResult.message || 'Update failed');
        
        logUserAction('Update Profile');
        localStorage.removeItem('appDataCache'); // Force data refresh
        await fetchData(true);
        currentUser = appData.users.find(u => u.UserName === currentUser.UserName);
        const sessionData = { user: currentUser, timestamp: new Date().getTime() };
        localStorage.setItem('orderAppSession', JSON.stringify(sessionData));
        updateProfileDisplay();
        closeEditProfileModal();
        alert('Profile បានកែសម្រួលដោយជោគជ័យ។');
        if (newPassword) {
            alert('អ្នកបានផ្លាស់ប្តូរពាក្យសម្ងាត់, សូម Log Out ហើយ Log In ចូលម្តងទៀត។');
        }
    } catch (error) {
        console.error('Profile Update Error:', error);
        errorP.textContent = 'ការកែសម្រួលមានបញ្ហា។';
    } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = 'រក្សាទុក';
    }
}

// --- Page Logic: Customer ---
function handlePhoneInput(e) {
    const input = e.target;
    const logoImg = document.getElementById('phone-carrier-logo');

    let phoneNumber = input.value.replace(/[^0-9]/g, '');

    if (phoneNumber.length > 1 && phoneNumber.startsWith('00')) {
         phoneNumber = '0' + phoneNumber.substring(2);
    } else if (phoneNumber.length > 0 && !phoneNumber.startsWith('0')) {
         phoneNumber = '0' + phoneNumber;
    }
    input.value = phoneNumber;

    let foundCarrier = null;

    if (phoneNumber.length >= 2 && appData.phoneCarriers) {
        for (const carrier of appData.phoneCarriers) {
            const prefixesKey = Object.keys(carrier).find(k => k.toLowerCase().includes('prefixes'));
            if (prefixesKey) {
                const prefixes = (carrier[prefixesKey] || '').split(',');
                for (const prefix of prefixes) {
                    if (prefix && phoneNumber.startsWith(prefix.trim())) {
                        foundCarrier = carrier;
                        break;
                    }
                }
            }
            if (foundCarrier) break;
        }
    }
    if (foundCarrier && foundCarrier.CarrierLogoURL) {
        logoImg.src = convertGoogleDriveUrl(foundCarrier.CarrierLogoURL);
        logoImg.classList.remove('hidden');
    } else {
        logoImg.classList.add('hidden');
        logoImg.src = '';
    }
}


function populateStaticDropdowns() {
    const provinceSelect = document.getElementById('province');
    const districtSelect = document.getElementById('district');
    const sangkatSelect = document.getElementById('sangkat');
    const internalShippingMethodSelect = document.getElementById('internal-shipping-method');
    const bankAccountSelect = document.getElementById('bank-account-select');

    const provinces = [...new Set(appData.locations.map(item => item.Province))].sort();
    provinceSelect.innerHTML = '<option value="">-- ជ្រើសរើស ខេត្ត/រាជធានី --</option>' + provinces.map(p => `<option value="${p}">${p}</option>`).join('');
    
    provinceSelect.addEventListener('change', () => {
        const selectedProvince = provinceSelect.value;
        const districts = [...new Set(appData.locations.filter(l => l.Province === selectedProvince).map(l => l.District))].sort();
        districtSelect.innerHTML = '<option value="">-- ជ្រើសរើស ស្រុក/ខណ្ឌ --</option>' + districts.map(d => `<option value="${d}">${d}</option>`).join('');
        sangkatSelect.innerHTML = '<option value="">-- ជ្រើសរើស ឃុំ/សង្កាត់ --</option>';
    });
    
    districtSelect.addEventListener('change', () => {
        const selectedProvince = provinceSelect.value;
        const selectedDistrict = districtSelect.value;
        const sangkats = [...new Set(appData.locations.filter(l => l.Province === selectedProvince && l.District === selectedDistrict && l.Sangkat).map(l => l.Sangkat))].sort();
        sangkatSelect.innerHTML = '<option value="">-- ជ្រើសរើស ឃុំ/សង្កាត់ (អាចរំលងបាន) --</option>' + sangkats.map(s => `<option value="${s}">${s}</option>`).join('');
    });

    internalShippingMethodSelect.innerHTML = '<option value="">-- ជ្រើសរើស --</option>' + appData.shippingMethods.map(s => `<option value="${s.MethodName}">${s.MethodName}</option>`).join('');
    bankAccountSelect.innerHTML = '<option value="">-- ជ្រើសរើសគណនី --</option>' + (appData.bankAccounts || []).map(b => `<option value="${b.BankName}">${b.BankName}</option>`).join('');

    bankAccountSelect.addEventListener('change', e => {
        const selectedBankName = e.target.value;
        const bankLogoPreview = document.getElementById('bank-logo-preview');
        if (!bankLogoPreview) return;

        if (selectedBankName) {
            const bank = appData.bankAccounts.find(b => b.BankName === selectedBankName);
            if (bank && bank.LogoURL) {
                bankLogoPreview.src = convertGoogleDriveUrl(bank.LogoURL);
                bankLogoPreview.classList.remove('hidden');
            } else {
                bankLogoPreview.classList.add('hidden');
            }
        } else {
            bankLogoPreview.classList.add('hidden');
        }
    });
}

function buildTeamSelectionUI(teams) {
    const teamButtonsContainer = document.getElementById('team-selection-buttons');
    teamButtonsContainer.innerHTML = teams.map(team => 
        `<button class="selection-button" data-team="${team}">ក្រុម ${team}</button>`
    ).join('');
    
    teamButtonsContainer.querySelectorAll('.selection-button').forEach(btn => {
        btn.addEventListener('click', (e) => {
            selectedTeam = e.target.dataset.team;
            setupPageSelection();
            showPage('selectionPage');
        });
    });
}

function handleBackToRoleSelection(e) {
    e.preventDefault();
    profileDropdown.classList.add('hidden');
    resetApp(false); // Reset without full page reload
    appContainer.innerHTML = '';
    appContainer.classList.add('hidden');
    showPage('roleSelectionPage');
}

function setupPageSelection() {
    const pageSelectionButtons = document.getElementById('page-selection-buttons');
    const userPages = appData.pages.filter(p => p.Team === selectedTeam);
    pageSelectionButtons.innerHTML = userPages.map(page => 
        `<button class="selection-button" data-page="${page.PageName}" data-telegram-value="${page.TelegramValue}">${page.PageName}</button>`
    ).join('');

    pageSelectionButtons.querySelectorAll('.selection-button').forEach(btn => {
        btn.addEventListener('click', (e) => {
            order.page = e.target.dataset.page;
            order.telegramValue = e.target.dataset.telegramValue;
            showPage('customerPage');
        });
    });

    // Hide "back to team selection" button for single-team users
    const backBtn = document.getElementById('back-to-team-btn');
    const teams = (currentUser.Team || '').split(',').map(t => t.trim()).filter(Boolean);
    if (backBtn) {
        backBtn.classList.toggle('hidden', teams.length <= 1);
    }
}

// ... The rest of the script is largely the same as the original ...
// --- (All functions from setShippingFeeMode to the end of the script) ---
function setShippingFeeMode(isFeeEnabled) {
    const shippingFeeInput = document.getElementById('shipping-fee-amount');
    const feeBtn = document.getElementById('shipping-fee-btn');
    const noFeeBtn = document.getElementById('no-shipping-fee-btn');
    if (isFeeEnabled) {
        feeBtn.classList.replace('btn-secondary', 'btn-primary');
        noFeeBtn.classList.replace('btn-primary', 'btn-secondary');
        shippingFeeInput.classList.remove('hidden');
    } else {
        noFeeBtn.classList.replace('btn-secondary', 'btn-primary');
        feeBtn.classList.replace('btn-primary', 'btn-secondary');
        shippingFeeInput.classList.add('hidden');
        shippingFeeInput.value = '';
    }
}

function validateAndGoToProducts() {
    const name = document.getElementById('customer-name').value;
    const phone = document.getElementById('customer-phone').value;
    const province = document.getElementById('province').value;
    
    if (!name || !phone || !province) {
        alert('សូមបំពេញ ឈ្មោះ, លេខទូរស័ព្ទ, និងខេត្ត/រាជធានី។');
        return;
    }

    const feeInput = document.getElementById('shipping-fee-amount');
    const isFeeEnabled = !feeInput.classList.contains('hidden');
    if (isFeeEnabled && feeInput.value.trim() === '') {
        alert('សូមបញ្ចូលថ្លៃសេវាដឹកជញ្ជូន។');
        return;
    }
    
    order.customer.name = name;
    order.customer.phone = phone;
    order.customer.province = province;
    order.customer.district = document.getElementById('district').value;
    order.customer.sangkat = document.getElementById('sangkat').value;
    order.customer.additionalLocation = document.getElementById('add-location-details').checked ? document.getElementById('additional-location').value : '';
    const fee = parseFloat(feeInput.value);
    order.customer.shippingFee = isNaN(fee) || fee < 0 || !isFeeEnabled ? 0 : fee;

    if (order.products.length === 0) addProduct(false);
    showPage('productsPage');
}

function renumberProductForms() {
    const productForms = document.querySelectorAll('#product-form-container > div[id^="product-form-"]');
    productForms.forEach((form, index) => {
        const titleElement = form.querySelector('h3');
        if (titleElement) {
            titleElement.textContent = `ផលិតផលទី ${index + 1}`;
        }
    });
}

function addProduct(saveCurrent = true) {
    const productFormContainer = document.getElementById('product-form-container');
    if (saveCurrent) {
        const lastProductForm = document.querySelector(`#product-form-container > div:last-child`);
        if (lastProductForm) {
             const lastId = parseInt(lastProductForm.id.split('-').pop());
             if (!saveProductData(lastId, false)) return;
        }
    }
    productFormCounter++;
    const formHtml = createProductForm(productFormCounter);
    productFormContainer.insertAdjacentHTML('beforeend', formHtml);
    attachProductFormListeners(productFormCounter);
    renumberProductForms();
}

function createProductForm(id) {
    return `<div id="product-form-${id}" class="p-4 border border-gray-700 rounded-lg space-y-4 relative">
        <button onclick="removeProduct(event, ${id})" class="absolute top-2 right-2 text-2xl text-gray-500 hover:text-white">&times;</button>
        <h3 class="font-bold text-lg text-blue-400">ផលិតផល</h3>
        <div class="flex flex-col sm:flex-row items-start space-y-4 sm:space-y-0 sm:space-x-4">
            <img id="product-image-${id}" src="https://placehold.co/100x100/1f2937/4b5563?text=Product" class="w-24 h-24 object-cover rounded-md bg-gray-800 flex-shrink-0 cursor-pointer mx-auto sm:mx-0" onclick="showImagePreview(this.src)">
            <div class="w-full space-y-2">
                <div><label class="block text-sm font-medium text-gray-400">ឈ្មោះទំនិញ</label><div class="flex items-center space-x-2">
                    <input type="text" id="product-name-${id}" list="product-suggestions" class="form-input w-full" placeholder="វាយដើម្បីស្វែងរក...">
                    <button type="button" onclick="startBarcodeScanner(${id})" class="btn btn-secondary p-2.5"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" transform="rotate(90 12 12) scale(0.8)"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 8h4m-4 8h4m-4-4h16m-4 4h4m-4-8h4"/></svg></button>
                </div></div>
                <div>
                    <div class="flex items-center"><input type="checkbox" id="specify-color-checkbox-${id}" class="h-4 w-4 rounded bg-gray-700 border-gray-600 text-blue-600 focus:ring-blue-500"><label for="specify-color-checkbox-${id}" class="ml-2 text-gray-300">បញ្ជាក់ពណ៌</label></div>
                    <input type="text" id="color-details-${id}" list="color-suggestions" class="form-input w-full mt-2 hidden" placeholder="ឧ. ខ្មៅ/Black, ស/White">
                </div>
            </div>
        </div>
        <div class="grid grid-cols-2 gap-4">
            <div><label class="block text-sm font-medium text-gray-400">ចំនួន</label><input type="number" id="product-quantity-${id}" value="1" min="1" class="form-input w-full mt-1"></div>
            <div><label class="block text-sm font-medium text-gray-400">តម្លៃដើម (ឯកតា)</label><input type="number" id="product-price-${id}" class="form-input w-full mt-1" placeholder="0.00"></div>
        </div>
        <div><label class="block text-sm font-medium text-gray-400">បញ្ចុះតម្លៃ (អាចរំលងបាន)</label><div class="grid grid-cols-3 gap-2 sm:gap-4 mt-1">
            <input type="number" id="discount-percent-${id}" class="form-input" placeholder="%">
            <input type="number" id="discount-amount-${id}" class="form-input" placeholder="$">
            <input type="number" id="final-price-${id}" class="form-input" placeholder="តម្លៃចុងក្រោយ">
        </div></div>
        <div class="bg-gray-800 p-3 rounded-md text-right"><p class="text-sm text-gray-400">តម្លៃចុងក្រោយ (ឯកតា): <span id="final-unit-price-${id}" class="font-bold text-white">0.00$</span></p><p class="text-lg text-gray-300">សរុប: <span id="product-total-${id}" class="font-bold text-xl text-blue-400">0.00$</span></p></div>
    </div>`;
}

function attachProductFormListeners(id) {
    const nameInput = document.getElementById(`product-name-${id}`);
    const quantityInput = document.getElementById(`product-quantity-${id}`);
    const priceInput = document.getElementById(`product-price-${id}`);
    const percentInput = document.getElementById(`discount-percent-${id}`);
    const amountInput = document.getElementById(`discount-amount-${id}`);
    const finalPriceInput = document.getElementById(`final-price-${id}`);
    const imageEl = document.getElementById(`product-image-${id}`);
    const colorCheckbox = document.getElementById(`specify-color-checkbox-${id}`);
    const colorInput = document.getElementById(`color-details-${id}`);

    nameInput.addEventListener('input', () => {
        const selectedProduct = appData.products.find(p => p.ProductName === nameInput.value);
        if(selectedProduct) {
            priceInput.value = selectedProduct.Price;
            imageEl.src = convertGoogleDriveUrl(selectedProduct.ImageURL);
            calculateDiscount(id, 'custom');
        }
    });

    [quantityInput, priceInput].forEach(el => el.addEventListener('input', () => calculateDiscount(id, 'custom')));
    percentInput.addEventListener('input', () => calculateDiscount(id, 'percent'));
    amountInput.addEventListener('input', () => calculateDiscount(id, 'amount'));
    finalPriceInput.addEventListener('input', () => calculateDiscount(id, 'custom'));

    colorCheckbox.addEventListener('change', () => {
        colorInput.classList.toggle('hidden', !colorCheckbox.checked);
        if (!colorCheckbox.checked) {
            colorInput.value = ''; 
        }
    });
}

function calculateDiscount(id, inputType) {
    const originalPrice = parseFloat(document.getElementById(`product-price-${id}`).value) || 0;
    const percentInput = document.getElementById(`discount-percent-${id}`);
    const amountInput = document.getElementById(`discount-amount-${id}`);
    const finalPriceInput = document.getElementById(`final-price-${id}`);
    let finalPrice = originalPrice;

    if (originalPrice > 0) {
        if (inputType === 'percent') {
            const percent = parseFloat(percentInput.value) || 0;
            finalPrice = originalPrice * (1 - percent / 100);
            amountInput.value = (originalPrice - finalPrice).toFixed(2);
            finalPriceInput.value = finalPrice.toFixed(2);
        } else if (inputType === 'amount') {
            const amount = parseFloat(amountInput.value) || 0;
            finalPrice = originalPrice - amount;
            percentInput.value = amount > 0 ? ((amount / originalPrice) * 100).toFixed(2) : '';
            finalPriceInput.value = finalPrice.toFixed(2);
        } else { 
            finalPrice = parseFloat(finalPriceInput.value);
            if (isNaN(finalPrice)) finalPrice = originalPrice;
            const discountAmount = originalPrice - finalPrice;
            amountInput.value = discountAmount > 0 ? discountAmount.toFixed(2) : '';
            percentInput.value = discountAmount > 0 ? ((discountAmount / originalPrice) * 100).toFixed(2) : '';
        }
    } else {
         [percentInput, amountInput, finalPriceInput].forEach(el => el.value = '');
    }

    document.getElementById(`final-unit-price-${id}`).textContent = `${finalPrice.toFixed(2)}$`;
    calculateProductTotal(id, finalPrice);
}

function calculateProductTotal(id, finalPrice) {
    const quantity = parseInt(document.getElementById(`product-quantity-${id}`).value) || 1;
    document.getElementById(`product-total-${id}`).textContent = `${(quantity * finalPrice).toFixed(2)}$`;
}

function startBarcodeScanner(formId) {
    currentScannerFormId = formId;
    scannerContainer.classList.remove('hidden');
    html5QrCode = new Html5Qrcode("reader");
    const config = { fps: 10, qrbox: { width: 250, height: 250 } };
    html5QrCode.start({ facingMode: "environment" }, config, onScanSuccess)
        .catch(err => console.error("Unable to start scanning.", err));
}

function stopBarcodeScanner() {
    if (html5QrCode && html5QrCode.isScanning) {
        html5QrCode.stop().then(() => {
            scannerContainer.classList.add('hidden');
        }).catch(err => console.error("Error stopping scanner.", err));
    } else {
        scannerContainer.classList.add('hidden');
    }
}

function onScanSuccess(decodedText, decodedResult) {
    stopBarcodeScanner();
    const product = appData.products.find(p => p.Barcode == decodedText);
    if (product && currentScannerFormId) {
        const nameInput = document.getElementById(`product-name-${currentScannerFormId}`);
        nameInput.value = product.ProductName;
        nameInput.dispatchEvent(new Event('input', { bubbles: true })); 
    } else {
        alert(`រកមិនឃើញផលិតផលដែលមានបាកូដ: ${decodedText}`);
    }
}

function removeProduct(event, id) {
    event.preventDefault(); 
    const formToRemove = document.getElementById(`product-form-${id}`);
    if(formToRemove){
         formToRemove.remove();
    }
    order.products = order.products.filter(p => p.id !== id);
    renumberProductForms();
}

function saveProductData(formId, isFinalizing) {
    const form = document.querySelector(`#product-form-${formId}`);
    if (!form) return true;
    
    const name = form.querySelector(`#product-name-${formId}`).value.trim();
    if (!name && !isFinalizing) return true;

    const quantity = parseInt(form.querySelector(`#product-quantity-${formId}`).value);
    const price = parseFloat(form.querySelector(`#product-price-${formId}`).value);
    const finalPrice = parseFloat(form.querySelector(`#final-price-${formId}`).value) || price;
    const discountPercent = parseFloat(form.querySelector(`#discount-percent-${formId}`).value) || 0;

    if (isFinalizing && (!name || isNaN(quantity) || quantity <= 0 || isNaN(price) || price < 0)) {
        alert(`ទិន្នន័យផលិតផលមិនត្រឹមត្រូវ។ សូមបំពេញឈ្មោះ, ចំនួន, និងតម្លៃ។`);
        return false;
    }
    
    const existingProductIndex = order.products.findIndex(p => p.id === formId);
    const productData = {
        id: formId, name, quantity, originalPrice: price,
        colorInfo: form.querySelector(`#color-details-${formId}`).value.trim(),
        finalPrice: finalPrice, total: finalPrice * quantity,
        discountPercent: discountPercent,
        image: form.querySelector(`#product-image-${formId}`).src
    };

    if (name) { 
        if (existingProductIndex > -1) order.products[existingProductIndex] = productData;
        else order.products.push(productData);
    }
    return true;
}

function goToReview() {
    const productForms = document.querySelectorAll('#product-form-container > div[id^="product-form-"]');
    order.products = [];
    let allFormsValid = true;
    productForms.forEach(form => {
        const formId = parseInt(form.id.split('-').pop());
        if (!saveProductData(formId, true)) {
            allFormsValid = false;
        }
    });
    
    if (!allFormsValid) return;

    order.products = order.products.filter(p => p.name && p.name.trim() !== '');

    if (order.products.length === 0) {
        alert('សូមបញ្ចូលផលិតផលយ៉ាងហោចណាស់មួយ។');
        return;
    }
    
    renderReviewPage();
    showPage('reviewPage');
}

function renderShippingDetails(event) {
    const selectedMethodName = event.target.value;
    const container = document.getElementById('shipping-details-container');
    container.innerHTML = '';
    order.shipping.details = null; 

    const method = appData.shippingMethods.find(m => m.MethodName === selectedMethodName);
    if (!method) return;
    
    order.shipping.details = method.MethodName;

    if (method.RequireDriverSelection) {
        renderDriverSelection(container, 'driver-selection');
    } else if (method.AllowManualDriver) {
        if (method.LogoURL) {
            container.innerHTML += `<div class="flex justify-center"><img src="${convertGoogleDriveUrl(method.LogoURL)}" class="h-24 object-contain"></div>`;
        }
        container.innerHTML += `<div class="mt-4">
            <div class="flex items-center"><input type="checkbox" id="manual-driver-checkbox" class="h-4 w-4 rounded bg-gray-700 border-gray-600 text-blue-600 focus:ring-blue-500"><label for="manual-driver-checkbox" class="ml-2 text-gray-300">បញ្ជាក់អ្នកដឹកដោយខ្លួនឯង</label></div>
            <div id="manual-driver-selection-container" class="mt-4 hidden"></div>
        </div>`;
        
        document.getElementById('manual-driver-checkbox').addEventListener('change', e => {
            const manualContainer = document.getElementById('manual-driver-selection-container');
            const isChecked = e.target.checked;
            manualContainer.classList.toggle('hidden', !isChecked);
            if (isChecked) {
                renderDriverSelection(manualContainer, 'manual-driver-selection');
            } else {
                manualContainer.innerHTML = '';
                order.shipping.details = method.MethodName; 
            }
        });
    } else {
         if (method.LogoURL) {
            container.innerHTML += `<div class="flex justify-center"><img src="${convertGoogleDriveUrl(method.LogoURL)}" class="h-24 object-contain"></div>`;
        }
    }
}

function renderDriverSelection(container, radioGroupName) {
    const drivers = appData.drivers || [];
    if (drivers.length > 0) {
        container.innerHTML = `<label class="block text-sm font-medium text-gray-400 mb-2">ជ្រើសរើសអ្នកដឹក *</label><div class="grid grid-cols-2 md:grid-cols-3 gap-4">${drivers.map(driver => `
            <label class="flex flex-col items-center p-3 bg-gray-800 rounded-lg border-2 border-transparent cursor-pointer hover:border-blue-500">
                <input type="radio" name="${radioGroupName}" value="${driver.DriverName}" class="hidden">
                <img src="${convertGoogleDriveUrl(driver.ImageURL)}" class="w-20 h-20 rounded-full object-cover mb-2">
                <span class="text-sm font-medium text-center">${driver.DriverName}</span>
            </label>
        `).join('')}</div>`;
        container.querySelectorAll(`input[name="${radioGroupName}"]`).forEach(radio => {
            radio.addEventListener('change', (e) => {
                order.shipping.details = e.target.value;
                container.querySelectorAll('label').forEach(label => label.classList.remove('border-blue-500', 'bg-gray-700'));
                e.target.parentElement.classList.add('border-blue-500', 'bg-gray-700');
            });
        });
    } else {
        container.innerHTML = `<p class="text-center text-gray-500">មិនមានទិន្នន័យអ្នកដឹកជញ្ជូនទេ។</p>`;
    }
}

function setInternalCostMode(isCostEnabled) {
    const costInput = document.getElementById('internal-shipping-cost');
    const costBtn = document.getElementById('internal-cost-btn');
    const noCostBtn = document.getElementById('no-internal-cost-btn');

    if (isCostEnabled) {
        costBtn.classList.replace('btn-secondary', 'btn-primary');
        noCostBtn.classList.replace('btn-primary', 'btn-secondary');
        costInput.classList.remove('hidden');
    } else {
        noCostBtn.classList.replace('btn-secondary', 'btn-primary');
        costBtn.classList.replace('btn-primary', 'btn-secondary');
        costInput.classList.add('hidden');
        costInput.value = '';
    }
}

function handlePaymentStatusChange(event) {
    const isPaid = event.target.value === 'Paid';
    document.getElementById('payment-details-paid').classList.toggle('hidden', !isPaid);
    document.getElementById('payment-details-unpaid').classList.toggle('hidden', isPaid);
}

function renderReviewPage() {
    const reviewList = document.getElementById('review-list');
    if(order.products.length === 0) {
        reviewList.innerHTML = '<p class="text-center text-gray-500">មិនមានផលិតផលដែលត្រូវត្រួតពិនិត្យទេ។</p>';
        return;
    }
    reviewList.innerHTML = order.products.map(p => {
         const originalTotal = p.originalPrice * p.quantity;
         return `
        <div class="flex flex-col sm:flex-row items-start bg-gray-800 p-3 rounded-lg border border-gray-700">
            <img src="${p.image}" class="w-20 h-20 object-cover rounded-md mr-4 cursor-pointer" onclick="showImagePreview(this.src)">
            <div class="flex-grow space-y-1 w-full">
                <p class="font-bold text-white text-lg">${p.name}</p>
                <div class="text-xs text-gray-400 grid grid-cols-2 gap-x-4">
                    <p>ថ្លៃដើម: ${p.originalPrice.toFixed(2)}$/ឯកតា</p>
                    <p>សរុបដើម: ${originalTotal.toFixed(2)}$</p>
                    <p>បញ្ចុះតម្លៃ: ${p.discountPercent}%</p>
                    ${p.colorInfo ? `<p class="col-span-2 text-green-400">ពណ៌: ${p.colorInfo}</p>` : ''}
                </div>
            </div>
            <div class="text-right flex-shrink-0 ml-0 sm:ml-4 mt-2 sm:mt-0 w-full sm:w-auto border-t sm:border-t-0 border-gray-700 pt-2 sm:pt-0">
                <p class="text-sm text-gray-300">ចំនួន: ${p.quantity}</p>
                <p class="font-bold text-xl text-blue-400 mt-1">${p.total.toFixed(2)}$</p>
                <div class="mt-2">
                    <button class="text-xs text-yellow-400 hover:underline" onclick="editProduct(${p.id})">កែ</button>
                    <button class="text-xs text-red-400 hover:underline ml-2" onclick="deleteProductFromReview(${p.id})">លុប</button>
                </div>
            </div>
        </div>`
    }).join('');
    updateReviewTotals();
}
function updateReviewTotals() {
    const totalQuantity = order.products.reduce((sum, p) => sum + p.quantity, 0);
    const subtotal = order.products.reduce((sum, p) => sum + p.total, 0);
    order.subtotal = subtotal;
    document.getElementById('review-total-quantity').textContent = totalQuantity;
    document.getElementById('review-subtotal').textContent = `${subtotal.toFixed(2)}$`;
}
function editProduct(id) { showPage('productsPage'); }
function deleteProductFromReview(id) {
    if (confirm('តើអ្នកពិតជាចង់លុបផលិតផលនេះមែនទេ?')) {
        order.products = order.products.filter(p => p.id !== id);
        const formToRemove = document.getElementById(`product-form-${id}`);
        if (formToRemove) formToRemove.remove();
        renumberProductForms();
        renderReviewPage();
    }
}
function goToShipping() {
    if (order.products.length === 0) {
        alert('សូមបន្ថែមផលិតផលមុននឹងបន្ត។');
        showPage('productsPage');
        return;
    }
    showPage('shippingPage');
}
function goToFinalConfirmation() {
    order.shipping.method = document.getElementById('internal-shipping-method').value;

    if (!order.shipping.method) {
        alert('សូមជ្រើសរើសវិធីសាស្រ្តដឹកជញ្ជូនជាមុនសិន។');
        return;
    }

    const method = appData.shippingMethods.find(m => m.MethodName === order.shipping.method);

    if (method && method.RequireDriverSelection) {
        const selectedDriver = document.querySelector('#shipping-details-container input[name="driver-selection"]:checked');
         if (!selectedDriver) {
            alert('វិធីសាស្ត្រដឹកជញ្ជូននេះ តម្រូវឲ្យជ្រើសរើសអ្នកដឹក។');
            return;
         }
         order.shipping.details = selectedDriver.value;
    } else if (method && method.AllowManualDriver) {
        const manualDriverCheckbox = document.getElementById('manual-driver-checkbox');
        if (manualDriverCheckbox && manualDriverCheckbox.checked) {
             const selectedManualDriver = document.querySelector('#manual-driver-selection-container input[name="manual-driver-selection"]:checked');
             if (!selectedManualDriver) {
                alert('សូមជ្រើសរើសអ្នកដឹកជញ្ជូន។');
                return;
             }
             order.shipping.details = selectedManualDriver.value;
        }
    }

    const costInput = document.getElementById('internal-shipping-cost');
    const isCostEnabled = !costInput.classList.contains('hidden');
    if (isCostEnabled && costInput.value.trim() === '') {
        alert('សូមបញ្ចូលតម្លៃសេវាដឹក (ចំណាយ) ឬចុច "មិនគិតថ្លៃ"។');
        return;
    }

    const cost = parseFloat(costInput.value);
    order.shipping.cost = isNaN(cost) || cost < 0 || !isCostEnabled ? 0 : cost;
    renderFinalConfirmationPage();
    showPage('finalConfirmationPage');
}
function renderFinalConfirmationPage() {
    document.getElementById('final-customer-name').textContent = order.customer.name;
    document.getElementById('final-customer-phone').textContent = order.customer.phone;
    
    const locationParts = [order.customer.province, order.customer.district, order.customer.sangkat].filter(Boolean);
    document.getElementById('final-customer-location').textContent = locationParts.join(', ');

    document.getElementById('final-customer-address').textContent = order.customer.additionalLocation || '(មិនបានបញ្ជាក់)';
    
    document.getElementById('final-products-list').innerHTML = order.products.map(p => `
         <div class="flex items-start bg-gray-800/50 p-3 rounded-lg">
            <img src="${p.image}" class="w-16 h-16 object-cover rounded-md mr-4 cursor-pointer" onclick="showImagePreview(this.src)">
            <div class="flex-grow">
                <p class="font-bold text-white">${p.name}</p>
                <p class="text-sm text-gray-400">ចំនួន: ${p.quantity} x ${p.finalPrice.toFixed(2)}$</p>
                ${p.colorInfo ? `<p class="text-xs text-green-400">ពណ៌: ${p.colorInfo}</p>` : ''}
            </div>
            <p class="font-semibold text-lg text-blue-400">${p.total.toFixed(2)}$</p>
        </div>
    `).join('');

    document.getElementById('final-customer-shipping-fee').textContent = `${order.customer.shippingFee.toFixed(2)}$`;
    document.getElementById('final-internal-method').textContent = order.shipping.method || '(មិនបានជ្រើសរើស)';
    document.getElementById('final-internal-details').textContent = (order.shipping.details && order.shipping.details !== order.shipping.method) ? order.shipping.details : '(មិនបានបញ្ជាក់)';
    document.getElementById('final-internal-cost').textContent = `${order.shipping.cost.toFixed(2)}$`;
    order.grandTotal = order.subtotal + order.customer.shippingFee;
    document.getElementById('final-grand-total').textContent = `${order.grandTotal.toFixed(2)}$`;
}

async function handleSubmitOrder() {
    const paymentStatus = document.querySelector('input[name="payment-status"]:checked').value;
    const bankAccount = document.getElementById('bank-account-select').value;
    if (paymentStatus === 'Paid' && !bankAccount) {
        alert('សូមជ្រើសរើសគណនីធនាគារទទួលជាមុនសិន។');
        return; 
    }

    const isScheduled = document.getElementById('schedule-telegram').checked;
    const scheduleTimeInput = document.getElementById('telegram-schedule-time');
    if(isScheduled && !scheduleTimeInput.value){
        alert('សូមជ្រើសរើសថ្ងៃខែនិងពេលវេលាសម្រាប់កំណត់ពេលបញ្ជូនសារ។');
        return;
    }

    const submitOrderBtn = document.getElementById('submit-order-btn');
    const spinner = document.getElementById('loading-spinner');
    const btnText = document.getElementById('submit-btn-text');
    
    submitOrderBtn.disabled = true;
    spinner.classList.remove('hidden');
    btnText.textContent = 'កំពុងបញ្ជូន...';

    if (paymentStatus === 'Paid') {
        order.payment = { status: 'Paid', info: bankAccount };
    } else {
         order.payment = { status: 'Unpaid', info: 'ឥវ៉ាន់COD' };
    }

    order.telegram.schedule = isScheduled;
    order.telegram.time = isScheduled ? new Date(scheduleTimeInput.value).toISOString() : null;
    order.note = document.getElementById('final-order-note').value.trim();
    
    const orderData = { 
        action: 'submitOrder', currentUser, selectedTeam, page: order.page, telegramValue: order.telegramValue,
        customer: order.customer, products: order.products.map(({ id, ...rest }) => rest), 
        shipping: order.shipping, payment: order.payment, telegram: order.telegram, 
        subtotal: order.subtotal, grandTotal: order.grandTotal, note: order.note
    };

    try {
        const response = await fetch(WEB_APP_URL, {
            method: 'POST', body: JSON.stringify(orderData),
            headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        });

        const result = await response.json();
        if (result.status !== 'success') {
            throw new Error(result.message || 'Unknown server error');
        }
        logUserAction('Submit Order', { orderId: result.orderId, team: selectedTeam });
        showFeedback(true, 'ការកម្ម៉ង់ត្រូវបានបញ្ជូនដោយជោគជ័យ!');
        setTimeout(() => resetApp(true), 3000);

    } catch (error) {
        console.error('Submission Error:', error);
        showFeedback(false, `មានបញ្ហាក្នុងការបញ្ជូន: ${error.message}`);
        setTimeout(() => resetApp(true), 3000);
    } finally {
        submitOrderBtn.disabled = false;
        spinner.classList.add('hidden');
        btnText.textContent = 'បញ្ជូនទិន្នន័យ';
    }
}

function showFeedback(isSuccess, message) {
    const feedbackEl = document.getElementById('submit-feedback');
    document.getElementById('feedback-success-icon').classList.toggle('hidden', !isSuccess);
    document.getElementById('feedback-error-icon').classList.toggle('hidden', isSuccess);
    document.getElementById('feedback-message').textContent = message;
    feedbackEl.classList.remove('hidden');
    setTimeout(() => feedbackEl.classList.add('opacity-100', 'scale-100'), 10);
}
function resetApp(isAfterSubmit) {
    if (isAfterSubmit) {
        const feedbackEl = document.getElementById('submit-feedback');
        feedbackEl.classList.remove('opacity-100', 'scale-100');
        setTimeout(() => feedbackEl.classList.add('hidden'), 500);
    }
    order = { page: null, telegramValue: null, customer: {}, products: [], shipping: {}, payment: {}, telegram: {}, subtotal: 0, grandTotal: 0, note: '' };
    productFormCounter = 0;
    if (document.getElementById('product-form-container')) {
        document.getElementById('product-form-container').innerHTML = '';
        document.querySelectorAll('input[type="text"], input[type="tel"], input[type="number"], input[type="datetime-local"], textarea').forEach(input => input.value = '');
        document.querySelectorAll('select').forEach(select => select.selectedIndex = 0);
        document.querySelectorAll('input[type="checkbox"], input[type="radio"]').forEach(cb => {
            cb.checked = false;
            if (cb.name === 'payment-status' && cb.value === 'Unpaid') cb.checked = true;
        });
        document.getElementById('additional-location').classList.add('hidden');
        setShippingFeeMode(true);
        setInternalCostMode(true);
        document.getElementById('telegram-schedule-time').classList.add('hidden');
        handlePaymentStatusChange({ target: { value: 'Unpaid' } });
        document.getElementById('shipping-details-container').innerHTML = '';
        document.getElementById('phone-carrier-logo').classList.add('hidden');
        const bankLogoPreview = document.getElementById('bank-logo-preview');
        if (bankLogoPreview) bankLogoPreview.classList.add('hidden');
    }

    const teams = (currentUser.Team || '').split(',').map(t => t.trim()).filter(Boolean);
    if (currentUser.IsSystemAdmin && teams.length > 0) {
        showPage('roleSelectionPage');
    } else if (teams.length > 1) {
        showPage('teamSelectionPage');
    } else if (teams.length === 1) {
        showPage('selectionPage');
    } else {
         // It's a pure admin, so do nothing or go to admin dash
    }
}
function convertGoogleDriveUrl(url) {
    if (!url || typeof url !== 'string') return 'https://placehold.co/100x100/1f2937/4b5563?text=IMG';
    const viewerRegex = /\/d\/([a-zA-Z0-9_-]+)/;
    const match = url.match(viewerRegex);
    if (match && match[1]) return `https://lh3.googleusercontent.com/d/${match[1]}`;
    const ucRegex = /uc\?id=([a-zA-Z0-9_-]+)/;
    const ucMatch = url.match(ucRegex);
    if (ucMatch && ucMatch[1]) return `https://lh3.googleusercontent.com/d/${ucMatch[1]}`;
    if (url.startsWith('https://lh3.googleusercontent.com') || !url.includes('drive.google.com')) return url;
    return 'https://placehold.co/100x100/1f2937/4b5563?text=Error';
}
function updateProductSuggestions() {
    const productDatalist = document.getElementById('product-suggestions');
    if (appData && appData.products) {
        productDatalist.innerHTML = appData.products.map(p => `<option value="${p.ProductName}"></option>`).join('');
    }
}
function updateColorSuggestions() {
    const colorDatalist = document.getElementById('color-suggestions');
    if (appData && appData.colors) {
        colorDatalist.innerHTML = appData.colors.map(c => `<option value="${c.ColorName}"></option>`).join('');
    }
}
function showImagePreview(src) {
    if (!src || src.includes('placehold.co')) return;
    previewImage.src = src;
    imagePreviewModal.classList.remove('hidden');
    setTimeout(() => {
        imagePreviewModal.classList.add('opacity-100');
        previewImage.classList.add('scale-100');
    }, 10);
}
function hideImagePreview() {
    imagePreviewModal.classList.remove('opacity-100');
    previewImage.classList.remove('scale-100');
    setTimeout(() => {
        imagePreviewModal.classList.add('hidden');
        previewImage.src = '';
    }, 300);
}
async function uploadFile(file, sheetName, primaryKey, columnName) {
    if (isUploading) {
        alert('An upload is already in progress.');
        return null;
    }
    isUploading = true;
    dataLoader.classList.remove('hidden');

    try {
        const reader = new FileReader();
        const fileData = await new Promise((resolve, reject) => {
            reader.onloadend = () => resolve(reader.result);
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });

        const base64Data = fileData.split(',')[1];

        const payload = { 
            action: 'uploadImage', 
            fileData: base64Data, 
            fileName: file.name, 
            mimeType: file.type,
            sheetName: sheetName,
            primaryKey: primaryKey,
            columnName: columnName,
            adminUser: currentUser.UserName
        };

        const response = await fetch(WEB_APP_URL, {
            method: 'POST', body: JSON.stringify(payload),
            headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        });
        const result = await response.json();
        if (result.status !== 'success') throw new Error(result.message || 'Upload failed on the server.');
        return result.url;
    } catch (error) {
        console.error('Upload Error:', error);
        alert(`មានបញ្ហាក្នុងការ Upload: ${error.message}`);
        return null;
    } finally {
        isUploading = false;
        dataLoader.classList.add('hidden');
    }
}

async function showConfirmation(title, message, options = {}) {
    const {
        primaryText = 'យល់ព្រម',
        secondaryText = 'បោះបង់'
    } = options;

    const modal = document.getElementById('confirmation-modal');
    document.getElementById('confirmation-title').textContent = title;
    document.getElementById('confirmation-message').textContent = message;
    
    const primaryBtn = document.getElementById('confirm-action-btn');
    const secondaryBtn = document.getElementById('confirm-cancel-btn');
    
    primaryBtn.textContent = primaryText;
    secondaryBtn.textContent = secondaryText;

    modal.classList.remove('hidden');

    return new Promise(resolve => {
        primaryBtn.onclick = () => {
            modal.classList.add('hidden');
            resolve('primary');
        };
        secondaryBtn.onclick = () => {
            modal.classList.add('hidden');
            resolve('secondary');
        };
    });
}


// --- Admin "Login As" Feature ---
function showImpersonationBanner() {
    const banner = document.getElementById('impersonation-banner');
    if (!banner || !originalAdminUser) return;
    banner.innerHTML = `
        <span>កំពុងមើលក្នុងនាម <strong>${currentUser.FullName}</strong>.</span>
        <button onclick="returnToAdmin()" class="ml-4 px-2 py-1 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700">ត្រឡប់ទៅគណនី Admin</button>
    `;
    banner.classList.remove('hidden');
    document.body.style.paddingTop = '40px';
    appHeader.style.top = '40px';
}

async function loginAsUser(username) {
    const choice = await showConfirmation(
        `ចូលប្រើជា ${username}`,
        'សូមជ្រើសរើសរបៀបដែលអ្នកចង់ចូលប្រើ៖', {
            primaryText: 'ប្រើសិទ្ធិ User (Admin នៅពីក្រោយ)', // Impersonate
            secondaryText: 'ចូលជា User ពេញសិទ្ធិ' // Full Login
        }
    );

    const targetUser = appData.admin.users.find(u => u.UserName === username);
    if (!targetUser) {
        alert('User not found.');
        return;
    }

    if (choice === 'primary') { // Impersonate with admin oversight
        logUserAction('Admin: Impersonate Start', { targetUser: username });
        const adminSession = { user: currentUser, timestamp: new Date().getTime() };
        localStorage.setItem('originalAdminSession', JSON.stringify(adminSession));

        const userSession = { user: targetUser, timestamp: new Date().getTime() };
        localStorage.setItem('orderAppSession', JSON.stringify(userSession));
        
        window.location.reload();
    } else if (choice === 'secondary') { // "Full Login" as user, no admin trace
        logUserAction('Admin: Full Login As', { targetUser: username });
        
        localStorage.removeItem('originalAdminSession');
        const userSession = { user: targetUser, timestamp: new Date().getTime() };
        localStorage.setItem('orderAppSession', JSON.stringify(userSession));
        
        window.location.reload();
    }
}

function returnToAdmin() {
    logUserAction('Admin: Return to Admin');
    const adminSessionString = localStorage.getItem('originalAdminSession');
    if (adminSessionString) {
        localStorage.setItem('orderAppSession', adminSessionString);
        localStorage.removeItem('originalAdminSession');
        window.location.reload();
    } else {
        alert('No original admin session found. Logging out.');
        logout();
    }
}

// --- NEW: Admin Order View/Edit Functions ---
function openAdminOrderDetailView(orderId) {
    const orderData = allOrdersData.find(o => o['Order ID'] === orderId);
    if (!orderData) {
        alert('រកមិនឃើញទិន្នន័យប្រតិបត្តិការណ៍ទេ។');
        return;
    }

    const modal = document.getElementById('admin-edit-modal');
    const modalContent = document.getElementById('admin-edit-modal-content');

    modalContent.innerHTML = `
        <div class="flex justify-between items-center mb-6">
            <h2 class="text-2xl font-bold text-white">កែសម្រួលប្រតិបត្តិការណ៍ <span class="font-mono text-xl text-blue-400">${orderId}</span></h2>
            <button onclick="closeAdminEditModal()" class="text-2xl text-gray-500 hover:text-white">&times;</button>
        </div>
        <div id="admin-order-edit-form" class="space-y-4 text-sm">
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div><label class="font-semibold text-gray-400">Customer Name</label><input type="text" id="edit-Customer Name" value="${orderData['Customer Name'] || ''}" class="form-input w-full mt-1"></div>
                <div><label class="font-semibold text-gray-400">Customer Phone</label><input type="text" id="edit-Customer Phone" value="${orderData['Customer Phone'] || ''}" class="form-input w-full mt-1"></div>
                <div><label class="font-semibold text-gray-400">Location</label><input type="text" id="edit-Location" value="${orderData['Location'] || ''}" class="form-input w-full mt-1"></div>
                <div><label class="font-semibold text-gray-400">Address Details</label><input type="text" id="edit-Address Details" value="${orderData['Address Details'] || ''}" class="form-input w-full mt-1"></div>
            </div>
            <hr class="border-gray-700">
            <div>
                <label class="font-semibold text-gray-400">Products (JSON)</label>
                <textarea id="edit-Products (JSON)" class="form-textarea w-full h-32 mt-1 font-mono text-xs">${orderData['Products (JSON)'] || '[]'}</textarea>
            </div>
            <hr class="border-gray-700">
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div><label class="font-semibold text-gray-400">Shipping Method</label><input type="text" id="edit-Internal Shipping Method" value="${orderData['Internal Shipping Method'] || ''}" class="form-input w-full mt-1"></div>
                <div><label class="font-semibold text-gray-400">Shipping Details</label><input type="text" id="edit-Internal Shipping Details" value="${orderData['Internal Shipping Details'] || ''}" class="form-input w-full mt-1"></div>
                <div><label class="font-semibold text-gray-400">Payment Status</label><input type="text" id="edit-Payment Status" value="${orderData['Payment Status'] || ''}" class="form-input w-full mt-1"></div>
                <div><label class="font-semibold text-gray-400">Payment Info</label><input type="text" id="edit-Payment Info" value="${orderData['Payment Info'] || ''}" class="form-input w-full mt-1"></div>
                <div><label class="font-semibold text-gray-400">Shipping Fee (Customer)</label><input type="number" id="edit-Shipping Fee (Customer)" value="${orderData['Shipping Fee (Customer)'] || 0}" class="form-input w-full mt-1"></div>
                <div><label class="font-semibold text-gray-400">Internal Cost</label><input type="number" id="edit-Internal Cost" value="${orderData['Internal Cost'] || 0}" class="form-input w-full mt-1"></div>
            </div>
        </div>
        <div class="flex justify-end pt-6 mt-6 border-t border-gray-700">
            <button onclick="closeAdminEditModal()" class="btn btn-secondary mr-4">បោះបង់</button>
            <button onclick="saveAdminOrderChanges('${orderId}')" class="btn btn-primary">រក្សាទុកការផ្លាស់ប្តូរ</button>
        </div>
    `;
    
    modal.classList.remove('hidden');
}

async function saveAdminOrderChanges(orderId) {
    const form = document.getElementById('admin-order-edit-form');
    const originalOrderData = allOrdersData.find(o => o['Order ID'] === orderId);
    const updatedData = {};
    let hasChanges = false;
    
    // Get all headers from the original data to iterate over editable fields
    const editableHeaders = [
        'Customer Name', 'Customer Phone', 'Location', 'Address Details', 
        'Products (JSON)', 'Internal Shipping Method', 'Internal Shipping Details',
        'Payment Status', 'Payment Info', 'Shipping Fee (Customer)', 'Internal Cost'
    ];

    for (const header of editableHeaders) {
        const input = form.querySelector(`#edit-${header}`);
        if (input) {
            const newValue = input.type === 'number' ? parseFloat(input.value) : input.value;
            if (String(newValue) !== String(originalOrderData[header])) {
                updatedData[header] = newValue; // Only include changed fields
                hasChanges = true;
            }
        }
    }
    
    // Manual calculation for Grand Total if relevant fields changed
    const subtotal = JSON.parse(updatedData['Products (JSON)'] || originalOrderData['Products (JSON)']).reduce((sum, p) => sum + (p.total || (p.quantity * p.finalPrice)), 0);
    const shippingFee = updatedData['Shipping Fee (Customer)'] !== undefined ? updatedData['Shipping Fee (Customer)'] : originalOrderData['Shipping Fee (Customer)'];
    const newGrandTotal = subtotal + shippingFee;
    
    if(newGrandTotal !== originalOrderData['Grand Total']) {
        updatedData['Subtotal'] = subtotal;
        updatedData['Grand Total'] = newGrandTotal;
        hasChanges = true;
    }


    if (!hasChanges) {
        alert("មិនមានការផ្លាស់ប្តូរដែលត្រូវរក្សាទុកទេ។");
        return;
    }

    const choice = await showConfirmation(
        'បញ្ជាក់ការកែសម្រួល',
        'តើអ្នកពិតជាចង់រក្សាទុកការផ្លាស់ប្តូរសម្រាប់ប្រតិបត្តិការណ៍នេះមែនទេ?'
    );

    if (choice === 'primary') {
        dataLoader.classList.remove('hidden');
        try {
             const payload = {
                action: 'adminUpdateOrder',
                orderId: orderId,
                updatedData: updatedData,
                adminUser: currentUser.UserName
            };
            const response = await fetch(WEB_APP_URL, {
                method: 'POST', body: JSON.stringify(payload),
                headers: { 'Content-Type': 'text/plain;charset=utf-8' },
            });
            const result = await response.json();
            if (result.status !== 'success') throw new Error(result.message);

            // Update local data and re-render table
            const index = allOrdersData.findIndex(o => o['Order ID'] === orderId);
            if (index > -1) {
                allOrdersData[index] = { ...allOrdersData[index], ...updatedData };
            }
            adminDataCache.orders = allOrdersData;
            localStorage.setItem('adminOrdersCache', JSON.stringify(allOrdersData));
            applyOrderFilters();
            logUserAction('Admin: Update Order', { orderId: orderId, changes: updatedData });
            closeAdminEditModal();
            alert(`ប្រតិបត្តិការណ៍ ${orderId} បានធ្វើបច្ចុប្បន្នភាពដោយជោគជ័យ។`);
        } catch (error) {
             alert(`Error updating order: ${error.message}`);
        } finally {
            dataLoader.classList.add('hidden');
        }
    }
}

