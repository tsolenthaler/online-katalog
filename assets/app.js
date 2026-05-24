const DATA_CANDIDATES = ["data/catalog.json", "data/catalog_sample.json"];

const state = {
  items: [],
  filtered: [],
  search: "",
  author: "",
  genre: "",
};

const elements = {
  searchInput: document.querySelector("#searchInput"),
  authorSelect: document.querySelector("#authorSelect"),
  genreSelect: document.querySelector("#genreSelect"),
  shareSearch: document.querySelector("#shareSearch"),
  shareStatus: document.querySelector("#shareStatus"),
  resetFilters: document.querySelector("#resetFilters"),
  totalCount: document.querySelector("#totalCount"),
  resultCount: document.querySelector("#resultCount"),
  coverCount: document.querySelector("#coverCount"),
  results: document.querySelector("#results"),
  activeFilters: document.querySelector("#activeFilters"),
  topAuthors: document.querySelector("#topAuthors"),
  topGenres: document.querySelector("#topGenres"),
  cardTemplate: document.querySelector("#bookCardTemplate"),
};

function normalize(value) {
  return (value || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function syncUrlFromState() {
  const params = new URLSearchParams();
  if (state.search.trim()) {
    params.set("q", state.search.trim());
  }
  if (state.author.trim()) {
    params.set("author", state.author.trim());
  }
  if (state.genre.trim()) {
    params.set("genre", state.genre.trim());
  }

  const query = params.toString();
  const next = `${window.location.pathname}${query ? `?${query}` : ""}`;
  window.history.replaceState({}, "", next);
}

function applyStateFromUrl() {
  const params = new URLSearchParams(window.location.search);
  state.search = (params.get("q") || "").trim();
  state.author = (params.get("author") || "").trim();
  state.genre = (params.get("genre") || "").trim();
}

function buildSearchQueryFromState() {
  const params = new URLSearchParams();
  if (state.search.trim()) {
    params.set("q", state.search.trim());
  }
  if (state.author.trim()) {
    params.set("author", state.author.trim());
  }
  if (state.genre.trim()) {
    params.set("genre", state.genre.trim());
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

function buildShareUrl() {
  return `${window.location.origin}${window.location.pathname}${buildSearchQueryFromState()}`;
}

async function copyTextToClipboard(value) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(value);
    return;
  }

  const textArea = document.createElement("textarea");
  textArea.value = value;
  textArea.setAttribute("readonly", "true");
  textArea.style.position = "absolute";
  textArea.style.left = "-9999px";
  document.body.appendChild(textArea);
  textArea.select();
  const success = document.execCommand("copy");
  document.body.removeChild(textArea);
  if (!success) {
    throw new Error("copy-failed");
  }
}

function showShareStatus(message) {
  elements.shareStatus.textContent = message;
}

async function loadCatalog() {
  for (const path of DATA_CANDIDATES) {
    try {
      const response = await fetch(path, { cache: "no-store" });
      if (!response.ok) {
        continue;
      }
      const payload = await response.json();
      if (payload && Array.isArray(payload.items)) {
        return payload.items;
      }
    } catch (_err) {
      // Try next candidate silently.
    }
  }

  throw new Error("Katalogdaten konnten nicht geladen werden.");
}

function buildOptions(values, selectElement, defaultLabel) {
  const unique = Array.from(new Set(values.filter(Boolean))).sort((a, b) =>
    a.localeCompare(b, "de", { sensitivity: "base" })
  );

  selectElement.innerHTML = "";
  const base = document.createElement("option");
  base.value = "";
  base.textContent = defaultLabel;
  selectElement.appendChild(base);

  for (const value of unique) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    selectElement.appendChild(option);
  }
}

function computeTop(items, selector, limit) {
  const map = new Map();
  for (const item of items) {
    const value = selector(item);
    if (!value) {
      continue;
    }
    map.set(value, (map.get(value) || 0) + 1);
  }

  return [...map.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "de", { sensitivity: "base" }))
    .slice(0, limit);
}

function computeTopGenre(items, limit) {
  const map = new Map();

  for (const item of items) {
    const genres = Array.isArray(item.genres) ? item.genres : [];
    for (const genre of genres) {
      const text = String(genre || "").trim();
      if (!text) {
        continue;
      }
      map.set(text, (map.get(text) || 0) + 1);
    }
  }

  return [...map.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "de", { sensitivity: "base" }))
    .slice(0, limit);
}

function renderRanking(target, entries, emptyLabel) {
  target.innerHTML = "";
  if (!entries.length) {
    const li = document.createElement("li");
    li.innerHTML = `<span>${emptyLabel}</span><strong>0</strong>`;
    target.appendChild(li);
    return;
  }

  for (const [name, count] of entries) {
    const li = document.createElement("li");
    const label = document.createElement("span");
    const value = document.createElement("strong");
    label.textContent = name;
    value.textContent = String(count);
    li.append(label, value);
    target.appendChild(li);
  }
}

function matches(item) {
  const searchTerm = normalize(state.search);
  const authorFilter = state.author;
  const genreFilter = state.genre;

  const haystack = normalize(
    [item.title, item.author, item.isbn, item.search_text, ...(item.genres || [])].join(" ")
  );

  if (searchTerm && !haystack.includes(searchTerm)) {
    return false;
  }

  if (authorFilter && item.author !== authorFilter) {
    return false;
  }

  if (genreFilter) {
    const genres = Array.isArray(item.genres) ? item.genres : [];
    if (!genres.includes(genreFilter)) {
      return false;
    }
  }

  return true;
}

