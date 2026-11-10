// Библиотека конспектов. Хранится в браузере (IndexedDB), на сервер не отправляется.
// Запись: { id, title, topic, tags, created, source, filename, summary, paragraphs?, text?, translation? }

const Library = (() => {
  const DB_NAME = "multitoolfrench";
  const STORE = "notes";
  let dbPromise = null;
  const memory = new Map(); // запасной вариант, если IndexedDB недоступна (например, приватный режим)
  let useMemory = false;

  function open() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      if (!("indexedDB" in window)) return reject(new Error("IndexedDB недоступна"));
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "id" });
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    }).catch((err) => {
      useMemory = true;
      console.warn("Библиотека хранится только до закрытия страницы:", err);
      return null;
    });
    // Просим браузер не удалять данные при нехватке места.
    try { navigator.storage?.persist?.(); } catch { /* не страшно */ }
    return dbPromise;
  }

  async function run(mode, action) {
    const db = await open();
    if (useMemory || !db) return action(null);
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const req = action(tx.objectStore(STORE));
      tx.oncomplete = () => resolve(req ? req.result : undefined);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error || new Error("Не удалось сохранить: возможно, закончилось место."));
    });
  }

  async function all() {
    const items = await run("readonly", (s) => (s ? s.getAll() : null));
    return useMemory ? [...memory.values()] : items || [];
  }

  async function get(id) {
    const item = await run("readonly", (s) => (s ? s.get(id) : null));
    return useMemory ? memory.get(id) || null : item || null;
  }

  async function put(note) {
    if (useMemory) memory.set(note.id, note);
    await run("readwrite", (s) => (s ? s.put(note) : null));
    if (useMemory) memory.set(note.id, note);
    changed();
    return note;
  }

  async function remove(id) {
    memory.delete(id);
    await run("readwrite", (s) => (s ? s.delete(id) : null));
    changed();
  }

  // Сохраняет новый конспект. Если такой уже есть — оставляет его (вдруг тему меняли вручную).
  async function save(note) {
    const existing = await get(note.id);
    if (existing) return existing;
    return put(note);
  }

  async function topics() {
    const set = new Set((await all()).map((n) => n.topic).filter(Boolean));
    return [...set].sort((a, b) => a.localeCompare(b, "ru"));
  }

  const listeners = [];
  function changed() {
    for (const fn of listeners) fn();
  }

  return {
    all, get, put, remove, save, topics,
    onChange: (fn) => listeners.push(fn),
    openNote: (id) => LibraryView.open(id),
    get temporary() { return useMemory; },
  };
})();

// ---------- Вкладка «Конспекты» ----------

