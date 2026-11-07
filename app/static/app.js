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

$("clear").addEventListener("click", () => {
  source.value = "";
  result.textContent = "";
  copyBtn.disabled = true;
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

  try {
    const res = await fetch("/api/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, direction }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Не удалось перевести текст.");
    }
    result.textContent = data.translation;
    copyBtn.disabled = false;
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

// ---------- Расшифровка ----------

const fileInput = $("file-input");
const dropzone = $("dropzone");
const uploadView = $("upload-view");
const jobView = $("job-view");
const transcriptEl = $("transcript");
const downloadBtn = $("download");

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
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/transcribe");
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      const pct = e.loaded / e.total;
      setProgress(pct, `Загружаю файл… ${Math.round(pct * 100)}%`);
    }
  };
  xhr.onload = () => {
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
    $("job-note").hidden = true;
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
  const key = paragraphs.map((p) => p.fr.length + ":" + p.ru.length).join(",");
  if (key === lastRenderKey) return;
  lastRenderKey = key;

  const rows = [];
  if (paragraphs.length) {
    const head = document.createElement("div");
    head.className = "row head";
    head.append(cell("", ""), cell("", "Французский"), cell("", "Русский"));
    rows.push(head);
  }
  for (const p of paragraphs) {
    const row = document.createElement("div");
    row.className = "row";
    row.append(
      cell("time", formatTime(p.start)),
      cell("fr", p.fr),
      p.ru ? cell("ru", p.ru) : cell("ru pending", "перевод появится позже…"),
    );
    rows.push(row);
  }
  transcriptEl.replaceChildren(...rows);
}

downloadBtn.addEventListener("click", () => {
  if (!currentJob) return;
  const lines = [`Расшифровка: ${currentJob.filename}`, ""];
  for (const p of currentJob.paragraphs) {
    lines.push(`[${formatTime(p.start)} – ${formatTime(p.end)}]`);
    lines.push(`FR: ${p.fr}`);
    lines.push(`RU: ${p.ru || "(нет перевода)"}`);
    lines.push("");
  }
  const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/plain;charset=utf-8" });
  const base = currentJob.filename.replace(/\.[^.]+$/, "") || "transcript";
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${base}_fr-ru.txt`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});

// Если страницу перезагрузили во время обработки — продолжаем следить за задачей.
const savedJob = store.get("mtf_job");
if (savedJob) {
  showJobView("");
  setProgress(0, "Проверяю статус…");
  poll(savedJob);
}
