const $ = (id) => document.getElementById(id);

// Небольшая обёртка над localStorage: в приватном режиме он может быть недоступен.
const store = {
  get(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  },
  set(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch { /* не страшно */ }
  },
};

// Если сервер просит пароль — переходим на страницу входа.
function needsLogin(status) {
  if (status === 401) {
    location.href = "/login.html";
    return true;
  }
  return false;
}

// Вкладки
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => {
    const active = t.dataset.tab === name;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", active);
  });
  document.querySelectorAll(".panel").forEach((p) => {
    p.hidden = p.id !== `tab-${name}`;
  });
  store.set("mtf_tab", name);
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => showTab(tab.dataset.tab));
});
const savedTab = store.get("mtf_tab");
if (savedTab && document.querySelector(`.tab[data-tab="${savedTab}"]`)) showTab(savedTab);

// Направление перевода
let direction = "auto";
document.querySelectorAll(".dir").forEach((btn) => {
  btn.addEventListener("click", () => {
    direction = btn.dataset.dir;
    document.querySelectorAll(".dir").forEach((b) => b.classList.toggle("active", b === btn));
    $("detected").textContent = "";
  });
});

const source = $("source");
const result = $("result");
const translateBtn = $("translate");
const copyBtn = $("copy");
const errorBox = $("error");

source.addEventListener("input", () => {
  $("counter").textContent = `${source.value.length.toLocaleString("ru-RU")} / 20 000`;
});

source.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    translate();
  }
});

const summaryBtn = $("summary-btn");
const summaryTranslate = $("summary-translate");
let lastTranslatedText = "";

$("clear").addEventListener("click", () => {
  source.value = "";
  result.textContent = "";
  copyBtn.disabled = true;
  summaryBtn.disabled = true;
  summaryTranslate.hidden = true;
  errorBox.hidden = true;
  $("detected").textContent = "";
  source.dispatchEvent(new Event("input"));
  source.focus();
});

copyBtn.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(result.textContent);
    copyBtn.textContent = "Скопировано";
  } catch {
    copyBtn.textContent = "Не удалось";
  }
  setTimeout(() => (copyBtn.textContent = "Копировать"), 1500);
});

translateBtn.addEventListener("click", translate);

const LANG_LABEL = { fr: "французский", ru: "русский" };

async function translate() {
  const text = source.value.trim();
  if (!text || translateBtn.disabled) return;

  errorBox.hidden = true;
  translateBtn.disabled = true;
  translateBtn.textContent = "Перевожу…";
  result.classList.add("loading");
  result.textContent = "Перевожу…";
  copyBtn.disabled = true;
  summaryBtn.disabled = true;
  summaryTranslate.hidden = true;

  try {
    const res = await fetch("/api/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, direction }),
    });
    if (needsLogin(res.status)) return;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Не удалось перевести текст.");
    }
    result.textContent = data.translation;
    copyBtn.disabled = false;
    summaryBtn.disabled = false;
    lastTranslatedText = text;
    if (direction === "auto") {
      $("detected").textContent = `Определено: ${LANG_LABEL[data.source]} → ${LANG_LABEL[data.target]}`;
    }
  } catch (err) {
    result.textContent = "";
    errorBox.textContent = err.message === "Failed to fetch" ? "Нет связи с сервером." : err.message;
    errorBox.hidden = false;
  } finally {
    result.classList.remove("loading");
    translateBtn.disabled = false;
    translateBtn.textContent = "Перевести";
  }
}

summaryBtn.addEventListener("click", () => {
  loadSummary({
    btn: summaryBtn,
    container: summaryTranslate,
    url: "/api/summary",
    body: { text: lastTranslatedText },
    note: {
      id: `text-${Date.now()}`,
      source: "text",
      filename: null,
      text: lastTranslatedText,
      translation: result.textContent,
    },
  });
});

// ---------- Конспект ----------

function errorText(err) {
  return err.message === "Failed to fetch" ? "Нет связи с сервером." : err.message;
}

