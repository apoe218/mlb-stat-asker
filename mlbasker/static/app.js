/* Stat Asker front end: one search box, answers rendered as cards. No dependencies. */
(() => {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const form = $("#form"), input = $("#q"), clearBtn = $("#clear"), sugg = $("#sugg");
  const landing = $("#landing"), results = $("#results"), statusEl = $("#status");

  const ICON = {
    search: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>',
    clock: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16zm.5-13H11v6l5.2 3.2.8-1.3-4.5-2.7V7z"/></svg>',
    info: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>',
  };

  // Example questions, grouped for the landing page and reused for autocomplete.
  const GROUPS = [
    { title: "Players", items: [
      "How many home runs does Aaron Judge have this season?",
      "Judge vs Ohtani",
      "Who leads MLB in ERA?",
      "Paul Skenes strikeouts in April",
    ]},
    { title: "Teams & games", items: [
      "How are the Dodgers doing?",
      "What was the score of the Brewers game last night?",
      "Yankees vs Red Sox",
      "How did the 1955 Brooklyn Dodgers do?",
    ]},
    { title: "History", items: [
      "Last time a game ended on a pickoff at first base",
      "List all unassisted triple plays",
      "Who won the 1956 World Series?",
      "Last walk-off grand slam in the postseason",
      "Highest scoring game ever",
      "Last time a pitcher struck out 20",
    ]},
  ];
  const POOL = GROUPS.flatMap((g) => g.items).concat([
    "Standings", "Top 10 in home runs this season", "Who led the league in home runs in 1961?",
    "How many no-hitters in 2012?", "List all perfect games", "Who hit for the cycle in 2009?",
    "Longest game ever", "Most strikeouts in a single game", "How many walk-offs in 2025?",
    "What is OPS?", "How many World Series have the Yankees won?", "Babe Ruth career home runs",
    "Shohei Ohtani stats", "Last no-hitter by the Dodgers", "How many games did the Yankees win in 2019?",
  ]);

  // ---------- helpers ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`(.+?)`/g, "<code>$1</code>")
    .replace(/\n{2,}/g, "<br><br>").replace(/\n/g, " ");
  const isNum = (v) => /^(?:[-+−–—]|[-+−–]?#?\.?\d[\d.,]*(?:\s?[–-]\s?\d[\d.,]*)?)$/.test(String(v).trim());
  const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  function highlight(text, terms) {
    let out = esc(text);
    const ts = [...new Set((terms || []).filter((t) => t && t.length >= 3))].sort((a, b) => b.length - a.length);
    if (!ts.length) return out;
    const re = new RegExp("(" + ts.map((t) => reEsc(esc(t))).join("|") + ")", "gi");
    return out.replace(re, "<mark>$1</mark>");
  }

  const store = {
    get() { try { return JSON.parse(localStorage.getItem("recent") || "[]"); } catch { return []; } },
    add(q) {
      try {
        const list = [q, ...this.get().filter((x) => x.toLowerCase() !== q.toLowerCase())].slice(0, 8);
        localStorage.setItem("recent", JSON.stringify(list));
      } catch { /* private mode: history just isn't remembered */ }
    },
  };

  // ---------- rendering ----------
  function table(cols, rows, opts = {}) {
    const hi = new Set(opts.hi || []), best = new Set((opts.best || []).map(([r, c]) => r + ":" + c));
    const numeric = cols.map((_, c) => rows.length > 0 && rows.every((r) => r[c] === "" || r[c] == null || isNum(r[c])));
    let h = `<div class="tablewrap"><table class="${opts.cmp ? "cmp" : ""}"><thead><tr>`;
    h += cols.map((c, i) => `<th class="${numeric[i] && i > 0 ? "num" : ""}">${esc(c)}</th>`).join("");
    h += "</tr></thead><tbody>";
    rows.forEach((r, ri) => {
      h += `<tr class="${hi.has(ri) ? "hi" : ""}">`;
      r.forEach((v, ci) => {
        const cls = [numeric[ci] && ci > 0 ? "num" : "", best.has(ri + ":" + ci) ? "best" : "", String(v).length > 44 ? "wrap" : ""]
          .filter(Boolean).join(" ");
        h += `<td class="${cls}">${esc(v)}</td>`;
      });
      h += "</tr>";
    });
    return h + "</tbody></table></div>";
  }

  function items(list, terms) {
    const solo = list.length === 1;
    return '<ol class="list">' + list.map((it) => {
      const pills = (it.meta || []).map((m) => `<span class="pill">${esc(m)}</span>`).join("");
      return `<li class="item ${it.rank ? "" : "norank"} ${solo ? "solo" : ""}">` +
        (it.rank ? `<span class="rank">${it.rank}</span>` : "") +
        `<div class="meta">${solo ? "" : `<span class="when">${esc(it.date)}</span>`}${pills}</div>` +
        `<p class="title">${esc(it.title)}</p>` +
        (it.text ? `<p class="snippet">${highlight(it.text, terms)}</p>` : "") + "</li>";
    }).join("") + "</ol>";
  }

  function render(a) {
    const quiet = ["notice", "error", "help", "needs_index"].includes(a.kind);
    let h = `<article class="card ${quiet ? "notice" : ""}">`;
    if (a.title) h += `<p class="eyebrow">${esc(a.title)}</p>`;

    if (a.headline) {
      h += `<h1 class="headline">${esc(a.headline)}</h1>`;
      if (a.subhead) h += `<p class="subhead">${esc(a.subhead)}</p>`;
      if (a.kind === "define") h += `<p class="para">${md(a.text.split(": ").slice(1).join(": "))}</p>`;
      else if (quiet && a.text) h += `<p class="para">${md(a.text)}</p>`;
    } else {
      h += `<p class="lede">${md(a.text)}</p>`;
    }

    if (a.tiles && a.tiles.length) {
      h += '<div class="tiles">' + a.tiles.map((t) =>
        `<div class="tile ${t.emphasis ? "em" : ""} ${String(t.value).length > 11 ? "text" : ""}"><div class="v">${esc(t.value)}</div><div class="l">${esc(t.label)}</div></div>`).join("") + "</div>";
    }

    if (a.results && a.results.length) {
      h += items(a.results, a.highlights);
    } else if (a.rows && a.rows.length && !(a.tiles.length && a.rows.length === 1 && a.kind !== "compare")) {
      h += table(a.columns, a.rows, { hi: a.highlight_rows, best: a.best_cells, cmp: a.kind === "compare" });
    }

    if (a.notes && a.notes.length) {
      h += '<div class="notes">' + a.notes.map((n) => `<div class="note">${ICON.info}<div>${md(n)}</div></div>`).join("") + "</div>";
    }
    const secs = (a.took_ms / 1000).toFixed(a.took_ms < 100 ? 2 : 1);
    const src = (a.footnote || "").replace(/\s*Data through [^.]*\.\s*/g, " ").trim().replace(/\.$/, "");
    h += `<p class="src">${esc(src)}${src ? " · " : ""}${secs} s</p></article>`;

    if (a.suggestions && a.suggestions.length) {
      const label = a.kind === "help" ? "Try asking" : quiet ? "Try instead" : "Related searches";
      h += `<section class="related"><h2>${label}</h2><div class="rel-grid">` +
        a.suggestions.map((s) => `<button class="rel" data-q="${esc(s)}">${ICON.search}<span>${esc(s)}</span></button>`).join("") +
        "</div></section>";
    }
    results.innerHTML = h;
  }

  function skeleton() {
    results.innerHTML = '<article class="card skeleton" aria-busy="true"><div class="sk" style="height:12px;width:90px"></div>' +
      '<div class="sk" style="height:38px;width:60%"></div><div class="sk" style="height:14px;width:40%"></div>' +
      '<div class="sk" style="height:84px;margin-top:10px"></div></article>';
  }

  // ---------- state ----------
  let token = 0;
  function setMode(mode) {
    document.body.className = "mode-" + mode;
    landing.hidden = mode !== "home";
    results.hidden = mode === "home";
    clearBtn.hidden = !input.value;
  }

  async function search(q, push = true) {
    q = q.trim();
    if (!q) return home(push);
    closeSugg();
    input.value = q;
    setMode("results");
    skeleton();
    if (push) history.pushState({ q }, "", "?q=" + encodeURIComponent(q));
    document.title = q + " – Stat Asker";
    store.add(q);
    const mine = ++token;
    try {
      const r = await fetch("/api/search?q=" + encodeURIComponent(q));
      if (!r.ok) throw new Error(r.status);
      const a = await r.json();
      if (mine === token) { render(a); window.scrollTo(0, 0); input.blur(); }
    } catch {
      if (mine === token) render({ kind: "error", title: "Something went wrong", text: "I couldn't reach the server. Is it still running?", tiles: [], took_ms: 0, notes: [], suggestions: [] });
    }
  }

  function home(push = true) {
    token++;
    input.value = "";
    setMode("home");
    document.title = "Stat Asker";
    if (push && location.search) history.pushState({}, "", "/");
    closeSugg();
  }

  // ---------- autocomplete ----------
  let active = -1, shown = [];
  function closeSugg() { sugg.hidden = true; sugg.innerHTML = ""; input.setAttribute("aria-expanded", "false"); active = -1; shown = []; }

  function openSugg() {
    const v = input.value.trim().toLowerCase();
    const recent = store.get().filter((r) => !v || r.toLowerCase().includes(v)).slice(0, 4).map((t) => ({ t, kind: "recent" }));
    const have = new Set(recent.map((r) => r.t.toLowerCase()));
    const pool = POOL.filter((p) => !have.has(p.toLowerCase()) && (!v || p.toLowerCase().includes(v)))
      .sort((a, b) => (a.toLowerCase().startsWith(v) ? 0 : 1) - (b.toLowerCase().startsWith(v) ? 0 : 1))
      .slice(0, v ? 6 : 0).map((t) => ({ t, kind: "pool" }));
    shown = recent.concat(pool).slice(0, 8);
    if (!shown.length) return closeSugg();
    sugg.innerHTML = shown.map((s, i) => {
      const text = esc(s.t);
      const label = v ? text.replace(new RegExp("(" + reEsc(esc(v)) + ")", "i"), "<mark>$1</mark>") : text;
      return `<li role="option" id="s${i}" data-i="${i}" aria-selected="false">${s.kind === "recent" ? ICON.clock : ICON.search}<span>${label}</span></li>`;
    }).join("");
    sugg.hidden = false;
    input.setAttribute("aria-expanded", "true");
    active = -1;
  }

  function move(d) {
    if (sugg.hidden) return openSugg();
    active = (active + d + shown.length) % shown.length;
    [...sugg.children].forEach((li, i) => li.setAttribute("aria-selected", String(i === active)));
    input.value = shown[active].t;
    clearBtn.hidden = false;
  }

  // ---------- events ----------
  form.addEventListener("submit", (e) => { e.preventDefault(); search(input.value); });
  input.addEventListener("input", () => { clearBtn.hidden = !input.value; openSugg(); });
  input.addEventListener("focus", () => { if (!sugg.children.length) openSugg(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Escape") { if (!sugg.hidden) closeSugg(); else if (input.value) { input.value = ""; clearBtn.hidden = true; } else input.blur(); }
  });
  clearBtn.addEventListener("click", () => { input.value = ""; clearBtn.hidden = true; input.focus(); openSugg(); });
  sugg.addEventListener("mousedown", (e) => {
    const li = e.target.closest("li"); if (!li) return;
    e.preventDefault(); search(shown[+li.dataset.i].t);
  });
  document.addEventListener("click", (e) => { if (!form.contains(e.target)) closeSugg(); });
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-q]"); if (b) search(b.dataset.q);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== input && !e.metaKey && !e.ctrlKey) { e.preventDefault(); input.focus(); input.select(); }
  });
  window.addEventListener("popstate", () => {
    const q = new URLSearchParams(location.search).get("q");
    q ? search(q, false) : home(false);
  });
  document.querySelector(".brand").addEventListener("click", (e) => { e.preventDefault(); home(true); input.focus(); });

  // ---------- landing + footer ----------
  $("#groups").innerHTML = GROUPS.map((g) =>
    `<div class="group"><h2>${esc(g.title)}</h2><div class="chips">` +
    g.items.map((s) => `<button class="chip" data-q="${esc(s)}">${esc(s)}</button>`).join("") + "</div></div>").join("");

  async function status() {
    try {
      const s = await (await fetch("/api/status")).json();
      const i = s.index || {};
      const d = new Date(s.cutoff + "T12:00:00");
      const when = d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
      const hist = i.all_games
        ? `${i.all_games.toLocaleString()} games · plays ${i.games ? i.first_season + "–" + i.last_season : "not built yet"}`
        : "history index not built yet";
      const parts = [`Data through ${when}`, hist].concat(s.syncing ? ["updating…"] : []);
      statusEl.innerHTML = `<span class="dot ${s.syncing ? "busy" : ""}"></span>` +
        parts.map((x) => `<span class="nw">${esc(x)}</span>`).join(" · ");
      if (s.syncing) setTimeout(status, 20000);
    } catch { statusEl.textContent = "Offline"; }
  }

  // ---------- boot ----------
  status();
  const initial = new URLSearchParams(location.search).get("q");
  if (initial) search(initial, false); else { setMode("home"); input.focus(); }
})();
