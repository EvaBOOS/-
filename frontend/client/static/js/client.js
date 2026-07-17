const API_BASE = '/api/v1';
let authToken = localStorage.getItem('client_token');
let currentClient = null;
let currentPage = 1;
let pollingInterval = null;

// API Helper
async function api(endpoint, options = {}) {
    const url = `${API_BASE}${endpoint}`;
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };
    
    if (authToken) {
        headers['Authorization'] = `Bearer ${authToken}`;
    }
    
    const response = await fetch(url, {
        ...options,
        headers
    });
    
    if (response.status === 401) {
        logout();
        throw new Error('Сессия истекла');
    }
    
    const data = await response.json();
    
    if (!response.ok) {
        throw new Error(data.detail || 'Ошибка API');
    }
    
    return data;
}

// Auth Functions
async function login(email, password) {
    const data = await api('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password })
    });
    
    authToken = data.access_token;
    localStorage.setItem('client_token', authToken);
    
    return data;
}

function logout() {
    authToken = null;
    currentClient = null;
    localStorage.removeItem('client_token');
    if (pollingInterval) {
        clearInterval(pollingInterval);
        pollingInterval = null;
    }
    showScreen('login');
}

async function checkAuth() {
    if (!authToken) {
        showScreen('login');
        return false;
    }
    
    try {
        const user = await api('/auth/me');
        if (user.role !== 'client') {
            logout();
            return false;
        }
        
        currentClient = await api('/client/profile');
        updateCreditsDisplay();
        document.getElementById('company-name').textContent = currentClient.company_name || 'Моя компания';
        
        return true;
    } catch (e) {
        logout();
        return false;
    }
}

function updateCreditsDisplay() {
    if (currentClient) {
        document.getElementById('credits-count').textContent = currentClient.credits_remaining;
    }
}

// Screen Navigation
function showScreen(screenName) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(`${screenName}-screen`).classList.add('active');
}

function showPage(pageName) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById(`page-${pageName}`).classList.add('active');
    
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelector(`.nav-item[data-page="${pageName}"]`)?.classList.add('active');
    
    const titles = {
        'generate': 'Создать видео',
        'history': 'Мои видео',
        'profile': 'Профиль'
    };
    document.getElementById('page-title').textContent = titles[pageName] || pageName;
    
    if (pageName === 'history') {
        loadGenerations();
    } else if (pageName === 'profile') {
        loadProfile();
    }
}

// Generate Video
async function generateVideo(text, language) {
    return await api('/client/generate', {
        method: 'POST',
        body: JSON.stringify({
            original_text: text,
            target_language: language
        })
    });
}

// Load Generations
async function loadGenerations(page = 1, statusFilter = '') {
    currentPage = page;
    
    try {
        let url = `/client/generations?page=${page}&per_page=12`;
        if (statusFilter) {
            url += `&status_filter=${statusFilter}`;
        }
        
        const result = await api(url);
        renderGenerations(result);
    } catch (e) {
        console.error('Failed to load generations:', e);
    }
}

function renderGenerations(data) {
    const container = document.getElementById('generations-list');
    
    if (!data.items.length) {
        container.innerHTML = `
            <div class="empty-state" style="grid-column: 1/-1;">
                <div class="empty-state-icon">📹</div>
                <h3>Пока нет видео</h3>
                <p>Создайте своё первое видео во вкладке "Создать видео"</p>
            </div>
        `;
        document.getElementById('pagination').innerHTML = '';
        return;
    }
    
    container.innerHTML = data.items.map(gen => {
        const statusClass = getStatusClass(gen.status);
        const statusIcon = getStatusIcon(gen.status);
        const statusText = getStatusText(gen.status);
        
        return `
            <div class="generation-card" onclick="openGenerationModal(${gen.id})">
                <div class="generation-thumbnail ${statusClass}">
                    ${statusIcon}
                </div>
                <div class="generation-info">
                    <h4>${truncateText(gen.original_text, 50)}</h4>
                    <div class="generation-meta">
                        <span class="status-badge status-${gen.status}">${statusText}</span>
                        <span>${formatDate(gen.created_at)}</span>
                    </div>
                </div>
            </div>
        `;
    }).join('');
    
    renderPagination(data);
}

