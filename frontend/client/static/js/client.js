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

async function registerAccount(fullName, email, password, acceptedTerms, marketingOptIn) {
    const response = await fetch(`${API_BASE}/public/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            full_name: fullName,
            email,
            password,
            accepted_terms: acceptedTerms,
            marketing_opt_in: marketingOptIn
        })
    });
    const data = await response.json();
    if (!response.ok) {
        const detail = data.detail;
        throw new Error(typeof detail === 'string' ? detail : (detail?.[0]?.msg || 'Не удалось зарегистрироваться'));
    }
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

// Token packages / ЮKassa checkout
async function loadPaymentPackages() {
    const container = document.getElementById('payment-packages');
    if (!container) return;
    try {
        const packages = await api('/client/payments/packages');
        container.innerHTML = packages.map((p) => `
            <div class="package-card">
                <span class="package-label">${escapeHtml(p.label)}</span>
                <span class="package-tokens">${p.tokens} токенов</span>
                <span class="package-price">${Number(p.price_rub).toLocaleString('ru-RU')} ₽</span>
                <button type="button" class="btn btn-secondary" data-package-id="${p.id}">Купить</button>
            </div>
        `).join('');
        container.querySelectorAll('[data-package-id]').forEach((btn) => {
            btn.addEventListener('click', () => buyPackage(btn.dataset.packageId, btn));
        });
    } catch (e) {
        container.innerHTML = `<p class="hint">Не удалось загрузить пакеты: ${escapeHtml(e.message || String(e))}</p>`;
    }
}

async function buyPackage(packageId, btn) {
    if (btn) btn.disabled = true;
    try {
        const result = await api('/client/payments/create', {
            method: 'POST',
            body: JSON.stringify({ package_id: packageId })
        });
        window.location.href = result.confirmation_url;
    } catch (e) {
        alert('Не удалось начать оплату: ' + (e.message || String(e)));
        if (btn) btn.disabled = false;
    }
}

async function checkPaymentReturn() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('payment') !== 'return') return;

    history.replaceState(null, '', window.location.pathname);
    const msg = document.getElementById('payment-status-msg');
    if (msg) {
        msg.style.display = 'block';
        msg.textContent = 'Проверяем оплату…';
    }

    const before = currentClient ? currentClient.credits_remaining : null;
    for (let i = 0; i < 8; i++) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        try {
            const credits = await api('/client/credits');
            if (currentClient) currentClient.credits_remaining = credits.credits_remaining;
            updateCreditsDisplay();
            if (before !== null && credits.credits_remaining !== before) {
                if (msg) msg.textContent = 'Оплата прошла — токены начислены.';
                return;
            }
        } catch (_) { /* ignore transient */ }
    }
    if (msg) msg.textContent = 'Если оплата прошла успешно, токены появятся в течение минуты.';
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
        'viral': 'Вирусный монтаж',
        'clips': 'AI-клипы',
        'radar': 'Радар трендов',
        'history': 'Мои видео',
        'profile': 'Профиль'
    };
    document.getElementById('page-title').textContent = titles[pageName] || pageName;
    
    if (pageName === 'history') {
        loadGenerations();
    } else if (pageName === 'profile') {
        loadProfile();
        loadPaymentPackages();
    } else if (pageName === 'viral') {
        loadFontOptions(document.getElementById('viral-font'), true);
        applyViralPrefillFromRadar();
    } else if (pageName === 'clips') {
        loadFontOptions(document.getElementById('clips-font'), true);
    } else if (pageName === 'radar') {
        loadRadarTrends(false);
        loadRadarInsights();
    }
}

// Generate Video
async function generateVideo(text, language, productUrl = '', genre = 'default') {
    const body = {
        original_text: text,
        target_language: language,
        genre: genre || 'default',
    };
    if (productUrl) body.product_url = productUrl;
    return await api('/client/generate', {
        method: 'POST',
        body: JSON.stringify(body)
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
            <div class="generation-card" data-generation-id="${gen.id}" role="button" tabindex="0">
                <div class="generation-thumbnail ${statusClass}">
                    ${statusIcon}
                </div>
                <div class="generation-info">
                    <h4>${gen.mode === 'viral_edit' ? '✨ ' : gen.mode === 'ai_clips' ? '✂️ ' : ''}${escapeHtml(truncateText(gen.original_text, 50))}</h4>
                    <div class="generation-meta">
                        <span class="status-badge status-${gen.status}">${statusText}</span>
                        <span>${formatDate(gen.created_at)}</span>
                    </div>
                </div>
            </div>
        `;
    }).join('');

    container.querySelectorAll('.generation-card[data-generation-id]').forEach((card) => {
        const id = Number(card.dataset.generationId);
        card.addEventListener('click', () => openGenerationModal(id));
        card.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                openGenerationModal(id);
            }
        });
    });
    
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
    if (['script_generation', 'voice_synthesis', 'avatar_generation', 'transcription', 'viral_edit', 'clipping', 'video_processing'].includes(status)) return 'processing';
    return '';
}

function getStatusIcon(status) {
    const icons = {
        pending: '⏳',
        script_generation: '📝',
        voice_synthesis: '🎙️',
        avatar_generation: '👤',
        transcription: '🎤',
        viral_edit: '✨',
        clipping: '✂️',
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
        transcription: 'Распознавание речи',
        viral_edit: 'AI-монтаж',
        clipping: 'Нарезка клипов',
        video_processing: 'Обработка видео',
        completed: 'Готово',
        failed: 'Ошибка'
    };
    return texts[status] || status;
}

