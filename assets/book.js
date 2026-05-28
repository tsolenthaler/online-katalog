const DATA_CANDIDATES = ["data/catalog.json", "data/catalog_sample.json"];

const elements = {
  backToSearch: document.querySelector("#backToSearch"),
  title: document.querySelector("#bookTitle"),
  author: document.querySelector("#bookAuthor"),
  cover: document.querySelector("#bookCover"),
  meta: document.querySelector("#bookMeta"),
  sources: document.querySelector("#bookSources"),
  description: document.querySelector("#bookDescription"),
  genres: document.querySelector("#bookGenres"),
  related: document.querySelector("#relatedBooks"),
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

function getRouteParam() {
  const params = new URLSearchParams(window.location.search);
  return (params.get("book") || "").trim();
}

function getReturnToParam() {
  const params = new URLSearchParams(window.location.search);
  return (params.get("returnTo") || "").trim();
}

function sanitizeReturnTo(value) {
  if (!value) {
    return "search.html";
  }
  if (value.startsWith("http://") || value.startsWith("https://") || value.startsWith("//")) {
    return "search.html";
  }
  const allowedPrefixes = [
    "index.html",
    "./index.html",
    "/index.html",
    "search.html",
    "./search.html",
    "/search.html",
  ];
  if (!allowedPrefixes.some((prefix) => value.startsWith(prefix))) {
    return "search.html";
  }
  return value;
}

function findBook(items, routeValue) {
  const exact = items.find((item) => (item.isbn || item.id || "") === routeValue);
  if (exact) {
    return exact;
  }

  const normalized = normalize(routeValue);
  if (!normalized) {
    return null;
  }

  return (
    items.find((item) => normalize(item.isbn || "") === normalized) ||
    items.find((item) => normalize(item.id || "") === normalized) ||
    null
  );
}

function setText(element, value, fallback) {
  element.textContent = value && String(value).trim() ? String(value).trim() : fallback;
}

function addSourceLink(linkMap, key, label, href) {
  if (!href || !label || linkMap.has(key)) {
    return;
  }
  linkMap.set(key, { label, href });
}

function buildSourceLinks(book) {
  const links = new Map();
  const isbn = String(book.isbn || "").trim();
  const isbnSource = String(book.isbn_source || "").trim().toLowerCase();
  const metadataSource = String(book.metadata_source || "").trim().toLowerCase();

  if (isbnSource === "dnb") {
    addSourceLink(
      links,
      "dnb",
      "DNB",
      `https://portal.dnb.de/opac.htm?method=simpleSearch&query=${encodeURIComponent(isbn || book.title || "")}`
    );
  }

  if (isbnSource === "openlibrary" || metadataSource.includes("openlibrary")) {
    addSourceLink(
      links,
      "openlibrary",
      "OpenLibrary",
      isbn ? `https://openlibrary.org/isbn/${encodeURIComponent(isbn)}` : "https://openlibrary.org/"
    );
  }

  if (metadataSource.includes("google_books")) {
    addSourceLink(
      links,
      "google_books",
      "Google Books",
      isbn
        ? `https://books.google.com/books?vid=ISBN${encodeURIComponent(isbn)}`
        : "https://books.google.com/"
    );
  }

  if (!links.size) {
    if (isbn) {
      addSourceLink(
        links,
        "dnb",
        "DNB",
        `https://portal.dnb.de/opac.htm?method=simpleSearch&query=${encodeURIComponent(isbn)}`
      );
      addSourceLink(
        links,
        "openlibrary",
        "OpenLibrary",
        `https://openlibrary.org/isbn/${encodeURIComponent(isbn)}`
      );
    } else {
      addSourceLink(links, "dnb", "DNB", "https://www.dnb.de/");
      addSourceLink(links, "openlibrary", "OpenLibrary", "https://openlibrary.org/");
    }
  }

  return [...links.values()];
}

function renderSourceLinks(book) {
  const links = buildSourceLinks(book);
  if (!links.length) {
    elements.sources.textContent = "";
    return;
  }

  const chunks = links.map(
    (entry) => `<a href="${entry.href}" target="_blank" rel="noopener noreferrer">${entry.label}</a>`
  );
  elements.sources.innerHTML = `Quelle: ${chunks.join(" | ")}`;
}

function renderGenres(genres) {
  elements.genres.innerHTML = "";
  if (!genres.length) {
    return;
  }

  for (const genre of genres) {
    const li = document.createElement("li");
    li.textContent = genre;
    elements.genres.appendChild(li);
  }
}

function makeRelatedItem(item, returnTo) {
  const box = document.createElement("article");
  box.className = "related-item";

  const link = document.createElement("a");
  link.href = `book.html?book=${encodeURIComponent(item.isbn || item.id || "")}&returnTo=${encodeURIComponent(returnTo)}`;
  link.textContent = item.title || "Ohne Titel";

  const info = document.createElement("p");
  info.textContent = item.author || "Autor unbekannt";

  box.append(link, info);
  return box;
}

function renderRelated(items, current, returnTo) {
  const currentKey = current.isbn || current.id || "";
  const currentGenres = Array.isArray(current.genres) ? current.genres : [];

  const related = items
    .filter((item) => (item.isbn || item.id || "") !== currentKey)
    .map((item) => {
      const sameAuthor = item.author && current.author && item.author === current.author;
      const genres = Array.isArray(item.genres) ? item.genres : [];
      const sharedGenres = genres.filter((genre) => currentGenres.includes(genre)).length;
      return {
        item,
        score: (sameAuthor ? 100 : 0) + sharedGenres,
      };
    })
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score || (a.item.title || "").localeCompare(b.item.title || "", "de"))
    .slice(0, 8)
    .map((entry) => entry.item);

  elements.related.innerHTML = "";
  if (!related.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Keine ähnlichen Titel gefunden.";
    elements.related.appendChild(empty);
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const item of related) {
    fragment.appendChild(makeRelatedItem(item, returnTo));
  }
  elements.related.appendChild(fragment);
}