function renderPagination(data) {
    const container = document.getElementById('pagination');
    
    if (data.pages <= 1) {
        container.innerHTML = '';
        return;
    }
    
    let html = '';
    
    html += `<button ${currentPage === 1 ? 'disabled' : ''} onclick="loadGenerations(${currentPage - 1})">←</button>`;
    
    for (let i = 1; i <= data.pages; i++) {
        if (i === 1 || i === data.pages || (i >= currentPage - 2 && i <= currentPage + 2)) {
            html += `<button class="${i === currentPage ? 'active' : ''}" onclick="loadGenerations(${i})">${i}</button>`;
        } else if (i === currentPage - 3 || i === currentPage + 3) {
            html += `<button disabled>...</button>`;
        }
    }
    
    html += `<button ${currentPage === data.pages ? 'disabled' : ''} onclick="loadGenerations(${currentPage + 1})">→</button>`;
    
    container.innerHTML = html;
}

function getStatusClass(status) {
    if (status === 'completed') return 'completed';
    if (status === 'failed') return 'failed';
    if (['script_generation', 'voice_synthesis', 'avatar_generation', 'video_processing'].includes(status)) return 'processing';
    return '';
}

function getStatusIcon(status) {
    const icons = {
        pending: '⏳',
        script_generation: '📝',
        voice_synthesis: '🎙️',
        avatar_generation: '👤',
        video_processing: '🎬',
        completed: '✅',
        failed: '❌'
    };
    return icons[status] || '❓';
}

function getStatusText(status) {
    const texts = {
        pending: 'Ожидание',
        script_generation: 'Генерация сценария',
        voice_synthesis: 'Озвучка',
        avatar_generation: 'Создание аватара',
        video_processing: 'Обработка видео',
        completed: 'Готово',
        failed: 'Ошибка'
    };
    return texts[status] || status;
}

// Generation Modal
let currentGenerationId = null;

async function openGenerationModal(generationId) {
    currentGenerationId = generationId;
    
    try {
        const gen = await api(`/client/generations/${generationId}`);
        renderGenerationModal(gen);
        document.getElementById('generation-modal').classList.add('active');
        
        if (!['completed', 'failed'].includes(gen.status)) {
            startPolling(generationId);
        }
    } catch (e) {
        alert('Ошибка загрузки: ' + e.message);
    }
}

function renderGenerationModal(gen) {
    document.getElementById('modal-status').textContent = getStatusText(gen.status);
    document.getElementById('modal-status').className = `status-badge status-${gen.status}`;
    document.getElementById('modal-progress').style.width = `${gen.progress_percent}%`;
    document.getElementById('modal-progress-text').textContent = `${gen.progress_percent}%`;
    
    document.getElementById('modal-original-text').textContent = gen.original_text;
    
    const scriptSection = document.getElementById('script-section');
    if (gen.generated_script) {
        scriptSection.style.display = 'block';
        document.getElementById('modal-generated-script').textContent = gen.generated_script;
    } else {
        scriptSection.style.display = 'none';
    }
    
    const errorSection = document.getElementById('error-section');
    if (gen.error_message) {
        errorSection.style.display = 'block';
        document.getElementById('modal-error').textContent = gen.error_message;
    } else {
        errorSection.style.display = 'none';
    }
    
    const videoSection = document.getElementById('video-section');
    const downloadBtn = document.getElementById('download-btn');
    
    if (gen.status === 'completed' && gen.final_video_path) {
        videoSection.style.display = 'block';
        downloadBtn.style.display = 'inline-flex';
        downloadBtn.href = `/api/v1/client/generations/${gen.id}/download`;
        
        document.getElementById('modal-duration').textContent = 
            `Длительность: ${gen.duration_seconds || 0} сек`;
        document.getElementById('modal-size').textContent = 
            `Размер: ${formatFileSize(gen.file_size_bytes || 0)}`;
    } else {
        videoSection.style.display = 'none';
        downloadBtn.style.display = 'none';
    }
}

function startPolling(generationId) {
    if (pollingInterval) {
        clearInterval(pollingInterval);
    }
    
    pollingInterval = setInterval(async () => {
        try {
            const gen = await api(`/client/generations/${generationId}`);
            renderGenerationModal(gen);
            
            if (['completed', 'failed'].includes(gen.status)) {
                clearInterval(pollingInterval);
                pollingInterval = null;
                loadGenerations(currentPage);
                
                const credits = await api('/client/credits');
                currentClient.credits_remaining = credits.credits_remaining;
                updateCreditsDisplay();
            }
        } catch (e) {
            console.error('Polling error:', e);
        }
    }, 3000);
}

