(function () {
  'use strict';

  document.querySelectorAll('[data-password-toggle]').forEach(function (button) {
    var input = button.parentElement.querySelector('input[type="password"], input[type="text"]');
    var icon = button.querySelector('i');
    if (!input) { return; }

    button.addEventListener('click', function () {
      var showing = input.type === 'password';
      input.type = showing ? 'text' : 'password';
      button.setAttribute('aria-pressed', showing ? 'true' : 'false');
      button.setAttribute('aria-label', showing ? 'Ocultar contraseña' : 'Mostrar contraseña');
      if (icon) {
        icon.classList.toggle('bx-show', !showing);
        icon.classList.toggle('bx-hide', showing);
      }
      input.focus({ preventScroll: true });
    });
  });
})();
