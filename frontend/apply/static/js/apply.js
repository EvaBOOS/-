const API_BASE = '/api/v1';
let captchaToken = '';

async function mountSmartCaptcha(containerId) {
    try {
        const cfg = await fetch(`${API_BASE}/public/antispam`).then((r) => r.json());
        if (!cfg.captcha_enabled || !cfg.site_key) return;
        const box = document.getElementById(containerId);
        if (!box) return;
        if (!window.smartCaptcha) {
            await new Promise((resolve, reject) => {
                const s = document.createElement('script');
                s.src = 'https://smartcaptcha.yandexcloud.net/captcha.js';
                s.onload = resolve;
                s.onerror = reject;
                document.head.appendChild(s);
            });
        }
        if (window.smartCaptcha && window.smartCaptcha.render) {
            window.smartCaptcha.render(box, {
                sitekey: cfg.site_key,
                callback: (token) => { captchaToken = token; },
            });
        }
    } catch (e) {
        console.warn('captcha init', e);
    }
}

const companyLabel = document.getElementById('company-or-social-label');
const companyInput = document.getElementById('company-or-social');

document.querySelectorAll('input[name="account-type"]').forEach((radio) => {
    radio.addEventListener('change', () => {
        if (radio.value === 'blogger' && radio.checked) {
            companyLabel.textContent = 'Ссылка на канал / соцсети';
            companyInput.placeholder = 'https://...';
        } else if (radio.checked) {
            companyLabel.textContent = 'Компания';
            companyInput.placeholder = 'Название компании';
        }
    });
});

document.getElementById('apply-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errorEl = document.getElementById('form-error');
    errorEl.textContent = '';

    const accountType = document.querySelector('input[name="account-type"]:checked').value;
    const companyOrSocial = document.getElementById('company-or-social').value || null;

    const payload = {
        full_name: document.getElementById('full-name').value,
        email: document.getElementById('email').value,
        phone: document.getElementById('phone').value || null,
        account_type: accountType,
        company_name: accountType === 'company' ? companyOrSocial : null,
        portfolio_url: accountType === 'blogger' ? companyOrSocial : null,
        message: document.getElementById('message').value || null,
        accepted_terms: document.getElementById('accepted-terms').checked,
        captcha_token: captchaToken || null,
    };

    try {
        const response = await fetch(`${API_BASE}/public/apply`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Не удалось отправить заявку');
        }

        document.getElementById('apply-form').classList.add('hidden');
        document.getElementById('success-message').classList.add('active');
    } catch (err) {
        errorEl.textContent = err.message;
    }
});

mountSmartCaptcha('apply-captcha');