const LibraryView = (() => {
  const listView = $("lib-list-view");
  const detailView = $("lib-detail-view");
  const listEl = $("lib-list");
  const topicsEl = $("lib-topics");
  const search = $("lib-search");
  const sort = $("lib-sort");
  let activeTopic = store.get("mtf_lib_topic") || "";
  let currentId = null;
  let visibleNotes = [];
  let askAll = null;
  let askOne = null;

  function matches(note, query) {
    if (!query) return true;
    const s = note.summary || {};
    const hay = [
      note.title, note.topic, note.filename, ...(note.tags || []), s.overview,
      ...(s.sections || []).flatMap((sec) => [sec.title, ...(sec.points || [])]),
      ...(s.takeaways || []),
    ].join(" ").toLowerCase();
    return query.toLowerCase().split(/\s+/).every((word) => hay.includes(word));
  }

  function sorted(notes) {
    const mode = sort.value;
    return notes.sort((a, b) => {
      if (mode === "title") return (a.title || "").localeCompare(b.title || "", "ru");
      if (mode === "old") return (a.created || "").localeCompare(b.created || "");
      return (b.created || "").localeCompare(a.created || "");
    });
  }

  function chip(label, count, value) {
    const b = el("button", "chip" + (activeTopic === value ? " active" : ""));
    b.append(label, el("span", "count", String(count)));
    b.addEventListener("click", () => {
      activeTopic = value;
      store.set("mtf_lib_topic", value);
      render();
    });
    return b;
  }

  async function render() {
    const notes = await Library.all();
    const counts = new Map();
    for (const n of notes) counts.set(n.topic || "Без темы", (counts.get(n.topic || "Без темы") || 0) + 1);
    if (activeTopic && !counts.has(activeTopic)) activeTopic = "";

    const chips = [chip("Все", notes.length, "")];
    for (const t of [...counts.keys()].sort((a, b) => a.localeCompare(b, "ru"))) {
      chips.push(chip(t, counts.get(t), t));
    }
    topicsEl.replaceChildren(...chips);
    topicsEl.hidden = notes.length === 0;

    const visible = sorted(notes.filter((n) =>
      (!activeTopic || (n.topic || "Без темы") === activeTopic) && matches(n, search.value.trim())));

    visibleNotes = visible;
    $("ask-all").hidden = notes.length === 0;
    askAll?.updateScope();

    if (!notes.length) {
      listEl.replaceChildren(el("p", "empty",
        "Здесь будут ваши конспекты. Нажмите «Конспект» после перевода или расшифровки — он сохранится сюда автоматически."));
    } else if (!visible.length) {
      listEl.replaceChildren(el("p", "empty", "Ничего не нашлось."));
    } else {
      listEl.replaceChildren(...visible.map(card));
    }
    $("lib-temporary").hidden = !Library.temporary;
  }

  function card(note) {
    const c = el("button", "note-card");
    c.append(el("div", "note-title", note.title || "Без названия"));
    const source = note.source === "transcript" ? `Расшифровка: ${note.filename}` : "Текст";
    c.append(el("div", "note-meta", `${note.topic || "Без темы"} · ${formatDate(note.created)} · ${source}`));
    if (note.summary?.overview) c.append(el("div", "note-overview", note.summary.overview));
    if (note.tags?.length) {
      const tags = el("div", "tags");
      for (const t of note.tags) tags.append(el("span", "tag", t));
      c.append(tags);
    }
    c.addEventListener("click", () => open(note.id));
    return c;
  }

  async function open(id) {
    const note = await Library.get(id);
    if (!note) return;
    currentId = id;
    listView.hidden = true;
    detailView.hidden = false;

    $("lib-topic").value = note.topic || "";
    const topicList = $("lib-topic-list");
    topicList.replaceChildren(...(await Library.topics()).map((t) => {
      const o = document.createElement("option");
      o.value = t;
      return o;
    }));

    const extra = $("lib-extra");
    extra.replaceChildren();
    if (note.paragraphs?.length) {
      extra.append(
        el("span", "hint", "Расшифровка с переводом:"),
        button("TXT", () => downloadText(
          transcriptToText(note.filename, note.paragraphs), `${fileBase(note)}_perevod.txt`)),
        busyButton("PDF", () => downloadTranscriptPdf(
          note.filename, note.paragraphs, `${fileBase(note)}_perevod.pdf`)),
      );
    }
    if (note.text) {
      const details = el("details", "source-text");
      details.append(el("summary", "", "Исходный текст и перевод"));
      details.append(el("p", "", note.text));
      if (note.translation) details.append(el("p", "translation", note.translation));
      extra.append(details);
    }
    extra.hidden = !extra.childElementCount;

    renderSummary($("lib-summary"), note);
    askOne?.reset();
    window.scrollTo({ top: 0 });
  }

  function scopeLabel() {
    const n = visibleNotes.length;
    const count = `${n} ${plural(n, "запись", "записи", "записей")}`;
    if (search.value.trim()) return `Вопрос по найденным: ${count}`;
    if (activeTopic) return `Вопрос по теме «${activeTopic}»: ${count}`;
    return `Вопрос по всем конспектам: ${count}`;
  }

  function back() {
    currentId = null;
    detailView.hidden = true;
    listView.hidden = false;
    render();
  }

  async function update(changes) {
    const note = await Library.get(currentId);
    if (!note) return;
    const updated = { ...note, ...changes };
    await Library.put(updated);
    renderSummary($("lib-summary"), updated);
  }

  $("lib-back").addEventListener("click", back);
  search.addEventListener("input", render);
  sort.addEventListener("change", () => {
    store.set("mtf_lib_sort", sort.value);
    render();
  });
  const savedSort = store.get("mtf_lib_sort");
  if (savedSort) sort.value = savedSort;

  $("lib-topic").addEventListener("change", () => {
    const topic = $("lib-topic").value.trim();
    if (topic) update({ topic });
  });
  $("lib-rename").addEventListener("click", async () => {
    const note = await Library.get(currentId);
    const title = prompt("Новое название конспекта:", note?.title || "");
    if (title && title.trim()) update({ title: title.trim() });
  });
  $("lib-delete").addEventListener("click", async () => {
    const note = await Library.get(currentId);
    if (note && confirm(`Удалить конспект «${note.title}»? Это нельзя отменить.`)) {
      await Library.remove(currentId);
      back();
    }
  });

  // Резервная копия: файл JSON со всеми конспектами.
  $("lib-export").addEventListener("click", async () => {
    const notes = await Library.all();
    const data = JSON.stringify({ app: "multitoolfrench", version: 1, notes }, null, 1);
    const date = new Date().toISOString().slice(0, 10);
    downloadBlob(new Blob([data], { type: "application/json" }), `konspekty_${date}.json`);
  });
  $("lib-import").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      const notes = Array.isArray(data) ? data : data.notes;
      if (!Array.isArray(notes)) throw new Error();
      let added = 0;
      for (const n of notes) {
        if (n && typeof n.id === "string" && n.summary && !(await Library.get(n.id))) {
          await Library.put(n);
          added++;
        }
      }
      alert(`Добавлено конспектов: ${added}.`);
    } catch {
      alert("Не удалось прочитать файл. Нужна резервная копия, скачанная из этого приложения.");
    }
  });

  Library.onChange(() => {
    if (!currentId) render();
  });

  askAll = createAskBox($("ask-all"), {
    heading: "Спросить по конспектам",
    placeholder: "Например: что говорилось о кнелях? Какие даты упоминались?",
    getScope: async () => ({ notes: visibleNotes, label: scopeLabel() }),
  });
  askOne = createAskBox($("ask-one"), {
    heading: "Спросить по этой записи",
    placeholder: "Задайте вопрос по этой записи…",
    getScope: async () => {
      const note = currentId ? await Library.get(currentId) : null;
      return { notes: note ? [note] : [], label: note ? `По записи «${note.title}»` : "" };
    },
  });
  render();

  return { open, render };
})();

