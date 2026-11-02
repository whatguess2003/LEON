(function () {
  var host = window.location.hostname;
  var local = host === "localhost" || host === "127.0.0.1" || host === "::1";
  var api = (local ? "http://localhost:8002" : "https://api.inap.ai") + "/api";
  var storeKey = "leon_onboard_chat";
  var greeting = "Hey. I'll open your account first. What's your WhatsApp number, with the country code?";
  var rootTemplate = "read.onboarding";
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
  var screen = null;
  var templateKey = rootTemplate;
  var templateLoading = false;
  var busy = false;

  function qs(id) { return document.getElementById(id); }

  function authHeaders(json) {
    var headers = {};
    if (json) headers["Content-Type"] = "application/json";
    try {
      var token = localStorage.getItem("leon_jwt");
      if (token) headers.Authorization = "Bearer " + token;
    } catch (error) {}
    return headers;
  }

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
        templateKey = data.templateKey || rootTemplate;
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
        templateKey: templateKey,
      }));
    } catch (error) {}
  }

  function progress() {
    var line = qs("progress");
    if (!line) return;
    if (screen && (screen.header || screen.body)) {
      line.textContent = screen.header || screen.body;
      return;
    }
    var current = todos.filter(function (row) { return row.state === "now"; })[0];
    var open = todos.filter(function (row) { return row.state !== "done"; }).length === 0 && todos.length;
    line.textContent = current && current.detail ? current.detail : (open ? "The hotel is open" : "What's your WhatsApp number, with the country code?");
  }

  function paintGuide() {
    var list = qs("todos");
    var facts = qs("highlights");
    var chips = qs("suggestion-row");
    var field = qs("input");
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
    if (field) field.placeholder = screen && screen.purpose === "input" && screen.body ? screen.body : "Message LEON-INAP";
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

  function listRows(current) {
    var rows = [];
    ((current && current.sections) || []).forEach(function (section) {
      (section.rows || []).forEach(function (item) { rows.push(item); });
    });
    return rows;
  }

  function mountList(box) {
    if (!screen || !screen.body) return;
    var wrap = document.createElement("div");
    wrap.className = "max-w-3xl mx-auto flex mb-4";
    var mark = document.createElement("div");
    mark.className = "w-7 h-7 rounded-full bg-blue-600 text-white flex items-center justify-center text-xs font-semibold mr-3 flex-shrink-0";
    mark.textContent = "L";
    wrap.appendChild(mark);
    var bubbleNode = document.createElement("div");
    bubbleNode.className = "wa-list";
    bubbleNode.dataset.onboardList = "1";
    if (screen.header) {
      var header = document.createElement("div");
      header.className = "wa-list-header";
      header.textContent = screen.header;
      bubbleNode.appendChild(header);
    }
    var body = document.createElement("div");
    body.className = "wa-list-body";
    body.textContent = screen.body;
    bubbleNode.appendChild(body);
    var footer = document.createElement("div");
    footer.className = "wa-list-footer";
    var foot = document.createElement("span");
    foot.textContent = screen.footer || "";
    var time = document.createElement("span");
    var clock = new Date();
    time.textContent = String(clock.getHours()).padStart(2, "0") + ":" + String(clock.getMinutes()).padStart(2, "0");
    footer.appendChild(foot);
    footer.appendChild(time);
    bubbleNode.appendChild(footer);
    if (listRows(screen).length) {
      var open = document.createElement("button");
      open.type = "button";
      open.className = "wa-action";
      open.innerHTML = '<i class="fas fa-list"></i> ' + (screen.button || "Open");
      open.addEventListener("click", function () { openSheet(screen); });
      bubbleNode.appendChild(open);
    }
    wrap.appendChild(bubbleNode);
    box.appendChild(wrap);
  }

  function openSheet(current) {
    var sheet = qs("onboard-sheet");
    var title = qs("onboard-sheet-title");
    var rows = qs("onboard-sheet-rows");
    if (!sheet || !rows) return;
    var items = listRows(current);
    if (!items.length) return;
    if (title) title.textContent = current.button || "";
    rows.innerHTML = "";
    (current.sections || []).forEach(function (section) {
      if (section.title) {
        var name = document.createElement("div");
        name.className = "px-4 pt-4 pb-1 text-sm font-semibold";
        name.style.color = "#111b21";
        name.textContent = section.title;
        rows.appendChild(name);
      }
      (section.rows || []).forEach(function (item) {
        var choice = document.createElement("button");
        choice.type = "button";
        choice.className = "block w-full text-left px-4 py-3";
        choice.style.borderTop = "1px solid #e9edef";
        choice.style.background = "#fff";
        var label = document.createElement("div");
        label.className = "text-sm";
        label.style.color = "#111b21";
        label.textContent = item.title || "";
        choice.appendChild(label);
        if (item.description) {
          var detail = document.createElement("div");
          detail.className = "text-xs mt-1";
          detail.style.color = "#667781";
          detail.textContent = item.description;
          choice.appendChild(detail);
        }
        choice.addEventListener("click", function () { chooseRow(item); });
        rows.appendChild(choice);
      });
    });
    sheet.classList.remove("hidden");
  }

  function chooseRow(item) {
    var sheet = qs("onboard-sheet");
    if (sheet) sheet.classList.add("hidden");
    if (!item || !item.id || templateLoading) return;
    if (item.title) messages.push({ role: "person", text: item.title, nav: true });
    templateLoading = true;
    paint();
    loadTemplate(item.id, true).then(function () { templateLoading = false; });
  }

  function paint() {
    var box = qs("messages");
    box.innerHTML = "";
    messages.forEach(function (row) { box.appendChild(bubble(row)); });
    mountList(box);
    if (busy) box.appendChild(bubble({ role: "leon", text: "Writing..." }));
    paintGuide();
    box.scrollTop = box.scrollHeight;
  }

  function prior() {
    return messages.filter(function (row) { return row.text !== greeting && !row.nav; }).slice(-16).map(function (row) {
      return { role: row.role, text: row.text };
    });
  }

  function loadTemplate(id, announce) {
    var key = id || templateKey || rootTemplate;
    return fetch(api + "/onboard/template?id=" + encodeURIComponent(key), {
      credentials: "include",
      headers: authHeaders(false),
    }).then(function (response) {
      return response.json().then(function (data) {
        return { ok: response.ok, data: data || {} };
      });
    }).then(function (result) {
      if (!result.ok || !result.data.screen || !result.data.screen.body) {
        if (announce) messages.push({ role: "leon", text: "That step is not on file." });
        if (announce) persist();
        if (announce) paint();
        return;
      }
      screen = result.data.screen;
      templateKey = key;
      if (messages.length === 1 && messages[0].text === greeting) messages = [];
      persist();
      paint();
    }).catch(function () {
      if (!announce) return;
      messages.push({ role: "leon", text: "I could not reach the server." });
      persist();
      paint();
    });
  }

  function ensureTemplate() {
    if (fresh && !fields.hotel_name) return;
    if (screen || templateLoading) return;
    templateLoading = true;
    loadTemplate(templateKey, false).then(function () { templateLoading = false; });
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
    if (screen && screen.body) history.push({ role: "leon", text: screen.body });
    fetch(api + "/onboard/turn", {
      method: "POST",
      headers: authHeaders(true),
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
      ensureTemplate();
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
    fetch(api + "/guide", { credentials: "include", headers: authHeaders(false) }).then(function (response) {
      return response.json();
    }).then(function (guide) {
      if (!guide || !guide.hotel_name || !(guide.todos || []).length) return;
      if (fields.hotel_name && fields.hotel_name !== guide.hotel_name) return;
      applyGuide(guide);
      persist();
      paint();
      ensureTemplate();
    }).catch(function () {});
  }

  function closeSheet() {
    var sheet = qs("onboard-sheet");
    if (sheet) sheet.classList.add("hidden");
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (window.top !== window.self) qs("composer-row").classList.add("pr-48");
    load();
    if (!messages.length) messages = [{ role: "leon", text: greeting }];
    paint();
    persist();
    ensureTemplate();
    resume();
    qs("composer").addEventListener("submit", send);
    qs("input").addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) send(event);
    });
    qs("onboard-sheet-close").addEventListener("click", closeSheet);
    qs("onboard-sheet").addEventListener("click", function (event) {
      if (event.target === qs("onboard-sheet")) closeSheet();
    });
  });
})();
