(function () {
  var API = (location.hostname === "localhost" || location.hostname === "127.0.0.1") ? "http://localhost:8002" : "https://api.inap.ai";
  var MODULE_LABEL = { front_desk: "Front desk", finance: "Finance", fnb: "Food and beverage", hr: "HR" };
  var day = "";
  var lastNote = "";

  function qs(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function readControl(card) {
    var kind = card.dataset.kind;
    var readers = {
      boolean: function () {
        var value = card.querySelector("select").value;
        return value === "" ? undefined : value === "true";
      },
      choice: function () {
        var value = card.querySelector("select").value;
        return value === "" ? undefined : value;
      },
      choices: function () {
        return Array.prototype.map.call(card.querySelectorAll("input[type=checkbox]:checked"), function (input) { return input.value; });
      },
      time: function () { return card.querySelector("input[type=time]").value; },
      amount: function () { return card.querySelector("input[type=number]").value; },
      tax_codes: function () {
        return card.querySelector("input[type=text]").value.split(",").map(function (part) { return part.trim(); }).filter(function (part) { return part; });
      }
    };
    var reader = readers[kind];
    return reader ? reader() : undefined;
  }

  function currentText(row) {
    if (row.value === null || row.value === undefined || row.value === "") return "Not set";
    var match = (row.options || []).filter(function (option) { return option.value === row.value; })[0];
    if (match) return match.meaning;
    if (Array.isArray(row.value)) return row.value.join(", ") || "Not set";
    if (row.value === true) return "Yes";
    if (row.value === false) return "No";
    return String(row.value);
  }

  function fillControl(card, row) {
    var kind = row.value_type;
    var box = el("div", "mt-2");
    var builders = {
      boolean: function () {
        var select = el("select", "border rounded px-2 py-1 text-sm w-full");
        [["", "Choose"], ["true", "Yes"], ["false", "No"]].forEach(function (pair) {
          var option = el("option", "", pair[1]);
          option.value = pair[0];
          select.appendChild(option);
        });
        select.value = row.value === true ? "true" : (row.value === false ? "false" : "");
        box.appendChild(select);
      },
      choice: function () {
        var select = el("select", "border rounded px-2 py-1 text-sm w-full");
        select.appendChild(el("option", "", "Choose")).value = "";
        (row.options || []).forEach(function (option) {
          var node = el("option", "", option.meaning || option.value);
          node.value = option.value;
          select.appendChild(node);
        });
        select.value = row.value || "";
        box.appendChild(select);
      },
      choices: function () {
        var held = Array.isArray(row.value) ? row.value : [];
        (row.options || []).forEach(function (option) {
          var label = el("label", "flex items-center gap-2 text-sm mt-1");
          var input = document.createElement("input");
          input.type = "checkbox";
          input.value = option.value;
          input.checked = held.indexOf(option.value) !== -1;
          label.appendChild(input);
          label.appendChild(el("span", "", option.meaning || option.value));
          box.appendChild(label);
        });
      },
      time: function () {
        var input = document.createElement("input");
        input.type = "time";
        input.className = "border rounded px-2 py-1 text-sm";
        input.value = row.value || "";
        box.appendChild(input);
      },
      amount: function () {
        var input = document.createElement("input");
        input.type = "number";
        input.min = "0";
        input.step = "0.01";
        input.className = "border rounded px-2 py-1 text-sm w-40";
        input.value = row.value == null ? "" : String(row.value);
        box.appendChild(input);
      },
      tax_codes: function () {
        var input = document.createElement("input");
        input.type = "text";
        input.className = "border rounded px-2 py-1 text-sm w-full";
        input.placeholder = "Tax codes, first applied first";
        input.value = Array.isArray(row.value) ? row.value.join(", ") : "";
        box.appendChild(input);
      }
    };
    (builders[kind] || function () { box.appendChild(el("p", "text-sm text-gray-500", "This setting is edited elsewhere.")); })();
    card.appendChild(box);
  }

  function moduleNote(state) {
    if (!state) return "";
    if (state.governed && state.enabled === null) return "Answer whether the hotel uses this module.";
    if (state.enabled === false) return "This module is turned off.";
    var missing = (state.missing || []).map(function (item) { return item.question; });
    return missing.length ? "Still needed: " + missing.join(" ") : "Ready.";
  }

  function render(data) {
    day = data.day || "";
    var date = qs("settings-effective");
    if (date && !date.value) date.value = day;
    var list = qs("settings-list");
    list.innerHTML = "";
    var groups = {};
    (data.settings || []).forEach(function (row) {
      (groups[row.module_name] = groups[row.module_name] || []).push(row);
    });
    Object.keys(groups).forEach(function (name) {
      var section = el("section", "bg-white border rounded p-3");
      section.appendChild(el("h2", "text-lg font-semibold text-gray-900", MODULE_LABEL[name] || name));
      section.appendChild(el("p", "text-sm text-gray-500 mb-3", moduleNote((data.modules || {})[name])));
      groups[name].sort(function (a, b) { return (a.display_order || 0) - (b.display_order || 0); }).forEach(function (row) {
        var card = el("div", "border-t py-3");
        card.dataset.kind = row.value_type;
        card.dataset.key = row.setting_key;
        card.appendChild(el("div", "text-sm font-medium text-gray-900", row.question));
        card.appendChild(el("div", "text-xs text-gray-500 mt-1", row.explanation || ""));
        var now = el("div", "text-xs text-gray-700 mt-1");
        now.appendChild(el("span", "font-medium", "In effect: "));
        now.appendChild(document.createTextNode(currentText(row)));
        card.appendChild(now);
        if (row.required_when && Object.keys(row.required_when).length) {
          card.appendChild(el("div", "text-xs text-gray-400 mt-1", "Asked when " + Object.keys(row.required_when).map(function (key) {
            return key + " is " + row.required_when[key];
          }).join(", ")));
        }
        fillControl(card, row);
        if (row.sop_prompt) {
          var sop = document.createElement("textarea");
          sop.className = "border rounded px-2 py-1 text-sm w-full mt-2";
          sop.rows = 2;
          sop.placeholder = row.sop_prompt;
          sop.value = row.sop_text || "";
          card.appendChild(sop);
        }
        var actions = el("div", "flex items-center gap-2 mt-2");
        var button = el("button", "bg-indigo-600 text-white px-3 py-1 rounded text-sm", "Save");
        button.type = "button";
        var status = el("span", "text-xs text-gray-500", "");
        button.addEventListener("click", function () { save(card, status); });
        actions.appendChild(button);
        actions.appendChild(status);
        card.appendChild(actions);
        section.appendChild(card);
      });
      list.appendChild(section);
    });
    qs("settings-status").textContent = (lastNote ? lastNote + ". " : "") + ((data.settings || []).length ? "Operating date " + day : "No settings");
    lastNote = "";
  }

  function save(card, status) {
    var value = readControl(card);
    var effective = (qs("settings-effective") && qs("settings-effective").value) || day;
    if (value === undefined || value === "" || (Array.isArray(value) && value.length === 0)) {
      status.textContent = "Choose a value first";
      return;
    }
    var sop = card.querySelector("textarea");
    status.textContent = "Saving...";
    fetch(API + "/api/settings", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        setting_key: card.dataset.key,
        value: value,
        effective_from: effective,
        sop_text: sop ? sop.value : ""
      })
    }).then(function (response) {
      return response.json().then(function (body) { return { ok: response.ok, body: body }; });
    }).then(function (result) {
      if (!result.ok || (result.body && result.body.ok === false)) {
        status.textContent = (result.body && result.body.error) || "Save failed";
        return;
      }
      lastNote = "Saved " + card.dataset.key;
      return load();
    }).catch(function () { status.textContent = "Save failed"; });
  }

  function load() {
    qs("settings-status").textContent = "Loading...";
    return fetch(API + "/api/auth/me", { credentials: "include" }).then(function (response) {
      return response.ok ? response.json() : {};
    }).then(function (me) {
      qs("settings-hotel").textContent = "Hotel: " + ((me && me.hotel_name) || "-");
      return fetch(API + "/api/settings", { credentials: "include" });
    }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok || body.ok === false) throw new Error((body && body.error) || "Could not load settings");
        render(body);
      });
    }).catch(function (error) {
      qs("settings-status").textContent = error.message || "Could not load settings";
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    qs("settings-refresh").addEventListener("click", load);
    load();
  });
})();