async function submitViralEdit(
    file,
    language,
    style = 'dynamic',
    format = '9:16',
    dubLanguage = '',
    fontId = '',
    sourceUrl = '',
    voiceoverText = '',
    platform = 'auto',
    intensity = 'full',
    genre = 'default',
    hookVariants = 1,
    kineticSubtitles = false,
    volumetricHook = false,
    rightsConfirmed = false,
    aiDisclosureRequested = false,
) {
    const formData = new FormData();
    if (file) formData.append('file', file);
    if (sourceUrl) formData.append('source_url', sourceUrl);
    formData.append('language', language);
    formData.append('style', style);
    formData.append('format', format);
    formData.append('dub_language', dubLanguage || '');
    formData.append('font_id', fontId || '');
    formData.append('platform', platform || 'auto');
    formData.append('intensity', intensity || 'full');
    formData.append('genre', genre || 'default');
    formData.append('hook_variants', String(hookVariants || 1));
    formData.append('kinetic_subtitles', kineticSubtitles ? 'true' : 'false');
    formData.append('volumetric_hook', volumetricHook ? 'true' : 'false');
    formData.append('rights_confirmed', rightsConfirmed ? 'true' : 'false');
    formData.append('ai_disclosure_requested', aiDisclosureRequested ? 'true' : 'false');
    if (voiceoverText) formData.append('voiceover_text', voiceoverText);
    const headers = {};
    if (authToken) headers['Authorization'] = `Bearer ${authToken}`;

    const response = await fetch(`${API_BASE}/client/viral-edit`, {
        method: 'POST',
        headers,
        body: formData,
    });

    if (response.status === 401) {
        logout();
        throw new Error('Сессия истекла');
    }

    const data = await response.json();
    if (!response.ok) {
        const detail = data.detail;
        throw new Error(typeof detail === 'string' ? detail : (detail?.[0]?.msg || 'Ошибка загрузки'));
    }
    return data;
}

async function loadFontOptions(selectEl, includeAuto = false) {
    if (!selectEl) return;
    const isProfile = selectEl.id === 'profile-font-select';
    const statusEl = document.getElementById('profile-font-gallery-status');
    if (isProfile && statusEl) statusEl.textContent = 'Загрузка списка…';

    try {
        const data = await api('/client/assets/fonts');
        const fonts = (data.fonts || []).filter((f) => f.ready !== false);
        window.__fontCatalog = fonts;

        selectEl.innerHTML = '';
        if (includeAuto) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'Авто / из профиля';
            selectEl.appendChild(opt);
        }
        fonts.forEach((f) => {
            const opt = document.createElement('option');
            opt.value = f.id;
            opt.textContent = `${f.label} — ${f.vibe || f.family}`;
            selectEl.appendChild(opt);
        });

        if (typeof selectEl._rebuildCustomOptions === 'function') {
            selectEl._rebuildCustomOptions();
        }

        if (isProfile) {
            renderFontGallery(fonts, selectEl.value || fonts[0]?.id || '');
            if (statusEl) {
                statusEl.textContent = fonts.length
                    ? `${fonts.length} шрифтов · кликни карточку, чтобы увидеть превью`
                    : 'Шрифты не найдены';
            }
        }
    } catch (e) {
        console.error('Failed to load fonts', e);
        selectEl.innerHTML = '';
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = `Не удалось загрузить: ${e.message || 'ошибка'}`;
        selectEl.appendChild(opt);
        if (typeof selectEl._rebuildCustomOptions === 'function') {
            selectEl._rebuildCustomOptions();
        }
        if (isProfile && statusEl) statusEl.textContent = e.message || 'Ошибка загрузки';
    }
}

function renderFontGallery(fonts, selectedId) {
    const gallery = document.getElementById('profile-font-gallery');
    const selectEl = document.getElementById('profile-font-select');
    if (!gallery || !selectEl) return;

    gallery.innerHTML = '';
    fonts.forEach((f) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'font-card' + (f.id === selectedId ? ' is-selected' : '');
        btn.dataset.fontId = f.id;
        btn.innerHTML =
            `<span class="font-card-name">${escapeHtml(f.label)}</span>` +
            `<span class="font-card-meta">${escapeHtml(f.vibe || '')} · ${escapeHtml(f.license || 'OFL')}</span>`;
        btn.addEventListener('click', () => {
            selectEl.value = f.id;
            gallery.querySelectorAll('.font-card').forEach((c) => {
                c.classList.toggle('is-selected', c.dataset.fontId === f.id);
            });
            previewLibraryFont(f);
        });
        gallery.appendChild(btn);
    });

    const initial = fonts.find((f) => f.id === selectedId) || fonts[0];
    if (initial) {
        selectEl.value = initial.id;
        previewLibraryFont(initial);
    }
}

const __loadedFontFaces = new Set();

async function previewLibraryFont(font) {
    const preview = document.getElementById('profile-font-preview');
    const meta = document.getElementById('profile-font-preview-meta');
    if (!preview || !font) return;

    const familyCss = `VG_${font.id.replace(/[^a-z0-9_-]/gi, '_')}`;
    try {
        if (!__loadedFontFaces.has(font.id)) {
            const url = `${API_BASE}/client/assets/fonts/${encodeURIComponent(font.id)}/file`;
            const res = await fetch(url, {
                headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
            });
            if (!res.ok) throw new Error('Не удалось скачать файл шрифта');
            const blob = await res.blob();
            const objUrl = URL.createObjectURL(blob);
            const face2 = new FontFace(familyCss, `url(${objUrl})`, {
                weight: String(font.weight || 400),
                style: 'normal',
            });
            await face2.load();
            document.fonts.add(face2);
            __loadedFontFaces.add(font.id);
        }
        preview.style.fontFamily = `"${familyCss}", Arial, sans-serif`;
        if (meta) meta.textContent = `${font.label} · ${font.family} · ${font.license || 'OFL'}`;
    } catch (e) {
        preview.style.fontFamily = 'Arial, sans-serif';
        if (meta) meta.textContent = `Превью недоступно: ${e.message || 'ошибка'}`;
    }
}