// Составляет конспект, сохраняет его в библиотеку и показывает.
async function loadSummary({ btn, container, url, body, note }) {
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Составляю…";
  container.hidden = false;
  container.className = "summary loading";
  container.textContent = "Составляю конспект… Для длинной записи это может занять минуту.";
  try {
    const topics = await Library.topics().catch(() => []);
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(body || {}), topics }),
    });
    if (needsLogin(res.status)) return null;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Не удалось составить конспект.");
    }
    const saved = await Library.save({
      ...note,
      title: data.title || note.filename || "Конспект",
      topic: data.topic || "Без темы",
      tags: data.tags || [],
      created: new Date().toISOString(),
      summary: data,
    });
    renderSummary(container, saved, { savedNotice: true });
    container.scrollIntoView({ behavior: "smooth", block: "start" });
    return saved;
  } catch (err) {
    container.className = "summary";
    container.replaceChildren(el("p", "error", errorText(err)));
    return null;
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

function formatDate(iso) {
  try {
    return new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
  } catch {
    return "";
  }
}

function noteMeta(note) {
  const parts = [`Тема: ${note.topic || "Без темы"}`];
  if (note.created) parts.push(formatDate(note.created));
  if (note.filename) parts.push(note.filename);
  return parts.join(" · ");
}

const TRANSLIT = {
  а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i", й: "y",
  к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f",
  х: "kh", ц: "ts", ч: "ch", ш: "sh", щ: "sch", ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya",
  і: "i", ї: "yi", є: "ye", ґ: "g",
};

function fileBase(note) {
  const base = (note.filename ? note.filename.replace(/\.[^.]+$/, "") : note.title) || "text";
  // Латиница и цифры — так имя файла не теряется в браузерах.
  const latin = [...base].map((ch) => {
    const t = TRANSLIT[ch.toLowerCase()];
    if (t === undefined) return ch;
    return ch === ch.toLowerCase() ? t : t.charAt(0).toUpperCase() + t.slice(1);
  }).join("");
  const ascii = latin.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/[^\w.-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60);
  return ascii || "konspekt";
}

function summaryToText(note) {
  const s = note.summary;
  const lines = [note.title || "Конспект", noteMeta(note), "", s.overview, ""];
  for (const sec of s.sections) {
    lines.push((sec.start ? `[${sec.start}] ` : "") + sec.title);
    for (const point of sec.points) lines.push(`  • ${point}`);
    lines.push("");
  }
  if (s.takeaways.length) {
    lines.push("Главные выводы");
    for (const t of s.takeaways) lines.push(`  • ${t}`);
  }
  return lines.join("\r\n");
}

function transcriptToText(title, paragraphs) {
  const lines = [`Расшифровка: ${title}`, ""];
  for (const p of paragraphs) {
    lines.push(`[${formatTime(p.start)} – ${formatTime(p.end)}]`);
    lines.push(`Оригинал: ${p.original}`);
    lines.push(`Перевод:  ${p.ru || "(нет перевода)"}`);
    lines.push("");
  }
  return lines.join("\r\n");
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function list(items) {
  const ul = el("ul");
  for (const item of items) ul.append(el("li", "", item));
  return ul;
}

function button(label, onClick, className = "ghost") {
  const b = el("button", className, label);
  b.addEventListener("click", onClick);
  return b;
}

// Кнопка, которая на время действия показывает «…» и сообщает об ошибке.
function busyButton(label, action) {
  const b = el("button", "ghost", label);
  b.addEventListener("click", async () => {
    b.disabled = true;
    b.textContent = "…";
    try {
      await action();
      b.textContent = label;
    } catch (err) {
      b.textContent = "Ошибка";
      alert(errorText(err));
      setTimeout(() => (b.textContent = label), 1500);
    } finally {
      b.disabled = false;
    }
  });
  return b;
}

function downloadSummaryPdf(note) {
  return downloadPdf("/api/pdf/summary", {
    title: note.title || "Конспект",
    meta: noteMeta(note),
    summary: note.summary,
  }, `${fileBase(note)}_konspekt.pdf`);
}

function downloadTranscriptPdf(title, paragraphs, filename) {
  return downloadPdf("/api/pdf/transcript", {
    title,
    paragraphs: paragraphs.map((p) => ({ start: p.start, original: p.original, ru: p.ru })),
  }, filename);
}

// Рисует конспект. options.savedNotice — показать, что он сохранён в библиотеку.
function renderSummary(container, note, options = {}) {
  container.className = "summary";
  const s = note.summary;

  const copy = button("Копировать", async () => {
    try {
      await navigator.clipboard.writeText(summaryToText(note));
      copy.textContent = "Скопировано";
    } catch {
      copy.textContent = "Не удалось";
    }
    setTimeout(() => (copy.textContent = "Копировать"), 1500);
  });
  const txt = button("TXT", () => downloadText(summaryToText(note), `${fileBase(note)}_konspekt.txt`));
  const pdf = busyButton("PDF", () => downloadSummaryPdf(note));

  const buttons = el("div", "footer-buttons");
  buttons.append(copy, txt, pdf);
  const head = el("div", "summary-head");
  head.append(el("h2", "", note.title || "Конспект"), buttons);

  const parts = [head, el("div", "summary-meta", noteMeta(note))];
  if (note.tags && note.tags.length) {
    const tags = el("div", "tags");
    for (const t of note.tags) tags.append(el("span", "tag", t));
    parts.push(tags);
  }
  if (options.savedNotice) {
    const notice = el("p", "saved-notice", "Сохранено во вкладке «Конспекты». ");
    notice.append(button("Открыть", () => {
      showTab("library");
      Library.openNote(note.id);
    }, "link"));
    parts.push(notice);
  }
  if (s.overview) parts.push(el("p", "overview", s.overview));
  for (const sec of s.sections) {
    const h3 = el("h3");
    if (sec.start) h3.append(el("span", "time", sec.start));
    h3.append(sec.title);
    parts.push(h3, list(sec.points));
  }
  if (s.takeaways.length) {
    parts.push(el("h3", "", "Главные выводы"), list(s.takeaways));
  }
  container.replaceChildren(...parts);
  container.hidden = false;
}

function downloadBlob(blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function downloadText(text, filename) {
  downloadBlob(new Blob(["﻿" + text], { type: "text/plain;charset=utf-8" }), filename);
}

async function downloadPdf(url, body, filename) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (needsLogin(res.status)) return;
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : "Не удалось сделать PDF.");
  }
  downloadBlob(await res.blob(), filename);
}

