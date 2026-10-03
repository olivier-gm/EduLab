/* Reintentos acotados; el servidor mantiene la referencia y evita cobros repetidos. */
(() => {
  const form = document.querySelector('[data-pay]');
  if (form) form.addEventListener('submit', () => {
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    button.textContent = form.querySelector('[name="method"]:checked').value === 'binance' ? 'Verificando pago…' : 'Enviando referencia…';
  });
  document.querySelectorAll('[data-binance-payment]').forEach(panel => {
    const message = panel.querySelector('[data-verification-message]');
    const button = panel.querySelector('[data-verification-retry]');
    const correct = panel.querySelector('[data-verification-correct]');
    let timer, attempts = 0, busy = false;
    const wait = Math.max(6, Number(panel.dataset.retryAfter) - Date.now() / 1000);

    async function verify() {
      if (busy) return;
      clearTimeout(timer);
      busy = true;
      button.disabled = true;
      attempts++;
      try {
        const response = await fetch(panel.dataset.verifyUrl, {
          method: 'POST', credentials: 'same-origin',
          body: new URLSearchParams({csrf_token: panel.dataset.csrf})
        });
        if (!response.ok) throw new Error('No disponible');
        const result = await response.json();
        message.textContent = result.message;
        correct.hidden = result.provider_status !== 'NOT_FOUND' || result.status !== 'pending';
        if (result.status === 'approved') {
          message.classList.replace('plans-alert--warn', 'plans-alert--ok');
          button.hidden = true;
          setTimeout(() => location.reload(), 1400);
        } else if (result.status === 'rejected') {
          location.replace('/plans?error=' + encodeURIComponent(result.message));
        } else if (result.retryable && attempts < 5) {
          timer = setTimeout(verify, Math.max(6, result.retry_after) * 1000);
        }
      } catch (_) {
        message.textContent = 'No pudimos comprobar el pago todavía. Tu referencia se conserva; puedes volver a comprobarlo.';
        if (attempts < 5) timer = setTimeout(verify, 6000);
      } finally {
        busy = false;
        button.disabled = false;
      }
    }
    button.addEventListener('click', () => { attempts = 0; verify(); });
    correct.addEventListener('submit', () => { clearTimeout(timer); });
    timer = setTimeout(verify, wait * 1000);
  });
})();
