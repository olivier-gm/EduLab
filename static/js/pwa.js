(function () {
  'use strict';
  var promptEvent = null;
  var dialog;
  var installed = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  var icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3v12m-5-5 5 5 5-5M5 15v5h14v-5"/></svg>';
  document.querySelectorAll('.nav__actions').forEach(function (actions) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'pwa-download';
    button.setAttribute('data-install-app', '');
    button.setAttribute('aria-label', 'Descargar EduLab');
    button.title = 'Añadir EduLab a la pantalla de inicio';
    button.innerHTML = icon;
    actions.prepend(button);
  });
  function hideButtons() {
    document.querySelectorAll('[data-install-app]').forEach(function (button) { button.hidden = true; });
  }
  if (installed) hideButtons();
  window.addEventListener('beforeinstallprompt', function (event) {
    event.preventDefault();
    promptEvent = event;
    if (dialog) dialog.querySelector('[data-native-install]').hidden = false;
  });
  window.addEventListener('appinstalled', function () {
    promptEvent = null;
    hideButtons();
    if (dialog && dialog.open) dialog.close();
  });
  document.addEventListener('click', function (event) {
    if (!event.target.closest('[data-install-app]')) return;
    if (!dialog) {
      dialog = document.createElement('dialog');
      dialog.className = 'pwa-dialog';
      dialog.setAttribute('aria-labelledby', 'pwa-title');
      dialog.innerHTML = '<button type="button" class="pwa-dialog__close" aria-label="Cerrar instrucciones">×</button>' +
        '<div class="pwa-dialog__brand"><img src="/static/img/icon-192.png" alt=""><strong>Edu<span>Lab</span></strong></div>' +
        '<h2 id="pwa-title">EduLab en tu pantalla de inicio</h2><p>Ábrelo como una app, con su propio icono. Necesitas internet para generar tus documentos.</p>' +
        '<details data-platform="android"><summary>Android</summary><ol><li>Abre el menú del navegador <strong>⋮</strong></li><li>Elige <strong>Instalar aplicación</strong> o <strong>Añadir a pantalla de inicio</strong>.</li><li>Confirma con <strong>Instalar</strong> o <strong>Añadir</strong>.</li></ol></details>' +
        '<details data-platform="ios"><summary>iPhone o iPad</summary><ol><li>Toca <strong>Compartir</strong> en el navegador.</li><li>Selecciona <strong>Añadir a pantalla de inicio</strong>.</li><li>Confirma con <strong>Añadir</strong>. Si la opción no aparece, abre EduLab en Safari.</li></ol></details>' +
        '<details data-platform="desktop"><summary>Computadora</summary><ol><li>Busca el icono de instalación en la barra de direcciones o <strong>Instalar EduLab</strong> en el menú de Chrome o Edge.</li><li>Confirma con <strong>Instalar</strong>.</li></ol><p>En Safari para Mac: Archivo → Añadir al Dock.</p></details>' +
        '<button type="button" class="pwa-dialog__install" data-native-install hidden>Instalar EduLab</button>';
      document.body.append(dialog);
      var platform = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1) ? 'ios' : /Android/.test(navigator.userAgent) ? 'android' : 'desktop';
      dialog.querySelector('[data-platform="' + platform + '"]').open = true;
      dialog.querySelector('.pwa-dialog__close').addEventListener('click', function () { dialog.close(); });
      dialog.addEventListener('click', function (e) { if (e.target === dialog && (e.clientX < dialog.getBoundingClientRect().left || e.clientX > dialog.getBoundingClientRect().right || e.clientY < dialog.getBoundingClientRect().top || e.clientY > dialog.getBoundingClientRect().bottom)) dialog.close(); });
      dialog.querySelector('[data-native-install]').addEventListener('click', async function () {
        if (!promptEvent) return;
        var current = promptEvent;
        promptEvent = null;
        dialog.querySelector('[data-native-install]').hidden = true;
        dialog.close();
        try { await current.prompt(); await current.userChoice; } catch (_) { /* Las instrucciones siguen disponibles. */ }
      });
    }
    dialog.querySelector('[data-native-install]').hidden = !promptEvent;
    dialog.showModal();
  });
  if ('serviceWorker' in navigator && window.isSecureContext) {
    window.addEventListener('load', function () { navigator.serviceWorker.register('/sw.js').catch(function () { }); });
  }
})();
