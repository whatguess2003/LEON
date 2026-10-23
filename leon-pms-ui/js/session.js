(function () {
  var KEY = "leon_jwt";
  var HOTEL_KEY = "leon_hotel";
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

  function hotelFromToken(token) {
    var parts = String(token || "").split(".");
    if (parts.length < 2) return "";
    var body = parts[1].split("-").join("+").split("_").join("/");
    while (body.length % 4) body += "=";
    try {
      var data = JSON.parse(atob(body));
      return data.hotel_name || "";
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
      if (path.indexOf("/auth/logout") !== -1) {
        try { localStorage.removeItem(KEY); } catch (e) {}
        clearHotel();
        return response;
      }
      if (path.indexOf("/auth/signin") === -1) return response;
      return response.clone().json().then(function (data) {
        if (data && data.token) {
          try { localStorage.setItem(KEY, data.token); } catch (e) {}
        }
        var signedHotel = (data && data.hotel_name) || hotelFromToken(data && data.token);
        if (signedHotel) writeHotel(signedHotel);
        return response;
      }).catch(function () { return response; });
    });
  };
})();
