/* Página de espera de una generación: consulta el avance real cada pocos segundos y, cuando el
   servidor termina, pasa a la descarga (o al formulario con el motivo si falló).
   La barra es una estimación: cada etapa real fija un mínimo y dentro de ella avanza despacio,
   sin llegar al máximo de la etapa hasta que el servidor confirma la siguiente. */
(function () {
  'use strict';
  var box = document.getElementById('loader');
  if (!box) { return; }
  var statusUrl = box.getAttribute('data-status-url');
  var kind = box.getAttribute('data-kind');
  var documentsUrl = box.getAttribute('data-documents-url') || '/my_documents';
  var bar = document.getElementById('loader-bar');
  var pct = document.getElementById('loader-pct');
  var msg = document.getElementById('loader-msg');
  var foot = document.getElementById('loader-foot');

  // step: paso de la lista; from/to: tramo de la barra; text: mensaje.
  var REPORT = {
    prepare: { step: 1, from: 4, to: 10, text: 'Ordenando los datos de tu portada…' },
    validate: { step: 2, from: 10, to: 22, text: 'Validando que el título sea apto para un trabajo académico…' },
    content: { step: 3, from: 22, to: 62, text: 'La IA está redactando el desarrollo del tema…' },
    sections: { step: 4, from: 62, to: 78, text: 'Escribiendo la introducción y la conclusión…' },
    bibliography: { step: 4, from: 78, to: 88, text: 'Buscando y ordenando las fuentes de la bibliografía…' },
    build: { step: 5, from: 88, to: 97, text: 'Maquetando el documento en Word y generando el PDF…' }
  };
  var GLOSSARY = {
    prepare: { step: 1, from: 4, to: 10, text: 'Preparando la portada del glosario…' },
    terms: { step: 2, from: 10, to: 28, text: 'Seleccionando y validando los términos…' },
    bibliography: { step: 3, from: 28, to: 42, text: 'Investigando las fuentes de cada tanda…' },
    definitions: { step: 3, from: 42, to: 82, text: 'Redactando definiciones breves para cada término…' },
    retry: { step: 3, from: 42, to: 82, text: 'Ajustando una tanda para completar sus definiciones…' },
    check: { step: 4, from: 82, to: 90, text: 'Comprobando la cantidad y el orden alfabético…' },
    build: { step: 5, from: 90, to: 97, text: 'Armando el glosario en Word y PDF…' }
  };
  var STAGES = kind === 'glossary' ? GLOSSARY : REPORT;

  var current = STAGES.prepare;
  var progress = current.from;
  var finished = false;
  var failures = 0;
  var total = 0;
  var detail = '';

  function paint() {
    bar.style.width = progress.toFixed(1) + '%';
    pct.textContent = Math.floor(progress) + '%';
    var message = detail || current.text;
    if (msg.textContent !== message) { msg.textContent = message; }
    var steps = box.querySelectorAll('.loader__step');
    for (var i = 0; i < steps.length; i++) {
      var n = parseInt(steps[i].getAttribute('data-lstep'), 10);
      steps[i].classList.toggle('is-active', n === current.step);
      steps[i].classList.toggle('is-done', n < current.step);
      var icon = steps[i].querySelector('i');
      if (icon) {
        icon.className = n < current.step ? 'bx bx-check-circle'
          : (n === current.step ? 'bx bx-loader-alt bx-spin' : 'bx bx-circle');
      }
    }
  }

  function setStage(name, completed, count) {
    var stage = STAGES[name];
    if (!stage) { return; }
    current = stage;
    total = count || 0;
    detail = '';
    if (total > 0) {
      progress = stage.from + (stage.to - stage.from) * Math.min(completed / total, 1);
      var labels = { terms: 'Términos seleccionados y validados', definitions: 'Términos definidos',
                     retry: 'Reintentando una tanda; términos definidos',
                     bibliography: 'Tandas de fuentes consultadas' };
      detail = (labels[name] || current.text) + ': ' + completed + ' de ' + total + '.';
    }
    if (progress < stage.from) { progress = stage.from; }
    paint();
  }

  // Avance lento dentro de la etapa actual (nunca pasa de su tope).
  window.setInterval(function () {
    if (finished || total > 0) { return; }
    progress += Math.max(0, current.to - progress) * 0.03 + 0.02;
    if (progress > current.to) { progress = current.to; }
    paint();
  }, 600);

  function go(url) {
    finished = true;
    window.location.href = url;
  }

  function poll() {
    if (finished) { return; }
    fetch(statusUrl, { headers: { 'Accept': 'application/json' }, cache: 'no-store', credentials: 'same-origin' })
      .then(function (response) {
        if (!response.ok) { throw new Error('http ' + response.status); }
        return response.json();
      })
      .then(function (data) {
        failures = 0;
        if (data.status === 'running') { setStage(data.stage, data.completed, data.total); return; }
        finished = true;
        if (data.status === 'done') {
          progress = 100;
          detail = '';
          current = { step: 6, text: '¡Listo! Abriendo tu documento…' };
          paint();
          window.setTimeout(function () { window.location.href = data.next; }, 450);
        } else {
          window.location.href = data.next;            // error: el formulario muestra el motivo
        }
      })
      .catch(function () {
        failures += 1;
        if (failures >= 6 && foot) {
          foot.innerHTML = 'No se pudo consultar el avance. El documento sigue generándose: revisa ' +
            '<a href="' + documentsUrl + '">Mis informes</a> en un momento.';
        }
      });
  }

  paint();
  poll();
  window.setInterval(poll, 2500);
})();
