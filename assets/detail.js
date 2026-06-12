(async function () {
  if (document.body.dataset.page !== 'detail') return;

  const helpers = window.catalogHelpers;
  const detailHost = helpers.qs('#detailView');
  const backLink = helpers.qs('#backToResults');
  const reportMount = helpers.qs('#detailReportMount');
  const id = helpers.params.get('id');
  const returnTo = helpers.params.get('returnTo');
  const REPORT_FIELDS = [
    { value: 'isbn', label: 'ISBN' },
    { value: 'title', label: 'Titel' },
    { value: 'author', label: 'Autor' },
    { value: 'description', label: 'Beschreibung' },
    { value: 'cover_url', label: 'Cover-URL' },
    { value: 'genre', label: 'Genre' },
    { value: 'type', label: 'Typ' },
    { value: 'owner', label: 'Besitzer' },
    { value: 'status', label: 'Status' },
    { value: 'custom', label: 'Anderes Feld' },
  ];

  function escapeCsv(value) {
    const str = String(value ?? '');
    if (str.includes(',') || str.includes('"') || str.includes('\n')) {
      return `"${str.replaceAll('"', '""')}"`;
    }
    return str;
  }

  function createDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  }

  function formatTimestamp(date) {
    const pad = (value) => String(value).padStart(2, '0');
    return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
  }

  function createFieldOptions(selected = 'description') {
    return REPORT_FIELDS.map((field) => `<option value="${field.value}"${field.value === selected ? ' selected' : ''}>${helpers.escapeHtml(field.label)}</option>`).join('');
  }

  function currentFieldValue(item, fieldName) {
    if (!fieldName || fieldName === 'custom') return '';
    if (fieldName === 'genre') return item.genre || (item.genres || []).join(', ');
    return item[fieldName] ?? '';
  }

  function fieldLabel(fieldName, customName) {
    if (fieldName === 'custom') return customName || 'custom';
    return REPORT_FIELDS.find((field) => field.value === fieldName)?.label || fieldName;
  }

  function buildReportPayload(item, form) {
    const formData = new FormData(form);
    const rows = [...form.querySelectorAll('[data-report-row]')];
    const changes = rows
      .map((row) => {
        const field = row.querySelector('[name="field[]"]')?.value || '';
        const customField = row.querySelector('[name="custom_field[]"]')?.value.trim() || '';
        const proposedValue = row.querySelector('[name="proposed_value[]"]')?.value.trim() || '';
        const fieldName = field === 'custom' ? customField : field;
        return {
          field,
          field_name: fieldName,
          label: fieldLabel(field, customField),
          current_value: currentFieldValue(item, fieldName),
          proposed_value: proposedValue,
        };
      })
      .filter((entry) => entry.field_name && entry.proposed_value);

    return {
      report_id: `report-${item.id}-${Date.now()}`,
      created_at: new Date().toISOString(),
      item_id: item.id,
      source_url: window.location.href,
      title: item.title || '',
      author: item.author || '',
      isbn: item.isbn || '',
      reporter_name: (formData.get('reporter_name') || '').toString().trim(),
      reporter_email: (formData.get('reporter_email') || '').toString().trim(),
      note: (formData.get('note') || '').toString().trim(),
      changes,
    };
  }

  function buildReportCsv(payload) {
    const header = [
      'report_id',
      'created_at',
      'item_id',
      'title',
      'author',
      'isbn',
      'reporter_name',
      'reporter_email',
      'field_name',
      'current_value',
      'proposed_value',
      'note',
      'source_url',
    ];
    const body = payload.changes.map((change) => [
      payload.report_id,
      payload.created_at,
      payload.item_id,
      payload.title,
      payload.author,
      payload.isbn,
      payload.reporter_name,
      payload.reporter_email,
      change.field_name,
      change.current_value,
      change.proposed_value,
      payload.note,
      payload.source_url,
    ].map(escapeCsv).join(','));
    return [header.join(','), ...body].join('\n');
  }

  function fileBaseName(item) {
    const slug = `${item.title || 'medium'}-${item.id || 'meldung'}`
      .toLowerCase()
      .normalize('NFKD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 80);
    return `${formatTimestamp(new Date())}-${slug || 'meldung'}`;
  }

  function addReportRow(rowsHost, item, selectedField = 'description') {
    const row = document.createElement('div');
    row.className = 'report-row';
    row.dataset.reportRow = 'true';
    row.innerHTML = `
      <div class="report-row-grid">
        <label>
          Feld
          <select name="field[]">${createFieldOptions(selectedField)}</select>
        </label>
        <label class="report-custom-field is-hidden">
          Eigener Feldname
          <input name="custom_field[]" placeholder="z. B. sprache" />
        </label>
        <label>
          Aktueller Wert
          <textarea name="current_value[]" rows="2" readonly></textarea>
        </label>
        <label>
          Neuer Wert
          <textarea name="proposed_value[]" rows="2" required></textarea>
        </label>
      </div>
      <div class="report-row-actions">
        <button type="button" class="button-secondary" data-remove-row>Feld entfernen</button>
      </div>
    `;

    const fieldSelect = row.querySelector('[name="field[]"]');
    const currentValue = row.querySelector('[name="current_value[]"]');
    const customFieldWrap = row.querySelector('.report-custom-field');

    function syncFieldState() {
      const selected = fieldSelect.value;
      const isCustom = selected === 'custom';
      customFieldWrap.classList.toggle('is-hidden', !isCustom);
      currentValue.value = isCustom ? '' : String(currentFieldValue(item, selected) ?? '');
    }

    fieldSelect.addEventListener('change', syncFieldState);
    row.querySelector('[data-remove-row]')?.addEventListener('click', () => {
      row.remove();
      if (!rowsHost.querySelector('[data-report-row]')) {
        addReportRow(rowsHost, item, 'description');
      }
    });

    syncFieldState();
    rowsHost.appendChild(row);
  }

  function renderReportForm(item) {
    if (!reportMount) return;

    reportMount.innerHTML = `
      <form id="detailReportForm" class="detail-report-form">
        <div class="report-grid compact">
          <label>
            Medium
            <input value="${helpers.escapeHtml(item.title || '')}" readonly />
          </label>
          <label>
            Autor
            <input value="${helpers.escapeHtml(item.author || '')}" readonly />
          </label>
          <label>
            ISBN
            <input value="${helpers.escapeHtml(item.isbn || '-')}" readonly />
          </label>
        </div>

        <div id="reportRows" class="report-rows"></div>

        <div class="report-actions-inline">
          <button type="button" class="button-secondary" id="addReportField">Weiteres Feld melden</button>
        </div>

        <div class="report-grid">
          <label>
            Name (optional)
            <input name="reporter_name" autocomplete="name" />
          </label>
          <label>
            E-Mail (optional)
            <input name="reporter_email" type="email" autocomplete="email" />
          </label>
          <label class="span-2">
            Hinweis
            <textarea name="note" rows="4" placeholder="Zusätzliche Erklärung oder Korrekturhinweis"></textarea>
          </label>
        </div>

        <div class="contrib-actions">
          <button type="submit">Mail vorbereiten</button>
          <button type="button" class="button-secondary" id="downloadReportJson">JSON herunterladen</button>
          <button type="button" class="button-secondary" id="downloadReportCsv">CSV herunterladen</button>
          <p id="detailReportMessage" aria-live="polite"></p>
        </div>

        <p class="report-help">
          Die erzeugte Datei kann per Mail weitergeleitet und anschließend im Repository unter <strong>data/reports</strong> abgelegt werden.
        </p>
      </form>
    `;

    const form = document.getElementById('detailReportForm');
    const rowsHost = document.getElementById('reportRows');
    const message = document.getElementById('detailReportMessage');
    const addFieldButton = document.getElementById('addReportField');
    const downloadJson = document.getElementById('downloadReportJson');
    const downloadCsv = document.getElementById('downloadReportCsv');

    function currentPayload() {
      const payload = buildReportPayload(item, form);
      if (!payload.changes.length) {
        message.textContent = 'Mindestens ein Feld mit neuem Wert ist erforderlich.';
        return null;
      }
      return payload;
    }

    addReportRow(rowsHost, item, 'description');

    addFieldButton?.addEventListener('click', () => {
      addReportRow(rowsHost, item, 'custom');
    });

    downloadJson?.addEventListener('click', () => {
      const payload = currentPayload();
      if (!payload) return;
      createDownload(
        new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' }),
        `${fileBaseName(item)}.json`,
      );
      message.textContent = 'JSON-Datei wurde heruntergeladen.';
    });

    downloadCsv?.addEventListener('click', () => {
      const payload = currentPayload();
      if (!payload) return;
      createDownload(
        new Blob([buildReportCsv(payload)], { type: 'text/csv;charset=utf-8' }),
        `${fileBaseName(item)}.csv`,
      );
      message.textContent = 'CSV-Datei wurde heruntergeladen.';
    });

    form?.addEventListener('submit', (event) => {
      event.preventDefault();
      const payload = currentPayload();
      if (!payload) return;

      const subject = `Katalogmeldung: ${payload.title || payload.item_id}`;
      const body = [
        'Guten Tag',
        '',
        'ich moechte eine Korrektur zu diesem Medium melden.',
        '',
        `Titel: ${payload.title}`,
        `Autor: ${payload.author}`,
        `ISBN: ${payload.isbn || '-'}`,
        `Link: ${payload.source_url}`,
        '',
        'Gemeldete Aenderungen:',
        ...payload.changes.map((change) => `- ${change.field_name}: ${change.proposed_value}`),
        '',
        payload.note ? `Hinweis: ${payload.note}` : '',
        '',
        'Die JSON- oder CSV-Datei kann direkt aus dem Formular heruntergeladen und dieser Mail angehaengt werden.',
      ].filter(Boolean).join('\n');

      window.location.href = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
      message.textContent = 'Mail wurde vorbereitet. Falls noetig, die heruntergeladene Datei manuell anhaengen.';
    });
  }

  if (returnTo) {
    backLink.href = returnTo;
  }

  if (!id) {
    detailHost.innerHTML = '<p>Kein Medium ausgewählt.</p>';
    return;
  }

  try {
    const res = await fetch('data/catalog.json', { cache: 'no-store' });
    if (!res.ok) throw new Error('Katalogdaten konnten nicht geladen werden.');
    const data = await res.json();
    const item = (data.items || []).find((entry) => String(entry.id) === id);

    if (!item) {
      detailHost.innerHTML = '<p>Medium nicht gefunden.</p>';
      if (reportMount) {
        reportMount.innerHTML = '<p>Ohne Medium kann keine Meldung erfasst werden.</p>';
      }
      return;
    }

    const title = item.title || 'Unbekanntes Medium';
    const author = item.author || 'Unbekannt';
    const isbn = (item.isbn || '').trim();
    const cover = item.cover_url || 'assets/placeholder-cover.svg';
    const description = item.description || 'Keine Beschreibung vorhanden.';
    const summary = `${title} von ${author} - ${description.slice(0, 120)}`;
    const googleBooksLink = item.google_books_link || (isbn ? `https://books.google.ch/books?vid=ISBN${encodeURIComponent(isbn)}&hl=de` : '');

    document.title = `${title} - Bibliothek Stein AR`;

    const ogTitle = document.querySelector('meta[property="og:title"]');
    const ogDescription = document.querySelector('meta[property="og:description"]');
    const ogImage = document.querySelector('meta[property="og:image"]');

    if (ogTitle) ogTitle.setAttribute('content', title);
    if (ogDescription) ogDescription.setAttribute('content', summary);
    if (ogImage) ogImage.setAttribute('content', cover);

    const encodedText = encodeURIComponent(`${title} - ${author}\n${window.location.href}`);
    const waLink = `https://wa.me/?text=${encodedText}`;

    detailHost.innerHTML = `
      <article class="detail-grid">
        <div>
          <img src="${helpers.escapeHtml(cover)}" alt="Cover von ${helpers.escapeHtml(title)}" onerror="this.src='assets/placeholder-cover.svg'" />
        </div>
        <div>
          <h1>${helpers.escapeHtml(title)}</h1>
          <p class="lead">${helpers.escapeHtml(author)}</p>
          <p>${helpers.escapeHtml(description)}</p>

          <ul class="meta-list">
            <li><strong>ISBN:</strong> ${helpers.escapeHtml(isbn || '-')}</li>
            <li><strong>Typ:</strong> ${helpers.escapeHtml(item.type || 'Buch')}</li>
            <li><strong>Genre:</strong> ${helpers.escapeHtml(item.genre || '-')}</li>
            <li><strong>Besitzer:</strong> ${helpers.escapeHtml(item.owner || '-')}</li>
            <li><strong>Status:</strong> ${helpers.escapeHtml(item.status || 'OK')}</li>
          </ul>

          <div class="link-row">
            ${item.openlibrary_link ? `<a class="button-secondary" href="${helpers.escapeHtml(item.openlibrary_link)}" target="_blank" rel="noopener">Open Library</a>` : ''}
            ${googleBooksLink ? `<a class="button-secondary" href="${helpers.escapeHtml(googleBooksLink)}" target="_blank" rel="noopener">Google Books</a>` : ''}
            ${item.dnb_link ? `<a class="button-secondary" href="${helpers.escapeHtml(item.dnb_link)}" target="_blank" rel="noopener">DNB</a>` : ''}
          </div>

          <div class="share-row">
            <a class="button-secondary" href="${waLink}" target="_blank" rel="noopener">Auf WhatsApp teilen</a>
            <button id="copyDetailLink" type="button">Link kopieren</button>
          </div>
        </div>
      </article>
    `;

    const copyButton = helpers.qs('#copyDetailLink');
    copyButton?.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(window.location.href);
        copyButton.textContent = 'Link kopiert';
        window.setTimeout(() => (copyButton.textContent = 'Link kopieren'), 1200);
      } catch {
        copyButton.textContent = 'Kopieren nicht möglich';
      }
    });

    renderReportForm(item);
  } catch (error) {
    detailHost.innerHTML = `<p>${helpers.escapeHtml(String(error))}</p>`;
    if (reportMount) {
      reportMount.innerHTML = '<p>Das Meldeformular konnte nicht geladen werden.</p>';
    }
  }
})();
