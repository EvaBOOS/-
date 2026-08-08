const API_BASE = '/api/v1';
let authToken = localStorage.getItem('admin_token');
let currentUser = null;

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
        throw new Error('Session expired');
    }
    
    const data = await response.json();
    
    if (!response.ok) {
        throw new Error(data.detail || 'API Error');
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
    localStorage.setItem('admin_token', authToken);
    
    return data;
}

function logout() {
    authToken = null;
    currentUser = null;
    localStorage.removeItem('admin_token');
    showScreen('login');
}

async function checkAuth() {
    if (!authToken) {
        showScreen('login');
        return false;
    }
    
    try {
        currentUser = await api('/auth/me');
        if (currentUser.role !== 'admin') {
            logout();
            return false;
        }
        document.getElementById('user-email').textContent = currentUser.email;
        return true;
    } catch (e) {
        logout();
        return false;
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
        'overview': 'Обзор',
        'clients': 'Клиенты',
        'applications': 'Заявки',
        'create-client': 'Новый клиент'
    };
    document.getElementById('page-title').textContent = titles[pageName] || pageName;

    if (pageName === 'overview') {
        loadStats();
    } else if (pageName === 'clients') {
        loadClients();
    } else if (pageName === 'applications') {
        loadApplications();
    }
}

const ACCOUNT_TYPE_LABELS = { company: 'Компания', blogger: 'Блогер' };