function renderBook(book, allItems, returnTo) {
  document.title = `${book.title || "Buchdetails"} | Bibliothek Stein AR`;
  setText(elements.title, book.title, "Ohne Titel");
  setText(elements.author, book.author, "Autor unbekannt");

  if (book.cover_url) {
    elements.cover.src = book.cover_url;
    elements.cover.alt = `Cover von ${book.title || "Buch"}`;
  } else {
    elements.cover.alt = "Kein Cover verfügbar";
  }

  const meta = [];
  if (book.isbn) {
    meta.push(`ISBN ${book.isbn}`);
  }
  if (book.metadata_source) {
    meta.push(`Quelle: ${book.metadata_source}`);
  }
  setText(elements.meta, meta.join(" | "), "Keine weiteren Metadaten verfügbar.");
  renderSourceLinks(book);

  setText(
    elements.description,
    book.description,
    "Zu diesem Titel ist derzeit keine Beschreibung verfügbar."
  );

  const genres = Array.isArray(book.genres) ? book.genres : [];
  renderGenres(genres);
  renderRelated(allItems, book, returnTo);
}

function renderGlobalError(message) {
  setText(elements.title, "Buch nicht gefunden", "Buch nicht gefunden");
  setText(elements.author, "", "");
  setText(elements.meta, "", "");
  setText(elements.sources, "", "");
  setText(elements.description, message, message);
  elements.genres.innerHTML = "";
  elements.related.innerHTML = "";
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.textContent = message;
  elements.related.appendChild(empty);
}

async function init() {
  const routeValue = getRouteParam();
  const returnTo = sanitizeReturnTo(getReturnToParam());
  elements.backToSearch.href = returnTo;

  if (!routeValue) {
    renderGlobalError("Kein Buchparameter gefunden. Bitte aus der Suche öffnen.");
    return;
  }

  try {
    const items = await loadCatalog();
    const book = findBook(items, routeValue);
    if (!book) {
      renderGlobalError("Der Titel wurde im aktuellen Katalog nicht gefunden.");
      return;
    }
    renderBook(book, items, returnTo);
  } catch (_err) {
    renderGlobalError("Katalog konnte nicht geladen werden. Bitte zuerst data/catalog.json erzeugen.");
  }
}

init();
