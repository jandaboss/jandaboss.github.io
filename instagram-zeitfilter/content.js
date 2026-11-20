// Instagram Zeitfilter – Content Script
// Lädt die Beiträge eines Profils über die Instagram-Web-API (mit deiner
// bestehenden Anmeldung) und zeigt sie sortiert / nach Zeitraum gefiltert an.

(() => {
  "use strict";
  if (window.__igZeitfilterLoaded) return;
  window.__igZeitfilterLoaded = true;

  const IG_APP_ID = "936619743392459"; // öffentliche App-ID des Instagram-Webclients
  const PAGE_SIZE = 33;
  const REQUEST_DELAY_MS = 900; // Pause zwischen Anfragen, um Rate-Limits zu vermeiden

  const RESERVED = new Set([
    "", "explore", "reels", "reel", "p", "tv", "direct", "accounts", "stories",
    "about", "legal", "developer", "web", "challenge", "emails", "session",
    "your_activity", "notifications", "lite", "topics", "locations", "tags",
  ]);

  const MONTHS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"];

  // ---------- Zustand ----------
  const state = {
    username: null,
    userId: null,
    posts: [],          // { code, takenAt (Sek.), thumb, type, caption, pinned }
    nextMaxId: null,
    moreAvailable: true,
    loading: false,
    abort: false,
  };

  // ---------- API ----------
  async function igFetch(url) {
    const res = await fetch(url, {
      credentials: "include",
      headers: { "x-ig-app-id": IG_APP_ID, "x-requested-with": "XMLHttpRequest" },
    });
    if (res.status === 429) throw new Error("Instagram hat zu viele Anfragen gemeldet (429). Bitte ein paar Minuten warten.");
    if (res.status === 401 || res.status === 403) throw new Error("Kein Zugriff – bist du bei Instagram angemeldet und darfst das Profil sehen?");
    if (!res.ok) throw new Error(`Instagram-Fehler ${res.status}`);
    return res.json();
  }

  async function resolveUserId(username) {
    const data = await igFetch(
      `https://www.instagram.com/api/v1/users/web_profile_info/?username=${encodeURIComponent(username)}`
    );
    const user = data?.data?.user;
    if (!user) throw new Error(`Profil „${username}“ nicht gefunden.`);
    if (user.is_private && !user.followed_by_viewer) {
      throw new Error("Dieses Profil ist privat und du folgst ihm nicht.");
    }
    return { id: user.id, total: user.edge_owner_to_timeline_media?.count ?? null };
  }

  function pickThumb(item) {
    const media = item.carousel_media?.[0] ?? item;
    const cands = media.image_versions2?.candidates ?? item.image_versions2?.candidates ?? [];
    if (!cands.length) return "";
    // Kleinste Version >= 320px wählen, sonst die kleinste vorhandene
    const sorted = [...cands].sort((a, b) => a.width - b.width);
    return (sorted.find((c) => c.width >= 320) ?? sorted[sorted.length - 1]).url;
  }

  function mapItem(item) {
    let type = "Foto";
    if (item.media_type === 8) type = "Album";
    else if (item.media_type === 2) type = item.product_type === "clips" ? "Reel" : "Video";
    return {
      code: item.code,
      takenAt: item.taken_at,
      thumb: pickThumb(item),
      type,
      caption: item.caption?.text ?? "",
      pinned: Boolean(item.timeline_pinned_user_ids?.length),
    };
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  /**
   * Lädt Seiten nach, bis entweder alles geladen ist oder (falls stopBefore
   * gesetzt) der erste nicht angeheftete Beitrag älter als stopBefore ist.
   * Der Feed kommt von Instagram immer „neueste zuerst“.
   */
  async function loadPosts({ stopBefore = null, onProgress }) {
    state.loading = true;
    state.abort = false;
    try {
      if (!state.userId) {
        const info = await resolveUserId(state.username);
        state.userId = info.id;
        state.total = info.total;
      }
      while (state.moreAvailable && !state.abort) {
        const oldest = oldestNonPinned();
        if (stopBefore != null && oldest != null && oldest < stopBefore) break;

        const qs = new URLSearchParams({ count: String(PAGE_SIZE) });
        if (state.nextMaxId) qs.set("max_id", state.nextMaxId);
        const data = await igFetch(`https://www.instagram.com/api/v1/feed/user/${state.userId}/?${qs}`);

        const known = new Set(state.posts.map((p) => p.code));
        for (const it of data.items ?? []) {
          if (!known.has(it.code)) state.posts.push(mapItem(it));
        }
        state.nextMaxId = data.next_max_id ?? null;
        state.moreAvailable = Boolean(data.more_available && state.nextMaxId);
        onProgress?.();
        if (state.moreAvailable) await sleep(REQUEST_DELAY_MS);
      }
    } finally {
      state.loading = false;
    }
  }

  function oldestNonPinned() {
    let min = null;
    for (const p of state.posts) if (!p.pinned && (min == null || p.takenAt < min)) min = p.takenAt;
    return min;
  }

  // ---------- Hilfsfunktionen ----------
  function usernameFromUrl() {
    const seg = location.pathname.split("/").filter(Boolean)[0] ?? "";
    return RESERVED.has(seg.toLowerCase()) ? "" : seg;
  }

  const fmtDate = (sec) =>
    new Date(sec * 1000).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });

  const toInputDate = (d) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

  // "YYYY-MM-DD" -> Unix-Sekunden (lokale Zeit, Tagesanfang bzw. -ende)
  function parseInputDate(v, endOfDay) {
    if (!v) return null;
    const [y, m, d] = v.split("-").map(Number);
    const date = endOfDay ? new Date(y, m - 1, d, 23, 59, 59) : new Date(y, m - 1, d, 0, 0, 0);
    return Math.floor(date.getTime() / 1000);
  }

  function el(tag, attrs = {}, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") e.className = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) if (c != null) e.append(c);
    return e;
  }

  // ---------- UI ----------
  const host = document.createElement("div");
  host.id = "ig-zeitfilter-host";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>${CSS()}</style>`;

  const fab = el("button", { class: "fab", title: "Zeitfilter öffnen" }, "📅");
  const overlay = el("div", { class: "overlay hidden" });
  root.append(fab, overlay);

  const ui = {};
  overlay.append(
    el("div", { class: "panel" },
      el("header", {},
        el("h2", {}, "📅 Zeitfilter"),
        el("button", { class: "icon", title: "Schließen", onclick: close }, "✕")
      ),
      el("div", { class: "controls" },
        el("label", {}, "Profil",
          (ui.user = el("input", { type: "text", placeholder: "benutzername", spellcheck: "false" }))),
        el("label", {}, "Von",
          (ui.from = el("input", { type: "date" }))),
        el("label", {}, "Bis",
          (ui.to = el("input", { type: "date" }))),
        el("label", {}, "Sortierung",
          (ui.sort = el("select", {},
            el("option", { value: "asc" }, "Älteste zuerst"),
            el("option", { value: "desc" }, "Neueste zuerst")))),
        el("label", {}, "Typ",
          (ui.type = el("select", {},
            el("option", { value: "" }, "Alle"),
            el("option", { value: "Foto" }, "Fotos"),
            el("option", { value: "Album" }, "Alben"),
            el("option", { value: "Video" }, "Videos"),
            el("option", { value: "Reel" }, "Reels")))),
        (ui.go = el("button", { class: "primary", onclick: run }, "Anzeigen")),
        (ui.stop = el("button", { class: "hidden", onclick: () => (state.abort = true) }, "Stopp"))
      ),
      el("div", { class: "presets" },
        preset("Letzte 30 Tage", () => [daysAgo(30), new Date()]),
        preset("Dieses Jahr", () => [new Date(new Date().getFullYear(), 0, 1), new Date()]),
        preset("Letztes Jahr", () => { const y = new Date().getFullYear() - 1; return [new Date(y, 0, 1), new Date(y, 11, 31)]; }),
        preset("Gesamter Zeitraum", () => [null, null]),
        el("span", { class: "hint" }, "Tipp: Nach dem Laden im Kalender unten auf einen Monat oder ein Jahr klicken.")
      ),
      (ui.status = el("div", { class: "status" })),
      (ui.calendar = el("div", { class: "calendar" })),
      el("div", { class: "results" }, (ui.grid = el("div", { class: "grid" })))
    )
  );

  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !overlay.classList.contains("hidden")) close(); });
  fab.addEventListener("click", open);
  ui.sort.addEventListener("change", render);
  ui.type.addEventListener("change", render);
  for (const inp of [ui.user, ui.from, ui.to]) inp.addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });

  function preset(label, fn) {
    return el("button", {
      class: "chip",
      onclick: () => {
        const [a, b] = fn();
        ui.from.value = a ? toInputDate(a) : "";
        ui.to.value = b ? toInputDate(b) : "";
        run();
      },
    }, label);
  }

  function daysAgo(n) { const d = new Date(); d.setDate(d.getDate() - n); return d; }

  function open() {
    const u = usernameFromUrl();
    if (u && u !== ui.user.value) ui.user.value = u;
    overlay.classList.remove("hidden");
    if (!ui.user.value) ui.user.focus();
  }

  function close() {
    overlay.classList.add("hidden");
  }

  function setStatus(text, isError = false) {
    ui.status.textContent = text;
    ui.status.classList.toggle("error", isError);
  }

  function currentFilter() {
    let from = parseInputDate(ui.from.value, false);
    let to = parseInputDate(ui.to.value, true);
    if (from != null && to != null && from > to) [from, to] = [parseInputDate(ui.to.value, false), parseInputDate(ui.from.value, true)];
    return { from, to };
  }

  async function run() {
    if (state.loading) return;
    const username = ui.user.value.trim().replace(/^@/, "");
    if (!username) { setStatus("Bitte einen Benutzernamen eingeben.", true); return; }

    if (username.toLowerCase() !== (state.username ?? "").toLowerCase()) {
      Object.assign(state, { username, userId: null, posts: [], nextMaxId: null, moreAvailable: true, total: null });
    }

    const { from } = currentFilter();
    ui.go.disabled = true;
    ui.stop.classList.remove("hidden");
    try {
      await loadPosts({
        stopBefore: from,
        onProgress: () => {
          const tot = state.total ? ` von ca. ${state.total}` : "";
          const old = oldestNonPinned();
          setStatus(`Lade Beiträge … ${state.posts.length}${tot} geladen` + (old ? ` (zurück bis ${fmtDate(old)})` : ""));
          render();
        },
      });
      render();
    } catch (err) {
      console.error("[Zeitfilter]", err);
      setStatus(err.message || String(err), true);
      render(false);
    } finally {
      ui.go.disabled = false;
      ui.stop.classList.add("hidden");
    }
  }

  function render(updateStatus = true) {
    const { from, to } = currentFilter();
    const type = ui.type.value;
    const asc = ui.sort.value === "asc";

    const list = state.posts
      .filter((p) => (from == null || p.takenAt >= from) && (to == null || p.takenAt <= to) && (!type || p.type === type))
      .sort((a, b) => (asc ? a.takenAt - b.takenAt : b.takenAt - a.takenAt));

    if (updateStatus && !state.loading && state.username) {
      const complete = !state.moreAvailable;
      const range = from || to ? ` im Zeitraum ${from ? fmtDate(from) : "Anfang"} – ${to ? fmtDate(to) : "heute"}` : "";
      setStatus(
        `${list.length} Beiträge${range}` +
          ` · ${state.posts.length} Beiträge von @${state.username} geladen` +
          (complete ? " (vollständig)" : state.abort ? " (abgebrochen – erneut „Anzeigen“ setzt fort)" : "")
      );
    }

    renderCalendar();

    ui.grid.replaceChildren(
      ...list.map((p) =>
        el("a", { class: "card", href: `https://www.instagram.com/p/${p.code}/`, target: "_blank", rel: "noopener", title: p.caption.slice(0, 300) },
          p.thumb ? el("img", { src: p.thumb, loading: "lazy", alt: "", referrerpolicy: "origin" }) : el("div", { class: "noimg" }, p.type),
          el("span", { class: "date" }, fmtDate(p.takenAt)),
          p.type !== "Foto" ? el("span", { class: "badge" }, p.type) : null,
          p.pinned ? el("span", { class: "badge pin" }, "📌") : null
        )
      )
    );
  }

  // Jahres-/Monatsübersicht: jede Zelle = Anzahl Beiträge, Klick setzt den Zeitraum
  function renderCalendar() {
    if (!state.posts.length) { ui.calendar.replaceChildren(); return; }
    const counts = new Map();
    let max = 0;
    for (const p of state.posts) {
      const d = new Date(p.takenAt * 1000);
      const key = `${d.getFullYear()}-${d.getMonth()}`;
      const n = (counts.get(key) ?? 0) + 1;
      counts.set(key, n);
      max = Math.max(max, n);
    }
    const years = [...new Set([...counts.keys()].map((k) => Number(k.split("-")[0])))].sort((a, b) => b - a);
    const { from, to } = currentFilter();

    const rows = years.map((y) =>
      el("div", { class: "row" },
        el("button", {
          class: "year",
          title: `Ganzes Jahr ${y}`,
          onclick: () => { ui.from.value = toInputDate(new Date(y, 0, 1)); ui.to.value = toInputDate(new Date(y, 11, 31)); run(); },
        }, String(y)),
        ...MONTHS.map((name, m) => {
          const n = counts.get(`${y}-${m}`) ?? 0;
          const start = Math.floor(new Date(y, m, 1).getTime() / 1000);
          const end = Math.floor(new Date(y, m + 1, 0, 23, 59, 59).getTime() / 1000);
          const inRange = (from != null || to != null) && (from == null || end >= from) && (to == null || start <= to);
          return el("button", {
            class: "month" + (n ? "" : " empty") + (inRange ? " sel" : ""),
            style: n ? `--a:${0.15 + 0.85 * (n / max)}` : "",
            title: `${name} ${y}: ${n} Beiträge`,
            onclick: () => { ui.from.value = toInputDate(new Date(y, m, 1)); ui.to.value = toInputDate(new Date(y, m + 1, 0)); run(); },
          }, el("small", {}, name), el("b", {}, n ? String(n) : "·"));
        })
      )
    );
    const note = state.moreAvailable
      ? el("div", { class: "hint" }, "Ältere Beiträge sind noch nicht geladen. „Gesamter Zeitraum“ lädt alles.")
      : null;
    ui.calendar.replaceChildren(...rows);
    if (note) ui.calendar.append(note);
  }

  function CSS() {
    return `
      :host { all: initial; }
      * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
      .hidden { display: none !important; }
      .fab {
        position: fixed; right: 20px; bottom: 84px; z-index: 2147483646;
        width: 52px; height: 52px; border-radius: 50%; border: 0; cursor: pointer; font-size: 24px;
        background: linear-gradient(45deg, #f09433, #dc2743 50%, #bc1888); color: #fff;
        box-shadow: 0 4px 14px rgba(0,0,0,.3);
      }
      .fab:hover { transform: scale(1.06); }
      .overlay {
        position: fixed; inset: 0; z-index: 2147483647; background: rgba(0,0,0,.6);
        display: flex; align-items: center; justify-content: center; padding: 16px;
      }
      .panel {
        --bg: #fff; --fg: #111; --muted: #737373; --line: #dbdbdb; --field: #fafafa; --accent: #0095f6;
        background: var(--bg); color: var(--fg); width: min(1100px, 100%); height: min(92vh, 100%);
        border-radius: 14px; display: flex; flex-direction: column; overflow: hidden;
      }
      @media (prefers-color-scheme: dark) {
        .panel { --bg: #121212; --fg: #f5f5f5; --muted: #a8a8a8; --line: #363636; --field: #1e1e1e; }
      }
      header { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-bottom: 1px solid var(--line); }
      h2 { margin: 0; font-size: 18px; }
      button { font: inherit; font-size: 14px; color: var(--fg); background: var(--field); border: 1px solid var(--line); border-radius: 8px; padding: 7px 12px; cursor: pointer; }
      button:disabled { opacity: .5; cursor: default; }
      button.primary { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
      button.icon { border: 0; background: none; font-size: 18px; }
      .controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; padding: 12px 16px 6px; }
      label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
      input, select { font: inherit; font-size: 14px; color: var(--fg); background: var(--field); border: 1px solid var(--line); border-radius: 8px; padding: 6px 8px; min-width: 130px; color-scheme: light dark; }
      .presets { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; padding: 4px 16px 8px; }
      .chip { border-radius: 999px; padding: 4px 10px; font-size: 13px; }
      .hint { font-size: 12px; color: var(--muted); margin-left: 4px; }
      .status { padding: 6px 16px; font-size: 13px; color: var(--muted); min-height: 28px; flex-shrink: 0; }
      .status.error { color: #ed4956; }
      .calendar { padding: 0 16px 8px; display: flex; flex-direction: column; gap: 3px; max-height: 30%; overflow: auto; flex-shrink: 0; }
      .row { display: grid; grid-template-columns: 60px repeat(12, minmax(0, 1fr)); gap: 3px; }
      .year { padding: 2px; font-size: 12px; font-weight: 600; }
      .month { padding: 2px 0; display: flex; flex-direction: column; align-items: center; line-height: 1.1;
        background: rgba(220, 39, 67, var(--a, 0)); border-color: transparent; }
      .month small { font-size: 9px; opacity: .75; }
      .month b { font-size: 12px; }
      .month.empty { background: var(--field); opacity: .5; }
      .month.sel { outline: 2px solid var(--accent); outline-offset: -2px; }
      .results { flex: 1; min-height: 0; overflow: auto; padding: 8px 16px 16px; }
      .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 6px; align-content: start; }
      .card { position: relative; display: block; aspect-ratio: 1 / 1; background: var(--field); border-radius: 6px; overflow: hidden; text-decoration: none; color: var(--fg); }
      .card img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block; }
      .card:hover img { filter: brightness(.85); }
      .noimg { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; height: 100%; color: var(--muted); }
      .date { position: absolute; left: 6px; bottom: 6px; background: rgba(0,0,0,.65); color: #fff; font-size: 12px; padding: 2px 6px; border-radius: 4px; }
      .badge { position: absolute; right: 6px; top: 6px; background: rgba(0,0,0,.65); color: #fff; font-size: 11px; padding: 2px 6px; border-radius: 4px; }
      .badge.pin { right: auto; left: 6px; }
      @media (max-width: 600px) {
        .grid { grid-template-columns: repeat(3, 1fr); }
        .results { padding: 8px; }
        .row { grid-template-columns: 44px repeat(12, minmax(0, 1fr)); }
        .month small { display: none; }
      }
    `;
  }

  document.documentElement.append(host);
})();