// Load Applications
async function loadApplications() {
    try {
        const applications = await api('/admin/applications?status_filter=pending');
        const tbody = document.getElementById('applications-table-body');

        if (!applications.length) {
            tbody.innerHTML = '<tr><td colspan="7">Нет новых заявок</td></tr>';
            return;
        }

        tbody.innerHTML = applications.map(a => `
            <tr>
                <td>${escapeHtml(a.full_name)}</td>
                <td>${escapeHtml(a.email)}</td>
                <td><span class="badge badge-info">${ACCOUNT_TYPE_LABELS[a.account_type] || a.account_type}</span></td>
                <td>${escapeHtml(a.company_name || a.portfolio_url || '-')}</td>
                <td>${escapeHtml(a.message || '-')}</td>
                <td>${new Date(a.created_at).toLocaleDateString('ru-RU')}</td>
                <td>
                    <button class="action-btn action-btn-edit" onclick="openApproveModal(${a.id}, '${escapeAttr(a.email)}', '${escapeAttr(a.company_name || a.portfolio_url || '')}')">Одобрить</button>
                    <button class="action-btn action-btn-danger" onclick="rejectApplication(${a.id})">Отклонить</button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        console.error('Failed to load applications:', e);
    }
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str ?? '';
    return div.innerHTML;
}

function escapeAttr(str) {
    return escapeHtml(str).replace(/'/g, '&#39;');
}

function openApproveModal(applicationId, email, company) {
    document.getElementById('approve-application-id').value = applicationId;
    document.getElementById('approve-email').value = email;
    document.getElementById('approve-password').value = '';
    document.getElementById('approve-company').value = company;
    document.getElementById('approve-plan').value = 'basic';
    document.getElementById('approve-discount').value = 0;
    document.getElementById('approve-error').textContent = '';
    document.getElementById('approve-modal').classList.add('active');
}

function closeApproveModal() {
    document.getElementById('approve-modal').classList.remove('active');
}

async function submitApproval() {
    const applicationId = document.getElementById('approve-application-id').value;
    const errorEl = document.getElementById('approve-error');
    errorEl.textContent = '';

    const password = document.getElementById('approve-password').value;
    if (!password || password.length < 8) {
        errorEl.textContent = 'Пароль должен быть не короче 8 символов';
        return;
    }

    try {
        await api(`/admin/applications/${applicationId}/approve`, {
            method: 'POST',
            body: JSON.stringify({
                user_password: password,
                company_name: document.getElementById('approve-company').value || null,
                subscription_plan: document.getElementById('approve-plan').value,
                discount_percent: parseInt(document.getElementById('approve-discount').value) || 0
            })
        });
        closeApproveModal();
        loadApplications();
    } catch (e) {
        errorEl.textContent = e.message;
    }
}

async function rejectApplication(applicationId) {
    if (!confirm('Отклонить эту заявку?')) return;
    try {
        await api(`/admin/applications/${applicationId}/reject`, {
            method: 'POST',
            body: JSON.stringify({})
        });
        loadApplications();
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}

// Load Stats
async function loadStats() {
    try {
        const stats = await api('/admin/stats');
        document.getElementById('stat-clients').textContent = stats.total_clients;
        document.getElementById('stat-active').textContent = stats.active_clients;
        document.getElementById('stat-videos').textContent = stats.total_generations;
        document.getElementById('stat-success').textContent = `${stats.success_rate}%`;
    } catch (e) {
        console.error('Failed to load stats:', e);
    }
}

// Load Clients
async function loadClients() {
    try {
        const clients = await api('/admin/clients');
        const tbody = document.getElementById('clients-table-body');
        
        tbody.innerHTML = clients.map(client => `
            <tr>
                <td>${client.id}</td>
                <td>${client.company_name || '-'}</td>
                <td><span class="badge badge-info">${ACCOUNT_TYPE_LABELS[client.account_type] || client.account_type}</span></td>
                <td>${client.user_email || '-'}</td>
                <td><span class="badge badge-info">${client.subscription_plan}</span></td>
                <td>${client.credits_remaining} / ${getPlanLimit(client.subscription_plan)}</td>
                <td>
                    <span class="badge ${client.is_active ? 'badge-success' : 'badge-danger'}">
                        ${client.is_active ? 'Активен' : 'Неактивен'}
                    </span>
                </td>
                <td>
                    <button class="action-btn action-btn-edit" onclick="openClientModal(${client.id})">
                        Редактировать
                    </button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        console.error('Failed to load clients:', e);
    }
}

function getPlanLimit(plan) {
    const limits = { basic: 15, standard: 30, premium: 60 };
    return limits[plan] || 15;
}

// Create Client
async function createClient(formData) {
    const data = {
        user_email: formData.email,
        user_password: formData.password,
        user_full_name: formData.name,
        company_name: formData.company,
        account_type: formData.accountType,
        discount_percent: formData.discountPercent,
        offer_notes: formData.offerNotes || null,
        subscription_plan: formData.plan,
        elevenlabs_voice_id: formData.voiceId || null,
        heygen_avatar_id: formData.avatarId || null
    };
    
    return await api('/admin/clients', {
        method: 'POST',
        body: JSON.stringify(data)
    });
}

// Client Modal
let currentClientId = null;

async function openClientModal(clientId) {
    currentClientId = clientId;
    
    try {
        const client = await api(`/admin/clients/${clientId}`);
        
        document.getElementById('edit-client-id').value = client.id;
        document.getElementById('edit-company').value = client.company_name || '';
        document.getElementById('edit-account-type').value = client.account_type || 'company';
        document.getElementById('edit-discount').value = client.discount_percent || 0;
        document.getElementById('edit-offer-notes').value = client.offer_notes || '';
        document.getElementById('edit-plan').value = client.subscription_plan;
        document.getElementById('edit-voice').value = client.elevenlabs_voice_id || '';
        document.getElementById('edit-avatar').value = client.heygen_avatar_id || '';
        document.getElementById('edit-active').checked = client.is_active;
        
        if (client.branding) {
            document.getElementById('edit-watermark-position').value = client.branding.watermark_position;
            document.getElementById('edit-font-name').value = client.branding.subtitle_font_name;
            document.getElementById('edit-font-size').value = client.branding.subtitle_font_size;
            document.getElementById('edit-font-color').value = client.branding.subtitle_font_color;
            document.getElementById('edit-emphasis-style').value = client.branding.subtitle_emphasis_style || 'color';
            document.getElementById('edit-accent-color').value = client.branding.subtitle_accent_color || '#FFE500';
        }

        await loadLibraryFonts();
        
        document.getElementById('current-credits').textContent = client.credits_remaining;
        document.getElementById('used-credits').textContent = client.credits_used_this_month;
        
        ['edit-plan', 'edit-account-type', 'edit-watermark-position', 'edit-emphasis-style', 'edit-library-font'].forEach((id) => {
            document.getElementById(id)?.dispatchEvent(new Event('change', { bubbles: true }));
        });
        
        document.getElementById('client-modal').classList.add('active');
        showTab('general');
    } catch (e) {
        alert('Ошибка загрузки данных клиента: ' + e.message);
    }
}

async function loadLibraryFonts() {
    const select = document.getElementById('edit-library-font');
    if (!select) return;
    try {
        const data = await api('/admin/assets/fonts');
        const current = select.value;
        select.innerHTML = '<option value="">— авто по теме видео —</option>';
        (data.fonts || []).forEach((f) => {
            const opt = document.createElement('option');
            opt.value = f.id;
            opt.textContent = `${f.label} · ${f.vibe}${f.cached ? ' ✓' : ''}`;
            select.appendChild(opt);
        });
        if (current) select.value = current;
    } catch (e) {
        console.warn('Library fonts unavailable', e);
    }
}

async function applyLibraryFont() {
    if (!currentClientId) return;
    const fontId = document.getElementById('edit-library-font')?.value;
    if (!fontId) {
        alert('Выберите шрифт из списка');
        return;
    }
    try {
        const data = await api(
            `/admin/clients/${currentClientId}/branding/library-font?font_id=${encodeURIComponent(fontId)}`,
            { method: 'POST', body: '{}' }
        );
        document.getElementById('edit-font-name').value = data.family;
        alert(data.message || 'Шрифт применён');
        await loadLibraryFonts();
    } catch (e) {
        alert('Не удалось применить шрифт: ' + e.message);
    }
}

function closeModal() {
    document.getElementById('client-modal').classList.remove('active');
    currentClientId = null;
}

function showTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    
    document.querySelector(`.tab-btn[data-tab="${tabName}"]`).classList.add('active');
    document.getElementById(`tab-${tabName}`).classList.add('active');
}

