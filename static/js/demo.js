/* ============================================================
   GULLIETH · Simulador de servidor para la demo estática
   Todo ocurre en el navegador: nada se envía ni se guarda en un servidor.
   ============================================================ */
(function () {
  'use strict';

  var KEY = 'gullieth_demo_doc';
  var page = (location.pathname.split('/').pop() || 'index.html').replace(/\.html$/, '') || 'index';

  /* ---------- utilidades ---------- */
  function $(sel, ctx) { return (ctx || document).querySelector(sel); }
  function $$(sel, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(sel)); }
  function up(s) { return (s || '').trim().toUpperCase(); }

  function toast(text) {
    var el = $('.demo-toast');
    if (!el) {
      el = document.createElement('div');
      el.className = 'demo-toast';
      el.setAttribute('role', 'status');
      document.body.appendChild(el);
    }
    el.textContent = text;
    el.classList.add('is-on');
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.classList.remove('is-on'); }, 3600);
  }

  /* ---------- distintivo "demo" con acceso a todas las pantallas ---------- */
  function addBadge() {
    var box = document.createElement('div');
    box.className = 'demo-badge' + (page === 'form' ? ' demo-badge--raised' : '');
    box.innerHTML =
      '<button type="button" class="demo-badge__btn" aria-expanded="false">Versión demo</button>' +
      '<div class="demo-badge__menu">' +
      '<p>Sin servidor: nada se guarda ni se envía.</p>' +
      '<a href="index.html">Inicio</a><a href="form.html">Crear documento</a>' +
      '<a href="plans.html">Planes</a><a href="login.html">Iniciar sesión</a>' +
      '<a href="register.html">Registro</a><a href="admin.html">Panel admin</a></div>';
    document.body.appendChild(box);
    var btn = $('.demo-badge__btn', box);
    btn.addEventListener('click', function () {
      var open = box.classList.toggle('is-open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    document.addEventListener('click', function (e) {
      if (!box.contains(e.target)) { box.classList.remove('is-open'); }
    });
  }

  /* ---------- FORMULARIO: "generar" = guardar los datos y mostrar la descarga ---------- */
  function initForm() {
    var form = document.getElementById('docForm');
    if (!form) { return; }
    form.addEventListener('submit', function (e) {
      // builder.js ya validó y, si todo está bien, arrancó el overlay de carga.
      if (e.defaultPrevented) { return; }
      e.preventDefault();
      var data = {};
      new FormData(form).forEach(function (value, key) { data[key] = String(value); });
      try { sessionStorage.setItem(KEY, JSON.stringify(data)); } catch (err) { /* sin storage */ }
      setTimeout(function () { location.href = 'download.html'; }, 8500);
    });
  }

  /* ---------- Datos del documento ---------- */
  var MONTHS = ['ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO', 'JULIO', 'AGOSTO',
    'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE'];

  function sampleData() {
    return {
      instituto: 'universidad', u: 'Universidad Central de Venezuela', area: 'Facultad de Ingeniería',
      carrera: 'Ingeniería Informática', teacher: 'Prof. María Rodríguez', asignatura: 'Metodología',
      city: 'Caracas', date: '', title: 'El impacto de la inteligencia artificial en la educación',
      input1: 'Juan García', id1: '30123456', input2: 'Ana Pérez', id2: '28987654',
      'global-mode': 'ia', incluir_introduccion: '1', incluir_conclusion: '1'
    };
  }

  function loadData() {
    try {
      var raw = sessionStorage.getItem(KEY);
      if (raw) { return JSON.parse(raw); }
    } catch (e) { /* sin storage */ }
    return sampleData();
  }

  function longDate(ddmmyyyy) {
    var m = /^(\d\d)\/(\d\d)\/(\d{4})$/.exec(ddmmyyyy || '');
    if (!m) { return ''; }
    return m[1] + ' DE ' + MONTHS[parseInt(m[2], 10) - 1] + ' DE ' + m[3];
  }

  function titleCase(s) {
    var small = ['de', 'del', 'la', 'las', 'los', 'y', 'para', 'sus', 'su', 'al', 'el', 'en'];
    return (s || '').trim().split(/\s+/).map(function (w, i) {
      var lw = w.toLowerCase();
      return (i > 0 && small.indexOf(lw) !== -1) ? lw : lw.charAt(0).toUpperCase() + lw.slice(1);
    }).join(' ');
  }

  function fileStem(d) {
    return (titleCase(d.title || 'Documento').replace(/[\\/:*?"<>|]+/g, '_').slice(0, 80).trim()) || 'Documento';
  }

  function paragraphsOf(text) {
    return (text || '').split(/\r?\n+/).map(function (s) { return s.trim(); }).filter(Boolean);
  }

  /* Texto de demostración (en la versión real lo redacta Gemini) */
  function demoSections(d) {
    var t = (d.title || 'el tema').trim();
    var tl = t.charAt(0).toLowerCase() + t.slice(1);
    var subs = [];
    for (var i = 1; i <= 8; i++) { if ((d['subtitle_' + i] || '').trim()) { subs.push(d['subtitle_' + i].trim()); } }
    if (!subs.length) { subs = ['Antecedentes y contexto', 'Conceptos fundamentales', 'Aplicaciones y perspectivas']; }

    var sections = [];
    if (d.incluir_introduccion) {
      sections.push({
        heading: 'Introducción', level: 1, paragraphs: [
          'Este documento es una muestra generada por la versión de demostración de Gullieth. En la versión completa, una inteligencia artificial investiga y redacta el contenido sobre ' + tl + ', con búsqueda de información actualizada.',
          'A continuación se presenta la estructura que tendría el trabajo: una introducción, un desarrollo dividido en apartados y una conclusión, todo con el formato académico venezolano.'
        ]
      });
    }
    var body = [{
      text: 'Nota: el texto de este apartado es de demostración y no proviene de una investigación real.',
      note: true
    }];
    var out = { heading: titleCase(t), level: 1, paragraphs: [body[0].text], blocks: [] };
    subs.forEach(function (s) {
      out.blocks.push({ subtitle: titleCase(s), paragraphs: [
        'En este apartado se desarrollaría "' + s.toLowerCase() + '" en relación con ' + tl + ', explicando sus ideas principales, los datos más relevantes y los ejemplos que ayuden a comprender el tema con claridad.',
        'La versión completa amplía cada punto con detalle, mantiene un lenguaje formal y conecta las ideas entre sí para que el trabajo se lea como un solo texto coherente.'
      ] });
    });
    sections.push(out);
    if (d.incluir_conclusion) {
      sections.push({
        heading: 'Conclusión', level: 1, paragraphs: [
          'En resumen, ' + tl + ' es un tema que admite un análisis amplio. Este ejemplo muestra cómo Gullieth organiza la información, la separa en apartados y la entrega lista para descargar en Word y PDF.'
        ]
      });
    }
    return sections;
  }

  function manualSections(d) {
    var sections = [];
    var intro = d.incluir_introduccion ? paragraphsOf(d.introduccion) : [];
    var body = paragraphsOf(d.body);
    var concl = d.incluir_conclusion ? paragraphsOf(d.conclusion) : [];
    if (intro.length) { sections.push({ heading: 'Introducción', level: 1, paragraphs: intro }); }
    if (body.length) { sections.push({ heading: titleCase(d.title), level: 1, paragraphs: body, blocks: [] }); }
    if (concl.length) { sections.push({ heading: 'Conclusión', level: 1, paragraphs: concl }); }
    return sections;
  }

  function model(d) {
    var bach = d.instituto === 'bachiller';
    var header = ['REPÚBLICA BOLIVARIANA DE VENEZUELA',
      bach ? 'MINISTERIO DEL PODER POPULAR PARA LA EDUCACIÓN' : 'MINISTERIO DEL PODER POPULAR PARA LA EDUCACIÓN UNIVERSITARIA',
      up(d.u), up(d.area), up(d.carrera)].filter(Boolean);

    var students = [];
    for (var i = 1; i <= 8; i++) {
      var n = up(d['input' + i]);
      if (n) { students.push(n + (d['id' + i] ? ' C.I- ' + d['id' + i].trim() : '')); }
    }

    var left = [];
    if (up(d.teacher)) { left.push(['DOCENTE:', '']); left.push([up(d.teacher), '']); }
    if (up(d.asignatura)) { left.push(['MATERIA:', ' ' + up(d.asignatura)]); }
    var per = up(d.periodo);
    if ((d.academico || '').trim()) { left.push([up(d.academico) + ':', per ? ' ' + per : '']); }
    else if (per) { left.push(['PERIODO:', ' ' + per]); }
    if (up(d.seccion)) { left.push(['SECCION:', ' "' + up(d.seccion) + '"']); }

    var date = longDate(d.date);
    var city = up(d.city);
    var footer = [city, date].filter(Boolean).join(', ');

    var manual = d['global-mode'] === 'standard';
    var sections = manual ? manualSections(d) : demoSections(d);

    return {
      header: header, title: up(d.title), students: students, left: left, footer: footer,
      studentsLabel: students.length > 1 ? 'ALUMNOS:' : (students.length ? 'ALUMNO:' : ''),
      sections: sections, manual: manual
    };
  }

  /* ---------- Logo de la universidad (mismo criterio que builder.js) ---------- */
  function slugify(name) {
    return (name || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
      .replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  }

  function loadLogo(name) {
    var slug = slugify(name);
    if (!slug) { return Promise.resolve(null); }
    var exts = ['png', 'jpg', 'jpeg', 'webp'];
    function attempt(i) {
      if (i >= exts.length) { return Promise.resolve(null); }
      return fetch('static/logos/' + slug + '.' + exts[i]).then(function (r) {
        if (!r.ok) { return attempt(i + 1); }
        return r.blob().then(function (blob) {
          return new Promise(function (resolve) {
            var reader = new FileReader();
            reader.onload = function () {
              var img = new Image();
              img.onload = function () {
                resolve({ dataUrl: reader.result, ext: exts[i], w: img.width, h: img.height, blob: blob });
              };
              img.onerror = function () { resolve(null); };
              img.src = reader.result;
            };
            reader.readAsDataURL(blob);
          });
        });
      }).catch(function () { return attempt(i + 1); });
    }
    return attempt(0);
  }

  function saveBlob(blob, filename) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }

  /* ---------- WORD (.docx real) ---------- */
  function buildDocx(d) {
    var D = window.docx;
    var m = model(d);
    return loadLogo(d.u).then(function (logo) {
      var FONT = 'Arial';
      var kids = [];
      var run = function (text, o) {
        o = o || {};
        return new D.TextRun({ text: text, font: FONT, size: o.size || 24, bold: !!o.bold,
          italics: !!o.italics, underline: o.underline ? {} : undefined, color: '000000' });
      };
      var center = function (text, o) {
        o = o || {};
        return new D.Paragraph({ alignment: D.AlignmentType.CENTER, spacing: o.spacing || { after: 0 },
          children: [run(text, o)] });
      };

      if (logo) {
        var lw = 90;
        kids.push(new D.Paragraph({
          alignment: D.AlignmentType.CENTER,
          children: [new D.ImageRun({
            type: logo.ext === 'jpg' ? 'jpg' : logo.ext,
            data: Uint8Array.from(atob(logo.dataUrl.split(',')[1]), function (c) { return c.charCodeAt(0); }),
            transformation: { width: lw, height: Math.round(lw * logo.h / logo.w) }
          })]
        }));
      }
      m.header.forEach(function (line) { kids.push(center(line)); });

      var titleSize = m.title.length > 200 ? 24 : (m.title.length > 110 ? 28 : 35);
      kids.push(center(m.title || 'SIN TÍTULO', { size: titleSize, spacing: { before: 2600, after: 2600 } }));

      if (m.studentsLabel) {
        kids.push(new D.Paragraph({
          tabStops: [{ type: D.TabStopType.RIGHT, position: 9360 }],
          children: [new D.TextRun({ children: [new D.Tab(), m.studentsLabel], font: FONT, size: 24, underline: {} })]
        }));
      }
      var rows = Math.max(m.left.length, m.students.length);
      for (var i = 0; i < rows; i++) {
        var l = m.left[i], s = m.students[i];
        var kidsRow = [];
        if (l) {
          kidsRow.push(run(l[0], { underline: !!l[1] || /:$/.test(l[0]) }));
          if (l[1]) { kidsRow.push(run(l[1])); }
        }
        if (s) { kidsRow.push(new D.TextRun({ children: [new D.Tab(), s], font: FONT, size: 24 })); }
        kids.push(new D.Paragraph({ tabStops: [{ type: D.TabStopType.RIGHT, position: 9360 }],
          spacing: { after: 60 }, children: kidsRow }));
      }
      if (m.footer) { kids.push(center(m.footer, { spacing: { before: 1200 } })); }

      if (m.sections.length) {
        // Índice (sin números de página: en Word se puede insertar uno automático)
        kids.push(new D.Paragraph({ children: [new D.PageBreak()] }));
        kids.push(center('Índice', { size: 32, bold: true, spacing: { after: 240 } }));
        m.sections.forEach(function (sec) {
          kids.push(new D.Paragraph({ spacing: { after: 80 }, children: [run(sec.heading)] }));
          (sec.blocks || []).forEach(function (b) {
            kids.push(new D.Paragraph({ indent: { left: 420 }, spacing: { after: 60 }, children: [run(b.subtitle)] }));
          });
        });
        kids.push(new D.Paragraph({ children: [new D.PageBreak()] }));

        m.sections.forEach(function (sec, idx) {
          kids.push(new D.Paragraph({
            heading: D.HeadingLevel.HEADING_1, alignment: D.AlignmentType.CENTER,
            spacing: { before: idx ? 360 : 0, after: 360 },
            children: [run(sec.heading, { size: 28, bold: true })]
          }));
          var para = function (text, note) {
            kids.push(new D.Paragraph({
              alignment: D.AlignmentType.JUSTIFIED, spacing: { after: 240, line: 360 },
              children: [run(text, { italics: !!note })]
            }));
          };
          sec.paragraphs.forEach(function (p, pi) { para(p, sec.paragraphs.length && !m.manual && sec.blocks && pi === 0); });
          (sec.blocks || []).forEach(function (b) {
            kids.push(new D.Paragraph({
              heading: D.HeadingLevel.HEADING_2, spacing: { before: 240, after: 120 },
              children: [run(b.subtitle, { bold: true })]
            }));
            b.paragraphs.forEach(function (p) { para(p, false); });
          });
        });
      }

      var doc = new D.Document({
        creator: 'Gullieth (demo)', title: d.title || 'Documento',
        styles: { default: { document: { run: { font: FONT, size: 24 } } } },
        sections: [{ properties: { page: { margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } } }, children: kids }]
      });
      return D.Packer.toBlob(doc);
    });
  }

  /* ---------- PDF (jsPDF) ---------- */
  function buildPdf(d) {
    var J = window.jspdf.jsPDF;
    var m = model(d);
    return loadLogo(d.u).then(function (logo) {
      var pdf = new J({ unit: 'pt', format: 'letter' });
      var W = 612, H = 792, M = 72, CW = W - 2 * M;
      var y = M;

      pdf.setFont('helvetica', 'normal');
      pdf.setFontSize(12);
      pdf.setTextColor(0);

      if (logo) {
        var lw = 70, lh = Math.round(lw * logo.h / logo.w);
        try { pdf.addImage(logo.dataUrl, logo.ext === 'jpg' || logo.ext === 'jpeg' ? 'JPEG' : logo.ext.toUpperCase(), (W - lw) / 2, y, lw, lh); }
        catch (e) { lh = 0; }
        y += lh + 10;
      } else { y += 6; }

      m.header.forEach(function (line) {
        pdf.splitTextToSize(line, CW).forEach(function (l) { pdf.text(l, W / 2, y, { align: 'center' }); y += 15; });
      });

      // Título centrado en la página
      var tSize = m.title.length > 200 ? 12 : (m.title.length > 110 ? 14 : 17.5);
      pdf.setFontSize(tSize);
      var tLines = pdf.splitTextToSize(m.title || 'SIN TÍTULO', CW);
      var tLead = tSize * 1.3;
      var ty = H / 2 - (tLines.length * tLead) / 2 + tSize;
      tLines.forEach(function (l) { pdf.text(l, W / 2, ty, { align: 'center' }); ty += tLead; });
      pdf.setFontSize(12);

      // Bloque de docente / integrantes cerca del pie
      var rows = Math.max(m.left.length, m.students.length) + (m.studentsLabel ? 1 : 0);
      var by = Math.max(ty + 30, H - M - 30 - rows * 16);
      var underlined = function (text, x, yy, align) {
        pdf.text(text, x, yy, { align: align || 'left' });
        var w = pdf.getTextWidth(text);
        var x0 = align === 'right' ? x - w : x;
        pdf.line(x0, yy + 1.5, x0 + w, yy + 1.5);
      };
      if (m.studentsLabel) { underlined(m.studentsLabel, W - M, by, 'right'); by += 16; }
      var count = Math.max(m.left.length, m.students.length);
      for (var i = 0; i < count; i++) {
        var l = m.left[i];
        if (l) {
          if (l[1] || /:$/.test(l[0])) { underlined(l[0], M, by); } else { pdf.text(l[0], M, by); }
          if (l[1]) { pdf.text(l[1].trim(), M + pdf.getTextWidth(l[0]) + 5, by); }
        }
        if (m.students[i]) { pdf.text(m.students[i], W - M, by, { align: 'right' }); }
        by += 16;
      }
      if (m.footer) { pdf.text(m.footer, W / 2, H - M + 6, { align: 'center' }); }

      if (m.sections.length) {
        pdf.addPage();
        y = M;
        pdf.setFont('helvetica', 'bold'); pdf.setFontSize(16);
        pdf.text('Índice', W / 2, y + 10, { align: 'center' });
        pdf.setFont('helvetica', 'normal'); pdf.setFontSize(12);
        y += 46;
        m.sections.forEach(function (sec) {
          pdf.text(sec.heading, M, y); y += 20;
          (sec.blocks || []).forEach(function (b) { pdf.text(b.subtitle, M + 22, y); y += 18; });
        });

        pdf.addPage();
        y = M;
        var LH = 18;
        var need = function (h) { if (y + h > H - M) { pdf.addPage(); y = M; } };
        var paragraph = function (text, italic) {
          pdf.setFont('helvetica', italic ? 'italic' : 'normal'); pdf.setFontSize(12);
          var lines = pdf.splitTextToSize(text, CW);
          lines.forEach(function (ln, idx) {
            need(LH);
            pdf.text(ln, M, y, idx < lines.length - 1 ? { align: 'justify', maxWidth: CW } : {});
            y += LH;
          });
          y += 8;
        };
        m.sections.forEach(function (sec, idx) {
          if (idx) { y += 16; }
          need(60);
          pdf.setFont('helvetica', 'bold'); pdf.setFontSize(14);
          pdf.splitTextToSize(sec.heading, CW).forEach(function (h) { pdf.text(h, W / 2, y, { align: 'center' }); y += 20; });
          y += 10;
          sec.paragraphs.forEach(function (p, pi) { paragraph(p, !m.manual && sec.blocks && pi === 0); });
          (sec.blocks || []).forEach(function (b) {
            need(50);
            pdf.setFont('helvetica', 'bold'); pdf.setFontSize(12);
            y += 6; pdf.text(b.subtitle, M, y); y += 20;
            b.paragraphs.forEach(function (p) { paragraph(p, false); });
          });
        });
      }
      return pdf.output('blob');
    });
  }

  /* ---------- PANTALLA DE DESCARGA ---------- */
  function initDownload() {
    var d = loadData();
    var stem = fileStem(d);
    $$('.file-tag b').forEach(function (b) { b.textContent = stem; });

    $$('a[data-dl]').forEach(function (link) {
      link.addEventListener('click', function (e) {
        e.preventDefault();
        var kind = link.getAttribute('data-dl');
        if (link.classList.contains('is-busy')) { return; }
        var original = link.innerHTML;
        link.classList.add('is-busy');
        link.textContent = 'Preparando…';
        var job = kind === 'pdf' ? buildPdf(d) : buildDocx(d);
        job.then(function (blob) {
          saveBlob(blob, stem + (kind === 'pdf' ? '.pdf' : '.docx'));
        }).catch(function (err) {
          console.error(err);
          toast('No se pudo generar el archivo en el navegador.');
        }).then(function () {
          link.classList.remove('is-busy');
          link.innerHTML = original;
        });
      });
    });
  }

  /* ---------- PLANES: "Ya pagué" simulado ---------- */
  function initPlans() {
    var form = $('[data-pay]');
    if (!form) { return; }
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var ref = $('#f-ref', form);
      if (!ref || ref.value.trim().length < 4) { toast('La referencia debe tener al menos 4 caracteres.'); return; }
      var note = document.createElement('div');
      note.className = 'plans-alert plans-alert--ok';
      note.innerHTML = '<i class="bx bx-check-circle"></i> Demo: en la versión real un administrador revisaría la referencia y activaría tu plan.';
      form.parentNode.replaceChild(note, form);
    });
  }

  /* ---------- LOGIN / REGISTRO simulados ---------- */
  function initAuth() {
    $$('form').forEach(function (form) {
      if (!$('input[type="password"]', form)) { return; }
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        toast('Demo: sesión iniciada. Entrando al formulario…');
        setTimeout(function () { location.href = 'form.html'; }, 900);
      });
    });
  }

  /* ---------- ADMIN: acciones simuladas ---------- */
  function initAdmin() {
    $$('form').forEach(function (form) {
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        toast('Demo: acción simulada, los cambios no se guardan.');
      });
    });
  }

  // Expuesto para pruebas automáticas de la generación de archivos.
  window.GullDemo = { buildDocx: buildDocx, buildPdf: buildPdf, model: model, sampleData: sampleData };

  /* ---------- arranque ---------- */
  addBadge();
  if (page === 'form') { initForm(); }
  if (page === 'download') { initDownload(); }
  if (page === 'plans') { initPlans(); }
  if (page === 'login' || page === 'register') { initAuth(); }
  if (page === 'admin') { initAdmin(); }
})();
