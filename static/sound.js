(function () {
    // Audio feedback — Android app (Capacitor) only.
    const isAndroidApp = !!(window.Capacitor
        && typeof window.Capacitor.getPlatform === 'function'
        && window.Capacitor.getPlatform() === 'android');

    if (!isAndroidApp) return;

    const STORAGE_KEY = 'laundrySoundEnabled';
    const CLICK_SOUND = './option.mp3';
    const WELCOME_SOUND = './start.mp3';

    const clickAudio = new Audio(CLICK_SOUND);
    clickAudio.preload = 'auto';
    clickAudio.volume = 0.5;

    const welcomeAudio = new Audio(WELCOME_SOUND);
    welcomeAudio.preload = 'auto';
    welcomeAudio.volume = 0.7;

    function isEnabled() {
        return localStorage.getItem(STORAGE_KEY) !== '0';
    }

    function playClick() {
        if (!isEnabled()) return;
        try {
            clickAudio.currentTime = 0;
            const p = clickAudio.play();
            if (p && p.catch) p.catch(function () {});
        } catch (e) { /* ignore */ }
    }

    function playWelcome() {
        if (!isEnabled()) return;
        try {
            welcomeAudio.currentTime = 0;
            const p = welcomeAudio.play();
            if (p && p.catch) p.catch(function () {});
        } catch (e) { /* ignore */ }
    }

    // Delegated click listener in capture phase so it also catches dynamically
    // rendered machine cards and their inline onclick handlers.
    document.addEventListener('click', function (event) {
        const el = event.target;
        if (!(el instanceof Element)) return;
        if (el.closest('button, a, [role="button"], select, input[type="checkbox"]')) {
            playClick();
        }
    }, true);

    const ICON_ON = '<svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>';
    const ICON_OFF = '<svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>';

    function mountToggle() {
        const anchor = document.getElementById('lang-toggle');
        if (!anchor || !anchor.parentNode) return;

        const btn = document.createElement('button');
        btn.id = 'sound-toggle';
        btn.type = 'button';
        btn.className = 'inline-flex items-center justify-center w-9 h-9 border border-slate-200 rounded-lg text-slate-600 hover:bg-slate-50 btn-transition';
        btn.setAttribute('aria-label', 'Toggle sound');
        btn.title = 'Sound';

        function render() {
            btn.innerHTML = isEnabled() ? ICON_ON : ICON_OFF;
        }

        btn.addEventListener('click', function () {
            const next = !isEnabled() ? '1' : '0';
            localStorage.setItem(STORAGE_KEY, next);
            render();
            if (next === '1') playClick();
        });

        render();
        anchor.parentNode.insertBefore(btn, anchor);
    }

    function onReady() {
        mountToggle();
        // Welcome sound on app open.
        playWelcome();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', onReady);
    } else {
        onReady();
    }
})();
