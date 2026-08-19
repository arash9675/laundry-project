(function () {
    // Native local notifications — Android app (Capacitor) only.
    // Web push is unreliable in an Android WebView when the app is backgrounded,
    // so on Android we schedule local notifications that fire on the lock screen
    // even while the app is closed or in the background.
    const isAndroidApp = !!(window.Capacitor
        && typeof window.Capacitor.getPlatform === 'function'
        && window.Capacitor.getPlatform() === 'android');

    if (!isAndroidApp) return;

    function getNotifications() {
        return window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.LocalNotifications;
    }

    function stableId(machineId) {
        let h = 0;
        for (let i = 0; i < machineId.length; i++) h = (h * 31 + machineId.charCodeAt(i)) | 0;
        h = h >>> 0;
        return h % 1000000; // small positive int, safe for the plugin id
    }

    async function ensurePermission() {
        const n = getNotifications();
        if (!n) return false;
        try {
            const status = await n.checkPermissions();
            if (status && status.display === 'granted') return true;
            const req = await n.requestPermissions();
            return req && req.display === 'granted';
        } catch (e) { console.log('Native notification permission failed:', e); return false; }
    }

    window.TabuNativeAlarm = {
        // Schedule a 5-minute warning and a "finished" notification for a machine.
        async schedule(machineId, endTimeISO) {
            const n = getNotifications();
            if (!n) return;
            try {
                const ok = await ensurePermission();
                if (!ok) return;

                const end = new Date(endTimeISO).getTime();
                const now = Date.now();
                const warnAt = new Date(end - 5 * 60 * 1000);

                const notifications = [];
                if (warnAt.getTime() > now) {
                    notifications.push({
                        id: stableId(machineId),
                        title: 'Machine ' + machineId + ' almost done',
                        body: '5 minutes left',
                        schedule: { at: warnAt }
                    });
                }
                notifications.push({
                    id: stableId(machineId) + 1,
                    title: 'Machine ' + machineId + ' finished',
                    body: 'Your laundry is ready',
                    schedule: { at: new Date(end) }
                });

                await n.schedule({ notifications });
            } catch (e) { console.log('Native notification schedule failed:', e); }
        },

        async cancel(machineId) {
            const n = getNotifications();
            if (!n) return;
            try {
                const base = stableId(machineId);
                await n.cancel({ notifications: [{ id: base }, { id: base + 1 }] });
            } catch (e) { console.log('Native notification cancel failed:', e); }
        }
    };
})();
