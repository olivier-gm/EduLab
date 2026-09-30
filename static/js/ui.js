/* ============================================================
   GULLIETH · Comportamientos compartidos de interfaz
   (navbar móvil, sombra al hacer scroll y animaciones de entrada)
   ============================================================ */

(function () {
  'use strict';

  /* ---- Menú móvil ---- */
  var burger = document.querySelector('[data-burger]');
  var links = document.querySelector('[data-nav-links]');

  if (burger && links) {
    burger.addEventListener('click', function () {
      var open = links.classList.toggle('is-open');
      burger.classList.toggle('is-open', open);
      burger.setAttribute('aria-expanded', open ? 'true' : 'false');
    });

    links.addEventListener('click', function (e) {
      if (e.target.closest('a')) {
        links.classList.remove('is-open');
        burger.classList.remove('is-open');
        burger.setAttribute('aria-expanded', 'false');
      }
    });
  }

  /* ---- Sombra de la barra al desplazarse ---- */
  var nav = document.querySelector('[data-nav]');
  if (nav) {
    var onScroll = function () {
      nav.classList.toggle('is-stuck', window.scrollY > 12);
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
  }

  /* ---- Aparición progresiva de secciones ----
     Se calcula con getBoundingClientRect en lugar de IntersectionObserver:
     es igual de barato aquí y no depende de que el observador se dispare. */
  var pending = Array.prototype.slice.call(document.querySelectorAll('.reveal'));

  if (pending.length) {
    document.documentElement.classList.add('has-js');

    var ticking = false;

    var checkReveals = function () {
      ticking = false;
      var vh = window.innerHeight || document.documentElement.clientHeight;
      pending = pending.filter(function (el) {
        var r = el.getBoundingClientRect();
        if (r.top < vh - 40 && r.bottom > 0) {
          el.classList.add('is-visible');
          return false;
        }
        return true;
      });
      if (!pending.length) {
        window.removeEventListener('scroll', request);
        window.removeEventListener('resize', request);
      }
    };

    var request = function () {
      if (ticking) { return; }
      ticking = true;
      window.requestAnimationFrame(checkReveals);
    };

    window.addEventListener('scroll', request, { passive: true });
    window.addEventListener('resize', request);
    window.addEventListener('load', request);
    document.addEventListener('visibilitychange', request);
    checkReveals();
  }
})();