async function saveClient() {
    if (!currentClientId) return;
    
    try {
        // Update general settings
        await api(`/admin/clients/${currentClientId}`, {
            method: 'PATCH',
            body: JSON.stringify({
                company_name: document.getElementById('edit-company').value,
                account_type: document.getElementById('edit-account-type').value,
                discount_percent: parseInt(document.getElementById('edit-discount').value) || 0,
                offer_notes: document.getElementById('edit-offer-notes').value || null,
                subscription_plan: document.getElementById('edit-plan').value,
                elevenlabs_voice_id: document.getElementById('edit-voice').value || null,
                heygen_avatar_id: document.getElementById('edit-avatar').value || null,
                is_active: document.getElementById('edit-active').checked
            })
        });
        
        // Update branding
        await api(`/admin/clients/${currentClientId}/branding`, {
            method: 'PATCH',
            body: JSON.stringify({
                watermark_position: document.getElementById('edit-watermark-position').value,
                subtitle_font_name: document.getElementById('edit-font-name').value,
                subtitle_font_size: parseInt(document.getElementById('edit-font-size').value),
                subtitle_font_color: document.getElementById('edit-font-color').value,
                subtitle_emphasis_style: document.getElementById('edit-emphasis-style').value,
                subtitle_accent_color: document.getElementById('edit-accent-color').value
            })
        });
        
        closeModal();
        loadClients();
    } catch (e) {
        alert('Ошибка сохранения: ' + e.message);
    }
}