function summarizeFilterState() {
  const chips = [];
  if (state.search) {
    chips.push(`Suche: ${state.search}`);
  }
  if (state.author) {
    chips.push(`Autor: ${state.author}`);
  }
  if (state.genre) {
    chips.push(`Genre: ${state.genre}`);
  }

  elements.activeFilters.textContent = chips.length ? chips.join(" | ") : "Alle Bücher";
}

function makeBookCard(item) {
  const node = elements.cardTemplate.content.firstElementChild.cloneNode(true);
  const cover = node.querySelector(".book-card__cover");
  const title = node.querySelector(".book-card__title");
  const author = node.querySelector(".book-card__author");
  const desc = node.querySelector(".book-card__desc");
  const meta = node.querySelector(".book-card__meta");
  const tags = node.querySelector(".book-card__tags");
  const detailLink = node.querySelector(".book-card__link");

  title.textContent = item.title || "Ohne Titel";
  author.textContent = item.author || "Autor unbekannt";

  const description = (item.description || "").trim();
  desc.textContent = description || "Keine Kurzbeschreibung verfügbar.";

  if (item.cover_url) {
    cover.src = item.cover_url;
    cover.alt = `Cover von ${item.title || "Buch"}`;
  } else {
    cover.alt = "Kein Cover verfügbar";
  }

  const info = [];
  if (item.isbn) {
    info.push(`ISBN ${item.isbn}`);
  }
  if (item.metadata_source) {
    info.push(`Quelle: ${item.metadata_source}`);
  }
  meta.textContent = info.join(" | ");

  const genres = Array.isArray(item.genres) ? item.genres.slice(0, 4) : [];
  for (const genre of genres) {
    const li = document.createElement("li");
    li.textContent = genre;
    tags.appendChild(li);
  }

  const routeValue = item.isbn || item.id || "";
  const searchQuery = buildSearchQueryFromState();
  const returnTo = `index.html${searchQuery}`;
  detailLink.href = `book.html?book=${encodeURIComponent(routeValue)}&returnTo=${encodeURIComponent(returnTo)}`;
  detailLink.setAttribute("aria-label", `Details zu ${item.title || "Buch"} anzeigen`);

  return node;
}

function renderResults() {
  state.filtered = state.items.filter(matches);

  elements.results.innerHTML = "";
  if (!state.filtered.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Keine Treffer gefunden. Bitte Suche oder Filter anpassen.";
    elements.results.appendChild(empty);
  } else {
    const fragment = document.createDocumentFragment();
    for (const item of state.filtered) {
      fragment.appendChild(makeBookCard(item));
    }
    elements.results.appendChild(fragment);
  }

  elements.totalCount.textContent = String(state.items.length);
  elements.resultCount.textContent = String(state.filtered.length);
  elements.coverCount.textContent = String(state.filtered.filter((item) => item.cover_url).length);

  summarizeFilterState();
  renderRanking(elements.topAuthors, computeTop(state.filtered, (item) => item.author, 8), "Keine Autor-Daten");
  renderRanking(elements.topGenres, computeTopGenre(state.filtered, 8), "Keine Genre-Daten");
  syncUrlFromState();
}

function wireEvents() {
  elements.searchInput.addEventListener("input", (event) => {
    state.search = event.target.value;
    renderResults();
  });

  elements.authorSelect.addEventListener("change", (event) => {
    state.author = event.target.value;
    renderResults();
  });

  elements.genreSelect.addEventListener("change", (event) => {
    state.genre = event.target.value;
    renderResults();
  });

  elements.resetFilters.addEventListener("click", () => {
    state.search = "";
    state.author = "";
    state.genre = "";

    elements.searchInput.value = "";
    elements.authorSelect.value = "";
    elements.genreSelect.value = "";

    renderResults();
  });

  elements.shareSearch.addEventListener("click", async () => {
    try {
      const url = buildShareUrl();
      await copyTextToClipboard(url);
      showShareStatus("Such-Link in die Zwischenablage kopiert.");
    } catch (_err) {
      showShareStatus("Kopieren fehlgeschlagen. URL bitte manuell aus der Adressleiste kopieren.");
    }
  });
}

function renderError(message) {
  elements.results.innerHTML = "";
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.textContent = message;
  elements.results.appendChild(empty);
}

async function init() {
  try {
    const items = await loadCatalog();
    state.items = items;

    applyStateFromUrl();

    buildOptions(
      items.map((item) => item.author || ""),
      elements.authorSelect,
      "Alle Autoren"
    );

    buildOptions(
      items.flatMap((item) => (Array.isArray(item.genres) ? item.genres : [])),
      elements.genreSelect,
      "Alle Genres"
    );

    const authorValues = new Set(items.map((item) => item.author || ""));
    if (state.author && !authorValues.has(state.author)) {
      state.author = "";
    }

    const genreValues = new Set(
      items.flatMap((item) => (Array.isArray(item.genres) ? item.genres : []))
    );
    if (state.genre && !genreValues.has(state.genre)) {
      state.genre = "";
    }

    elements.searchInput.value = state.search;
    elements.authorSelect.value = state.author;
    elements.genreSelect.value = state.genre;

    wireEvents();
    renderResults();
  } catch (_err) {
    renderError("Katalog konnte nicht geladen werden. Bitte zuerst data/catalog.json erzeugen.");
  }
}

init();
