(function () {
  var KEY = "leon_jwt";
  var HOTEL_KEY = "leon_hotel";
  var HOTEL_ID_KEY = "leon_hotel_id";
  var API_ORIGIN = (location.hostname === "localhost" || location.hostname === "127.0.0.1") ? "http://localhost:8002" : "https://api.inap.ai";
  var original = window.fetch.bind(window);

  function writeHotel(hotel) {
    if (!hotel) return;
    var secure = location.protocol === "https:" ? "; Secure" : "";
    document.cookie = HOTEL_KEY + "=" + encodeURIComponent(hotel) + "; path=/; max-age=604800; SameSite=Lax" + secure;
    try { localStorage.setItem(HOTEL_KEY, hotel); } catch (e) {}
  }

  function clearHotel() {
    document.cookie = HOTEL_KEY + "=; path=/; max-age=0; SameSite=Lax";
    try { localStorage.removeItem(HOTEL_KEY); } catch (e) {}
  }

  function readHotel() {
    var parts = document.cookie.split(";");
    for (var i = 0; i < parts.length; i++) {
      var part = parts[i].trim();
      if (part.indexOf(HOTEL_KEY + "=") === 0) return decodeURIComponent(part.slice(HOTEL_KEY.length + 1));
    }
    return "";
  }

  function claim(token, key) {
    var parts = String(token || "").split(".");
    if (parts.length < 2) return "";
    var body = parts[1].split("-").join("+").split("_").join("/");
    while (body.length % 4) body += "=";
    try {
      var data = JSON.parse(atob(body));
      return data[key] || "";
    } catch (e) {
      return "";
    }
  }

  function hotelFromToken(token) {
    return claim(token, "hotel_name");
  }

  // The hotel_id is kept with the hotel name it belongs to, so a stored id never outlives a hotel switch.
  function writeHotelId(id, hotel) {
    if (!id || !hotel) return;
    try { localStorage.setItem(HOTEL_ID_KEY, JSON.stringify({ hotel: hotel, id: id })); } catch (e) {}
  }

  function readHotelId() {
    try {
      var saved = JSON.parse(localStorage.getItem(HOTEL_ID_KEY) || "{}");
      return saved.hotel && saved.hotel === readHotel() ? (saved.id || "") : "";
    } catch (e) {
      return "";
    }
  }

  var token = "";
  try { token = localStorage.getItem(KEY) || ""; } catch (e) {}
  var hotel = readHotel();
  try { if (!hotel) hotel = localStorage.getItem(HOTEL_KEY) || ""; } catch (e) {}
  if (!hotel && token) hotel = hotelFromToken(token);
  if (hotel) writeHotel(hotel);
  window.leonHotel = readHotel;

  // Session hotel_id for /api/hotels/{hotel_id}/... paths: the stored one, else read once from /api/auth/me.
  window.leonHotelId = function () {
    var known = readHotelId();
    if (known) return Promise.resolve(known);
    return window.fetch(API_ORIGIN + "/api/auth/me", { credentials: "include" })
      .then(function (response) { return response.ok ? response.json() : {}; })
      .then(function (me) {
        if (me && me.hotel_id) writeHotelId(me.hotel_id, me.hotel_name || readHotel());
        return (me && me.hotel_id) || "";
      });
  };

  function apiUrl(input) {
    if (typeof input === "string") return input;
    if (input && input.url) return input.url;
    return "";
  }

  function isApi(url) {
    return url.indexOf("api.inap.ai") !== -1 || url.indexOf("localhost:8002") !== -1 || url.indexOf("127.0.0.1:8002") !== -1;
  }

  window.fetch = function (input, init) {
    var url = apiUrl(input);
    if (!isApi(url)) return original(input, init);
    var next = init ? Object.assign({}, init) : {};
    var headers = new Headers(next.headers || (typeof input !== "string" && input ? input.headers : undefined));
    var token = "";
    try { token = localStorage.getItem(KEY) || ""; } catch (e) {}
    if (token && !headers.has("Authorization")) headers.set("Authorization", "Bearer " + token);
    next.headers = headers;
    if (!next.credentials) next.credentials = "include";
    return original(input, next).then(function (response) {
      var path = url.split("?")[0];
      if (path.indexOf("/auth/logout") !== -1 || path.indexOf("/auth/signout") !== -1) {
        try { localStorage.removeItem(KEY); localStorage.removeItem(HOTEL_ID_KEY); } catch (e) {}
        clearHotel();
        return response;
      }
      if (response.status === 401) {
        var page = (location.pathname || "").split("/").pop();
        if (page !== "login.html" && page !== "onboarding.html") {
          try { localStorage.removeItem(KEY); localStorage.removeItem(HOTEL_ID_KEY); } catch (e) {}
          clearHotel();
          (window.top || window).location.href = "login.html";
        }
        return response;
      }
      var storesToken = path.indexOf("/auth/signin") !== -1 || path.indexOf("/auth/switch_hotel") !== -1 || path.indexOf("/auth/signup") !== -1;
      if (!storesToken) return response;
      return response.clone().json().then(function (data) {
        if (data && data.token) {
          try { localStorage.setItem(KEY, data.token); } catch (e) {}
        }
        var signedHotel = (data && data.hotel_name) || hotelFromToken(data && data.token);
        if (signedHotel) writeHotel(signedHotel);
        writeHotelId((data && data.hotel_id) || claim(data && data.token, "hotel_id"), signedHotel);
        return response;
      }).catch(function () { return response; });
    });
  };
})();
