(function () {
  var KEY = "leon_jwt";
  var original = window.fetch.bind(window);

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
        return response;
      }
      if (path.indexOf("/auth/signin") === -1) return response;
      return response.clone().json().then(function (data) {
        if (data && data.token) {
          try { localStorage.setItem(KEY, data.token); } catch (e) {}
        }
        return response;
      }).catch(function () { return response; });
    });
  };
})();
