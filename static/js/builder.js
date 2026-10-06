/* ============================================================
   EDULAB · Constructor de documento
   Controla el asistente por pasos, la vista previa en vivo de la
   portada y el overlay de progreso durante la generación.

   IMPORTANTE: los atributos "name" de los campos son los que espera
   el backend (form_processor.FormProcessor) y no deben cambiarse:
   u, area, carrera, teacher, asignatura, seccion, periodo, academico,
   city, date, title, gblock-template-canvas-integrantes,
   input1..input8, id1..id8, subtitle_1..subtitle_8,
   body, introduccion, conclusion, incluir_introduccion, incluir_conclusion.
   ============================================================ */

(function () {
  'use strict';

  var MAX_STUDENTS = 8;
  var MAX_TOPICS = 8;
  var MESES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
    'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'];

  var $ = function (sel, ctx) { return (ctx || document).querySelector(sel); };
  var $$ = function (sel, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(sel)); };

  var form = $('#docForm');
  if (!form) { return; }

  var up = function (v) { return (v || '').toUpperCase(); };
  var setText = function (el, value) { if (el) { el.textContent = value; } };

  /* ==========================================================
     1. VISTA PREVIA DE LA PORTADA
     ========================================================== */
  var pv = {
    paper: $('#pv-paper'),
    ministerio: $('#pv-ministerio'),
    u: $('#pv-u'),
    area: $('#pv-area'),
    carrera: $('#pv-carrera'),
    crest: $('#pv-crest'),
    title: $('#pv-title'),
    teacherWrap: $('#pv-teacher-wrap'),
    teacher: $('#pv-teacher'),
    asigWrap: $('#pv-asig-wrap'),
    asignatura: $('#pv-asignatura'),
    periodoWrap: $('#pv-periodo-wrap'),
    periodo: $('#pv-periodo'),
    seccionWrap: $('#pv-seccion-wrap'),
    seccion: $('#pv-seccion'),
    studentsLabel: $('#pv-students-label'),
    students: $('#pv-students'),
    foot: $('#pv-foot')
  };

  var show = function (el, on) { if (el) { el.style.display = on ? '' : 'none'; } };

  function renderHeader() {
    setText(pv.u, up($('#f-u').value) || 'INSTITUCIÓN');
    setText(pv.area, up($('#f-area').value));
    setText(pv.carrera, up($('#f-carrera').value));
    show(pv.area, !!$('#f-area').value && isUni());
    show(pv.carrera, !!$('#f-carrera').value && isUni());
  }

  function renderTitle() {
    setText(pv.title, up($('#f-title').value) || 'TÍTULO DEL TRABAJO');
  }

  function renderInfoBlock() {
    var teacher = up($('#f-teacher').value);
    var asig = up($('#f-asignatura').value);
    var periodo = $('#f-periodo').value;
    var academico = $('#f-academico').value;
    var seccion = up($('#f-seccion').value);

    setText(pv.teacher, teacher);
    show(pv.teacherWrap, !!teacher);

    setText(pv.asignatura, asig);
    show(pv.asigWrap, !!asig);

    // El backend arma "SEMESTRE: 1º" (academico + periodo)
    var periodoLabel = academico ? up(academico) + ':' : '';
    setText($('#pv-periodo-label'), periodoLabel);
    setText(pv.periodo, periodo);
    show(pv.periodoWrap, !!(periodo || academico));

    setText(pv.seccion, seccion ? '"' + seccion + '"' : '');
    show(pv.seccionWrap, !!seccion);
  }

  function renderFoot() {
    var city = up($('#f-city').value);
    var date = $('#f-date').value; // dd/mm/yyyy
    var parts = [];
    if (city) { parts.push(city); }
    if (date) { parts.push(up(dateToText(date))); }
    setText(pv.foot, parts.join(', '));
  }

  function renderStudentsPreview() {
    if (!pv.students) { return; }
    pv.students.innerHTML = '';
    var rows = $$('.student-row');
    var filled = 0;

    rows.forEach(function (row) {
      var name = up(($('.js-student-name', row) || {}).value || '');
      var ci = (($('.js-student-id', row) || {}).value || '').trim();
      if (!name && !ci) { return; }
      filled++;
      var a = document.createElement('span');
      a.textContent = name;
      var b = document.createElement('span');
      b.textContent = ci ? 'C.I- ' + ci : '';
      pv.students.appendChild(a);
      pv.students.appendChild(b);
    });

    setText(pv.studentsLabel, filled > 1 ? 'ALUMNOS:' : 'ALUMNO:');
    if (pv.studentsLabel) { show(pv.studentsLabel.parentNode, filled > 0); }
  }

  function dateToText(ddmmyyyy) {
    if (!ddmmyyyy || ddmmyyyy.length !== 10) { return ''; }
    var d = ddmmyyyy.slice(0, 2);
    var m = parseInt(ddmmyyyy.slice(3, 5), 10);
    var y = ddmmyyyy.slice(6, 10);
    if (!MESES[m - 1]) { return ''; }
    return d + ' de ' + MESES[m - 1] + ' de ' + y;
  }

  /* ---- Escudo / logo de la institución ---- */
  var logoBase = form.getAttribute('data-logo-base') || '/static/logos';
  var LOGO_EXT = ['png', 'jpg', 'jpeg', 'webp', 'svg'];

  function slugify(name) {
    return name.normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase().trim()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '');
  }

  var logoToken = 0;
  function renderCrest() {
    if (!pv.crest) { return; }
    var slug = slugify($('#f-u').value || '');
    logoToken++;
    var token = logoToken;

    if (!slug) {
      pv.crest.classList.remove('is-on');
      return;
    }

    // Primero la miniatura web (static/logos/thumbs, la crea tools/fix_logos.py): pesa ~25 KB en vez
    // de ~180 KB y la vista previa muestra el escudo a menos de 80 px. Si esa universidad todavía
    // no la tiene, se prueba el logo completo como antes.
    var candidates = [logoBase + '/thumbs/' + slug + '.webp'].concat(LOGO_EXT.map(function (ext) {
      return logoBase + '/' + slug + '.' + ext;
    }));
    var i = 0;
    var tryNext = function () {
      if (token !== logoToken) { return; }
      if (i >= candidates.length) {
        pv.crest.classList.remove('is-on');
        return;
      }
      var probe = new Image();
      var url = candidates[i++];
      probe.onload = function () {
        if (token !== logoToken) { return; }
        pv.crest.src = url;
        pv.crest.classList.add('is-on');
      };
      probe.onerror = tryNext;
      probe.src = url;
    };
    tryNext();
  }

  /* ==========================================================
     2. MODO INSTITUCIÓN (universidad / bachillerato)
     ========================================================== */
  function isUni() {
    var r = $('input[name="instituto"]:checked');
    return !r || r.value === 'universidad';
  }

  // El bachillerato venezolano se organiza por grado/año; la universidad,
  // por año/semestre/trimestre/trayecto. No tiene sentido ofrecer "Grado"
  // en universidad ni "Semestre"/"Trimestre"/"Trayecto" en bachillerato.
  var ACADEMICO_OPTIONS = {
    bach: [
      { value: '', label: 'Sin especificar' },
      { value: 'Grado', label: 'Grado' },
      { value: 'Año', label: 'Año' }
    ],
    uni: [
      { value: '', label: 'Sin especificar' },
      { value: 'Año', label: 'Año' },
      { value: 'Semestre', label: 'Semestre' },
      { value: 'Trimestre', label: 'Trimestre' },
      { value: 'Trayecto', label: 'Trayecto' }
    ]
  };

  function applyAcademicoOptions() {
    var sel = $('#f-academico');
    var opts = ACADEMICO_OPTIONS[isUni() ? 'uni' : 'bach'];
    var current = sel.value;

    sel.innerHTML = '';
    opts.forEach(function (o) {
      var opt = document.createElement('option');
      opt.value = o.value;
      opt.textContent = o.label;
      sel.appendChild(opt);
    });

    // Si la selección anterior ya no existe en el nuevo modo (p.ej. venía
    // de "Semestre" y se cambió a bachillerato), se vuelve a "Sin especificar".
    sel.value = opts.some(function (o) { return o.value === current; }) ? current : '';
  }

  function applyInstitute() {
    var uni = isUni();
    $('#f-u').placeholder = uni ? 'Ej. Universidad Central de Venezuela' : 'Ej. U.E. Nacional Simón Bolívar';
    setText($('#lbl-u'), uni ? 'Universidad o instituto' : 'Unidad educativa / liceo');
    show($('#wrap-area'), uni);
    show($('#wrap-carrera'), uni);
    setText(pv.ministerio, uni
      ? 'MINISTERIO DEL PODER POPULAR PARA LA EDUCACIÓN UNIVERSITARIA'
      : 'MINISTERIO DEL PODER POPULAR PARA LA EDUCACIÓN');
    applyAcademicoOptions();
    renderHeader();
    renderInfoBlock();
  }

  $$('input[name="instituto"]').forEach(function (r) {
    r.addEventListener('change', applyInstitute);
  });

  /* ==========================================================
     3. MODO DE CONTENIDO (IA / manual)
     ========================================================== */
  function applyMode() {
    var mode = ($('input[name="global-mode"]:checked') || {}).value || 'ia';
    var glossary = isGlossary();
    show($('#ia-block'), !glossary && mode === 'ia');
    show($('#manual-block'), !glossary && mode === 'standard');
    show($('#writer-options'), !glossary);
    show($('#scan-block'), !glossary && mode === 'ia');
    show($('#glossary-source-block'), glossary);
    show($('#glossary-block'), glossary);
    $('#f-incluir-intro').disabled = glossary;
    $('#f-incluir-concl').disabled = glossary;
    $('#f-incluir-intro').hidden = glossary;
    $('#f-incluir-concl').hidden = glossary;
    show($('label[for="f-incluir-intro"]'), !glossary);
    show($('label[for="f-incluir-concl"]'), !glossary);
    show($('#intro-option'), !glossary);
    show($('#conclusion-option'), !glossary);
    setText($('#sections-title'), glossary ? 'Bibliografía por término' : 'Secciones del informe');
    setText($('#bibliography-help'), glossary
      ? 'Cada término llevará su propia fuente, junto a su definición.'
      : 'La bibliografía irá en una página después de la conclusión, o al final si no hay conclusión.');
    setText($('#document-note'), glossary
      ? 'Recibirás Word y PDF con portada y glosario en orden alfabético, sin introducción ni conclusión.'
      : 'Recibirás Word y PDF con índice y páginas separadas para cada sección.');
    // La bibliografía por término de los glosarios es solo del plan Pro.
    var bibBox = $('#f-incluir-bib');
    var bibLocked = glossary && form.dataset.glossaryBib !== '1';
    bibBox.disabled = bibLocked;
    if (bibLocked) {
      bibBox.checked = false;
      setText($('#bibliography-help'), 'La bibliografía por término de los glosarios es exclusiva del plan Pro.');
    }
    applySections();
    applyGlossarySource();
  }

  function isGlossary() {
    return ($('input[name="document_kind"]:checked') || {}).value === 'glossary';
  }

  $$('input[name="document_kind"]').forEach(function (r) {
    r.addEventListener('change', applyMode);
  });

  function applyGlossarySource() {
    var list = ($('#glossary-list') || {}).checked;
    var glossary = isGlossary();
    show($('#glossary-count-wrap'), !list);
    show($('#glossary-list-wrap'), list);
    setText($('#title-label'), glossary ? (list ? 'Título del glosario' : 'Tema del glosario') : 'Título del trabajo');
    $('#f-title').placeholder = glossary ? 'Ej. Biología celular' : 'Ej. El sistema nervioso';
    setText($('#title-help'), glossary
      ? (list ? 'Da contexto a tu lista y aparecerá en la portada.' : 'La IA elegirá los términos relacionados con este tema.')
      : 'Sé específico: mientras más claro el título, mejor queda el desarrollo.');
  }

  $$('input[name="glossary_source"]').forEach(function (r) {
    r.addEventListener('change', applyGlossarySource);
  });

  function termLines() {
    return $('#f-glossary-terms').value.split(/\r?\n/).filter(function (line) { return line.trim(); });
  }

  $('#f-glossary-terms').addEventListener('input', function () {
    setText($('#terms-count'), termLines().length + ' de ' + form.dataset.maxTerms + ' términos');
  });

  var extractingTerms = false;
  $('#extract-terms').addEventListener('click', async function () {
    var file = $('#f-terms-file').files[0];
    var status = $('#terms-status');
    if (!file) { status.textContent = 'Selecciona primero un archivo.'; return; }
    if (file.size > 10 * 1024 * 1024) { status.textContent = 'El archivo supera 10 MB.'; return; }
    if (extractingTerms) return;
    extractingTerms = true;
    this.disabled = true;
    status.textContent = 'Leyendo la lista de términos…';
    var data = new FormData();
    data.append('terms_file', file);
    try {
      var response = await fetch(form.dataset.termsUrl, { method: 'POST', body: data });
      if (response.redirected) { throw new Error('Tu sesión venció. Inicia sesión de nuevo.'); }
      var result = await response.json();
      if (!response.ok) { throw new Error(result.error || 'No se pudo leer el archivo.'); }
      $('#f-glossary-terms').value = result.terms.join('\n');
      $('#f-glossary-terms').dispatchEvent(new Event('input'));
      status.textContent = 'Se leyeron ' + result.count + ' términos. Revisa la lista antes de generar.';
    } catch (error) {
      status.textContent = error.message || 'No se pudo leer el archivo. Pega la lista manualmente.';
    } finally {
      extractingTerms = false;
      this.disabled = false;
    }
  });

  $$('input[name="global-mode"]').forEach(function (r) {
    r.addEventListener('change', applyMode);
  });

  /* ==========================================================
     3b. SECCIONES OPCIONALES (introducción / conclusión)
     Los checkboxes valen tanto en modo IA (el backend no la pide) como en
     modo manual (el campo se oculta y no se envía contenido).
     ========================================================== */
  var chkIntro = $('#f-incluir-intro');
  var chkConcl = $('#f-incluir-concl');

  function applySections() {
    show($('#wrap-introduccion'), chkIntro.checked);
    show($('#wrap-conclusion'), chkConcl.checked);
    var bibliography = $('#f-incluir-bib').checked;
    show($('#wrap-bibliografia'), bibliography);
    var sections = [];
    if (isGlossary()) {
      setText($('#sections-summary'), bibliography ? 'Activada · una fuente junto a cada definición' : 'Opcional · desactivada');
      return;
    }
    if (chkIntro.checked) sections.push('Introducción');
    if (chkConcl.checked) sections.push('conclusión');
    setText($('#sections-summary'), (sections.length ? sections.join(' y ') : 'Solo desarrollo') +
      (bibliography ? ' · con bibliografía' : ' · sin bibliografía'));
  }

  chkIntro.addEventListener('change', applySections);
  chkConcl.addEventListener('change', applySections);
  $('#f-incluir-bib').addEventListener('change', applySections);

  /* ==========================================================
     4. TIPOGRAFÍA DE LA VISTA PREVIA
     ========================================================== */
  $$('input[name="fuente"]').forEach(function (r) {
    r.addEventListener('change', function () {
      pv.paper.classList.toggle('font-tnr', r.value === 'tnr' && r.checked);
    });
  });

  /* ==========================================================
     5. ESTUDIANTES (stepper dinámico)
     ========================================================== */
  var countInput = $('#f-count');       // name="gblock-template-canvas-integrantes"
  var countOut = $('#stu-count');
  var studentsBox = $('#students');
  var emptyMsg = $('#students-empty');

  function studentCount() { return parseInt(countInput.value || '0', 10) || 0; }

  function renderStudents() {
    var n = studentCount();
    var current = $$('.student-row', studentsBox).length;

    // Quitar filas sobrantes
    while (current > n) {
      studentsBox.removeChild(studentsBox.lastElementChild);
      current--;
    }
    // Añadir filas faltantes
    for (var i = current + 1; i <= n; i++) {
      studentsBox.appendChild(buildStudentRow(i));
    }

    countOut.textContent = n === 0 ? '—' : n;
    show(emptyMsg, n === 0);
    $('#stu-minus').disabled = n <= 0;
    $('#stu-plus').disabled = n >= MAX_STUDENTS;
    renderStudentsPreview();
  }

  // Sólo letras (con acentos/ñ) y espacios: un nombre no lleva dígitos ni símbolos.
  var NAME_INVALID_RE = /[^A-Za-zÁÉÍÓÚáéíóúÑñÜü\s]/g;

  function sanitizeName(raw) {
    return raw.replace(NAME_INVALID_RE, '');
  }

  // Cédula: sólo dígitos y puntos, más como mucho una letra y sólo si va
  // de primera (p.ej. "V12.345.678"). Cualquier otra letra, o una letra
  // que no esté al inicio, se descarta en vez de rechazar todo el campo.
  function sanitizeCedula(raw) {
    var out = '';
    for (var i = 0; i < raw.length; i++) {
      var ch = raw[i];
      if (/[A-Za-z]/.test(ch)) {
        if (out.length === 0) { out += ch.toUpperCase(); }
      } else if (/[0-9.]/.test(ch)) {
        out += ch;
      }
    }
    return out;
  }

  function bindSanitizer(input, sanitizeFn) {
    input.addEventListener('input', function () {
      var caretFromEnd = input.value.length - input.selectionEnd;
      var clean = sanitizeFn(input.value);
      if (clean !== input.value) {
        input.value = clean;
        var pos = Math.max(0, clean.length - caretFromEnd);
        input.setSelectionRange(pos, pos);
      }
    });
  }

  function buildStudentRow(i) {
    var row = document.createElement('div');
    row.className = 'student-row';

    var num = document.createElement('div');
    num.className = 'student-row__n';
    num.textContent = i;

    var name = document.createElement('input');
    name.type = 'text';
    name.className = 'input js-student-name';
    name.name = 'input' + i;
    name.maxLength = 40;
    name.placeholder = 'Nombre y apellido';
    name.autocomplete = 'off';

    var ci = document.createElement('input');
    ci.type = 'text';
    ci.className = 'input js-student-id';
    ci.name = 'id' + i;
    ci.maxLength = 12;
    ci.inputMode = 'numeric';
    ci.placeholder = 'C.I.';
    ci.autocomplete = 'off';

    bindSanitizer(name, sanitizeName);
    bindSanitizer(ci, sanitizeCedula);
    name.addEventListener('input', renderStudentsPreview);
    ci.addEventListener('input', renderStudentsPreview);

    row.appendChild(num);
    row.appendChild(name);
    row.appendChild(ci);
    return row;
  }

  $('#stu-plus').addEventListener('click', function () {
    countInput.value = Math.min(MAX_STUDENTS, studentCount() + 1);
    renderStudents();
  });
  $('#stu-minus').addEventListener('click', function () {
    countInput.value = Math.max(0, studentCount() - 1);
    renderStudents();
  });

  /* ==========================================================
     6. TEMAS ESPECÍFICOS (subtitle_1 .. subtitle_8)
     ========================================================== */
  var topicsBox = $('#topics');
  var addTopic = $('#add-topic');

  function reindexTopics() {
    $$('.topic-row', topicsBox).forEach(function (row, idx) {
      var input = $('input', row);
      input.name = 'subtitle_' + (idx + 1);
      input.placeholder = 'Tema ' + (idx + 1) + ' a desarrollar';
    });
    addTopic.disabled = $$('.topic-row', topicsBox).length >= MAX_TOPICS;
    var count = $$('.topic-row', topicsBox).length;
    show($('#topics-empty'), count === 0);
    setText($('#topics-summary'), count ? count + (count === 1 ? ' tema añadido' : ' temas añadidos') : 'Opcional · la IA organiza el desarrollo');
    if (count) $('#ia-block').open = true;
  }

  function buildTopicRow() {
    var row = document.createElement('div');
    row.className = 'topic-row';

    var input = document.createElement('input');
    input.type = 'text';
    input.className = 'input';
    input.maxLength = 300;

    var del = document.createElement('button');
    del.type = 'button';
    del.setAttribute('aria-label', 'Quitar tema');
    del.innerHTML = '<i class="bx bx-trash"></i>';
    del.addEventListener('click', function () {
      row.remove();
      reindexTopics();
    });

    row.appendChild(input);
    row.appendChild(del);
    return row;
  }

  addTopic.addEventListener('click', function () {
    if ($$('.topic-row', topicsBox).length >= MAX_TOPICS) { return; }
    var row = buildTopicRow();
    topicsBox.appendChild(row);
    reindexTopics();
    $('input', row).focus();
  });

  /* ==========================================================
     6b. FOTO DE LA CONSIGNA -> título y temas
     ========================================================== */
  var scanningPhoto = false;
  $('#scan-photo').addEventListener('click', async function () {
    var file = $('#f-scan-photo').files[0];
    var status = $('#scan-status');
    if (!file) { status.textContent = 'Selecciona primero una foto.'; return; }
    if (!/\.(png|jpe?g|webp)$/i.test(file.name)) { status.textContent = 'La foto debe ser PNG, JPG o WebP.'; return; }
    if (file.size > 10 * 1024 * 1024) { status.textContent = 'La foto supera 10 MB.'; return; }
    if (scanningPhoto) return;
    scanningPhoto = true;
    this.disabled = true;
    status.textContent = 'Leyendo la consigna…';
    var data = new FormData();
    data.append('photo', file);
    try {
      var response = await fetch(form.dataset.scanUrl, { method: 'POST', body: data });
      if (response.redirected) { throw new Error('Tu sesión venció. Inicia sesión de nuevo.'); }
      var result = await response.json();
      if (!response.ok) { throw new Error(result.error || 'No se pudo leer la foto.'); }
      var titleInput = $('#f-title');
      titleInput.value = result.title;
      titleInput.dispatchEvent(new Event('input', { bubbles: true }));
      $$('.topic-row', topicsBox).forEach(function (row) { row.remove(); });
      result.topics.slice(0, MAX_TOPICS).forEach(function (topic) {
        var row = buildTopicRow();
        $('input', row).value = topic;
        topicsBox.appendChild(row);
      });
      reindexTopics();
      status.textContent = 'Se leyó el título y ' + result.topics.length + (result.topics.length === 1 ? ' tema' : ' temas') +
        '. Revísalos antes de generar.';
    } catch (error) {
      status.textContent = error.message || 'No se pudo leer la foto. Escribe el título a mano.';
    } finally {
      scanningPhoto = false;
      this.disabled = false;
    }
  });

  /* ==========================================================
     7. FECHA (el backend espera dd/mm/yyyy)
     ========================================================== */
  var datePicker = $('#f-date-picker');
  var dateHidden = $('#f-date');

  datePicker.addEventListener('change', function () {
    var v = this.value; // yyyy-mm-dd
    dateHidden.value = v ? v.slice(8, 10) + '/' + v.slice(5, 7) + '/' + v.slice(0, 4) : '';
    renderFoot();
  });

  $('#date-today').addEventListener('click', function () {
    var now = new Date();
    var pad = function (n) { return String(n).padStart(2, '0'); };
    datePicker.value = now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate());
    datePicker.dispatchEvent(new Event('change'));
  });

  /* ==========================================================
     8. ENLACES CAMPO → VISTA PREVIA
     ========================================================== */
  var bindings = [
    ['#f-u', function () { renderHeader(); renderCrest(); }],
    ['#f-area', renderHeader],
    ['#f-carrera', renderHeader],
    ['#f-title', renderTitle],
    ['#f-teacher', renderInfoBlock],
    ['#f-asignatura', renderInfoBlock],
    ['#f-periodo', renderInfoBlock],
    ['#f-academico', renderInfoBlock],
    ['#f-seccion', renderInfoBlock],
    ['#f-city', renderFoot]
  ];

  bindings.forEach(function (pair) {
    var el = $(pair[0]);
    if (!el) { return; }
    el.addEventListener('input', pair[1]);
    el.addEventListener('change', pair[1]);
  });

  /* ==========================================================
     9. AUTOCOMPLETE DE INSTITUCIONES
     ========================================================== */
  var listUrl = form.getAttribute('data-universities');
  var aliasesUrl = form.getAttribute('data-university-aliases');
  var universityAliases = Object.create(null);
  var acInput = $('#f-u');
  var acList = $('#ac-list');
  var acItems = [];       // all university names
  var acActive = -1;      // keyboard-highlighted index
  var acPicked = false;   // suppress reopen after selection

  function normalizeUniversitySearch(text) {
    return text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  }

  function highlightMatch(text, query) {
    var fragment = document.createDocumentFragment();
    var start = normalizeUniversitySearch(text).indexOf(normalizeUniversitySearch(query));
    if (!query || start < 0) {
      fragment.appendChild(document.createTextNode(text));
      return fragment;
    }
    fragment.appendChild(document.createTextNode(text.slice(0, start)));
    var mark = document.createElement('mark');
    mark.textContent = text.slice(start, start + query.length);
    fragment.appendChild(mark);
    fragment.appendChild(document.createTextNode(text.slice(start + query.length)));
    return fragment;
  }

  function renderAc(query) {
    acList.innerHTML = '';
    acActive = -1;
    if (!query || query.length < 2) {
      acList.classList.remove('is-open');
      return;
    }

    var lq = normalizeUniversitySearch(query);
    var matches = acItems.filter(function (name) {
      var normalizedName = normalizeUniversitySearch(name);
      return normalizedName.indexOf(lq) !== -1 ||
        (universityAliases[normalizedName] || []).some(function (alias) {
          return normalizeUniversitySearch(alias).indexOf(lq) !== -1;
        });
    });

    if (matches.length === 0) {
      acList.classList.remove('is-open');
      return;
    }

    // Cap at 12 results to keep it fast
    matches.slice(0, 12).forEach(function (name) {
      var div = document.createElement('div');
      div.className = 'ac-item';
      div.appendChild(highlightMatch(name, query));
      div.addEventListener('mousedown', function (e) {
        e.preventDefault(); // prevent blur before value is set
        acPicked = true;
        acInput.value = name;
        acList.classList.remove('is-open');
        acInput.dispatchEvent(new Event('input', { bubbles: true }));
      });
      acList.appendChild(div);
    });

    acList.classList.add('is-open');
  }

  function acNavigate(dir) {
    var items = $$('.ac-item', acList);
    if (!items.length) { return; }
    acActive = Math.max(-1, Math.min(items.length - 1, acActive + dir));
    items.forEach(function (el, i) { el.classList.toggle('is-active', i === acActive); });
    if (acActive >= 0) { items[acActive].scrollIntoView({ block: 'nearest' }); }
  }

  if (acInput && acList) {
    acInput.addEventListener('input', function () {
      if (acPicked) { acPicked = false; return; }
      renderAc(this.value.trim());
    });

    acInput.addEventListener('keydown', function (e) {
      if (!acList.classList.contains('is-open')) { return; }
      if (e.key === 'ArrowDown') { e.preventDefault(); acNavigate(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); acNavigate(-1); }
      else if (e.key === 'Enter' && acActive >= 0) {
        e.preventDefault();
        var items = $$('.ac-item', acList);
        if (items[acActive]) {
          acPicked = true;
          acInput.value = items[acActive].textContent;
          acList.classList.remove('is-open');
          acInput.dispatchEvent(new Event('input', { bubbles: true }));
        }
      }
      else if (e.key === 'Escape') { acList.classList.remove('is-open'); }
    });

    acInput.addEventListener('blur', function () {
      // Small delay so click on item fires first
      setTimeout(function () { acList.classList.remove('is-open'); }, 150);
    });

    acInput.addEventListener('focus', function () {
      if (this.value.trim().length >= 2) { renderAc(this.value.trim()); }
    });
  }

  if (listUrl) {
    fetch(listUrl, { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.text() : ''; })
      .then(function (txt) {
        if (!txt) { return; }
        acItems = txt.split('\n').map(function (l) { return l.trim(); }).filter(Boolean);
        if (document.activeElement === acInput) { renderAc(acInput.value.trim()); }
      })
      .catch(function () { /* la lista es opcional */ });
  }

  if (aliasesUrl) {
    fetch(aliasesUrl, { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (aliases) {
        Object.keys(aliases).forEach(function (name) {
          var values = aliases[name];
          if (typeof values === 'string') { values = [values]; }
          if (!Array.isArray(values)) { return; }
          universityAliases[normalizeUniversitySearch(name)] = values.filter(function (alias) {
            return typeof alias === 'string' && alias.trim();
          });
        });
        if (document.activeElement === acInput) { renderAc(acInput.value.trim()); }
      })
      .catch(function () { /* Las siglas son opcionales; la búsqueda por nombre sigue funcionando. */ });
  }

  /* ==========================================================
     10. ASISTENTE POR PASOS
     ========================================================== */
  var TOTAL_STEPS = 3;
  var step = 1;

  var btnPrev = $('#btn-prev');
  var btnNext = $('#btn-next');
  var btnSubmit = $('#btn-submit');

  function goTo(n, skipValidation) {
    if (n > step && !skipValidation && !validateStep(step)) { return; }
    step = Math.min(TOTAL_STEPS, Math.max(1, n));

    $$('.panel').forEach(function (p) {
      p.classList.toggle('is-active', p.getAttribute('data-panel') === String(step));
    });
    $$('.wizard__step').forEach(function (w) {
      var i = parseInt(w.getAttribute('data-step'), 10);
      w.classList.toggle('is-active', i === step);
      w.classList.toggle('is-done', i < step);
    });

    $('.form-nav').classList.toggle('is-first', step === 1);
    show(btnPrev, step > 1);
    show(btnNext, step < TOTAL_STEPS);
    show(btnSubmit, step === TOTAL_STEPS);

    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function fieldError(sel, on, msg) {
    var el = $(sel);
    if (!el) { return; }
    el.classList.toggle('is-error', on);
    el.setAttribute('aria-invalid', String(on));
    var box = el.parentNode.querySelector('.err-msg');
    if (box) {
      box.classList.toggle('is-on', on);
      if (on && msg) { box.textContent = msg; }
    }
    if (on) { el.focus(); }
  }

  function validateStep(n) {
    if (n === 1) {
      var u = $('#f-u');
      if (!u.value.trim()) {
        fieldError('#f-u', true, 'Indica el nombre de tu institución.');
        return false;
      }
      fieldError('#f-u', false);
    }
    if (n === 3) {
      var t = $('#f-title');
      if (t.value.trim().length < 5) {
        fieldError('#f-title', true, 'Escribe un título de al menos 5 caracteres.');
        return false;
      }
      fieldError('#f-title', false);
      if (scanningPhoto) { setText($('#scan-status'), 'Espera a que termine la lectura de la foto.'); return false; }
      if (isGlossary()) {
        if (extractingTerms) { setText($('#terms-status'), 'Espera a que termine la lectura del archivo.'); return false; }
        if ($('#glossary-list').checked) {
          var count = termLines().length;
          fieldError('#f-glossary-terms', count < 1 || count > Number(form.dataset.maxTerms), 'Revisa o pega entre 1 y ' + form.dataset.maxTerms + ' términos, uno por línea.');
          if (count < 1 || count > Number(form.dataset.maxTerms)) return false;
        } else {
          var amount = Number($('#f-glossary-count').value);
          var invalid = !Number.isInteger(amount) || amount < 1 || amount > Number(form.dataset.maxTerms);
          fieldError('#f-glossary-count', invalid, 'Elige una cantidad entera entre 1 y ' + form.dataset.maxTerms + '.');
          if (invalid) return false;
        }
      }
    }
    return true;
  }

  ['#f-title', '#f-glossary-count', '#f-glossary-terms'].forEach(function (sel) {
    $(sel).addEventListener('input', function () {
      if (this.classList.contains('is-error')) fieldError(sel, false);
    });
  });

  btnNext.addEventListener('click', function () { goTo(step + 1); });
  btnPrev.addEventListener('click', function () { goTo(step - 1, true); });

  $$('.wizard__step').forEach(function (w) {
    w.addEventListener('click', function () {
      var target = parseInt(w.getAttribute('data-step'), 10);
      goTo(target, target < step);
    });
  });

  /* ==========================================================
     11. VISTA PREVIA EN MÓVIL (hoja deslizable)
     ========================================================== */
  var previewEl = $('#preview');
  var fab = $('#preview-fab');
  if (fab) {
    fab.addEventListener('click', function () { previewEl.classList.add('is-open'); });
  }
  var pvClose = $('#preview-close');
  if (pvClose) {
    pvClose.addEventListener('click', function () { previewEl.classList.remove('is-open'); });
  }

  /* ==========================================================
     12. OVERLAY DE PROGRESO AL GENERAR
     ========================================================== */
  var loader = $('#loader');
  var bar = $('#loader-bar');
  var pct = $('#loader-pct');
  var msg = $('#loader-msg');
  var timer = null;
  var progress = 0;

  var PHASES = [
    { at: 0, step: 1, text: 'Ordenando los datos de tu portada…' },
    { at: 10, step: 2, text: 'Validando que el título sea apto para un trabajo académico…' },
    { at: 26, step: 3, text: 'La IA está redactando el desarrollo del tema…' },
    { at: 58, step: 3, text: 'Ampliando cada tema con detalle y coherencia…' },
    { at: 72, step: 4, text: 'Escribiendo la introducción y la conclusión…' },
    { at: 84, step: 5, text: 'Maquetando el documento en Word y generando el PDF…' },
    { at: 93, step: 5, text: 'Ya casi listo, dando los toques finales…' }
  ];

  var GLOSSARY_PHASES = [
    { at: 0, step: 1, text: 'Preparando la portada del glosario…' },
    { at: 10, step: 2, text: 'Revisando la lista y la cantidad de términos…' },
    { at: 26, step: 3, text: 'Redactando definiciones breves para cada término…' },
    { at: 72, step: 4, text: 'Comprobando la cantidad y el orden alfabético…' },
    { at: 84, step: 5, text: 'Armando el glosario en Word y PDF…' }
  ];

  function paintPhase() {
    var phases = isGlossary() ? GLOSSARY_PHASES : PHASES;
    var current = phases[0];
    for (var i = 0; i < phases.length; i++) {
      if (progress >= phases[i].at) { current = phases[i]; }
    }
    if (msg.textContent !== current.text) { msg.textContent = current.text; }

    $$('.loader__step').forEach(function (el) {
      var i = parseInt(el.getAttribute('data-lstep'), 10);
      el.classList.toggle('is-active', i === current.step);
      el.classList.toggle('is-done', i < current.step);
      var icon = $('i', el);
      if (icon) {
        icon.className = i < current.step ? 'bx bx-check-circle'
          : (i === current.step ? 'bx bx-loader-alt bx-spin' : 'bx bx-circle');
      }
    });
  }

  function startLoader(validating) {
    if (isGlossary()) {
      ['Preparando la portada', 'Revisando los términos', 'Definiendo los términos',
        'Comprobando el glosario', 'Armando el Word y el PDF'].forEach(function (label, i) {
        var item = $('[data-lstep="' + (i + 1) + '"]');
        item.innerHTML = '<i class="bx bx-circle"></i> ' + label;
      });
    }
    loader.classList.add('is-on');
    document.body.style.overflow = 'hidden';
    progress = 0;
    paintPhase();

    if (validating) {
      progress = 10;
      bar.style.width = '10%';
      pct.textContent = 'Validando';
      paintPhase();
      msg.textContent = 'Validando que el título sea apto para un trabajo académico…';
      return; // El indicador no avanza hasta recibir la validación real.
    }
    progress = 26;
    paintPhase();

    timer = setInterval(function () {
      // Avance asintótico calibrado para una generación de ~1 a 3 minutos:
      // nunca llega al 100%, eso sólo ocurre cuando el servidor responde
      // y el navegador cambia de página.
      progress += (100 - progress) * 0.0083 + 0.06;
      if (progress > 96) { progress = 96; }
      bar.style.width = progress.toFixed(1) + '%';
      pct.textContent = Math.floor(progress) + '%';
      paintPhase();
    }, 500);
  }

  function stopLoader() {
    if (timer) { clearInterval(timer); timer = null; }
    loader.classList.remove('is-on');
    document.body.style.overflow = '';
    btnSubmit.disabled = false;
  }

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    if (!validateStep(1)) { e.preventDefault(); goTo(1, true); return; }
    if (!validateStep(3)) { e.preventDefault(); goTo(3, true); return; }
    if (btnSubmit.disabled) { e.preventDefault(); return; }

    saveDraft();
    btnSubmit.disabled = true;
    var needsValidation = isGlossary() || ($('input[name="global-mode"]:checked') || {}).value !== 'standard';
    if (needsValidation) {
      startLoader(true);
      try {
        var validationData = new FormData();
        validationData.set('title', $('#f-title').value);
        var response = await fetch(form.dataset.titleUrl, {method: 'POST', body: validationData});
        var result = await response.json();
        if (!response.ok || !result.valid) {
          stopLoader();
          goTo(3, true);
          fieldError('#f-title', true, result.error || 'No se pudo validar el título. Inténtalo de nuevo.');
          $('#f-title').focus();
          return;
        }
      } catch (error) {
        stopLoader();
        goTo(3, true);
        fieldError('#f-title', true, 'No se pudo validar el título. Revisa tu conexión e inténtalo de nuevo.');
        return;
      }
    }
    startLoader();
    HTMLFormElement.prototype.submit.call(form);
  });

  // Si el usuario vuelve con el botón "atrás", el overlay no debe quedarse pegado
  window.addEventListener('pageshow', function (e) {
    if (e.persisted) { stopLoader(); }
  });

  /* ==========================================================
     13. ARRANQUE
     ========================================================== */
  applyInstitute();
  applyMode();
  applySections();
  renderStudents();
  reindexTopics();
  renderHeader();
  renderTitle();
  renderInfoBlock();
  renderFoot();

  /* ==========================================================
     14. BORRADOR: si la generación falla y el servidor devuelve al
     formulario con un mensaje de error, se restauran los datos que el
     usuario había escrito (antes se perdía todo y había que empezar de cero).
     ========================================================== */
  var DRAFT_KEY = 'gullieth_draft_v1';

  function saveDraft() {
    try {
      var data = [];
      $$('input, select, textarea', form).forEach(function (el) {
        if (!el.name || el.type === 'file' || el.type === 'submit') { return; }
        data.push([el.name, el.value, el.checked, el.type]);
      });
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify(data));
    } catch (e) { /* sin sessionStorage simplemente no se restaura */ }
  }

  function restoreDraft() {
    var data;
    try { data = JSON.parse(sessionStorage.getItem(DRAFT_KEY) || 'null'); } catch (e) { data = null; }
    if (!data) { return false; }

    var get = function (name) {
      for (var i = 0; i < data.length; i++) { if (data[i][0] === name) { return data[i]; } }
      return null;
    };
    var fire = function (el, type) { el.dispatchEvent(new Event(type, { bubbles: true })); };

    // 1) Estructura que cambia los campos disponibles: institución, estudiantes, temas
    data.forEach(function (d) {
      if (d[0] === 'instituto' && d[2]) {
        $$('input[name="instituto"]').forEach(function (r) { r.checked = (r.value === d[1]); });
      }
    });
    applyInstitute();

    var count = get('gblock-template-canvas-integrantes');
    if (count) { countInput.value = count[1]; }
    renderStudents();

    var topics = data.filter(function (d) { return /^subtitle_\d+$/.test(d[0]); }).length;
    for (var t = 0; t < topics && $$('.topic-row', topicsBox).length < MAX_TOPICS; t++) {
      topicsBox.appendChild(buildTopicRow());
    }
    reindexTopics();

    // 2) Valores
    data.forEach(function (d) {
      var name = d[0], value = d[1], checked = d[2], type = d[3];
      if (name === 'date' || name === 'instituto') { return; }
      $$('[name="' + name + '"]', form).forEach(function (el) {
        if (type === 'radio') {
          if (el.value === value) { el.checked = checked; if (checked) { fire(el, 'change'); } }
        } else if (type === 'checkbox') {
          if (el.value === value) { el.checked = checked; fire(el, 'change'); }
        } else if (el.type === type || el.tagName === 'SELECT' || el.tagName === 'TEXTAREA') {
          el.value = value;
          fire(el, 'input');
          fire(el, 'change');
        }
      });
    });

    var date = get('date');
    if (date && /^\d\d\/\d\d\/\d{4}$/.test(date[1])) {
      datePicker.value = date[1].slice(6) + '-' + date[1].slice(3, 5) + '-' + date[1].slice(0, 2);
      fire(datePicker, 'change');
    }

    applyInstitute();
    applyMode();
    applySections();
    renderStudents();
    renderHeader();
    renderCrest();
    renderTitle();
    renderInfoBlock();
    renderFoot();
    goTo(3, true);
    return true;
  }

  var flashBox = $('.flash-stack');
  if (flashBox && flashBox.getAttribute('data-flash-error') === 'true') {
    restoreDraft();
  }
  // Un borrador solo sirve para el intento que acaba de fallar.
  try { sessionStorage.removeItem(DRAFT_KEY); } catch (e) { /* nada */ }
})();
