/**
 * Shared cookie-consent banner, loaded from a single place
 * (/legal/static/js/cookie-consent.js) by landing/client/apply — no
 * per-app duplication. Pure localStorage, no backend calls: the site
 * has no analytics/tracking scripts wired up yet, so this is
 * compliance-readiness infrastructure, not a real tracker gate today.
 * window.VGConsent.hasAnalyticsConsent() is the hook future tracking
 * code should check before firing.
 */
(function () {
    var STORAGE_KEY = 'vg_cookie_consent';

    function readConsent() {
        try {
            var raw = localStorage.getItem(STORAGE_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch (e) {
            return null;
        }
    }

    function writeConsent(value) {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
        } catch (e) {
            /* localStorage unavailable (private mode etc.) — banner will just reappear next visit */
        }
    }

    window.VGConsent = {
        hasAnalyticsConsent: function () {
            var c = readConsent();
            return !!(c && c.analytics === true);
        },
    };

    if (readConsent()) return;

    function init() {
        var bar = document.createElement('div');
        bar.setAttribute('role', 'dialog');
        bar.setAttribute('aria-label', 'Согласие на использование cookie');
        bar.style.cssText = [
            'position:fixed', 'left:0', 'right:0', 'bottom:0', 'z-index:9999',
            'display:flex', 'flex-wrap:wrap', 'align-items:center', 'gap:14px',
            'padding:16px clamp(16px,4vw,40px)',
            'background:#12140f', 'color:#eef0e8',
            'border-top:2px solid #12140f',
            'font-family:"Syne","Trebuchet MS",sans-serif', 'font-size:0.86rem',
        ].join(';');

        var text = document.createElement('span');
        text.style.cssText = 'flex:1 1 260px; line-height:1.4;';
        text.innerHTML = 'Мы используем файлы cookie для работы сайта, аналитики и улучшения сервиса. Подробнее — <a href="/legal/cookies" style="color:#c8f000;">Cookie Policy</a>.';

        var actions = document.createElement('div');
        actions.style.cssText = 'display:flex; gap:10px; flex:0 0 auto;';

        function makeButton(labelText, bg, color) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.textContent = labelText;
            btn.style.cssText = [
                'padding:9px 16px', 'border:2px solid ' + (color === '#12140f' ? '#12140f' : '#eef0e8'),
                'background:' + bg, 'color:' + color,
                'font-family:inherit', 'font-weight:800', 'font-size:0.84rem', 'cursor:pointer',
            ].join(';');
            return btn;
        }

        var acceptBtn = makeButton('Принять всё', '#c8f000', '#12140f');
        var necessaryBtn = makeButton('Только необходимые', 'transparent', '#eef0e8');

        acceptBtn.addEventListener('click', function () {
            writeConsent({ necessary: true, analytics: true, ts: Date.now() });
            bar.remove();
        });
        necessaryBtn.addEventListener('click', function () {
            writeConsent({ necessary: true, analytics: false, ts: Date.now() });
            bar.remove();
        });

        actions.appendChild(necessaryBtn);
        actions.appendChild(acceptBtn);
        bar.appendChild(text);
        bar.appendChild(actions);
        document.body.appendChild(bar);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
