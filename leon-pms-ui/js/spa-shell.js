// Simple SPA shell to keep chat persistent by loading pages into an iframe
(function(){
  var SIDEBAR_EXPANDED_W = 196;
  var SIDEBAR_COLLAPSED_W = 56; // icon-only width
  var _sidebarExpanded = false;
  function createStatus(container) {
    var status = document.getElementById('spa-status');
    if (!status) {
      status = document.createElement('div');
      status.id = 'spa-status';
      status.style.position = 'fixed';
      status.style.top = '12px';
      status.style.right = '12px';
      status.style.zIndex = '9999';
      status.style.display = 'none';
      status.style.background = '#ffffff';
      status.style.border = '1px solid rgba(0,0,0,0.1)';
      status.style.boxShadow = '0 4px 12px rgba(0,0,0,0.12)';
      status.style.borderRadius = '8px';
      status.style.padding = '8px 12px';
      status.style.fontSize = '22px';
      status.style.color = '#1e3a8a';
      var spinner = document.createElement('span');
      spinner.id = 'spa-status-spinner';
      spinner.style.display = 'inline-block';
      spinner.style.width = '40px';
      spinner.style.height = '40px';
      spinner.style.marginRight = '12px';
      spinner.style.border = '2px solid #2563eb';
      spinner.style.borderTopColor = 'transparent';
      spinner.style.borderRadius = '9999px';
      spinner.style.animation = 'spa-spin 0.8s linear infinite';
      var text = document.createElement('span');
      text.id = 'spa-status-text';
      text.style.fontWeight = '700';
      text.style.textTransform = 'uppercase';
      status.appendChild(spinner);
      status.appendChild(text);
      document.body.appendChild(status);
      try {
        var style = document.createElement('style');
        style.innerHTML = '@keyframes spa-spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}';
        document.head.appendChild(style);
      } catch(_) {}
    }
    return status;
  }

  function ensureSidebar() {
    var sidebar = document.getElementById('spa-sidebar');
    if (sidebar) return sidebar;
    sidebar = document.createElement('aside');
    sidebar.id = 'spa-sidebar';
    sidebar.style.position = 'fixed';
    sidebar.style.top = '0';
    sidebar.style.left = '0';
    sidebar.style.height = '100vh';
    sidebar.style.width = SIDEBAR_COLLAPSED_W + 'px';
    sidebar.style.background = '#ffffff';
    sidebar.style.borderRight = '1px solid rgba(0,0,0,0.06)';
    sidebar.style.boxShadow = '0 2px 12px rgba(0,0,0,0.04)';
    sidebar.style.zIndex = '9998';
    sidebar.style.display = 'flex';
    sidebar.style.flexDirection = 'column';
    sidebar.style.padding = '12px 8px';
    sidebar.style.transition = 'width 0.15s ease';
    sidebar.innerHTML = '';
    var header = document.createElement('div');
    header.style.display = 'flex';
    header.style.alignItems = 'center';
    header.style.gap = '8px';
    header.style.padding = '8px';
    header.innerHTML = '<i class="fas fa-hotel" style="color:#2563eb"></i><span class="spa-side-label" style="font-weight:700;color:#111827;">LEON PMS</span>';
    var nav = document.createElement('nav');
    nav.id = 'spa-side-links';
    nav.style.marginTop = '8px';
    nav.style.overflowY = 'auto';
    nav.style.flex = '1 1 auto';
    var openGroups = {};
    function makeLink(li) {
      var a = document.createElement('a');
      a.href = li.href;
      a.dataset.route = li.href;
      a.className = 'nav-link';
      a.style.display = 'flex';
      a.style.alignItems = 'center';
      a.style.gap = '10px';
      a.style.padding = '8px 10px';
      a.style.borderRadius = '8px';
      a.style.color = '#374151';
      a.style.textDecoration = 'none';
      a.style.fontSize = '12px';
      a.innerHTML = '<i class="fas '+li.icon+'" style="width:16px;text-align:center;color:'+li.color+';"></i><span class="spa-side-label">'+li.label+'</span>';
      a.onclick = function(e){ e.preventDefault(); window.spaNavigate(li.href, {}); };
      return a;
    }
    [
      {href:'index.html', icon:'fa-home', label:'Home', color:'#2563eb'},
      {href:'dashboard.html', icon:'fa-tachometer-alt', label:'Dashboard', color:'#8b5cf6'}
    ].forEach(function(li){ nav.appendChild(makeLink(li)); });
    var groups = [
      {id:'operations', label:'Operations', icon:'fa-tasks', color:'#3b82f6', items:[
        {href:'operations.html', icon:'fa-calendar-day', label:"Today's", color:'#3b82f6'},
        {href:'assignments.html', icon:'fa-clipboard-list', label:'Stay View', color:'#a855f7'},
        {href:'housekeeping.html', icon:'fa-broom', label:'Housekeeping', color:'#f97316'},
        {href:'maintenance.html', icon:'fa-tools', label:'Maintenance', color:'#ef4444'}
      ]},
      {id:'sales', label:'Sales', icon:'fa-handshake', color:'#f59e0b', items:[
        {href:'inventory.html', icon:'fa-boxes', label:'Inventory', color:'#f59e0b'},
        {href:'dynamic_pricing.html', icon:'fa-chart-line', label:'Dynamic Pricing', color:'#2563eb'},
        {href:'guests.html', icon:'fa-users', label:'Guests', color:'#06b6d4'},
        {href:'bookings.html', icon:'fa-calendar-check', label:'Bookings', color:'#ef4444'}
      ]},
      {id:'reports', label:'Reports', icon:'fa-chart-bar', color:'#22c55e', items:[
        {href:'analytics.html', icon:'fa-chart-line', label:'Analytics', color:'#22c55e'},
        {href:'activities.html', icon:'fa-clock', label:'Activity Logs', color:'#64748b'}
      ]},
      {id:'settings', label:'Settings', icon:'fa-cog', color:'#0ea5e9', items:[
        {href:'rooms.html', icon:'fa-bed', label:'Rooms Setup', color:'#10b981'},
        {href:'property_setup.html', icon:'fa-building', label:'Property Setup', color:'#0ea5e9'},
        {href:'ota_mapping.html', icon:'fa-random', label:'OTA Mapping', color:'#2563eb'}
      ]}
    ];
    groups.forEach(function(g){
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.dataset.groupToggle = g.id;
      btn.style.display = 'flex';
      btn.style.alignItems = 'center';
      btn.style.gap = '10px';
      btn.style.width = '100%';
      btn.style.padding = '8px 10px';
      btn.style.marginTop = '4px';
      btn.style.border = '0';
      btn.style.borderRadius = '8px';
      btn.style.background = 'transparent';
      btn.style.color = '#111827';
      btn.style.fontSize = '12px';
      btn.style.fontWeight = '700';
      btn.style.cursor = 'pointer';
      btn.style.textAlign = 'left';
      btn.innerHTML = '<i class="fas '+g.icon+'" style="width:16px;text-align:center;color:'+g.color+';"></i><span class="spa-side-label">'+g.label+'</span><span class="spa-side-label spa-caret" style="margin-left:auto;">▶</span>';
      var list = document.createElement('div');
      list.className = 'spa-group-items';
      list.dataset.group = g.id;
      list.style.display = 'none';
      list.style.paddingLeft = '8px';
      g.items.forEach(function(li){ list.appendChild(makeLink(li)); });
      btn.onclick = function(){
        openGroups[g.id] = !openGroups[g.id];
        if (!_sidebarExpanded) setSidebarExpanded(true);
        applyGroupVisibility();
      };
      nav.appendChild(btn);
      nav.appendChild(list);
    });
    sidebar._openGroups = openGroups;
    sidebar._applyGroupVisibility = function(){ applyGroupVisibility(); };
    sidebar.appendChild(header);
    sidebar.appendChild(nav);
    // Profile footer with manager/hotel + logout
    var footer = document.createElement('div');
    footer.id = 'spa-side-footer';
    footer.style.borderTop = '1px solid rgba(0,0,0,0.06)';
    footer.style.padding = '8px';
    footer.style.fontSize = '11px';
    var prof = document.createElement('div');
    prof.id = 'spa-side-profile';
    prof.innerHTML = '<div class="spa-side-label" style="font-weight:600;color:#111827;">&nbsp;</div>\
<div class="spa-side-label" style="color:#6b7280;">&nbsp;</div>';
    var ctr = document.createElement('div');
    ctr.style.marginTop = '6px';
    var btn = document.createElement('button');
    btn.id = 'spa-side-logout';
    btn.className = 'spa-side-label';
    btn.textContent = 'Sign Out';
    btn.style.background = '#ef4444';
    btn.style.color = '#ffffff';
    btn.style.border = '0';
    btn.style.borderRadius = '6px';
    btn.style.padding = '6px 10px';
    btn.style.fontSize = '11px';
    btn.style.cursor = 'pointer';
    btn.onclick = async function(){
      try {
        var api = (location.hostname==='localhost'||location.hostname==='127.0.0.1')?'http://localhost:8002':'https://api.inap.ai';
        await fetch(api + '/api/auth/logout', { method: 'POST', credentials: 'include' });
      } catch(_) {}
      window.location.href = 'login.html';
    };
    ctr.appendChild(btn);
    footer.appendChild(prof);
    footer.appendChild(ctr);
    sidebar.appendChild(footer);
    document.body.appendChild(sidebar);
    // Hide existing top nav if present to prevent duplication
    try { var topnav = document.querySelector('nav'); if (topnav) topnav.style.display = 'none'; } catch(_) {}
    // Collapse labels initially
    setSidebarExpanded(false);
    // Hover to expand/collapse
    sidebar.addEventListener('mouseenter', function(){ setSidebarExpanded(true); });
    sidebar.addEventListener('mouseleave', function(){ setSidebarExpanded(false); });
    return sidebar;
  }

  function ensureFrame() {
    var frame = document.getElementById('app-frame');
    if (!frame) {
      ensureSidebar();
      var container = document.getElementById('spa-frame-container');
      if (!container) {
        container = document.createElement('div');
        container.id = 'spa-frame-container';
        container.style.margin = '0';
        container.style.padding = '0';
        container.style.position = 'relative';
        container.style.overflow = 'visible';
        // Universal zoom wrapper
        var zoomWrap = document.getElementById('spa-zoom-wrap');
        if (!zoomWrap) {
          zoomWrap = document.createElement('div');
          zoomWrap.id = 'spa-zoom-wrap';
          zoomWrap.style.transformOrigin = '0 0';
          zoomWrap.style.width = '100%';
          zoomWrap.style.height = 'auto';
          zoomWrap.style.willChange = 'transform';
          zoomWrap.style.overflow = 'visible';
        }
        // Insert right after top nav so iframe sits directly under the navbar
        // Place container at body top and shift right for sidebar
        document.body.insertBefore(container, document.body.firstChild);
        container.appendChild(zoomWrap);
        createStatus(container);
      }
      frame = document.createElement('iframe');
      frame.id = 'app-frame';
      frame.style.width = '100%';
      frame.style.border = '0';
      frame.style.display = 'block';
      frame.style.zIndex = '1';
      var wrap = document.getElementById('spa-zoom-wrap') || container;
      wrap.appendChild(frame);
      applyZoom();
      try { frame.addEventListener('load', function(){ try { this.contentWindow && this.contentWindow.scrollTo(0,0); } catch(_) {} }); } catch(_) {}
    }
    sizeFrame();
    return frame;
  }

  function sizeFrame() {
    var frame = document.getElementById('app-frame');
    if (!frame) return;
    ensureSidebar();
    var sidebar = document.getElementById('spa-sidebar');
    var sideW = sidebar ? sidebar.getBoundingClientRect().width : 0;
    var navH = 0;
    // Compensate for zoom scale so modal space matches viewport
    var stored = null; try { stored = localStorage.getItem('LEON_UI_SCALE'); } catch(_) {}
    var scale = stored ? parseFloat(stored) : 0.75;
    if (!scale || scale <= 0 || scale > 1) scale = 0.75;
    var avail = Math.max(0, (window.innerHeight || 0) - navH);
    frame.style.height = (avail / scale) + 'px';
    // Shift container by collapsed width only to avoid layout shift on hover expand
    try { var container = document.getElementById('spa-frame-container'); if (container) { container.style.marginLeft = (typeof SIDEBAR_COLLAPSED_W !== 'undefined' ? SIDEBAR_COLLAPSED_W : 56) + 'px'; } } catch(_) {}
  }

  function setVisible(useFrame) {
    var frame = document.getElementById('app-frame');
    var main = document.querySelector('main');
    if (useFrame) {
      if (main) main.style.display = 'none';
      if (frame) frame.style.display = 'block';
    } else {
      if (frame) frame.style.display = 'none';
      if (main) main.style.display = '';
    }
  }

  function buildUrl(route, params) {
    var url = route || '/index.html';
    var p = new URLSearchParams(params || {});
    return url + (p.toString() ? ('?' + p.toString()) : '');
  }

  function updateActiveNav(route) {
    try {
      var links = document.querySelectorAll('#spa-sidebar nav a[data-route]');
      if (!links) return;
      links.forEach(function(a){
        var base = a.getAttribute('data-route') || '';
        // normalize
        var target = (route || '').replace(/^\//, '');
        var href = base.replace(/^\//, '');
        // reset styles
        a.style.backgroundColor = '';
        a.style.color = '#374151';
        // set active on match
        if ((target === '' && href.indexOf('index.html') !== -1) || (href && target && href.indexOf(target) !== -1)) {
          a.style.backgroundColor = 'rgba(59, 130, 246, 0.15)';
          a.style.color = '#1d4ed8';
          var list = a.parentElement;
          if (list && list.dataset && list.dataset.group) {
            var sidebar = document.getElementById('spa-sidebar');
            if (sidebar && sidebar._openGroups) sidebar._openGroups[list.dataset.group] = true;
          }
        }
      });
      applyGroupVisibility();
    } catch(_) {}
  }

  function applyGroupVisibility() {
    try {
      var sidebar = document.getElementById('spa-sidebar');
      if (!sidebar || !sidebar._openGroups) return;
      var lists = sidebar.querySelectorAll('.spa-group-items');
      lists.forEach(function(list){
        var id = list.dataset.group;
        var open = !!(_sidebarExpanded && sidebar._openGroups[id]);
        list.style.display = open ? 'block' : 'none';
        var btn = sidebar.querySelector('button[data-group-toggle="'+id+'"]');
        var caret = btn ? btn.querySelector('.spa-caret') : null;
        if (caret) caret.textContent = sidebar._openGroups[id] ? '▼' : '▶';
      });
    } catch(_) {}
  }

  function hookNavLinks() {
    document.addEventListener('click', function(e){
      var a = e.target && (e.target.closest ? e.target.closest('a[href]') : null);
      if (!a) return;
      var href = a.getAttribute('href');
      if (!href) return;
      if (href.startsWith('http') || href.startsWith('https:') || href.startsWith('mailto:') || href.startsWith('#')) return;
      e.preventDefault();
      window.spaNavigate(href, {});
    });
  }

  window.spaNavigate = function(route, params) {
    try {
      var frame = ensureFrame();
      var url = buildUrl(route, params);
      if (route && route.indexOf('index.html') !== -1) {
        setVisible(false);
        updateActiveNav('');
        return;
      }
      setVisible(true);
      try { window.scrollTo(0,0); } catch(_) {}
      applyZoom();
      updateActiveNav(route || '');
      if (frame.src !== url) frame.src = url;
      try { if (window.leonChat && window.leonChat.showPageDescription) { window.leonChat.showPageDescription(route); } } catch(_) {}
    } catch(_) {}
  };

  // Extended navigate with reload option
  window.spaNavigateEx = function(route, params, opts) {
    try {
      var frame = ensureFrame();
      var url = buildUrl(route, params);
      if (route && route.indexOf('index.html') !== -1) {
        setVisible(false);
        updateActiveNav('');
        return;
      }
      setVisible(true);
      try { window.scrollTo(0,0); } catch(_) {}
      updateActiveNav(route || '');
      var doReload = opts && opts.reload;
      if (doReload) {
        try { frame.contentWindow.location.reload(); }
        catch(e) { frame.src = url; }
        try { if (window.leonChat && window.leonChat.showPageDescription) { window.leonChat.showPageDescription(route); } } catch(_) {}
        return;
      }
      if (frame.src !== url) frame.src = url;
      try { if (window.leonChat && window.leonChat.showPageDescription) { window.leonChat.showPageDescription(route); } } catch(_) {}
    } catch(_) {}
  };

  window.spaShowStatus = function(message, type) {
    try {
      var status = createStatus();
      var spinner = document.getElementById('spa-status-spinner');
      var text = document.getElementById('spa-status-text');
      if (!status || !spinner || !text) return;
      if (type === 'success') {
        spinner.style.display = 'none';
        text.style.color = '#047857';
      } else {
        spinner.style.display = 'inline-block';
        text.style.color = '#1e3a8a';
      }
      text.textContent = message || (type === 'success' ? 'Update successful' : 'Updating...');
      status.style.display = 'flex';
      if (type === 'success') {
        setTimeout(function(){ try{ status.style.display = 'none'; }catch(_){} }, 5000);
      }
    } catch(_) {}
  };

  window.spaHideStatus = function() {
    try {
      var status = document.getElementById('spa-status');
      if (status) status.style.display = 'none';
    } catch(_) {}
  };

  window.spaReload = function() {
    try {
      var frame = ensureFrame();
      setVisible(true);
      updateActiveNav('');
      try { frame.contentWindow.location.reload(); }
      catch(e) { frame.src = frame.src; }
    } catch(_) {}
  };

  window.addEventListener('resize', sizeFrame);
  window.addEventListener('resize', function(){ try{ applyZoom(); }catch(_){} });
  document.addEventListener('DOMContentLoaded', function(){
    try { document.body.classList.add('spa-active'); } catch(_) {}
    ensureSidebar();
    ensureFrame();
    hookNavLinks();
    // set active based on current page at load
    updateActiveNav('');
    try { applyZoom(); } catch(_) {}
    try { updateSidebarProfile(); } catch(_) {}
    // Auto-route index.html (or root) into SPA default (dashboard)
    try {
      var path = (location.pathname || '').toLowerCase();
      if (path.endsWith('/') || path.endsWith('/index.html') || path.endsWith('index.html')) {
        window.spaNavigate('dashboard.html', {});
      }
    } catch(_) {}
  });

  function setSidebarExpanded(expanded) {
    try {
      _sidebarExpanded = !!expanded;
      var sidebar = document.getElementById('spa-sidebar');
      if (!sidebar) return;
      sidebar.style.width = (_sidebarExpanded ? SIDEBAR_EXPANDED_W : SIDEBAR_COLLAPSED_W) + 'px';
      var labels = sidebar.querySelectorAll('.spa-side-label');
      labels.forEach(function(el){
        if (_sidebarExpanded) {
          // Keep nav/header spans inline; force profile DIVs to block so they appear on separate rows
          var isSpan = (el.tagName || '').toUpperCase() === 'SPAN';
          el.style.display = isSpan ? 'inline' : 'block';
        } else {
          el.style.display = 'none';
        }
      });
      sizeFrame();
      applyGroupVisibility();
    } catch(_) {}
  }

  async function updateSidebarProfile() {
    try {
      var api = (location.hostname==='localhost'||location.hostname==='127.0.0.1')?'http://localhost:8002':'https://api.inap.ai';
      var res = await fetch(api + '/api/auth/me', { credentials: 'include' });
      if (!res.ok) return;
      var data = await res.json();
      var prof = document.getElementById('spa-side-profile');
      if (!prof) return;
      var hotel = (data.hotel_name || '').toString().toUpperCase();
      var manager = (data.manager_name || '').toString().toUpperCase();
      var html = '';
      html += '<div class="spa-side-label" style="font-weight:700;color:#111827;">'+ (hotel || '') +'</div>';
      html += '<div class="spa-side-label" style="color:#6b7280;">'+ (manager || '') +'</div>';
      prof.innerHTML = html;
    } catch(_) {}
  }

  function applyZoom() {
    try {
      var wrap = document.getElementById('spa-zoom-wrap');
      if (!wrap) return;
      // Read user preference (0.67 or 0.75), default 0.75
      var stored = localStorage.getItem('LEON_UI_SCALE');
      var scale = stored ? parseFloat(stored) : 0.75;
      if (!scale || scale <= 0 || scale > 1) scale = 0.75;
      // Prefer CSS zoom when supported
      wrap.style.zoom = '';
      var supportsZoom = 'zoom' in wrap.style;
      if (supportsZoom) {
        wrap.style.zoom = String(scale);
        // Reset transform if previously set
        wrap.style.transform = '';
        wrap.style.width = '100%';
      } else {
        // Fallback: scale with width compensation
        wrap.style.transform = 'scale(' + scale + ')';
        wrap.style.width = (100 / scale) + '%';
      }
      // Recompute iframe height based on scale
      sizeFrame();
    } catch(_) {}
  }
})();


