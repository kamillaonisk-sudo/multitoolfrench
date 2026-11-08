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
if (store.get("mtf_tab") === "transcribe") showTab("transcribe");

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
  loadSummary(summaryBtn, summaryTranslate, "/api/summary", { text: lastTranslatedText }, null);
});

// ---------- Конспект ----------

async function loadSummary(btn, container, url, body, sourceName) {
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Составляю…";
  container.hidden = false;
  container.className = "summary loading";
  container.textContent = "Составляю конспект… Для длинной записи это может занять минуту.";
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (needsLogin(res.status)) return null;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Не удалось составить конспект.");
    }
    renderSummary(container, data, sourceName);
    container.scrollIntoView({ behavior: "smooth", block: "start" });
    return data;
  } catch (err) {
    container.className = "summary";
    const p = document.createElement("p");
    p.className = "error";
    p.textContent = err.message === "Failed to fetch" ? "Нет связи с сервером." : err.message;
    container.replaceChildren(p);
    return null;
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

function summaryToText(s, sourceName) {
  const lines = [sourceName ? `Конспект: ${sourceName}` : "Конспект", "", s.overview, ""];
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

function renderSummary(container, s, sourceName) {
  container.className = "summary";
  const text = summaryToText(s, sourceName);

  const copy = el("button", "ghost", "Копировать");
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
      copy.textContent = "Скопировано";
    } catch {
      copy.textContent = "Не удалось";
    }
    setTimeout(() => (copy.textContent = "Копировать"), 1500);
  });
  const download = el("button", "ghost", "Скачать TXT");
  download.addEventListener("click", () => {
    const base = (sourceName || "text").replace(/\.[^.]+$/, "") || "text";
    downloadText(text, `${base}_konspekt.txt`);
  });

  const buttons = el("div", "footer-buttons");
  buttons.append(copy, download);
  const head = el("div", "summary-head");
  head.append(el("h2", "", "Конспект"), buttons);

  const parts = [head];
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

function downloadText(text, filename) {
  const blob = new Blob(["\ufeff" + text], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---------- Расшифровка ----------

const fileInput = $("file-input");
const dropzone = $("dropzone");
const uploadView = $("upload-view");
const jobView = $("job-view");
const transcriptEl = $("transcript");
const downloadBtn = $("download");
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
    jobSummaryBtn.disabled = false;
    $("job-note").hidden = true;
    if (job.summary && summaryJob.hidden) renderSummary(summaryJob, job.summary, job.filename);
  } else if (job.status === "error") {
    setProgress(job.progress, "Ошибка");
    $("job-error").textContent = job.error;
    $("job-error").hidden = false;
    $("job-note").hidden = true;
    store.set("mtf_job", null);
    downloadBtn.disabled = !job.paragraphs.length;
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

jobSummaryBtn.addEventListener("click", async () => {
  if (!currentJob) return;
  const data = await loadSummary(
    jobSummaryBtn, summaryJob, `/api/jobs/${currentJob.id}/summary`, null, currentJob.filename,
  );
  if (data) currentJob.summary = data;
});

downloadBtn.addEventListener("click", () => {
  if (!currentJob) return;
  const lines = [`Расшифровка: ${currentJob.filename}`, ""];
  for (const p of currentJob.paragraphs) {
    lines.push(`[${formatTime(p.start)} – ${formatTime(p.end)}]`);
    lines.push(`Оригинал: ${p.original}`);
    lines.push(`Перевод:  ${p.ru || "(нет перевода)"}`);
    lines.push("");
  }
  const base = currentJob.filename.replace(/\.[^.]+$/, "") || "transcript";
  downloadText(lines.join("\r\n"), `${base}_fr-ru.txt`);
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