async function submitAiClips(file, language, maxClips = 5, fontId = '', sourceUrl = '', platform = 'auto', kineticSubtitles = false, volumetricHook = false, rightsConfirmed = false, aiDisclosureRequested = false) {
    const formData = new FormData();
    if (file) formData.append('file', file);
    if (sourceUrl) formData.append('source_url', sourceUrl);
    formData.append('language', language);
    formData.append('max_clips', String(maxClips));
    formData.append('font_id', fontId || '');
    formData.append('platform', platform || 'auto');
    formData.append('kinetic_subtitles', kineticSubtitles ? 'true' : 'false');
    formData.append('volumetric_hook', volumetricHook ? 'true' : 'false');
    formData.append('rights_confirmed', rightsConfirmed ? 'true' : 'false');
    formData.append('ai_disclosure_requested', aiDisclosureRequested ? 'true' : 'false');

    const headers = {};
    if (authToken) headers['Authorization'] = `Bearer ${authToken}`;

    const response = await fetch(`${API_BASE}/client/clips`, {
        method: 'POST',
        headers,
        body: formData,
    });

    if (response.status === 401) {
        logout();
        throw new Error('Сессия истекла');
    }

    const data = await response.json();
    if (!response.ok) {
        const detail = data.detail;
        throw new Error(typeof detail === 'string' ? detail : (detail?.[0]?.msg || 'Ошибка загрузки'));
    }
    return data;
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

    const aiBadge = document.getElementById('modal-ai-badge');
    if (aiBadge) aiBadge.style.display = gen.api_responses?.ai_disclosure_requested === true ? 'inline-block' : 'none';

    document.getElementById('modal-original-text').textContent = gen.original_text;

    const viralitySection = document.getElementById('virality-section');
    const virality = gen.api_responses?.virality;
    if (virality && typeof virality.score === 'number') {
        viralitySection.style.display = 'block';
        document.getElementById('modal-virality-score').textContent =
            `${virality.score}/100` +
            (gen.api_responses?.edit_format ? ` · ${gen.api_responses.edit_format}` : '');
        document.getElementById('modal-virality-summary').textContent = virality.summary || '';
        const tipsEl = document.getElementById('modal-virality-tips');
        tipsEl.innerHTML = (virality.tips || [])
            .map((t) => `<li><strong>Совет</strong><span>${escapeHtml(String(t))}</span></li>`)
            .join('');
    } else {
        viralitySection.style.display = 'none';
    }

    const clipsSection = document.getElementById('clips-section');
    const clips = gen.api_responses?.clips;
    if (Array.isArray(clips) && clips.length) {
        clipsSection.style.display = 'block';
        document.getElementById('modal-clips-list').innerHTML = clips.map((c) => {
            const title = escapeHtml(c.title || `Клип ${c.index}`);
            const moment = c.moment ? ` · ${escapeHtml(c.moment)}` : '';
            const meta = `${c.duration || '?'}с · score ${c.score ?? '-'}${moment}`;
            return `<li><strong>${title}</strong><span>${escapeHtml(meta)} · <a href="#" data-clip-download="${c.index}">скачать</a></span></li>`;
        }).join('');
        document.querySelectorAll('[data-clip-download]').forEach((a) => {
            a.addEventListener('click', async (e) => {
                e.preventDefault();
                const idx = a.getAttribute('data-clip-download');
                try {
                    const response = await fetch(`${API_BASE}/client/generations/${gen.id}/clips/${idx}/download`, {
                        headers: authToken ? { Authorization: `Bearer ${authToken}` } : {}
                    });
                    if (!response.ok) throw new Error('Не удалось скачать клип');
                    const blob = await response.blob();
                    const url = URL.createObjectURL(blob);
                    const link = document.createElement('a');
                    link.href = url;
                    link.download = `clip_${gen.id}_${idx}.mp4`;
                    link.click();
                    URL.revokeObjectURL(url);
                } catch (err) {
                    alert(err.message);
                }
            });
        });
    } else if (clipsSection) {
        clipsSection.style.display = 'none';
    }

    const hooksSection = document.getElementById('hooks-section');
    const hookExports = gen.api_responses?.hook_exports;
    const hookVariants = gen.api_responses?.hook_variants || gen.api_responses?.edit_plan?.hook_variants;
    if (hooksSection) {
        const lines = [];
        if (Array.isArray(hookVariants) && hookVariants.length) {
            hookVariants.forEach((t, i) => {
                lines.push(`<li><strong>Хук ${i + 1}</strong><span>${escapeHtml(String(t))}</span></li>`);
            });
        }
        if (Array.isArray(hookExports) && hookExports.length) {
            hookExports.forEach((h) => {
                const idx = h.index;
                lines.push(
                    `<li><strong>Экспорт A/B #${idx}</strong><span>${escapeHtml(h.hook_text || '')} · ` +
                    `<a href="#" data-hook-download="${idx}">скачать</a></span></li>`
                );
            });
        }
        if (lines.length) {
            hooksSection.style.display = 'block';
            document.getElementById('modal-hooks-list').innerHTML = lines.join('');
            document.querySelectorAll('[data-hook-download]').forEach((a) => {
                a.addEventListener('click', async (e) => {
                    e.preventDefault();
                    const idx = a.getAttribute('data-hook-download');
                    try {
                        const response = await fetch(`${API_BASE}/client/generations/${gen.id}/hooks/${idx}/download`, {
                            headers: authToken ? { Authorization: `Bearer ${authToken}` } : {}
                        });
                        if (!response.ok) throw new Error('Не удалось скачать вариант хука');
                        const blob = await response.blob();
                        const url = URL.createObjectURL(blob);
                        const link = document.createElement('a');
                        link.href = url;
                        link.download = `hook_${gen.id}_${idx}.mp4`;
                        link.click();
                        URL.revokeObjectURL(url);
                    } catch (err) {
                        alert(err.message);
                    }
                });
            });
        } else {
            hooksSection.style.display = 'none';
        }
    }
    
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
        downloadBtn.href = '#';
        downloadBtn.onclick = (e) => {
            e.preventDefault();
            downloadGeneration(gen.id);
        };
        
        document.getElementById('modal-duration').textContent = 
            `Длительность: ${gen.duration_seconds || 0} сек`;
        document.getElementById('modal-size').textContent = 
            `Размер: ${formatFileSize(gen.file_size_bytes || 0)}`;

        loadVideoPreview(gen.id);
    } else {
        videoSection.style.display = 'none';
        downloadBtn.style.display = 'none';
        downloadBtn.onclick = null;
        clearVideoPreview();
    }
}