// ---------- Расшифровка ----------

const fileInput = $("file-input");
const dropzone = $("dropzone");
const uploadView = $("upload-view");
const jobView = $("job-view");
const transcriptEl = $("transcript");
const downloadBtn = $("download");
const pdfBtn = $("download-pdf");
const jobSummaryBtn = $("job-summary-btn");
const summaryJob = $("summary-job");

let currentJob = null;
let pollTimer = null;
let lastRenderKey = "";

function formatTime(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

function plural(n, one, few, many) {
  const n10 = n % 10, n100 = n % 100;
  if (n10 === 1 && n100 !== 11) return one;
  if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return few;
  return many;
}

function setProgress(fraction, stage) {
  $("progress-bar").style.width = `${Math.round(fraction * 100)}%`;
  $("job-stage").textContent = stage;
}

function showUpload(error) {
  clearTimeout(pollTimer);
  currentJob = null;
  store.set("mtf_job", null);
  uploadView.hidden = false;
  jobView.hidden = true;
  fileInput.value = "";
  $("upload-error").hidden = !error;
  $("upload-error").textContent = error || "";
}

function showJobView(filename) {
  uploadView.hidden = true;
  jobView.hidden = false;
  $("job-file").textContent = filename;
  $("job-error").hidden = true;
  $("job-note").hidden = false;
  transcriptEl.replaceChildren();
  lastRenderKey = "";
  downloadBtn.disabled = true;
  pdfBtn.disabled = true;
  jobSummaryBtn.disabled = true;
  summaryJob.hidden = true;
}

dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("drag");
});
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("drag");
  if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => {
  if (fileInput.files.length) uploadFile(fileInput.files[0]);
});

$("new-file").addEventListener("click", () => showUpload());

function uploadFile(file) {
  showJobView(file.name);
  setProgress(0, "Загружаю файл… 0%");

  const form = new FormData();
  form.append("file", file);
  form.append("language", $("language").value);
  store.set("mtf_lang", $("language").value);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/transcribe");
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      const pct = e.loaded / e.total;
      setProgress(pct, `Загружаю файл… ${Math.round(pct * 100)}%`);
    }
  };
  xhr.onload = () => {
    if (needsLogin(xhr.status)) return;
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch { /* пусто */ }
    if (xhr.status !== 200) {
      showUpload(typeof data.detail === "string" ? data.detail : "Не удалось загрузить файл.");
      return;
    }
    store.set("mtf_job", data.job_id);
    setProgress(0, "В очереди…");
    poll(data.job_id);
  };
  xhr.onerror = () => showUpload("Нет связи с сервером.");
  xhr.send(form);
}

