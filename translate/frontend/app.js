// PWA-логіка перекладача. Vanilla JS, без фреймворків — легко й швидко.
"use strict";

const API = {
  languages: "api/languages",
  translate: "api/translate",
};

const el = {
  source: document.getElementById("source"),
  target: document.getElementById("target"),
  swap: document.getElementById("swap"),
  input: document.getElementById("input"),
  output: document.getElementById("output"),
  translate: document.getElementById("translate"),
  clear: document.getElementById("clear"),
  copy: document.getElementById("copy"),
  counter: document.getElementById("counter"),
  status: document.getElementById("status"),
  toast: document.getElementById("toast"),
};

const MAX = 5000;
const PREFS_KEY = "translate.prefs";

// --- Утиліти ---

function toast(msg) {
  el.toast.textContent = msg;
  el.toast.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.toast.classList.remove("show"), 2200);
}

function savePrefs() {
  try {
    localStorage.setItem(
      PREFS_KEY,
      JSON.stringify({ source: el.source.value, target: el.target.value })
    );
  } catch (_) {}
}

function loadPrefs() {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY)) || {};
  } catch (_) {
    return {};
  }
}

// --- Ініціалізація мов ---

function option(value, label) {
  const o = document.createElement("option");
  o.value = value;
  o.textContent = label;
  return o;
}

async function initLanguages() {
  let langs;
  try {
    const resp = await fetch(API.languages);
    langs = (await resp.json()).languages;
  } catch (_) {
    // Офлайн-дефолт: достатньо для роботи UI без мережі.
    langs = [
      { code: "uk", name_native: "Українська" },
      { code: "en", name_native: "English" },
      { code: "cs", name_native: "Čeština" },
      { code: "hu", name_native: "Magyar" },
      { code: "ru", name_native: "Русский" },
    ];
  }

  el.source.appendChild(option("auto", "Визначити мову"));
  for (const l of langs) {
    el.source.appendChild(option(l.code, l.name_native));
    el.target.appendChild(option(l.code, l.name_native));
  }

  const prefs = loadPrefs();
  el.source.value = prefs.source || "uk";
  el.target.value = prefs.target || "cs";
}

// --- Дії ---

function updateCounter() {
  el.counter.textContent = `${el.input.value.length} / ${MAX}`;
}

function swapLanguages() {
  if (el.source.value === "auto") {
    toast("Спершу виберіть мову джерела");
    return;
  }
  const s = el.source.value;
  el.source.value = el.target.value;
  el.target.value = s;
  // Текст теж міняємо місцями, якщо є результат.
  if (el.output.textContent.trim()) {
    el.input.value = el.output.textContent;
    el.output.textContent = "";
    el.copy.hidden = true;
    updateCounter();
  }
  savePrefs();
}

let inflight = null;

async function translate() {
  const text = el.input.value.trim();
  if (!text) {
    toast("Введіть текст");
    return;
  }
  if (el.source.value === el.target.value) {
    el.output.textContent = text;
    return;
  }

  if (inflight) inflight.abort();
  inflight = new AbortController();

  el.translate.disabled = true;
  el.status.textContent = "Перекладаю…";

  try {
    const resp = await fetch(API.translate, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        source: el.source.value,
        target: el.target.value,
      }),
      signal: inflight.signal,
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `Помилка ${resp.status}`);
    }

    const data = await resp.json();
    el.output.textContent = data.text;
    el.copy.hidden = !data.text;
    el.status.textContent = data.cached ? "з кешу" : data.engine;
    if (el.source.value === "auto") {
      el.status.textContent += ` · ${data.source}`;
    }
  } catch (e) {
    if (e.name === "AbortError") return;
    el.status.textContent = "";
    toast(e.message || "Збій перекладу");
  } finally {
    el.translate.disabled = false;
    inflight = null;
  }
}

async function copyResult() {
  try {
    await navigator.clipboard.writeText(el.output.textContent);
    toast("Скопійовано");
  } catch (_) {
    toast("Не вдалося скопіювати");
  }
}

// --- Прив'язки ---

el.input.addEventListener("input", updateCounter);
el.translate.addEventListener("click", translate);
el.swap.addEventListener("click", swapLanguages);
el.clear.addEventListener("click", () => {
  el.input.value = "";
  el.output.textContent = "";
  el.copy.hidden = true;
  updateCounter();
  el.input.focus();
});
el.copy.addEventListener("click", copyResult);
el.source.addEventListener("change", savePrefs);
el.target.addEventListener("change", savePrefs);

// Ctrl/Cmd+Enter — швидкий переклад.
el.input.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    e.preventDefault();
    translate();
  }
});

// --- Старт + реєстрація service worker ---

initLanguages().then(updateCounter);

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  });
}