let previewObjectUrl = null;

function clearVideoPreview() {
    const video = document.getElementById('modal-video');
    video.removeAttribute('src');
    video.load();
    if (previewObjectUrl) {
        URL.revokeObjectURL(previewObjectUrl);
        previewObjectUrl = null;
    }
}

async function loadVideoPreview(generationId) {
    const video = document.getElementById('modal-video');
    clearVideoPreview();
    try {
        const response = await fetch(`${API_BASE}/client/generations/${generationId}/download`, {
            headers: authToken ? { Authorization: `Bearer ${authToken}` } : {}
        });
        if (!response.ok) throw new Error('preview failed');
        const blob = await response.blob();
        previewObjectUrl = URL.createObjectURL(blob);
        video.src = previewObjectUrl;
        video.load();
    } catch (e) {
        console.error('Video preview error:', e);
    }
}

async function downloadGeneration(generationId) {
    try {
        const response = await fetch(`${API_BASE}/client/generations/${generationId}/download`, {
            headers: authToken ? { Authorization: `Bearer ${authToken}` } : {}
        });
        if (response.status === 401) {
            logout();
            throw new Error('Сессия истекла');
        }
        if (!response.ok) {
            let detail = 'Ошибка скачивания';
            try {
                const data = await response.json();
                detail = data.detail || detail;
            } catch (_) {}
            throw new Error(detail);
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `video_${generationId}.mp4`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    } catch (e) {
        alert('Не удалось скачать видео: ' + e.message);
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
    clearVideoPreview();
    
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
        const wp = credits.watermark_policy || {};
        const policyEl = document.getElementById('profile-watermark-policy');
        if (policyEl) {
            policyEl.textContent = wp.clean_export === 'да' ? 'Без платформенного знака' : 'С watermark';
        }
        const hintEl = document.getElementById('profile-plan-hint');
        if (hintEl) {
            hintEl.textContent = wp.watermark
                ? `Watermark: ${wp.watermark}. Premium — чистое видео без LoudCut-знака.`
                : '';
        }
        
        document.getElementById('profile-font').textContent = branding.subtitle_font_name;
        document.getElementById('profile-font-color').textContent = branding.subtitle_font_color;
        document.getElementById('profile-font-color-preview').style.backgroundColor = branding.subtitle_font_color;

        const accentColor = branding.subtitle_accent_color || '#FFE500';
        document.getElementById('profile-accent-color').textContent = accentColor;
        document.getElementById('profile-accent-color-preview').style.backgroundColor = accentColor;

        const termsEl = document.getElementById('profile-terms-status');
        if (termsEl) {
            termsEl.textContent = client.terms_accepted_at
                ? `Принято ${formatDate(client.terms_accepted_at)}`
                : 'Не зафиксировано';
        }
        const marketingEl = document.getElementById('profile-marketing-status');
        if (marketingEl) {
            marketingEl.textContent = client.marketing_opt_in ? 'Да' : 'Нет';
        }

        if (branding.watermark_path) {
            document.getElementById('profile-watermark').innerHTML = 
                `<img src="/media/uploads/watermarks/${branding.watermark_path.split('/').pop()}" alt="Logo">`;
        }
    } catch (e) {
        console.error('Failed to load profile:', e);
    }

    // Always try fonts separately so a branding glitch doesn't leave "Загрузка…"
    await loadFontOptions(document.getElementById('profile-font-select'), false);
    await loadFontOptions(document.getElementById('viral-font'), true);
    await loadFontOptions(document.getElementById('clips-font'), true);
}

// --- Trend radar ---
let radarPollTimer = null;
let currentRadarInsightId = null;

async function loadRadarTrends(forceRefresh = false) {
    const err = document.getElementById('radar-error');
    const meta = document.getElementById('radar-meta');
    const list = document.getElementById('radar-list');
    if (!list) return;
    if (err) err.textContent = '';
    const platform = document.getElementById('radar-platform')?.value || 'youtube';
    const region = document.getElementById('radar-region')?.value || 'RU';
    const niche = (document.getElementById('radar-niche')?.value || '').trim();
    const qs = new URLSearchParams({
        platform,
        region,
        limit: '15',
        refresh: forceRefresh ? 'true' : 'false',
    });
    if (niche) qs.set('niche', niche);
    try {
        const data = await api(`/client/trends?${qs.toString()}`);
        const m = data.meta || {};
        if (meta) {
            meta.textContent = [
                m.status || '',
                m.message || '',
                m.count != null ? `записей: ${m.count}` : '',
                m.youtube_configured === false && platform === 'youtube'
                    ? 'Нужен YOUTUBE_API_KEY в .env'
                    : '',
            ].filter(Boolean).join(' · ');
        }
        const items = data.items || [];
        if (!items.length) {
            list.innerHTML = '<p class="hint">Пока пусто. Нажми «Обновить» или смени платформу/нишу.</p>';
            return;
        }
        list.innerHTML = items.map((it) => {
            const viewsRaw = it.metrics?.views != null ? it.metrics.views : it.metrics?.video_views;
            const views = viewsRaw != null ? ` · ${Number(viewsRaw).toLocaleString('ru-RU')} просмотров` : '';
            const rank = it.metrics?.rank != null ? ` · #${escapeHtml(String(it.metrics.rank))}` : '';
            const dur = it.metrics?.duration_sec != null ? ` · ${escapeHtml(String(it.metrics.duration_sec))}с` : '';
            return `
            <article class="radar-row" data-id="${it.id}" data-url="${escapeHtml(it.url || '')}">
                <div>
                    <h4>${escapeHtml(it.title || it.external_id)}</h4>
                    <p class="meta">${escapeHtml(it.platform)}${rank}${views}${dur}</p>
                </div>
                <div style="display:flex;gap:0.4rem;flex-wrap:wrap;">
                    <button type="button" class="btn btn-ghost radar-open-btn">Открыть</button>
                    <button type="button" class="btn btn-secondary radar-study-btn">Разобрать</button>
                </div>
            </article>`;
        }).join('');
        list.querySelectorAll('.radar-open-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                const url = btn.closest('.radar-row')?.dataset.url;
                if (url) window.open(url, '_blank', 'noopener');
            });
        });
        list.querySelectorAll('.radar-study-btn').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const row = btn.closest('.radar-row');
                const url = row?.dataset.url;
                const id = row?.dataset.id;
                if (!url) return;
                const urlInput = document.getElementById('radar-url');
                if (urlInput) urlInput.value = url;
                await startRadarAnalyze(url, id ? parseInt(id, 10) : null);
            });
        });
    } catch (e) {
        if (err) err.textContent = e.message || String(e);
    }
}

