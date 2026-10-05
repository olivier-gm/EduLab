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
  var index = 0;
  var timer;
  var changing = false;
  var visible = true;
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  // Precargar solo los logos seleccionados evita un destello al alternarlos.
  var images = universities.map(function (university) {
    var image = new Image();
    image.src = '/static/' + university.filename;
    return image;
  });

  function rotate() {
    if (changing) { return; }
    var next = (index + 1) % universities.length;
    // Un archivo que no cargó no sustituye la pareja actual de nombre y logo.
    for (var count = 0; count < universities.length; count++) {
      if (images[next].complete && images[next].naturalWidth > 0) { break; }
      next = (next + 1) % universities.length;
    }
    if (next === index || !images[next].naturalWidth) { return; }
    changing = true;
    cover.classList.add('is-changing');
    window.setTimeout(function () {
      index = next;
      name.textContent = universities[index].name.toLocaleUpperCase('es');
      logo.src = images[index].src;
      cover.classList.remove('is-changing');
      changing = false;
    }, 300);
  }

  function syncTimer() {
    window.clearInterval(timer);
    if (visible && !document.hidden && !reduced.matches) {
      timer = window.setInterval(rotate, 6000);
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
