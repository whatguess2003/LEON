(function () {
  var host = window.location.hostname;
  var local = host === "localhost" || host === "127.0.0.1" || host === "::1";
  var api = (local ? "http://localhost:8002" : "https://api.inap.ai") + "/api";
  var storeKey = "leon_onboard_chat";
  var greeting = "Hey. I'll open your account first. What's your WhatsApp number, with the country code?";
  var fresh = new URLSearchParams(window.location.search).get("fresh") === "1";
  var draft = {
    todos: [
      { step: "account", label: "Account", state: "now", detail: "What's your WhatsApp number, with the country code?" },
      { step: "hotel", label: "Hotel", state: "later", detail: "" },
      { step: "rooms", label: "Rooms", state: "later", detail: "" },
      { step: "policies", label: "Policies", state: "later", detail: "" },
      { step: "booking", label: "First booking", state: "later", detail: "" },
    ],
    highlights: [],
    suggestion: "",
  };
  var messages = [];
  var fields = {};
  var saved = [];
  var todos = draft.todos;
  var highlights = [];
  var suggestion = "";
  var busy = false;

  function qs(id) { return document.getElementById(id); }

  function applyGuide(guide) {
    if (!guide) return;
    if (guide.fields) fields = guide.fields;
    if (guide.todos && guide.todos.length) todos = guide.todos;
    if (guide.highlights) highlights = guide.highlights;
    suggestion = guide.suggestion || "";
    saved = todos.filter(function (row) { return row.state === "done"; }).map(function (row) { return row.step; });
  }

  function load() {
    if (fresh) {
      try { sessionStorage.removeItem(storeKey); } catch (error) {}
      return;
    }
    try {
      var raw = sessionStorage.getItem(storeKey);
      var data = raw ? JSON.parse(raw) : null;
      if (data && data.messages && data.messages.length) {
        messages = data.messages;
        fields = data.fields || {};
        saved = data.saved || [];
        todos = data.todos && data.todos.length ? data.todos : draft.todos;
        highlights = data.highlights || [];
        suggestion = data.suggestion || "";
      }
    } catch (error) {}
    if (!messages.length) messages = [{ role: "leon", text: greeting }];
  }

  function persist() {
    try {
      sessionStorage.setItem(storeKey, JSON.stringify({
        messages: messages,
        fields: fields,
        saved: saved,
        todos: todos,
        highlights: highlights,
        suggestion: suggestion,
      }));
    } catch (error) {}
  }

  function progress() {
    var current = todos.filter(function (row) { return row.state === "now"; })[0];
    var open = todos.filter(function (row) { return row.state !== "done"; }).length === 0 && todos.length;
    qs("progress").textContent = current && current.detail ? current.detail : (open ? "The hotel is open" : "What's your WhatsApp number, with the country code?");
  }

  function paintGuide() {
    var list = qs("todos");
    var facts = qs("highlights");
    var chips = qs("suggestion-row");
    list.innerHTML = "";
    facts.innerHTML = "";
    chips.innerHTML = "";
    todos.forEach(function (row) {
      var line = document.createElement("div");
      var tone = row.state === "later" ? "text-gray-400" : row.state === "now" ? "text-gray-900 font-semibold" : "text-green-700";
      line.className = "text-sm mt-2 " + tone;
      line.textContent = row.label + (row.detail ? " — " + row.detail : "");
      list.appendChild(line);
    });
    highlights.forEach(function (row) {
      var chip = document.createElement("span");
      chip.className = "inline-block bg-blue-50 text-blue-800 rounded-full px-2 py-1 mr-1 mb-1 text-xs";
      chip.textContent = row.label + ": " + row.value;
      facts.appendChild(chip);
    });
    if (suggestion) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "bg-blue-50 text-blue-800 rounded-full px-3 py-1 text-sm";
      button.textContent = suggestion;
      button.addEventListener("click", function () { sendText(suggestion); });
      chips.appendChild(button);
    }
    progress();
  }

  function bubble(row) {
    var wrap = document.createElement("div");
    wrap.className = "max-w-3xl mx-auto flex mb-4" + (row.role === "person" ? " justify-end" : "");
    if (row.role !== "person") {
      var mark = document.createElement("div");
      mark.className = "w-7 h-7 rounded-full bg-blue-600 text-white flex items-center justify-center text-xs font-semibold mr-3 flex-shrink-0";
      mark.textContent = "L";
      wrap.appendChild(mark);
    }
    var text = document.createElement("div");
    text.className = "rounded-lg px-3 py-2 text-sm max-w-lg " + (row.role === "person" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-900");
    text.textContent = row.text;
    wrap.appendChild(text);
    return wrap;
  }

  function paint() {
    var box = qs("messages");
    box.innerHTML = "";
    messages.forEach(function (row) { box.appendChild(bubble(row)); });
    if (busy) box.appendChild(bubble({ role: "leon", text: "Writing..." }));
    paintGuide();
    box.scrollTop = box.scrollHeight;
  }

  function prior() {
    return messages.filter(function (row) { return row.text !== greeting; }).slice(-16).map(function (row) {
      return { role: row.role, text: row.text };
    });
  }

  function sendText(text) {
    if (busy) return;
    text = String(text || "").trim();
    if (!text) return;
    qs("input").value = "";
    messages.push({ role: "person", text: text });
    busy = true;
    paint();
    var history = prior().slice(0, -1);
    fetch(api + "/onboard/turn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ message: text, history: history, fields: fields }),
    }).then(function (response) {
      return response.json().then(function (data) {
        return { ok: response.ok, data: data || {} };
      });
    }).then(function (result) {
      var data = result.data;
      applyGuide(data);
      if (data.saved) saved = data.saved;
      messages.push({ role: "leon", text: data.reply || "Say that once more." });
      persist();
    }).catch(function () {
      messages.push({ role: "leon", text: "I could not reach the server." });
    }).then(function () {
      busy = false;
      paint();
    });
  }

  function send(event) {
    event.preventDefault();
    sendText(qs("input").value);
  }

  function resume() {
    if (fresh || (fields.hotel_name && messages.length > 1)) return;
    fetch(api + "/guide", { credentials: "include" }).then(function (response) {
      return response.json();
    }).then(function (guide) {
      if (!guide || !guide.hotel_name || !(guide.todos || []).length) return;
      if (fields.hotel_name && fields.hotel_name !== guide.hotel_name) return;
      applyGuide(guide);
      persist();
      paint();
    }).catch(function () {});
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (window.top !== window.self) qs("composer-row").classList.add("pr-48");
    load();
    if (!messages.length) messages = [{ role: "leon", text: greeting }];
    paint();
    persist();
    resume();
    qs("composer").addEventListener("submit", send);
    qs("input").addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) send(event);
    });
  });
})();
