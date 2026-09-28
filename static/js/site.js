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

    usernameInput.addEventListener('input', syncUsernameTitle);
    syncUsernameTitle();
  }

  buttons.forEach((button) => {
    button.addEventListener('click', () => {
      const target = button.dataset.tab;

      buttons.forEach((btn) => btn.classList.toggle('active', btn === button));
      panels.forEach((panel) => panel.classList.toggle('active', panel.id === target));
    });
  });

  const pageContext = document.body.dataset.page || null;
  const gameState = document.getElementById('game-state');

  if (pageContext === 'waiting') {
    const waitingGameCode = document.body.dataset.gameCode || document.querySelector('input[name="game_code"]')?.value || null;
    if (!waitingGameCode) {
      return;
    }

    let currentHash = null;

    async function fetchGameHash() {
      try {
        const response = await fetch('/api/update/hash', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: `game_code=${encodeURIComponent(waitingGameCode)}`
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

  if (pageContext === 'game' && gameState) {
    const gameCodeValue = document.body.dataset.gameCode || document.querySelector('input[name="game_code"]')?.value || null;
    if (!gameCodeValue) {
      return;
    }

    let currentHash = null;
    const revealTimer = gameState.dataset.lastEvent === 'true' ? 4000 : 0;

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
      const interval = gameState.dataset.playerTurn === 'true' ? 5000 : 1500;
      setInterval(checkForChanges, interval);
      if (revealTimer) {
        setTimeout(() => window.location.reload(), revealTimer);
      }
    })();
  }
});
