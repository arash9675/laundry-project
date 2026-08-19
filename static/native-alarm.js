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
        try {
            return window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.LocalNotifications;
        } catch (e) {
            console.log('LocalNotifications plugin lookup failed:', e);
            return null;
        }
    }

    function stableId(machineId) {
        let h = 0;
        for (let i = 0; i < machineId.length; i++) h = (h * 31 + machineId.charCodeAt(i)) | 0;
        h = h >>> 0;
        return h % 1000000; // small positive int, safe for the plugin id
    }

    async function ensurePermission() {
        const n = getNotifications();
        if (!n) { console.log('LocalNotifications plugin not found'); return false; }
        try {
            let status = await n.checkPermissions();
            if (status && status.display === 'granted') return true;
            const req = await n.requestPermissions();
            console.log('LocalNotifications permission:', req && req.display);
            return !!(req && req.display === 'granted');
        } catch (e) {
            console.log('LocalNotifications permission error:', e);
            return false;
        }
    }

    window.TabuNativeAlarm = {
        // Schedule a 5-minute warning and a "finished" notification for a machine.
        async schedule(machineId, endTimeISO) {
            const n = getNotifications();
            if (!n) { console.log('TabuNativeAlarm: plugin unavailable'); return; }
            try {
                const ok = await ensurePermission();
                if (!ok) { console.log('TabuNativeAlarm: permission denied'); return; }

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
                console.log('TabuNativeAlarm: scheduled', notifications.length, 'notifications for', machineId);
            } catch (e) { console.log('TabuNativeAlarm schedule failed:', e); }
        },

        async cancel(machineId) {
            const n = getNotifications();
            if (!n) return;
            try {
                const base = stableId(machineId);
                await n.cancel({ notifications: [{ id: base }, { id: base + 1 }] });
                console.log('TabuNativeAlarm: cancelled for', machineId);
            } catch (e) { console.log('TabuNativeAlarm cancel failed:', e); }
        }
    };
})();
