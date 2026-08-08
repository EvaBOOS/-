const API_BASE = '/api/v1';

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
        message: document.getElementById('message').value || null
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