async function startRadarAnalyze(url, trendItemId = null) {
    const msg = document.getElementById('radar-analyze-msg');
    if (msg) msg.textContent = 'Запускаю разбор…';
    try {
        const body = { url };
        if (trendItemId) body.trend_item_id = trendItemId;
        const insight = await api('/client/trends/analyze', {
            method: 'POST',
            body: JSON.stringify(body),
        });
        currentRadarInsightId = insight.id;
        if (msg) msg.textContent = `Разбор #${insight.id} запущен…`;
        renderRadarInsight(insight);
        pollRadarInsight(insight.id);
        if (currentClient && insight.status === 'pending') {
            // credit deducted on completion; refresh later
        }
    } catch (e) {
        if (msg) msg.textContent = e.message || String(e);
    }
}

function renderRadarInsight(ins) {
    const box = document.getElementById('radar-insight');
    if (!box) return;
    if (!ins) {
        box.innerHTML = '<p class="hint">Выбери тренд или вставь ссылку на ролик ниже.</p>';
        return;
    }
    if (ins.status === 'failed') {
        box.innerHTML = `<h4>Ошибка</h4><p class="error-message">${escapeHtml(ins.error_message || 'fail')}</p>`;
        return;
    }
    if (ins.status !== 'completed') {
        box.innerHTML = `<h4>Разбор #${ins.id}</h4><p class="hint">${escapeHtml(ins.status)} · ${ins.progress_percent || 0}%</p>`;
        return;
    }
    const tips = (ins.tips || []).map((t) => `<li>${escapeHtml(t)}</li>`).join('');
    const hashtags = (ins.hashtags || []).map((h) => `<span class="chip">#${escapeHtml(h)}</span>`).join(' ');
    box.innerHTML = `
        <h4>${escapeHtml(ins.style_guess || 'dynamic')} · ${ins.duration_sec ? Math.round(ins.duration_sec) + 'с' : ''}</h4>
        <p><strong>Хук:</strong> ${escapeHtml(ins.hook_text || '—')}</p>
        <p>${escapeHtml(ins.transcript_summary || '')}</p>
        <p class="meta">Темп ~${ins.pace_wpm ? Math.round(ins.pace_wpm) : '—'} сл/мин</p>
        <ul>${tips}</ul>
        ${hashtags ? `<p class="meta"><strong>Хэштеги:</strong> ${hashtags}</p>` : ''}
        <button type="button" class="btn btn-primary" id="radar-apply-viral-btn" style="margin-top:0.75rem;">В вирусный монтаж</button>
    `;
    document.getElementById('radar-apply-viral-btn')?.addEventListener('click', () => applyRadarToViral(ins.id));
}

async function pollRadarInsight(id) {
    if (radarPollTimer) clearInterval(radarPollTimer);
    radarPollTimer = setInterval(async () => {
        try {
            const ins = await api(`/client/trends/insights/${id}`);
            renderRadarInsight(ins);
            if (ins.status === 'completed' || ins.status === 'failed') {
                clearInterval(radarPollTimer);
                radarPollTimer = null;
                loadRadarInsights();
                try {
                    const credits = await api('/client/credits');
                    currentClient.credits_remaining = credits.credits_remaining;
                    updateCreditsDisplay();
                } catch (_) { /* ignore */ }
            }
        } catch (_) { /* ignore transient */ }
    }, 4000);
}

async function loadRadarInsights() {
    try {
        const rows = await api('/client/trends/insights?limit=5');
        const latest = (rows || []).find((r) => r.status === 'completed') || (rows || [])[0];
        if (latest && !currentRadarInsightId) renderRadarInsight(latest);
    } catch (_) { /* ignore */ }
}