function closeModal() {
    document.getElementById('generation-modal').classList.remove('active');
    currentGenerationId = null;
    
    if (pollingInterval) {
        clearInterval(pollingInterval);
        pollingInterval = null;
    }
}

// Profile
async function loadProfile() {
    try {
        const client = await api('/client/profile');
        const branding = await api('/client/branding');
        const credits = await api('/client/credits');
        
        const planLimits = { basic: 15, standard: 30, premium: 60 };
        const planNames = { basic: 'Basic', standard: 'Standard', premium: 'Premium' };
        
        document.getElementById('profile-plan').textContent = planNames[client.subscription_plan];
        document.getElementById('profile-credits').textContent = 
            `${credits.credits_remaining} / ${planLimits[client.subscription_plan]}`;
        document.getElementById('profile-used').textContent = credits.credits_used_this_month;
        document.getElementById('profile-cycle').textContent = formatDate(credits.billing_cycle_start);
        
        document.getElementById('profile-font').textContent = branding.subtitle_font_name;
        document.getElementById('profile-font-color').textContent = branding.subtitle_font_color;
        document.getElementById('profile-font-color-preview').style.backgroundColor = branding.subtitle_font_color;
        
        if (branding.watermark_path) {
            document.getElementById('profile-watermark').innerHTML = 
                `<img src="/media/uploads/watermarks/${branding.watermark_path.split('/').pop()}" alt="Logo">`;
        }
    } catch (e) {
        console.error('Failed to load profile:', e);
    }
}

// Utilities
function truncateText(text, maxLength) {
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + '...';
}

function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('ru-RU', {
        day: '2-digit',
        month: '2-digit',
        year: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// Event Listeners
document.addEventListener('DOMContentLoaded', async () => {
    const isAuthenticated = await checkAuth();
    if (isAuthenticated) {
        showScreen('dashboard');
        showPage('generate');
    }
    
    // Login form
    document.getElementById('login-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const email = document.getElementById('email').value;
        const password = document.getElementById('password').value;
        const errorEl = document.getElementById('login-error');
        
        try {
            await login(email, password);
            const isClient = await checkAuth();
            if (isClient) {
                showScreen('dashboard');
                showPage('generate');
            } else {
                errorEl.textContent = 'Доступ только для клиентов';
            }
        } catch (e) {
            errorEl.textContent = e.message;
        }
    });
    
    // Logout
    document.getElementById('logout-btn').addEventListener('click', logout);
    
    // Navigation
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', () => {
            const page = item.dataset.page;
            showPage(page);
        });
    });
    
    // Character counter
    const scriptText = document.getElementById('script-text');
    const charCount = document.getElementById('char-count');
    scriptText.addEventListener('input', () => {
        charCount.textContent = scriptText.value.length;
    });
    
    // Generate form
    document.getElementById('generate-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const errorEl = document.getElementById('generate-error');
        errorEl.textContent = '';
        
        const text = document.getElementById('script-text').value;
        const language = document.getElementById('language').value;
        
        if (currentClient.credits_remaining <= 0) {
            errorEl.textContent = 'Недостаточно кредитов. Обратитесь к администратору.';
            return;
        }
        
        try {
            const gen = await generateVideo(text, language);
            
            currentClient.credits_remaining--;
            updateCreditsDisplay();
            
            document.getElementById('script-text').value = '';
            charCount.textContent = '0';
            
            showPage('history');
            openGenerationModal(gen.id);
        } catch (e) {
            errorEl.textContent = e.message;
        }
    });
    
    // Status filter
    document.getElementById('status-filter').addEventListener('change', (e) => {
        loadGenerations(1, e.target.value);
    });
    
    // Refresh button
    document.getElementById('refresh-btn').addEventListener('click', () => {
        const filter = document.getElementById('status-filter').value;
        loadGenerations(currentPage, filter);
    });
    
    // Modal controls
    document.querySelectorAll('.modal-close').forEach(btn => {
        btn.addEventListener('click', closeModal);
    });
    
    // Close modal on outside click
    document.getElementById('generation-modal').addEventListener('click', (e) => {
        if (e.target.id === 'generation-modal') closeModal();
    });
});
