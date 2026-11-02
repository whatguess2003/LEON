(function () {
  var host = window.location.hostname;
  var local = host === "localhost" || host === "127.0.0.1" || host === "::1";
  var api = (local ? "http://localhost:8002" : "https://api.inap.ai") + "/api";
  var token = new URLSearchParams(window.location.search).get("t") || "";
  var storeKey = "ella_guest_chat";
  var messages = [];
  var fields = {};
  var todos = [];
  var highlights = [];
  var suggestion = "";
  var whatsappUrl = "";
  var whatsapp = [];
  var busy = false;

  function qs(id) { return document.getElementById(id); }

  function load() {
    if (!token) {
      messages = [{ role: "ella", text: "Open the link from WhatsApp to fill in your details." }];
      return;
    }
    try {
      var data = JSON.parse(sessionStorage.getItem(storeKey) || "null");
      if (data && data.token === token && data.messages && data.messages.length) {
        messages = data.messages;
        fields = data.fields || {};
        todos = data.todos || [];
        highlights = data.highlights || [];
        suggestion = data.suggestion || "";
        whatsappUrl = data.whatsappUrl || "";
        whatsapp = data.whatsapp || [];
      }
    } catch (error) {}
  }

  function persist() {
    try {
      sessionStorage.setItem(storeKey, JSON.stringify({
        token: token, messages: messages, fields: fields, todos: todos,
        highlights: highlights, suggestion: suggestion, whatsappUrl: whatsappUrl,
        whatsapp: whatsapp,
      }));
    } catch (error) {}
  }

  function apply(data) {
    if (!data) return;
    if (data.fields) fields = data.fields;
    if (data.todos) todos = data.todos;
    if (data.highlights) highlights = data.highlights;
    suggestion = data.suggestion || "";
    if (data.whatsapp_url) whatsappUrl = data.whatsapp_url;
    mergeWhatsapp(data.whatsapp);
  }

  function mergeWhatsapp(rows) {
    var seen = {};
    whatsapp.forEach(function (row) { if (row.source_ref) seen[row.source_ref] = true; });
    var added = 0;
    (rows || []).forEach(function (row) {
      if (!row || !row.source_ref || seen[row.source_ref] || !row.body) return;
      seen[row.source_ref] = true;
      whatsapp.push(row);
      added += 1;
    });
    return added;
  }

  function progress() {
    var line = qs("progress");
    var current = todos.filter(function (row) { return row.state === "now"; })[0];
    var done = todos.length && todos.every(function (row) { return row.state === "done"; });
    line.textContent = current && current.detail ? current.detail : (done ? "Your details are in" : "Ella");
  }

  function paint() {
    var box = qs("messages");
    var list = qs("todos");
    var facts = qs("highlights");
    var chips = qs("suggestion-row");
    box.innerHTML = "";
    list.innerHTML = "";
    facts.innerHTML = "";
    chips.innerHTML = "";
    var inline = {};
    messages.forEach(function (row) {
      if (row.source_ref) inline[row.source_ref] = true;
      var wrap = document.createElement("div");
      wrap.className = "max-w-3xl mx-auto flex mb-4" + (row.role === "guest" ? " justify-end" : "");
      if (row.role !== "guest") {
        var mark = document.createElement("div");
        mark.className = "w-7 h-7 rounded-full bg-teal-600 text-white flex items-center justify-center text-xs font-semibold mr-3 flex-shrink-0";
        mark.textContent = "E";
        wrap.appendChild(mark);
      }
      if (row.template) {
        var card = document.createElement("div");
        card.className = "wa-list";
        card.dataset.template = row.template;
        var header = document.createElement("div");
        header.className = "wa-list-header";
        header.textContent = row.template;
        var body = document.createElement("div");
        body.className = "wa-list-body";
        body.textContent = row.text;
        var footer = document.createElement("div");
        footer.className = "wa-list-footer";
        var foot = document.createElement("span");
        foot.textContent = "WhatsApp template";
        footer.appendChild(foot);
        card.appendChild(header);
        card.appendChild(body);
        card.appendChild(footer);
        wrap.appendChild(card);
      } else {
        var text = document.createElement("div");
        text.className = "rounded-lg px-3 py-2 text-sm max-w-lg " + (row.role === "guest" ? "bg-teal-600 text-white" : "bg-gray-100 text-gray-900");
        text.textContent = row.text;
        wrap.appendChild(text);
      }
      box.appendChild(wrap);
    });
    whatsapp.forEach(function (row) {
      if (row.source_ref && inline[row.source_ref]) return;
      var wrap = document.createElement("div");
      var inbound = row.direction === "in";
      wrap.className = "max-w-3xl mx-auto flex mb-4" + (inbound ? " justify-end" : "");
      var text = document.createElement("div");
      text.className = "rounded-lg px-3 py-2 text-sm max-w-lg " + (inbound ? "bg-white text-gray-900 border border-gray-200" : "bg-white text-gray-900 border border-teal-200");
      var label = document.createElement("div");
      label.className = "text-xs text-gray-500 mb-1";
      label.textContent = "WhatsApp";
      text.appendChild(label);
      text.appendChild(document.createTextNode(row.body));
      wrap.appendChild(text);
      box.appendChild(wrap);
    });
    if (busy) {
      var wait = document.createElement("div");
      wait.className = "max-w-3xl mx-auto text-xs text-gray-500 mb-4";
      wait.textContent = "Ella is writing...";
      box.appendChild(wait);
    }
    todos.forEach(function (row) {
      var line = document.createElement("div");
      line.className = "text-sm mt-2 " + (row.state === "done" ? "text-green-700" : "text-gray-900 font-semibold");
      line.textContent = row.label + (row.detail ? " — " + row.detail : "");
      list.appendChild(line);
    });
    highlights.forEach(function (row) {
      var chip = document.createElement("span");
      chip.className = "inline-block bg-teal-50 text-teal-800 rounded-full px-2 py-1 mr-1 mb-1 text-xs";
      chip.textContent = row.label + ": " + row.value;
      facts.appendChild(chip);
    });
    if (suggestion && !busy) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "bg-teal-50 text-teal-800 rounded-full px-3 py-1 text-sm";
      button.textContent = suggestion;
      button.addEventListener("click", function () { sendText(suggestion); });
      chips.appendChild(button);
    }
    if (whatsappUrl && !busy) {
      var link = document.createElement("a");
      link.className = "inline-block bg-teal-600 text-white rounded-full px-3 py-1 text-sm";
      link.href = whatsappUrl;
      link.textContent = "Message the hotel";
      chips.appendChild(link);
    }
    progress();
    box.scrollTop = box.scrollHeight;
  }

  function prior() {
    return messages.filter(function (row) { return row.text && !row.template; });
  }

  function placeConfirmed(rows) {
    var seen = {};
    messages.forEach(function (row) { if (row.source_ref) seen[row.source_ref] = true; });
    var fresh = [];
    (rows || []).forEach(function (row) {
      if (!row || !row.body || !row.source_ref || seen[row.source_ref]) return;
      seen[row.source_ref] = true;
      fresh.push({
        role: "ella",
        text: row.body,
        source_ref: row.source_ref,
        template: row.template_name || "confirmed_booking",
      });
    });
    if (!fresh.length) return false;
    var index = messages.length;
    var found = false;
    for (var i = messages.length - 1; i >= 0; i--) {
      if (messages[i].complete) {
        index = i + 1;
        found = true;
        break;
      }
    }
    if (!found) {
      for (var j = messages.length - 1; j >= 0; j--) {
        if (messages[j].role === "ella" && !messages[j].template) {
          index = j + 1;
          break;
        }
      }
    }
    fresh.forEach(function (row) {
      messages.splice(index, 0, row);
      index += 1;
    });
    return true;
  }

  function sendText(text) {
    if (busy || !token) return;
    text = String(text || "").trim();
    if (!text) return;
    qs("input").value = "";
    messages.push({ role: "guest", text: text });
    busy = true;
    paint();
    var history = prior().slice(0, -1);
    fetch(api + "/guest/onboard/turn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: token, message: text, history: history, fields: fields }),
    }).then(function (response) {
      return response.json().then(function (data) { return { ok: response.ok, data: data || {} }; });
    }).then(function (result) {
      var data = result.data;
      if (!result.ok || (data.error && !data.reply)) {
        messages.push({ role: "ella", text: data.error || "I could not save that." });
      } else {
        apply(data);
        messages.push({
          role: "ella",
          text: data.reply || "Say that once more.",
          complete: Object.prototype.hasOwnProperty.call(data, "confirmed_bookings"),
        });
        placeConfirmed(data.confirmed_bookings);
      }
      persist();
    }).catch(function () {
      messages.push({ role: "ella", text: "I could not reach the server." });
    }).then(function () {
      busy = false;
      paint();
    });
  }

  function open() {
    if (!token || messages.length) {
      paint();
      return;
    }
    fetch(api + "/guest/onboard?token=" + encodeURIComponent(token)).then(function (response) {
      return response.json().then(function (data) { return { ok: response.ok, data: data || {} }; });
    }).then(function (result) {
      if (!result.ok || result.data.error) {
        messages = [{ role: "ella", text: (result.data && result.data.error) || "This link is not valid." }];
      } else {
        apply(result.data);
        messages = [{ role: "ella", text: result.data.greeting || "Hey, I'm Ella.", complete: !!result.data.complete }];
        placeConfirmed(result.data.confirmed_bookings);
      }
      persist();
      paint();
    }).catch(function () {
      messages = [{ role: "ella", text: "I could not reach the server." }];
      paint();
    });
  }

  function pullWhatsapp() {
    if (!token || busy) return;
    fetch(api + "/guest/onboard?token=" + encodeURIComponent(token)).then(function (response) {
      return response.json();
    }).then(function (data) {
      if (!data || data.error) return;
      var placed = placeConfirmed(data.confirmed_bookings);
      if (mergeWhatsapp(data.whatsapp) || placed) {
        persist();
        paint();
      }
    }).catch(function () {});
  }

  document.getElementById("composer").addEventListener("submit", function (event) {
    event.preventDefault();
    sendText(qs("input").value);
  });
  load();
  open();
  pullWhatsapp();
  setInterval(pullWhatsapp, 3000);
})();
