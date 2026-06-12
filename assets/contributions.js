(function () {
  if (document.body.dataset.page !== 'contrib') return;

  const form = document.getElementById('contribForm');
  const tbody = document.querySelector('#contribTable tbody');
  const download = document.getElementById('downloadCsv');
  const message = document.getElementById('contribMessage');

  const STORAGE_KEY = 'bsa_contributions';
  const rows = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');

  function escapeCsv(value) {
    const str = String(value ?? '');
    if (str.includes(',') || str.includes('"') || str.includes('\n')) {
      return `"${str.replaceAll('"', '""')}"`;
    }
    return str;
  }

  function render() {
    tbody.innerHTML = rows
      .map(
        (row) => `
        <tr>
          <td>${window.catalogHelpers.escapeHtml(row.title)}</td>
          <td>${window.catalogHelpers.escapeHtml(row.author)}</td>
          <td>${window.catalogHelpers.escapeHtml(row.type)}</td>
          <td>${window.catalogHelpers.escapeHtml(row.owner || '-')}</td>
          <td>${row.trade_interest ? 'Ja' : 'Nein'}</td>
        </tr>
      `,
      )
      .join('');

    message.textContent = `${rows.length} Einträge gespeichert`;
  }

  function save() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(rows));
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const fd = new FormData(form);
    rows.push({
      title: (fd.get('title') || '').toString().trim(),
      author: (fd.get('author') || '').toString().trim(),
      type: (fd.get('type') || '').toString().trim(),
      owner: (fd.get('owner') || '').toString().trim(),
      trade_interest: fd.get('trade_interest') === 'on',
    });
    save();
    render();
    form.reset();
  });

  download.addEventListener('click', () => {
    if (!rows.length) {
      message.textContent = 'Keine Einträge zum Export vorhanden.';
      return;
    }

    const header = ['title', 'author', 'type', 'owner', 'trade_interest'];
    const body = rows.map((r) => [r.title, r.author, r.type, r.owner, r.trade_interest ? 'yes' : 'no'].map(escapeCsv).join(','));
    const csv = [header.join(','), ...body].join('\n');

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'contributions.csv';
    a.click();
    URL.revokeObjectURL(url);

    message.textContent = 'CSV wurde heruntergeladen.';
  });

  render();
})();
