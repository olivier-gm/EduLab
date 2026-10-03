/* La espera procede del servidor; el reloj local solo actualiza la interfaz. */
(() => {
  document.querySelectorAll('[data-resend-wait]').forEach(form => {
    const button = form.querySelector('button[type="submit"]');
    const countdown = form.querySelector('[data-resend-countdown]');
    const announcement = form.querySelector('[data-resend-announcement]');
    const deadline = Date.now() + Math.max(0, Number(form.dataset.resendWait) || 0) * 1000;
    let sending = false, wasWaiting = deadline > Date.now();

    function update() {
      if (sending) return;
      const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      button.disabled = remaining > 0;
      countdown.textContent = remaining > 0
        ? `Podrás solicitar otro código en ${remaining} ${remaining === 1 ? 'segundo' : 'segundos'}.`
        : 'Ya puedes solicitar otro código.';
      if (!remaining && wasWaiting) {
        announcement.textContent = 'Ya puedes reenviar el código de verificación.';
        wasWaiting = false;
      }
    }

    form.addEventListener('submit', event => {
      if (sending || Date.now() < deadline) {
        event.preventDefault();
        return;
      }
      sending = true;
      button.disabled = true;
      countdown.textContent = 'Enviando un nuevo código…';
    });
    // Recalcula el tiempo real al volver a la pestaña o desde el historial.
    document.addEventListener('visibilitychange', update);
    window.addEventListener('pageshow', () => { sending = false; update(); });
    update();
    window.setInterval(update, 1000);
  });
})();