async function poll(jobId) {
  clearTimeout(pollTimer);
  let job;
  try {
    const res = await fetch(`/api/jobs/${jobId}`);
    if (needsLogin(res.status)) return;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showUpload(typeof data.detail === "string" ? data.detail : "Задача не найдена.");
      return;
    }
    job = data;
  } catch {
    $("job-stage").textContent = "Нет связи с сервером, пробую снова…";
    pollTimer = setTimeout(() => poll(jobId), 3000);
    return;
  }

  currentJob = job;
  $("job-file").textContent = job.filename;
  renderTranscript(job.paragraphs);

  if (job.status === "done") {
    setProgress(1, `Готово · ${job.paragraphs.length} ${plural(job.paragraphs.length, "абзац", "абзаца", "абзацев")} · расшифровка: ${job.engine}`);
    downloadBtn.disabled = false;
    pdfBtn.disabled = false;
    jobSummaryBtn.disabled = false;
    $("job-note").hidden = true;
    if (job.summary && summaryJob.hidden) showSavedJobSummary(job);
  } else if (job.status === "error") {
    setProgress(job.progress, "Ошибка");
    $("job-error").textContent = job.error;
    $("job-error").hidden = false;
    $("job-note").hidden = true;
    store.set("mtf_job", null);
    downloadBtn.disabled = !job.paragraphs.length;
    pdfBtn.disabled = !job.paragraphs.length;
  } else {
    setProgress(job.progress, job.stage);
    pollTimer = setTimeout(() => poll(jobId), 1500);
  }
}

function cell(className, text) {
  const el = document.createElement("div");
  el.className = className;
  el.textContent = text;
  return el;
}

function renderTranscript(paragraphs) {
  const key = paragraphs.map((p) => p.original.length + ":" + p.ru.length).join(",");
  if (key === lastRenderKey) return;
  lastRenderKey = key;

  const rows = [];
  if (paragraphs.length) {
    const head = document.createElement("div");
    head.className = "row head";
    head.append(cell("", ""), cell("", "Оригинал"), cell("", "Русский"));
    rows.push(head);
  }
  for (const p of paragraphs) {
    const row = document.createElement("div");
    row.className = "row";
    row.append(
      cell("time", formatTime(p.start)),
      cell("original", p.original),
      p.ru ? cell("ru", p.ru) : cell("ru pending", "перевод появится позже…"),
    );
    rows.push(row);
  }
  transcriptEl.replaceChildren(...rows);
}

function jobNote(job) {
  return {
    id: `job-${job.id}`,
    source: "transcript",
    filename: job.filename,
    paragraphs: job.paragraphs,
  };
}

// После перезагрузки страницы показываем уже готовый конспект (из библиотеки, если он там есть).
async function showSavedJobSummary(job) {
  let note = await Library.get(`job-${job.id}`).catch(() => null);
  if (!note) {
    note = await Library.save({
      ...jobNote(job),
      title: job.summary.title || job.filename,
      topic: job.summary.topic || "Без темы",
      tags: job.summary.tags || [],
      created: new Date().toISOString(),
      summary: job.summary,
    });
  }
  renderSummary(summaryJob, note, { savedNotice: true });
}

jobSummaryBtn.addEventListener("click", async () => {
  if (!currentJob) return;
  const saved = await loadSummary({
    btn: jobSummaryBtn,
    container: summaryJob,
    url: `/api/jobs/${currentJob.id}/summary`,
    body: {},
    note: jobNote(currentJob),
  });
  if (saved) currentJob.summary = saved.summary;
});

function transcriptFileBase(filename) {
  return fileBase({ filename: filename || "transcript" });
}

downloadBtn.addEventListener("click", () => {
  if (!currentJob) return;
  downloadText(
    transcriptToText(currentJob.filename, currentJob.paragraphs),
    `${transcriptFileBase(currentJob.filename)}_perevod.txt`,
  );
});

pdfBtn.addEventListener("click", async () => {
  if (!currentJob) return;
  pdfBtn.disabled = true;
  pdfBtn.textContent = "Делаю PDF…";
  try {
    await downloadTranscriptPdf(
      currentJob.filename, currentJob.paragraphs,
      `${transcriptFileBase(currentJob.filename)}_perevod.pdf`,
    );
  } catch (err) {
    $("job-error").textContent = errorText(err);
    $("job-error").hidden = false;
  } finally {
    pdfBtn.disabled = false;
    pdfBtn.textContent = "Скачать PDF";
  }
});

// Запоминаем выбранный язык записи.
const savedLang = store.get("mtf_lang");
if (savedLang && [...$("language").options].some((o) => o.value === savedLang)) {
  $("language").value = savedLang;
}

// Если страницу перезагрузили во время обработки — продолжаем следить за задачей.
const savedJob = store.get("mtf_job");
if (savedJob) {
  showJobView("");
  setProgress(0, "Проверяю статус…");
  poll(savedJob);
}
