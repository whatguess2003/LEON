// Hide page-level nav when rendered inside SPA iframe
(function(){
  try {
    document.addEventListener('DOMContentLoaded', function(){
      try {
        var raw = sessionStorage.getItem('leon_reopen_modal');
        if (raw) {
          sessionStorage.removeItem('leon_reopen_modal');
          try {
            var payload = JSON.parse(raw);
            setTimeout(function(){
              try {
                if (window.AssignmentsCalendar && typeof window.AssignmentsCalendar.reopen === 'function') {
                  window.AssignmentsCalendar.reopen(payload);
                } else {
                  var evt = new CustomEvent('leon:reopen-modal', { detail: payload });
                  window.dispatchEvent(evt);
                }
              } catch(_e) {}
            }, 300);
          } catch(_) {}
        }
      } catch(_) {}
    });
    if (window.top !== window.self) {
      // Mark embedded
      try { document.documentElement.classList.add('embedded'); document.body.classList.add('embedded'); } catch(_) {}
      // Remove page-level nav entirely to avoid empty space
      var nav = document.querySelector('nav');
      if (nav && nav.parentNode) try { nav.parentNode.removeChild(nav); } catch(_) { nav.style.display='none'; }
      var header = document.querySelector('header'); if (header && header.parentNode) try { header.parentNode.removeChild(header); } catch(_) { try { header.style.display='none'; } catch(_) {} }
      var footer = document.querySelector('footer'); if (footer && footer.parentNode) try { footer.parentNode.removeChild(footer); } catch(_) { try { footer.style.display='none'; } catch(_) {} }
      var aside = document.querySelector('aside'); if (aside && aside.parentNode) try { aside.parentNode.removeChild(aside); } catch(_) { try { aside.style.display='none'; } catch(_) {} }
      // Ensure main content uses full width without extra wrappers
      var main = document.querySelector('main');
      if (main) { main.style.margin = '0'; main.style.padding = '0'; }
      document.body.style.padding = '0';
      document.body.style.margin = '0';
      // Remove other empty wrappers commonly used for top spacing
      var app = document.getElementById('app');
      if (app) { app.style.minHeight = '0'; app.style.height='auto'; app.style.padding='0'; app.style.margin='0'; }
      // If main is hidden or absent, collapse page body height
      var shouldCollapse = false;
      try { if (!main) shouldCollapse = true; else if (getComputedStyle(main).display === 'none') shouldCollapse = true; } catch(_) {}
      if (shouldCollapse) {
        document.documentElement.style.height = '0';
        document.body.style.height = '0';
        if (app) app.style.display = 'none';
      }

      // Auto-resize: report height to parent for iframe resizing
      var lastH = 0;
      var postResize = function() {
        try {
          var h = Math.max(document.documentElement.scrollHeight, document.body.scrollHeight);
          if (!h || Math.abs(h - lastH) < 2) return;
          lastH = h;
          window.top.postMessage({ type: 'EMBED_RESIZE', height: h }, '*');
        } catch(_) {}
      };
      try { postResize(); } catch(_) {}
      try { window.addEventListener('resize', function(){ postResize(); }); } catch(_) {}
      try {
        var mo = new MutationObserver(function(){ postResize(); });
        mo.observe(document.documentElement, { childList: true, subtree: true, attributes: true });
      } catch(_) {}

      // Intercept in-page navigation links and delegate to parent
      try {
        document.addEventListener('click', function(e){
          try {
            var t = e.target;
            while (t && t.tagName && t.tagName.toLowerCase() !== 'a') t = t.parentElement;
            if (!t) return;
            var href = t.getAttribute('href');
            var tgt = (t.getAttribute('target')||'').toLowerCase();
            if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
            // Only intercept same-window navigations
            if (!tgt || tgt === '_self') {
              e.preventDefault(); e.stopPropagation();
              window.top.postMessage({ type: 'EMBED_NAVIGATE', href: href }, '*');
            }
          } catch(_) {}
        }, true);
      } catch(_) {}
    }
  } catch(_) {}
})();