async function addCredits() {
    if (!currentClientId) return;
    
    const amount = parseInt(document.getElementById('add-credits-amount').value);
    if (!amount || amount < 1) {
        alert('Укажите количество кредитов');
        return;
    }
    
    try {
        const result = await api(`/admin/clients/${currentClientId}/add-credits?credits=${amount}`, {
            method: 'POST'
        });
        
        document.getElementById('current-credits').textContent = result.new_balance;
        alert(result.message);
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}

async function resetCycle() {
    if (!currentClientId) return;
    
    if (!confirm('Сбросить цикл биллинга и восстановить кредиты?')) return;
    
    try {
        const result = await api(`/admin/clients/${currentClientId}/reset-cycle`, {
            method: 'POST'
        });
        
        document.getElementById('current-credits').textContent = result.credits_remaining;
        document.getElementById('used-credits').textContent = '0';
        alert(result.message);
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}

async function uploadFile(clientId, type) {
    const inputId = type === 'watermark' ? 'edit-watermark' : 'edit-font';
    const input = document.getElementById(inputId);
    
    if (!input.files.length) return;
    
    const formData = new FormData();
    formData.append('file', input.files[0]);
    
    try {
        const response = await fetch(`${API_BASE}/admin/clients/${clientId}/${type}`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`
            },
            body: formData
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail);
        }
        
        const result = await response.json();
        alert(result.message);
    } catch (e) {
        alert('Ошибка загрузки: ' + e.message);
    }
}

function enhanceCustomSelects(root = document) {
    root.querySelectorAll('select.select-ui').forEach((select) => {
        if (select.dataset.enhanced === '1') return;

        let wrap = select.closest('.select-wrap');
        if (!wrap) {
            wrap = document.createElement('div');
            wrap.className = 'select-wrap';
            select.parentNode.insertBefore(wrap, select);
            wrap.appendChild(select);
        }

        select.dataset.enhanced = '1';
        wrap.classList.add('select-enhanced');

        const trigger = document.createElement('button');
        trigger.type = 'button';
        trigger.className = 'select-trigger';
        trigger.setAttribute('aria-haspopup', 'listbox');

        const label = document.createElement('span');
        label.className = 'select-trigger-label';
        const arrow = document.createElement('span');
        arrow.className = 'select-trigger-arrow';
        arrow.setAttribute('aria-hidden', 'true');
        trigger.append(label, arrow);

        const menu = document.createElement('ul');
        menu.className = 'select-menu';
        menu.setAttribute('role', 'listbox');

        const syncLabel = () => {
            const selected = select.options[select.selectedIndex];
            label.textContent = selected ? selected.textContent : 'Выбрать';
            menu.querySelectorAll('.select-option').forEach((item) => {
                item.classList.toggle('is-selected', item.dataset.value === select.value);
            });
        };

        const buildOptions = () => {
            menu.innerHTML = '';
            Array.from(select.options).forEach((opt) => {
                const item = document.createElement('li');
                item.className = 'select-option';
                item.dataset.value = opt.value;
                item.setAttribute('role', 'option');
                item.textContent = opt.textContent;
                item.addEventListener('click', () => {
                    select.value = opt.value;
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                    syncLabel();
                    wrap.classList.remove('is-open');
                    trigger.setAttribute('aria-expanded', 'false');
                });
                menu.appendChild(item);
            });
            syncLabel();
        };

        buildOptions();
        wrap.append(trigger, menu);

        trigger.addEventListener('click', (e) => {
            e.preventDefault();
            const willOpen = !wrap.classList.contains('is-open');
            document.querySelectorAll('.select-wrap.is-open').forEach((openWrap) => {
                if (openWrap !== wrap) {
                    openWrap.classList.remove('is-open');
                    openWrap.querySelector('.select-trigger')?.setAttribute('aria-expanded', 'false');
                }
            });
            wrap.classList.toggle('is-open', willOpen);
            trigger.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
        });

        select.addEventListener('change', syncLabel);
    });

    if (!document.body.dataset.selectOutsideBound) {
        document.body.dataset.selectOutsideBound = '1';
        document.addEventListener('click', (e) => {
            if (e.target.closest('.select-wrap')) return;
            document.querySelectorAll('.select-wrap.is-open').forEach((wrap) => {
                wrap.classList.remove('is-open');
                wrap.querySelector('.select-trigger')?.setAttribute('aria-expanded', 'false');
            });
        });
    }
}

// Event Listeners
document.addEventListener('DOMContentLoaded', async () => {
    enhanceCustomSelects();
    // Check authentication
    const isAuthenticated = await checkAuth();
    if (isAuthenticated) {
        showScreen('dashboard');
        showPage('overview');
    }
    
    // Login form
    document.getElementById('login-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const email = document.getElementById('email').value;
        const password = document.getElementById('password').value;
        const errorEl = document.getElementById('login-error');
        
        try {
            await login(email, password);
            const isAdmin = await checkAuth();
            if (isAdmin) {
                showScreen('dashboard');
                showPage('overview');
            } else {
                errorEl.textContent = 'Доступ только для администраторов';
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
    
    // Create client form
    document.getElementById('create-client-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const errorEl = document.getElementById('create-error');
        const successEl = document.getElementById('create-success');
        errorEl.textContent = '';
        successEl.textContent = '';
        
        const formData = {
            email: document.getElementById('client-email').value,
            password: document.getElementById('client-password').value,
            name: document.getElementById('client-name').value,
            company: document.getElementById('client-company').value,
            accountType: document.getElementById('client-account-type').value,
            discountPercent: parseInt(document.getElementById('client-discount').value) || 0,
            offerNotes: document.getElementById('client-offer-notes').value,
            plan: document.querySelector('input[name="plan"]:checked').value,
            voiceId: document.getElementById('elevenlabs-voice').value,
            avatarId: document.getElementById('heygen-avatar').value
        };
        
        try {
            await createClient(formData);
            successEl.textContent = 'Клиент успешно создан!';
            e.target.reset();
        } catch (e) {
            errorEl.textContent = e.message;
        }
    });
    
    // Modal controls — close whichever modal the button lives in
    document.querySelectorAll('.modal-close, .modal-cancel').forEach(btn => {
        btn.addEventListener('click', () => {
            btn.closest('.modal')?.classList.remove('active');
            if (btn.closest('.modal')?.id === 'client-modal') currentClientId = null;
        });
    });

    document.getElementById('save-client-btn').addEventListener('click', saveClient);
    document.getElementById('add-credits-btn').addEventListener('click', addCredits);
    document.getElementById('reset-cycle-btn').addEventListener('click', resetCycle);
    document.getElementById('approve-submit-btn').addEventListener('click', submitApproval);

    // Tab switching
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => showTab(btn.dataset.tab));
    });

    // File uploads
    document.getElementById('edit-watermark').addEventListener('change', () => {
        if (currentClientId) uploadFile(currentClientId, 'watermark');
    });

    document.getElementById('edit-font').addEventListener('change', () => {
        if (currentClientId) uploadFile(currentClientId, 'font');
    });

    document.getElementById('apply-library-font-btn')?.addEventListener('click', applyLibraryFont);

    // Close modal on outside click
    document.querySelectorAll('.modal').forEach((modal) => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.classList.remove('active');
                if (modal.id === 'client-modal') currentClientId = null;
            }
        });
    });
});
