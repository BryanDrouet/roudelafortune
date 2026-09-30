document.addEventListener('DOMContentLoaded', () => {
    const buttons = document.querySelectorAll('.tab-button');
    const panels = document.querySelectorAll('.tab-panel');
    const usernameInput = document.getElementById('username');
    const usernameTitle = document.getElementById('username-title');

    if (usernameInput && usernameTitle) {
        const syncUsernameTitle = () => {
            const value = usernameInput.value.trim();
            usernameTitle.textContent = value ? `Mon pseudo : ${value}` : 'Mon pseudo';
        };

        const usernameForm = document.querySelector('form[action="/setusername"]');
        if (usernameForm) {
            usernameForm.addEventListener('submit', () => {
                const value = usernameInput.value.trim();
                if (value) {
                    syncUsernameTitle();
                }
            });
        }

        syncUsernameTitle();
    }

    buttons.forEach((button) => {
        button.addEventListener('click', () => {
            const target = button.dataset.tab;

            buttons.forEach((btn) => {
                const isActive = btn === button;
                btn.classList.toggle('active', isActive);
                btn.setAttribute('aria-selected', String(isActive));
                btn.tabIndex = isActive ? 0 : -1;
            });
            panels.forEach((panel) => {
                const isActive = panel.id === target;
                panel.classList.toggle('active', isActive);
                panel.hidden = !isActive;
            });
        });
    });

    const pageContext = document.body.dataset.page || null;
    const pageState = document.getElementById('page-state');
    const gameCodeValue = document.body.dataset.gameCode || document.querySelector('input[name="game_code"]')?.value || null;

    if (gameCodeValue && pageState) {
        const updatePresence = () => fetch('/api/presence', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: `game_code=${encodeURIComponent(gameCodeValue)}`
        }).catch(() => {});

        updatePresence();
        setInterval(updatePresence, 5000);
        window.addEventListener('pagehide', () => {
            navigator.sendBeacon('/api/presence/leave', new URLSearchParams({ game_code: gameCodeValue }));
        });
    }

    // Curseurs des joueurs en direct, façon Figma/Canva : haute fréquence, pas d'animation de rattrapage.
    function initCursorTracking(containerSelector) {
        const cursorContainer = document.querySelector(containerSelector);
        if (!cursorContainer || !gameCodeValue || !pageState) {
            return;
        }

        const myUsername = pageState.dataset.username || null;
        const cursorLayer = document.createElement('div');
        cursorLayer.className = 'cursor-layer';
        cursorContainer.appendChild(cursorLayer);

        const renderedCursors = new Map();
        let lastSent = 0;

        // Le curseur ne se met à jour que lorsqu'un joueur bouge réellement la souris, pas via un intervalle fixe.
        document.addEventListener('mousemove', (event) => {
            const now = performance.now();
            if (now - lastSent < 70) {
                return;
            }
            lastSent = now;

            const x = Math.max(0, Math.min(100, (event.clientX / window.innerWidth) * 100));
            const y = Math.max(0, Math.min(100, (event.clientY / window.innerHeight) * 100));

            fetch('/api/cursor', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: `game_code=${encodeURIComponent(gameCodeValue)}&x=${x.toFixed(2)}&y=${y.toFixed(2)}`
            }).catch(() => {});

        });

        async function fetchCursors() {
            try {
                const response = await fetch('/api/cursors', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: `game_code=${encodeURIComponent(gameCodeValue)}`
                });
                const data = await response.json();
                return data.cursors || [];
            } catch (error) {
                return [];
            }
        }

        function syncCursors(cursors) {
            const seen = new Set();

            cursors.forEach((cursor) => {
                if (cursor.username === myUsername) {
                    return;
                }
                seen.add(cursor.username);

                let el = renderedCursors.get(cursor.username);
                if (!el) {
                    el = document.createElement('div');
                    el.className = 'remote-cursor';
                    el.innerHTML = '<svg class="remote-cursor-pointer" width="20" height="20" viewBox="0 0 20 20"><path d="M0 0 L0 16 L4.5 12.5 L7.5 19 L10 18 L7 11.5 L13 11.5 Z" fill="currentColor" stroke="white" stroke-width="1.2" stroke-linejoin="round" stroke-linecap="round"/></svg><span class="remote-cursor-label"></span>';
                    cursorLayer.appendChild(el);
                    renderedCursors.set(cursor.username, el);
                }

                const x = Math.min(Number(cursor.x) || 0, ((window.innerWidth - 20) / window.innerWidth) * 100);
                const y = Math.min(Number(cursor.y) || 0, ((window.innerHeight - 20) / window.innerHeight) * 100);
                el.style.left = `${x}%`;
                el.style.top = `${y}%`;
                el.classList.toggle('label-left', x > 75);
                el.classList.toggle('label-above', y > 80);
                el.classList.toggle('is-admin', !!cursor.is_admin);
                el.classList.toggle('is-current', !!cursor.is_current);
                el.querySelector('.remote-cursor-label').textContent = cursor.username;
            });

            renderedCursors.forEach((el, username) => {
                if (!seen.has(username)) {
                    el.remove();
                    renderedCursors.delete(username);
                }
            });
        }

        const refreshCursors = async () => syncCursors(await fetchCursors());
        refreshCursors();
        setInterval(refreshCursors, 1000);
    }

    if (pageContext === 'waiting') {
        initCursorTracking('.waiting-shell');

        if (!gameCodeValue) {
            return;
        }

        let currentHash = null;

        async function fetchGameHash() {
            try {
                const response = await fetch('/api/update/hash', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: `game_code=${encodeURIComponent(gameCodeValue)}`
                });
                const data = await response.json();
                return data.hash;
            } catch (error) {
                console.error('Erreur lors de la récupération du hash :', error);
                return null;
            }
        }

        async function checkForChanges() {
            const newHash = await fetchGameHash();
            if (newHash && newHash !== currentHash) {
                currentHash = newHash;
                window.location.reload();
            }
        }

        (async () => {
            currentHash = await fetchGameHash();
            setInterval(checkForChanges, 2000);
        })();
    }

    if (pageContext === 'finished') {
        initCursorTracking('.finished-shell');

        let currentHash = null;
        const fetchGameHash = async () => {
            try {
                const response = await fetch('/api/update/hash', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: `game_code=${encodeURIComponent(gameCodeValue)}`
                });
                const data = await response.json();
                return data.hash;
            } catch (error) {
                return null;
            }
        };
        const checkForChanges = async () => {
            const newHash = await fetchGameHash();
            if (newHash && currentHash && newHash !== currentHash) {
                window.location.reload();
            }
            currentHash = newHash || currentHash;
        };
        fetchGameHash().then((hash) => {
            currentHash = hash;
            setInterval(checkForChanges, 1000);
        });
    }

    if (pageContext === 'game' && pageState) {
        initCursorTracking('.cursor-container');

        if (!gameCodeValue) {
            return;
        }

        let currentHash = null;
        const revealTimer = pageState.dataset.lastEvent === 'true' ? 4000 : 0;

        async function fetchGameHash() {
            try {
                const response = await fetch(`/api/update/hash`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: `game_code=${encodeURIComponent(gameCodeValue)}`
                });
                const data = await response.json();
                return data.hash;
            } catch (error) {
                console.error('Erreur lors de la récupération du hash :', error);
                return null;
            }
        }

        async function checkForChanges() {
            const newHash = await fetchGameHash();
            if (newHash && newHash !== currentHash) {
                currentHash = newHash;
                window.location.reload();
            }
        }

        (async () => {
            currentHash = await fetchGameHash();
            // Intervalle très court : le passage de main doit être quasi instantané, sans F5.
            setInterval(checkForChanges, 400);
            if (revealTimer) {
                setTimeout(() => window.location.reload(), revealTimer);
            }
        })();
    }
});