async function applyRadarToViral(insightId) {
    try {
        const pref = await api(`/client/trends/insights/${insightId}/apply-viral`, { method: 'POST' });
        sessionStorage.setItem('videogen_viral_prefill', JSON.stringify(pref));
        showPage('viral');
    } catch (e) {
        alert(e.message || String(e));
    }
}

function applyViralPrefillFromRadar() {
    const raw = sessionStorage.getItem('videogen_viral_prefill');
    if (!raw) return;
    try {
        const pref = JSON.parse(raw);
        sessionStorage.removeItem('videogen_viral_prefill');
        const urlEl = document.getElementById('viral-url');
        const styleEl = document.getElementById('viral-style');
        if (urlEl && pref.source_url) urlEl.value = pref.source_url;
        if (styleEl && pref.style) {
            styleEl.value = pref.style;
            if (typeof styleEl._rebuildCustomOptions === 'function') styleEl._rebuildCustomOptions();
        }
        const success = document.getElementById('viral-success');
        if (success) {
            success.style.display = 'block';
            success.textContent = pref.hook_hint
                ? `Из радара: стиль «${pref.style}». Хук-идея: ${pref.hook_hint}`
                : `Из радара подставлены ссылка и стиль «${pref.style}».`;
        }
    } catch (_) {
        sessionStorage.removeItem('videogen_viral_prefill');
    }
}

// Utilities
function truncateText(text, maxLength) {
    if (!text) return '';
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + '...';
}

function escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
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
        select._rebuildCustomOptions = buildOptions;
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
// Enter/Space activates a div-based [role="button"] control (preset cards),
// same as a native <button> would.
document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const el = e.target;
    if (el?.getAttribute && el.getAttribute('role') === 'button') {
        e.preventDefault();
        el.click();
    }
});

function wireDropzone(labelId, inputId, hintId) {
    const label = document.getElementById(labelId);
    const input = document.getElementById(inputId);
    const hint = document.getElementById(hintId);
    if (!label || !input) return;
    const defaultHint = hint ? hint.textContent : '';

    const updateHint = () => {
        if (!hint) return;
        const file = input.files?.[0];
        hint.textContent = file ? file.name : defaultHint;
    };
    input.addEventListener('change', updateHint);

    ['dragenter', 'dragover'].forEach((evt) => {
        label.addEventListener(evt, (e) => {
            e.preventDefault();
            e.stopPropagation();
            label.classList.add('is-dragover');
        });
    });
    ['dragleave', 'drop'].forEach((evt) => {
        label.addEventListener(evt, (e) => {
            e.preventDefault();
            e.stopPropagation();
            label.classList.remove('is-dragover');
        });
    });
    label.addEventListener('drop', (e) => {
        const files = e.dataTransfer?.files;
        if (files && files.length) {
            input.files = files;
            updateHint();
        }
    });
}

function wirePresetGrid(gridId, selectId) {
    const grid = document.getElementById(gridId);
    const select = document.getElementById(selectId);
    if (!grid || !select) return;
    grid.querySelectorAll('.preset-card').forEach((card) => {
        card.addEventListener('click', () => {
            grid.querySelectorAll('.preset-card').forEach((c) => c.classList.remove('is-active'));
            card.classList.add('is-active');
            select.value = card.dataset.value;
            select.dispatchEvent(new Event('change', { bubbles: true }));
        });
    });
}