// ---------- Вопросы по конспектам ----------

function noteForAsk(note) {
  return {
    id: note.id,
    title: note.title || "",
    topic: note.topic || "",
    created: note.created || "",
    filename: note.filename || null,
    summary: note.summary || {},
    paragraphs: note.paragraphs ? note.paragraphs.map((p) => ({ start: p.start, original: p.original })) : null,
    text: note.text || null,
  };
}

// Блок «вопрос — ответ». getScope() возвращает { notes, label }.
function createAskBox(container, { heading, placeholder, getScope }) {
  let history = [];

  const scopeEl = el("div", "hint ask-scope");
  const thread = el("div", "ask-thread");
  const input = el("textarea", "ask-input");
  input.rows = 2;
  input.placeholder = placeholder;
  input.maxLength = 2000;
  const send = el("button", "primary", "Спросить");
  const clear = button("Очистить", () => {
    history = [];
    thread.replaceChildren();
    clear.hidden = true;
  });
  clear.hidden = true;

  const row = el("div", "ask-row");
  const actions = el("div", "footer-buttons");
  actions.append(clear, send);
  row.append(input, actions);
  container.replaceChildren(el("h3", "", heading), scopeEl, thread, row);
  container.classList.add("ask");

  async function updateScope() {
    const { notes, label } = await getScope();
    scopeEl.textContent = notes.length ? label : "Нет записей для вопросов.";
    send.disabled = !notes.length;
  }

  function sourcesBlock(sources, notesById) {
    const wrap = el("div", "ask-sources");
    wrap.append(el("span", "hint", "Источники: "));
    for (const s of sources) {
      const n = notesById.get(s.note_id);
      if (!n) continue;
      const label = (n.title || "Без названия") + (s.time ? ` · ${s.time}` : "");
      wrap.append(button(label, () => LibraryView.open(n.id), "chip small"));
    }
    return wrap;
  }

  async function ask() {
    const question = input.value.trim();
    if (!question || send.disabled) return;
    const { notes, label } = await getScope();
    if (!notes.length) return;

    const q = el("div", "ask-q");
    q.append(el("div", "", question), el("div", "hint", label));
    const a = el("div", "ask-a loading", "Думаю…");
    thread.append(q, a);
    clear.hidden = false;
    input.value = "";
    send.disabled = true;
    a.scrollIntoView({ behavior: "smooth", block: "nearest" });

    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, notes: notes.map(noteForAsk), history }),
      });
      if (needsLogin(res.status)) return;
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Не удалось получить ответ.");

      a.className = "ask-a";
      a.replaceChildren(el("div", "ask-text", data.answer));
      const notesById = new Map(notes.map((n) => [n.id, n]));
      if (data.sources?.length) a.append(sourcesBlock(data.sources, notesById));
      const withText = notes.filter((n) => n.paragraphs?.length || n.text).length;
      if (data.notes_total === 1 && withText && !data.full_texts) {
        a.append(el("div", "hint ask-limit",
          "Запись очень длинная: ответ дан по конспекту, без полного текста."));
      } else if (data.notes_used < data.notes_total || data.full_texts < Math.min(withText, data.notes_used)) {
        a.append(el("div", "hint ask-limit",
          `Учтено записей: ${data.notes_used} из ${data.notes_total}; полный текст — у ${data.full_texts}, ` +
          "по остальным только конспект. Чтобы ответ был точнее, сузьте выбор темой или поиском."));
      }
      history.push({ question, answer: data.answer });
      history = history.slice(-6);
    } catch (err) {
      a.className = "ask-a";
      a.replaceChildren(el("p", "error", errorText(err)));
    } finally {
      send.disabled = false;
      input.focus();
    }
  }

  send.addEventListener("click", ask);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      ask();
    }
  });

  return {
    updateScope,
    reset() {
      history = [];
      thread.replaceChildren();
      clear.hidden = true;
      updateScope();
    },
  };
}
