(function () {
  'use strict';
  var cover = document.querySelector('[data-preview-university]');
  var data = document.getElementById('preview-universities');
  if (!cover || !data) { return; }
  var universities;
  try { universities = JSON.parse(data.textContent); } catch (error) { return; }
  if (universities.length < 2) { return; }
  var name = cover.querySelector('.pm-university__name');
  var logo = cover.querySelector('img');
  var FADE_MS = 300;          // igual que la transición de opacidad de .pm-university en landing.css
  var EVERY_MS = 6000;
  var DECODE_WAIT_MS = 400;   // tope de espera por la decodificación antes de mostrar igual
  var index = 0;
  var timer;
  var changing = false;
  var visible = true;
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)');

  // Miniatura web (static/logos/thumbs); si un logo todavía no la tiene, el archivo completo.
  function urlOf(university) { return '/static/' + (university.thumb || university.filename); }

  // Cada logo se descarga Y se decodifica por adelantado, cuando el navegador está libre. Antes se
  // asignaba el src en el mismo instante en que cambiaba el nombre: el texto aparecía y el logo
  // llegaba una décima después, porque el navegador apenas empezaba a decodificar la imagen.
  var ready = universities.map(function () { return false; });
  var images = universities.map(function (university) { return new Image(); });
  function preload() {
    universities.forEach(function (university, i) {
      var image = images[i];
      var done = function () { ready[i] = image.naturalWidth > 0; };
      image.onload = function () {
        if (image.decode) { image.decode().then(done, done); } else { done(); }
      };
      image.onerror = done;
      image.src = urlOf(university);
    });
  }
  if (document.readyState === 'complete') { preload(); } else { window.addEventListener('load', preload, { once: true }); }

  function rotate() {
    if (changing) { return; }
    // Un archivo que no cargó no sustituye la pareja actual de nombre y logo.
    var next = index;
    for (var step = 1; step < universities.length; step++) {
      var candidate = (index + step) % universities.length;
      if (ready[candidate]) { next = candidate; break; }
    }
    if (next === index) { return; }
    changing = true;
    cover.classList.add('is-changing');
    window.setTimeout(function () { swap(next); }, FADE_MS);
  }

  // Con el bloque invisible se cambian nombre y logo a la vez y solo se vuelve a mostrar cuando el
  // logo ya está decodificado y el navegador alcanzó a pintarlo: ambos aparecen en el mismo cuadro.
  function swap(next) {
    var finished = false;
    function reveal() {
      if (finished) { return; }
      finished = true;
      window.requestAnimationFrame(function () {
        window.requestAnimationFrame(function () {
          cover.classList.remove('is-changing');
          changing = false;
        });
      });
    }
    index = next;
    name.textContent = universities[index].name.toLocaleUpperCase('es');
    logo.src = images[index].src;
    window.setTimeout(reveal, DECODE_WAIT_MS);
    if (logo.decode) { logo.decode().then(reveal, reveal); } else { reveal(); }
  }

  function syncTimer() {
    window.clearInterval(timer);
    if (visible && !document.hidden && !reduced.matches) {
      timer = window.setInterval(rotate, EVERY_MS);
    }
  }
  document.addEventListener('visibilitychange', syncTimer);
  reduced.addEventListener('change', syncTimer);
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (entries) {
      visible = entries[0].isIntersecting;
      syncTimer();
    }).observe(cover);
  }
  syncTimer();
})();