function initVideoUploadWidgets() {
    wireDropzone('viral-dropzone', 'viral-file', 'viral-dropzone-hint');
    wireDropzone('clips-dropzone', 'clips-file', 'clips-dropzone-hint');
    wirePresetGrid('viral-genre-presets', 'viral-genre');

    if (window.VGScribble) {
        const marks = document.querySelectorAll('.mark-scribble');
        marks.forEach((svg) => VGScribble.draw(svg, 50, 50, 40, 40));
        setTimeout(() => {
            marks.forEach((svg, i) => setTimeout(() => VGScribble.reveal(svg), i * 60));
        }, 150);
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    enhanceCustomSelects();
    initVideoUploadWidgets();
    const isAuthenticated = await checkAuth();
    if (isAuthenticated) {
        showScreen('dashboard');
        showPage('generate');
        loadFontOptions(document.getElementById('profile-font-select'), false);
        loadFontOptions(document.getElementById('viral-font'), true);
        loadFontOptions(document.getElementById('clips-font'), true);
        checkPaymentReturn();
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
                loadFontOptions(document.getElementById('profile-font-select'), false);
                loadFontOptions(document.getElementById('viral-font'), true);
                loadFontOptions(document.getElementById('clips-font'), true);
            } else {
                errorEl.textContent = 'Доступ только для клиентов';
            }
        } catch (e) {
            errorEl.textContent = e.message;
        }
    });

    // Register / login toggle
    document.getElementById('show-register-btn')?.addEventListener('click', () => {
        document.getElementById('login-form').hidden = true;
        document.getElementById('register-form').hidden = false;
    });
    document.getElementById('show-login-btn')?.addEventListener('click', () => {
        document.getElementById('register-form').hidden = true;
        document.getElementById('login-form').hidden = false;
    });

    // Register form
    document.getElementById('register-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const fullName = document.getElementById('register-name').value;
        const email = document.getElementById('register-email').value;
        const password = document.getElementById('register-password').value;
        const acceptedTerms = document.getElementById('register-terms').checked;
        const marketingOptIn = document.getElementById('register-marketing').checked;
        const errorEl = document.getElementById('register-error');

        try {
            await registerAccount(fullName, email, password, acceptedTerms, marketingOptIn);
            const isClient = await checkAuth();
            if (isClient) {
                showScreen('dashboard');
                showPage('generate');
                loadFontOptions(document.getElementById('profile-font-select'), false);
                loadFontOptions(document.getElementById('viral-font'), true);
                loadFontOptions(document.getElementById('clips-font'), true);
            }
        } catch (e) {
            errorEl.textContent = e.message;
        }
    });

    // Logout
    document.getElementById('logout-btn').addEventListener('click', logout);

    document.getElementById('deactivate-account-btn')?.addEventListener('click', async () => {
        const msgEl = document.getElementById('deactivate-msg');
        if (!window.confirm('Деактивировать аккаунт? Доступ к кабинету будет закрыт; данные сохранятся и восстановление возможно через администратора.')) {
            return;
        }
        try {
            await api('/client/deactivate', { method: 'POST' });
            logout();
        } catch (e) {
            if (msgEl) msgEl.textContent = 'Ошибка: ' + e.message;
        }
    });

    document.getElementById('radar-refresh-btn')?.addEventListener('click', () => loadRadarTrends(true));
    document.getElementById('radar-analyze-btn')?.addEventListener('click', async () => {
        const url = (document.getElementById('radar-url')?.value || '').trim();
        if (!url) {
            const msg = document.getElementById('radar-analyze-msg');
            if (msg) msg.textContent = 'Вставьте ссылку на ролик';
            return;
        }
        await startRadarAnalyze(url, null);
    });
    
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
        const productUrl = (document.getElementById('product-url')?.value || '').trim();
        const genre = document.getElementById('avatar-genre')?.value || 'default';
        
        if (currentClient.credits_remaining <= 0) {
            errorEl.textContent = 'Недостаточно кредитов. Обратитесь к администратору.';
            return;
        }
        
        try {
            const gen = await generateVideo(text, language, productUrl, genre);
            
            currentClient.credits_remaining--;
            updateCreditsDisplay();
            
            document.getElementById('script-text').value = '';
            const productEl = document.getElementById('product-url');
            if (productEl) productEl.value = '';
            charCount.textContent = '0';
            
            showPage('history');
            openGenerationModal(gen.id);
        } catch (e) {
            errorEl.textContent = e.message;
        }
    });

    document.getElementById('viral-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const errorEl = document.getElementById('viral-error');
        const successEl = document.getElementById('viral-success');
        errorEl.textContent = '';
        successEl.style.display = 'none';

        const fileInput = document.getElementById('viral-file');
        const file = fileInput.files?.[0];
        const sourceUrl = (document.getElementById('viral-url')?.value || '').trim();
        const language = document.getElementById('viral-language').value;
        const style = document.getElementById('viral-style')?.value || 'dynamic';
        const format = document.getElementById('viral-format')?.value || '9:16';
        const dubLanguage = document.getElementById('viral-dub')?.value || '';
        const fontId = document.getElementById('viral-font')?.value || '';
        const voiceoverText = (document.getElementById('viral-voiceover')?.value || '').trim();
        const platform = document.getElementById('viral-platform')?.value || 'auto';
        const intensity = document.getElementById('viral-intensity')?.value || 'full';
        const genre = document.getElementById('viral-genre')?.value || 'default';
        const hookVariants = parseInt(document.getElementById('viral-hooks')?.value || '1', 10);
        const kineticSubtitles = document.getElementById('viral-kinetic')?.checked || false;
        const volumetricHook = document.getElementById('viral-volumetric')?.checked || false;
        const rightsConfirmed = document.getElementById('viral-rights')?.checked || false;
        const aiDisclosureRequested = document.getElementById('viral-ai-disclosure')?.checked || false;

        if (!file && !sourceUrl) {
            errorEl.textContent = 'Выберите видеофайл или вставьте ссылку';
            return;
        }
        if (file && sourceUrl) {
            errorEl.textContent = 'Укажите либо файл, либо ссылку — не оба сразу';
            return;
        }
        if (currentClient.credits_remaining <= 0) {
            errorEl.textContent = 'Недостаточно кредитов. Обратитесь к администратору.';
            return;
        }

        const submitBtn = e.target.querySelector('button[type="submit"]');
        if (submitBtn) submitBtn.disabled = true;

        try {
            const gen = await submitViralEdit(
                file || null,
                language,
                style,
                format,
                dubLanguage,
                fontId,
                sourceUrl,
                voiceoverText,
                platform,
                intensity,
                genre,
                hookVariants,
                kineticSubtitles,
                volumetricHook,
                rightsConfirmed,
                aiDisclosureRequested,
            );
            currentClient.credits_remaining--;
            updateCreditsDisplay();
            fileInput.value = '';
            const urlEl = document.getElementById('viral-url');
            if (urlEl) urlEl.value = '';
            const voEl = document.getElementById('viral-voiceover');
            if (voEl) voEl.value = '';
            successEl.textContent = sourceUrl
                ? 'Ссылка принята, скачивание и монтаж запущены — следи за прогрессом в «Мои видео».'
                : 'Монтаж запущен — следи за прогрессом в «Мои видео».';
            successEl.style.display = 'block';
            showPage('history');
            openGenerationModal(gen.id);
        } catch (err) {
            errorEl.textContent = err.message;
        } finally {
            if (submitBtn) submitBtn.disabled = false;
        }
    });

    document.getElementById('clips-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const errorEl = document.getElementById('clips-error');
        const successEl = document.getElementById('clips-success');
        errorEl.textContent = '';
        successEl.style.display = 'none';

        const fileInput = document.getElementById('clips-file');
        const file = fileInput.files?.[0];
        const sourceUrl = (document.getElementById('clips-url')?.value || '').trim();
        const language = document.getElementById('clips-language').value;
        const maxClips = parseInt(document.getElementById('clips-max')?.value || '5', 10);
        const fontId = document.getElementById('clips-font')?.value || '';
        const platform = document.getElementById('clips-platform')?.value || 'auto';
        const kineticSubtitles = document.getElementById('clips-kinetic')?.checked || false;
        const volumetricHook = document.getElementById('clips-volumetric')?.checked || false;
        const rightsConfirmed = document.getElementById('clips-rights')?.checked || false;
        const aiDisclosureRequested = document.getElementById('clips-ai-disclosure')?.checked || false;

        if (!file && !sourceUrl) {
            errorEl.textContent = 'Выберите видеофайл или вставьте ссылку';
            return;
        }
        if (file && sourceUrl) {
            errorEl.textContent = 'Укажите либо файл, либо ссылку — не оба сразу';
            return;
        }
        if (currentClient.credits_remaining <= 0) {
            errorEl.textContent = 'Недостаточно кредитов. Обратитесь к администратору.';
            return;
        }

        const submitBtn = e.target.querySelector('button[type="submit"]');
        if (submitBtn) submitBtn.disabled = true;

        try {
            const gen = await submitAiClips(file || null, language, maxClips, fontId, sourceUrl, platform, kineticSubtitles, volumetricHook, rightsConfirmed, aiDisclosureRequested);
            currentClient.credits_remaining--;
            updateCreditsDisplay();
            fileInput.value = '';
            const urlEl = document.getElementById('clips-url');
            if (urlEl) urlEl.value = '';
            successEl.textContent = sourceUrl
                ? 'Ссылка принята, скачивание и нарезка запущены — клипы появятся в «Мои видео».'
                : 'Нарезка запущена — клипы появятся в «Мои видео».';
            successEl.style.display = 'block';
            showPage('history');
            openGenerationModal(gen.id);
        } catch (err) {
            errorEl.textContent = err.message;
        } finally {
            if (submitBtn) submitBtn.disabled = false;
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

    document.getElementById('profile-apply-font-btn')?.addEventListener('click', async () => {
        const msg = document.getElementById('profile-font-msg');
        const fontId = document.getElementById('profile-font-select')?.value;
        if (!fontId) {
            if (msg) msg.textContent = 'Выбери шрифт из списка';
            return;
        }
        try {
            const form = new FormData();
            form.append('font_id', fontId);
            const headers = {};
            if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
            const res = await fetch(`${API_BASE}/client/branding/library-font`, {
                method: 'POST', headers, body: form,
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Ошибка');
            if (msg) msg.textContent = `Применён: ${data.family}`;
            document.getElementById('profile-font').textContent = data.family;
        } catch (err) {
            if (msg) msg.textContent = err.message;
        }
    });

    document.getElementById('profile-upload-font-btn')?.addEventListener('click', async () => {
        const msg = document.getElementById('profile-font-msg');
        const file = document.getElementById('profile-font-file')?.files?.[0];
        const family = document.getElementById('profile-font-family')?.value || '';
        if (!file) {
            if (msg) msg.textContent = 'Выбери файл .ttf / .otf';
            return;
        }
        try {
            const form = new FormData();
            form.append('file', file);
            if (family) form.append('family_name', family);
            const headers = {};
            if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
            const res = await fetch(`${API_BASE}/client/branding/font`, {
                method: 'POST', headers, body: form,
            });
            const data = await res.json();
            if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Ошибка загрузки');
            if (msg) msg.textContent = `Загружен: ${data.family}`;
            document.getElementById('profile-font').textContent = data.family;
            document.getElementById('profile-font-file').value = '';
        } catch (err) {
            if (msg) msg.textContent = err.message;
        }
    });

    document.getElementById('handwriting-template-btn')?.addEventListener('click', async () => {
        try {
            const response = await fetch(`${API_BASE}/client/branding/handwriting-template`, {
                headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
            });
            if (!response.ok) throw new Error('Не удалось скачать шаблон');
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'videogen-handwriting-template.png';
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        } catch (err) {
            alert(err.message || String(err));
        }
    });

    document.getElementById('handwriting-build-btn')?.addEventListener('click', async () => {
        const msg = document.getElementById('handwriting-font-msg');
        const file = document.getElementById('handwriting-photo-file')?.files?.[0];
        if (!file) {
            if (msg) msg.textContent = 'Выберите фото заполненного шаблона';
            return;
        }
        if (msg) msg.textContent = 'Собираем шрифт…';
        try {
            const form = new FormData();
            form.append('file', file);
            const headers = {};
            if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
            const res = await fetch(`${API_BASE}/client/branding/handwriting-font`, {
                method: 'POST', headers, body: form,
            });
            const data = await res.json();
            if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Ошибка сборки шрифта');
            if (msg) msg.textContent = `Готово: ${data.family}`;
            document.getElementById('profile-font').textContent = data.family;
            document.getElementById('handwriting-photo-file').value = '';
        } catch (err) {
            if (msg) msg.textContent = err.message || String(err);
        }
    });

    document.getElementById('profile-font-file')?.addEventListener('change', async (e) => {
        const file = e.target.files?.[0];
        const preview = document.getElementById('profile-font-preview');
        const meta = document.getElementById('profile-font-preview-meta');
        if (!file || !preview) return;
        try {
            const objUrl = URL.createObjectURL(file);
            const familyCss = `VG_upload_${Date.now()}`;
            const face = new FontFace(familyCss, `url(${objUrl})`);
            await face.load();
            document.fonts.add(face);
            preview.style.fontFamily = `"${familyCss}", Arial, sans-serif`;
            if (meta) meta.textContent = `Превью файла: ${file.name}`;
        } catch (err) {
            if (meta) meta.textContent = `Не удалось превью файла: ${err.message || 'ошибка'}`;
        }
    });
});
