(function () {
  var host = window.location.hostname;
  var local = host === "localhost" || host === "127.0.0.1" || host === "::1";
  var api = (local ? "http://localhost:8002" : "https://api.inap.ai") + "/api/onboarding";
  var ID_KEY = "leon_onboarding_id";
  var STATUS = {
    done: { icon: "fa-circle-check", tone: "text-green-600", label: "Done" },
    in_progress: { icon: "fa-circle-half-stroke", tone: "text-blue-600", label: "In progress" },
    skipped: { icon: "fa-forward", tone: "text-gray-400", label: "Skipped for now" },
    not_started: { icon: "fa-circle", tone: "text-gray-300", label: "Not started" },
  };
  var BATCH_ACTIONS = {
    draft: ["preview"],
    validated: ["preview", "commit"],
    committed: ["rollback"],
    rolled_back: [],
  };
  var ACTION_LABEL = { preview: "Check rows", commit: "Import", rollback: "Undo import" };
  var view = { conversation: [], checklist: [], open_questions: [], saved: [], sops: [], imports: [], importable: [] };
  var details = {};
  var busy = false;
  var onboardingId = "";

  function qs(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function button(label, cls, run) {
    var node = el("button", cls || "text-xs border border-gray-300 rounded px-2 py-1 mr-1 mt-1", label);
    node.type = "button";
    node.addEventListener("click", run);
    return node;
  }

  function claim(token, key) {
    var parts = String(token || "").split(".");
    if (parts.length < 2) return "";
    var body = parts[1].split("-").join("+").split("_").join("/");
    while (body.length % 4) body += "=";
    try { return JSON.parse(atob(body))[key] || ""; } catch (e) { return ""; }
  }

  function keepToken(token, hotel) {
    if (!token) return;
    try { localStorage.setItem("leon_jwt", token); } catch (e) {}
    var name = hotel || claim(token, "hotel_name");
    if (!name) return;
    var secure = location.protocol === "https:" ? "; Secure" : "";
    document.cookie = "leon_hotel=" + encodeURIComponent(name) + "; path=/; max-age=604800; SameSite=Lax" + secure;
    try {
      localStorage.setItem("leon_hotel", name);
      if (claim(token, "hotel_id")) localStorage.setItem("leon_hotel_id", JSON.stringify({ hotel: name, id: claim(token, "hotel_id") }));
    } catch (e) {}
  }

  function keepId(id) {
    if (!id) return;
    onboardingId = id;
    try { localStorage.setItem(ID_KEY, id); } catch (e) {}
  }

  function call(method, path, body) {
    var init = { method: method, credentials: "include", headers: { "Content-Type": "application/json" } };
    if (body) init.body = JSON.stringify(body);
    return fetch(api + path, init).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) throw new Error((data && (data.error || data.detail)) || "The server refused that.");
        return data;
      });
    });
  }

  // ---------- chat ----------

  function bubble(row) {
    var wrap = el("div", "max-w-3xl mx-auto flex mb-4" + (row.role === "person" ? " justify-end" : ""));
    if (row.role === "event") {
      wrap.className = "max-w-3xl mx-auto mb-4 text-center";
      wrap.appendChild(el("span", "inline-block text-xs text-gray-500 bg-gray-50 border border-gray-200 rounded-full px-3 py-1", row.text));
      return wrap;
    }
    if (row.role !== "person") {
      var mark = el("div", "w-7 h-7 rounded-full bg-blue-600 text-white flex items-center justify-center text-xs font-semibold mr-3 flex-shrink-0", "L");
      wrap.appendChild(mark);
    }
    var column = el("div", "max-w-lg");
    column.appendChild(el("div", "rounded-lg px-3 py-2 text-sm whitespace-pre-wrap " + (row.role === "person" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-900"), row.text));
    var marker = "Succeeded ";
    (row.results || []).filter(function (said) { return said.indexOf(marker) === 0; }).forEach(function (said) {
      column.appendChild(el("div", "text-xs text-green-700 mt-1", "\u2713 " + said.slice(marker.length)));
    });
    wrap.appendChild(column);
    return wrap;
  }

  function paintChat() {
    var box = qs("messages");
    box.innerHTML = "";
    view.conversation.forEach(function (row) { box.appendChild(bubble(row)); });
    if (busy) box.appendChild(bubble({ role: "leon", text: "Writing..." }));
    box.scrollTop = box.scrollHeight;
  }

  function say(text) {
    if (busy) return;
    var message = String(text || "").trim();
    var password = qs("password").value;
    if (message) view.conversation.push({ role: "person", text: message });
    qs("input").value = "";
    busy = true;
    paintChat();
    call("POST", "/turn", { onboarding_id: onboardingId, message: message, password: password }).then(function (data) {
      keepId(data.onboarding_id);
      keepToken(data.token, data.hotel_name);
      if (data.token) qs("password").value = "";
      view.conversation.push({ role: "leon", text: data.reply, results: (data.outcomes || []).map(function (o) { return o.said; }) });
      apply(data.state);
    }).catch(function (error) {
      view.conversation.push({ role: "leon", text: "I could not reach the server (" + error.message + "). Nothing new was saved." });
    }).then(function () {
      busy = false;
      paintChat();
    });
  }

  // ---------- panel ----------

  function apply(state) {
    if (!state) return;
    var conversation = view.conversation;
    view = Object.assign({}, view, state);
    view.conversation = state.conversation || conversation;
    qs("password-row").classList.toggle("hidden", !state.needs_password);
    var account = state.account || {};
    qs("subtitle").textContent = account.hotel_name || (account.user_name ? "Hi " + account.user_name : "Setting up LEON around your hotel");
    paintPanel();
  }

  function paintChecklist() {
    var box = qs("checklist");
    box.innerHTML = "";
    view.checklist.forEach(function (row) {
      var look = STATUS[row.status];
      var line = el("div", "flex items-start text-sm mb-1");
      line.title = row.why;
      line.appendChild(el("i", "fas " + look.icon + " " + look.tone + " mt-1 mr-2 text-xs"));
      var text = el("div", "min-w-0");
      text.appendChild(el("div", "", row.label));
      if (row.note) text.appendChild(el("div", "text-xs text-gray-500", row.note));
      line.appendChild(text);
      box.appendChild(line);
    });
  }

  function paintList(id, rows, text, empty) {
    var box = qs(id);
    box.innerHTML = "";
    if (!rows.length) box.appendChild(el("div", "text-xs text-gray-400", empty));
    rows.forEach(function (row) { box.appendChild(el("div", "text-xs text-gray-700 mb-1", text(row))); });
  }

  function paintSops() {
    var box = qs("sops");
    box.innerHTML = "";
    if (!view.sops.length) box.appendChild(el("div", "text-xs text-gray-400", "Paste a procedure in the chat or attach one."));
    view.sops.forEach(function (sop) {
      var card = el("div", "border border-gray-200 rounded-lg p-2 mb-2 text-xs");
      card.appendChild(el("div", "font-semibold text-gray-800", sop.title + " \u00b7 " + sop.status));
      if (sop.summary) card.appendChild(el("div", "text-gray-600 mt-1", sop.summary));
      if (sop.file_name) card.appendChild(el("div", "text-gray-400", sop.file_name));
      (sop.sections || []).forEach(function (section) {
        var keys = section.setting_keys || [];
        card.appendChild(el("div", "text-gray-700 mt-1", section.seq + ". " + (section.title ? section.title + ": " : "") + section.summary
          + (keys.length ? " \u2192 " + keys.join(", ") : "")));
      });
      (sop.links || []).forEach(function (link) {
        card.appendChild(el("div", "text-gray-500 mt-1", link.setting_key + ": " + link.summary + " (" + link.status.replace("_", " ") + ")"));
      });
      if (sop.status === "proposed") {
        card.appendChild(button("That's right, keep it", "", function () { act("POST", "/sops/" + sop.sop_id + "/confirm", {}); }));
        card.appendChild(button("Set aside", "", function () { act("POST", "/sops/" + sop.sop_id + "/reject", {}); }));
      }
      box.appendChild(card);
    });
  }

  function fieldsOf(entity) {
    var found = view.importable.filter(function (row) { return row.entity === entity; })[0];
    return found ? found.fields : [];
  }

  function mappingTable(batch, file) {
    var mapping = file.mapping;
    var wrap = el("div", "mt-2");
    var entity = el("select", "border border-gray-300 rounded px-1 py-1 text-xs w-full");
    view.importable.forEach(function (row) {
      var option = el("option", "", row.label);
      option.value = row.entity;
      option.selected = row.entity === mapping.entity;
      entity.appendChild(option);
    });
    wrap.appendChild(entity);
    var table = el("table", "w-full mt-2 text-xs");
    var picks = {};
    fieldsOf(mapping.entity).forEach(function (field) {
      var tr = el("tr", "border-t border-gray-100");
      tr.appendChild(el("td", "py-1 pr-1 align-top" + (field.required ? " font-semibold" : ""), field.field + (field.required ? " *" : "")));
      var td = el("td", "py-1");
      var select = el("select", "border border-gray-300 rounded px-1 py-0 text-xs w-full");
      [""].concat(file.headers).forEach(function (header) {
        var option = el("option", "", header || "(not in file)");
        option.value = header;
        option.selected = header === (mapping.fields[field.field] || {}).source;
        select.appendChild(option);
      });
      var fixed = el("input", "border border-gray-300 rounded px-1 py-0 text-xs w-full mt-1");
      fixed.placeholder = "or one value for every row";
      fixed.value = (mapping.fields[field.field] || {}).fixed || "";
      picks[field.field] = { select: select, fixed: fixed, format: (mapping.fields[field.field] || {}).format || "" };
      td.appendChild(select);
      td.appendChild(fixed);
      if (picks[field.field].format) td.appendChild(el("div", "text-gray-400", "format " + picks[field.field].format));
      var translated = (mapping.value_maps || {})[field.field] || {};
      Object.keys(translated).forEach(function (source) {
        td.appendChild(el("div", "text-gray-500", "\u201c" + source + "\u201d \u2192 " + translated[source]));
      });
      tr.appendChild(td);
      table.appendChild(tr);
    });
    wrap.appendChild(table);
    var dates = el("input", "border border-gray-300 rounded px-1 py-1 text-xs w-full mt-1");
    dates.placeholder = "Date format, e.g. %d/%m/%Y";
    dates.value = mapping.date_format || "";
    var country = el("input", "border border-gray-300 rounded px-1 py-1 text-xs w-full mt-1");
    country.placeholder = "Phone country code, e.g. 60";
    country.value = mapping.phone_country_code || "";
    wrap.appendChild(dates);
    wrap.appendChild(country);
    if (mapping.explanation) wrap.appendChild(el("div", "text-gray-500 mt-1", mapping.explanation));
    wrap.appendChild(button("Save mapping", "", function () {
      var fields = {};
      Object.keys(picks).forEach(function (key) { fields[key] = { source: picks[key].select.value, fixed: picks[key].fixed.value, format: picks[key].format }; });
      act("PUT", "/imports/" + batch.batch_id + "/files/" + file.file_id,
          { entity: entity.value, fields: entity.value === mapping.entity ? fields : {}, formats: { date_format: dates.value, phone_country_code: country.value } });
    }));
    wrap.appendChild(button("Remove file", "", function () { act("DELETE", "/imports/" + batch.batch_id + "/files/" + file.file_id); }));
    return wrap;
  }

  function sampleTable(file) {
    var sample = (details[file.file_id] || {}).sample || [];
    if (!sample.length) return el("div");
    var wrap = el("div", "overflow-x-auto mt-2");
    var table = el("table", "text-xs whitespace-nowrap");
    var head = el("tr", "text-gray-500");
    file.headers.forEach(function (header) { head.appendChild(el("th", "pr-2 text-left font-normal", header)); });
    table.appendChild(head);
    sample.forEach(function (row) {
      var tr = el("tr", "border-t border-gray-100");
      file.headers.forEach(function (header) { tr.appendChild(el("td", "pr-2", row[header])); });
      table.appendChild(tr);
    });
    wrap.appendChild(table);
    return wrap;
  }

  function counts(file) {
    if (file.valid_count === null || file.valid_count === undefined) return file.row_count + " rows, not checked yet";
    var text = file.row_count + " rows: " + file.valid_count + " ready, " + file.error_count + " with errors, " + file.duplicate_count + " duplicates";
    return file.created_count !== null && file.created_count !== undefined ? text + "; " + file.created_count + " imported" : text;
  }

  function paintImports() {
    var box = qs("imports");
    box.innerHTML = "";
    if (!view.imports.length) box.appendChild(el("div", "text-xs text-gray-400", "Attach an export from your old system (rooms, guests, reservations\u2026)."));
    view.imports.forEach(function (batch) {
      var card = el("div", "border border-gray-200 rounded-lg p-2 mb-2 text-xs");
      card.appendChild(el("div", "font-semibold text-gray-800", "Batch \u00b7 " + batch.status.replace("_", " ")));
      if (batch.detail) card.appendChild(el("div", "text-red-600", batch.detail));
      batch.files.forEach(function (file) {
        var part = el("div", "mt-2 border-t border-gray-100 pt-2");
        part.appendChild(el("div", "text-gray-800", file.file_name + " \u2192 " + file.label));
        part.appendChild(el("div", "text-gray-500", counts(file)));
        (file.errors || []).forEach(function (error) {
          part.appendChild(el("div", "text-red-600", (error.row_number ? "Row " + error.row_number + ": " : "") + error.message));
        });
        part.appendChild(sampleTable(file));
        if (batch.status === "draft" || batch.status === "validated") part.appendChild(mappingTable(batch, file));
        card.appendChild(part);
      });
      var actions = el("div", "mt-2");
      BATCH_ACTIONS[batch.status].forEach(function (name) {
        actions.appendChild(button(ACTION_LABEL[name], name === "commit" ? "text-xs bg-blue-600 text-white rounded px-2 py-1 mr-1 mt-1" : "",
          function () { act("POST", "/imports/" + batch.batch_id + "/" + name, {}); }));
      });
      card.appendChild(actions);
      box.appendChild(card);
      loadDetails(batch);
    });
  }

  function loadDetails(batch) {
    var missing = batch.files.filter(function (file) { return !details[file.file_id]; });
    if (!missing.length || batch.status === "rolled_back") return;
    missing.forEach(function (file) { details[file.file_id] = { sample: [] }; });
    call("GET", "/imports/" + batch.batch_id).then(function (found) {
      (found.files || []).forEach(function (file) { details[file.file_id] = file; });
      paintImports();
    }).catch(function () {});
  }

  function paintPanel() {
    paintChecklist();
    paintList("open-items", view.open_questions || [], function (row) { return (row.stale ? "\u23f3 " : "") + row.question; }, "Nothing waiting on you.");
    paintList("saved", (view.saved || []).slice().reverse(), function (row) { return row.summary; }, "Nothing saved yet.");
    paintSops();
    paintImports();
    paintInvites();
  }

  function paintInvites() {
    var box = qs("invites");
    box.innerHTML = "";
    var rows = view.invites || [];
    qs("invites-block").classList.toggle("hidden", !rows.length);
    rows.forEach(function (invite) {
      var line = el("div", "text-xs text-gray-700 mb-1");
      line.appendChild(el("span", "", invite.user_name + (invite.role_name ? " \u00b7 " + invite.role_name : "") + " \u00b7 " + invite.status));
      if (invite.status === "pending") {
        line.appendChild(button("Get sign-up link", "", function () {
          call("POST", "/invites/" + invite.invite_id + "/link").then(function (found) {
            var link = new URL(found.link, window.location.href).href;
            var shown = el("input", "border border-gray-300 rounded px-1 py-0 text-xs w-full mt-1");
            shown.readOnly = true;
            shown.value = link;
            line.appendChild(shown);
            shown.select();
            if (navigator.clipboard) navigator.clipboard.writeText(link).catch(function () {});
          }).catch(function (error) { line.appendChild(el("div", "text-red-600", error.message)); });
        }));
      }
      box.appendChild(line);
    });
  }

  // ---------- invite: an imported staff member sets their password ----------

  function inviteView(token) {
    qs("invite").classList.remove("hidden");
    qs("onboarding").classList.add("hidden");
    call("GET", "/invites/accept?token=" + encodeURIComponent(token)).then(function (found) {
      var invite = found.invite;
      qs("invite-text").textContent = "Hi " + invite.user_name + ", " + invite.hotel_name + " invited you to LEON. "
        + (invite.has_password ? "Enter your LEON password to join." : "Choose a password (at least 8 characters) to join.");
    }).catch(function (error) { qs("invite-text").textContent = error.message; qs("invite-form").classList.add("hidden"); });
    qs("invite-form").addEventListener("submit", function (event) {
      event.preventDefault();
      call("POST", "/invites/accept", { token: token, password: qs("invite-password").value }).then(function (done) {
        keepToken(done.token, done.hotel_name);
        window.location.href = "index.html";
      }).catch(function (error) { qs("invite-error").textContent = error.message; });
    });
  }

  function refresh() {
    return call("GET", "/state?onboarding_id=" + encodeURIComponent(onboardingId)).then(function (state) {
      keepId(state.onboarding_id);
      apply(state);
      paintChat();
      return state;
    });
  }

  function act(method, path, body) {
    var payload = body ? Object.assign({ onboarding_id: onboardingId }, body) : undefined;
    var url = body ? path : path + "?onboarding_id=" + encodeURIComponent(onboardingId);
    return call(method, url, payload).then(function (result) {
      (result.files || []).forEach(function (file) { details[file.file_id] = file; });
      return refresh();
    }).catch(function (error) {
      view.conversation.push({ role: "event", text: error.message });
      paintChat();
    });
  }

  // ---------- uploads ----------

  function readFile(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result).split(",").pop()); };
      reader.onerror = function () { reject(new Error("The file could not be read.")); };
      reader.readAsDataURL(file);
    });
  }

  function chip(file) {
    var node = el("span", "text-xs border border-gray-300 rounded-full px-2 py-1 text-gray-600", file.name);
    qs("pending").appendChild(node);
    return node;
  }

  function upload(file) {
    if (!file) return Promise.resolve();
    var node = chip(file);
    node.textContent = "Reading " + file.name + "\u2026";
    return readFile(file).then(function (content) {
      return call("POST", "/attachments", { onboarding_id: onboardingId, file_name: file.name, content_base64: content });
    }).then(function (result) {
      node.textContent = file.name;
      view.conversation.push({ role: "event", text: result.summary || (file.name + " attached") });
      paintChat();
      return refresh().then(function () { return true; });
    }).catch(function (error) {
      node.textContent = file.name + " \u2014 " + error.message;
      view.conversation.push({ role: "leon", text: error.message });
      paintChat();
    });
  }

  function uploadList(list) {
    var files = Array.prototype.slice.call(list || []);
    var sent = [];
    var chain = Promise.resolve();
    files.forEach(function (file) {
      chain = chain.then(function () {
        return upload(file).then(function (ok) { if (ok) sent.push(file.name); });
      });
    });
    chain.then(function () {
      if (sent.length) say("I attached " + sent.join(", ") + ". Please read them.");
    });
  }

  // ---------- resume ----------

  function banner(state) {
    if (!state.returning) return;
    var recap = state.recap || {};
    var parts = [];
    if (recap.done.length) parts.push("Done: " + recap.done.join(", ") + ".");
    if (recap.last_saved.length) parts.push("Last saved: " + recap.last_saved.slice(-2).join("; ") + ".");
    if (recap.open_questions.length) parts.push(recap.open_questions.length + " question" + (recap.open_questions.length === 1 ? "" : "s") + " still open.");
    qs("resume-text").textContent = "Welcome back. " + parts.join(" ");
    qs("resume").classList.remove("hidden");
  }

  document.addEventListener("DOMContentLoaded", function () {
    var params = new URLSearchParams(window.location.search);
    if (params.get("invite")) { inviteView(params.get("invite")); return; }
    if (params.get("fresh") === "1") { try { localStorage.removeItem(ID_KEY); } catch (e) {} }
    try { onboardingId = localStorage.getItem(ID_KEY) || ""; } catch (e) {}
    if (window.top !== window.self) qs("composer-row").classList.add("pr-48");
    refresh().then(function (state) {
      banner(state);
      if (!state.conversation.length || state.returning) say("");
    }).catch(function () {
      view.conversation.push({ role: "leon", text: "I could not reach the server." });
      paintChat();
    });
    qs("composer").addEventListener("submit", function (event) { event.preventDefault(); if (qs("input").value.trim()) say(qs("input").value); });
    qs("input").addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); if (qs("input").value.trim()) say(qs("input").value); }
    });
    qs("resume-close").addEventListener("click", function () { qs("resume").classList.add("hidden"); });
    qs("panel-toggle").addEventListener("click", function () { qs("panel").classList.toggle("hidden"); });
    qs("file").addEventListener("change", function (event) {
      uploadList(event.target.files);
      event.target.value = "";
    });
    qs("composer").addEventListener("dragover", function (event) { event.preventDefault(); });
    qs("composer").addEventListener("drop", function (event) {
      event.preventDefault();
      uploadList(event.dataTransfer.files);
    });
  });
})();
