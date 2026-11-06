const $ = (id) => document.getElementById(id);

// Вкладки
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("active", t === tab);
      t.setAttribute("aria-selected", t === tab);
    });
    document.querySelectorAll(".panel").forEach((p) => {
      p.hidden = p.id !== `tab-${tab.dataset.tab}`;
    });
  });
});

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
