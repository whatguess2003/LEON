// Maintenance Page Component
(function(){
  var API_ROOT = (location.hostname==='localhost'||location.hostname==='127.0.0.1') ? 'http://localhost:8002' : 'https://api.inap.ai';
  var HOTEL = '';
  var CACHE = { rawAll: '', blocksAll: [], hotel: '' };
  var SCHEDULES = { items: [] };

  function qs(id){ return document.getElementById(id); }
  function debounce(fn, wait){ var t; return function(){ var ctx=this, args=arguments; clearTimeout(t); t=setTimeout(function(){ fn.apply(ctx,args); }, wait); }; }

  async function initHotel(){
    try { var m=document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/); if(m&&m[1]) HOTEL = decodeURIComponent(m[1]); } catch(_) {}
    if (!HOTEL) {
      try { var res = await fetch(API_ROOT + '/api/auth/me', { credentials:'include' }); if(res.ok){ var d=await res.json(); HOTEL = (d && d.hotel_name) ? d.hotel_name : ''; } } catch(_) {}
    }
    var label = qs('mnt-hotel-name'); if (label) label.textContent = 'Hotel: ' + (HOTEL || '-');
  }

  async function triggerPmNow(){
    try {
      var hotel = HOTEL || '';
      if (!hotel) return;
      await fetch(API_ROOT + '/api/maintenance/pm_run_now?hotel_name=' + encodeURIComponent(hotel), { method:'POST', credentials:'include' });
    } catch(_) {}
  }

  // ----- Scheduled Maintenance -----
  async function loadSchedules(){
    try {
      var hotel = HOTEL; if (!hotel) return;
      var showInactive = !!(qs('sched-show-inactive') && qs('sched-show-inactive').checked);
      var url = API_ROOT + '/api/maintenance/schedules/read?hotel_name=' + encodeURIComponent(hotel) + '&active_only=' + (showInactive? 'false':'true') + '&limit=200';
      var res = await fetch(url, { credentials: 'include' });
      var data = await res.json();
      SCHEDULES = (data && typeof data === 'object') ? data : { items: [] };
      renderSchedules();
    } catch(_) { renderSchedules(true); }
  }

  function renderSchedules(isError){
    var el = qs('mnt-schedules'); if (!el) return;
    if (isError){ el.textContent = 'Failed to load.'; return; }
    var items = Array.isArray(SCHEDULES.items) ? SCHEDULES.items : [];
    if (!items.length){ el.textContent = 'No schedules.'; return; }
    // Group by issue
    var groups = {};
    items.forEach(function(it){
      var key = String(it.issue || '').toUpperCase();
      if (!groups[key]) groups[key] = [];
      groups[key].push(it);
    });
    var issueKeys = Object.keys(groups).sort();
    var html = issueKeys.map(function(issue){
      var key = issue.replace(/[^A-Z0-9_]/g,'_');
      var list = groups[issue];
      var header = '<div class="flex items-center justify-between mt-2">'
                 +   '<button class="iss-toggle font-semibold text-gray-800 flex items-center" data-iss="'+key+'">'
                 +     '<i class="fas fa-chevron-right mr-2" data-icon="'+key+'"></i>'
                 +     issue + ' ('+ list.length +')'
                 +   '</button>'
                 + '</div>';
      var rows = list.map(function(it){
        var tgt = it.target_type === 'ROOM' ? (it.room_number||'') : (it.area||'');
        var due = it.next_due_date || '-';
        var id = it.schedule_id || '';
        var badge = (it.active ? '<span class="ml-2 px-2 py-0.5 text-[10px] rounded bg-green-50 border border-green-200 text-green-700">active</span>'
                               : '<span class="ml-2 px-2 py-0.5 text-[10px] rounded bg-red-50 border border-red-200 text-red-700">inactive</span>');
        return '<div class="flex items-center justify-between bg-white border rounded p-2 mb-2">'
             +   '<div class="space-x-2"><button class="px-2 py-1 text-xs border rounded" data-ed="'+id+'">Edit</button></div>'
             +   '<div class="flex-1 ml-2"><div>'+ (it.urgency||'') +' • every '+ (it.interval_days||'-') +' days '+ badge +'</div>'
             +   '<div class="text-gray-600">'+ (it.target_type||'') +': '+ tgt +' • next due: '+ due +'</div></div>'
             + '</div>';
      }).join('');
      return header + '<div class="iss-body hidden" data-body="'+key+'">' + rows + '</div>';
    }).join('');
    el.innerHTML = html;
    // Bind edit buttons
    Array.from(el.querySelectorAll('button[data-ed]')).forEach(function(btn){
      btn.addEventListener('click', function(){ openScheduleModal(btn.getAttribute('data-ed')); });
    });
    // Bind issue toggles
    Array.from(el.querySelectorAll('button.iss-toggle')).forEach(function(btn){
      btn.addEventListener('click', function(){
        var k = btn.getAttribute('data-iss');
        var body = el.querySelector('[data-body="'+k+'"]');
        var icon = el.querySelector('i[data-icon="'+k+'"]');
        if (!body) return;
        if (body.classList.contains('hidden')){
          body.classList.remove('hidden');
          if (icon){ icon.classList.remove('fa-chevron-right'); icon.classList.add('fa-chevron-down'); }
        } else {
          body.classList.add('hidden');
          if (icon){ icon.classList.remove('fa-chevron-down'); icon.classList.add('fa-chevron-right'); }
        }
      });
    });
  }

  function openScheduleModal(scheduleId){
    var modal = qs('sched-modal'); if (!modal) return;
    var sid = qs('sch-id'); var r = qs('sch-room'); var a = qs('sch-area');
    var issue = qs('sch-issue'); var urg = qs('sch-urgency'); var iv = qs('sch-interval');
    var st = qs('sch-start'); var win = qs('sch-window'); var ooo = qs('sch-ooo'); var act = qs('sch-active');
    // Reset
    sid.value = ''; r.value = ''; a.value = ''; issue.value = ''; urg.value='NORMAL'; iv.value='90'; st.value=''; win.value=''; ooo.checked=false; act.checked=true;
    if (scheduleId){
      var it = (SCHEDULES.items||[]).find(function(x){ return String(x.schedule_id||'')===String(scheduleId); });
      if (it){
        sid.value = it.schedule_id||'';
        if ((it.target_type||'')==='ROOM'){ r.value = it.room_number||''; a.value=''; } else { a.value = it.area||''; r.value=''; }
        issue.value = it.issue||''; urg.value = it.urgency||'NORMAL'; iv.value = it.interval_days||'90'; st.value = it.start_date||''; win.value = it.preferred_window||''; ooo.checked = !!it.auto_out_of_order; act.checked = !!it.active;
      }
    }
    modal.classList.remove('hidden');
  }

  async function saveSchedule(){
    var hotel = HOTEL; if (!hotel){ alert('Hotel required'); return; }
    var sid = qs('sch-id').value.trim(); var r = qs('sch-room').value.trim(); var a = qs('sch-area').value.trim();
    var issue = qs('sch-issue').value.trim(); var urg = qs('sch-urgency').value; var iv = parseInt(qs('sch-interval').value||'90',10);
    var st = qs('sch-start').value.trim(); var win = qs('sch-window').value.trim(); var ooo = qs('sch-ooo').checked; var act = qs('sch-active').checked;
    var url = ''; var payload = {};
    if (sid){
      url = API_ROOT + '/api/maintenance/schedules/update';
      payload = { hotel_name: hotel, schedule_id: sid, room_number: r||undefined, area: a||undefined, issue: issue||undefined, urgency: urg||undefined, interval_days: isNaN(iv)?undefined:iv, start_date: st||undefined, preferred_window: win||undefined, auto_out_of_order: ooo, active: act };
    } else {
      url = API_ROOT + '/api/maintenance/schedules/setup';
      payload = { hotel_name: hotel, room_number: r||undefined, area: a||undefined, issue: issue, urgency: urg, interval_days: isNaN(iv)?90:iv, start_date: st, preferred_window: win||undefined, auto_out_of_order: ooo, active: act };
    }
    var res = await fetch(url, { method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
    var data = await res.json();
    if (!res.ok || data.error){ alert(data.error || 'Save failed'); return; }
    qs('sched-modal').classList.add('hidden');
    await loadSchedules();
  }

  async function populateRooms(){
    try {
      var sel = qs('mnt-room'); if (!sel) return;
      sel.innerHTML = '';
      var optAll = document.createElement('option'); optAll.value = 'ALL'; optAll.textContent = 'ALL'; sel.appendChild(optAll);
      var optOthers = document.createElement('option'); optOthers.value = 'OTHERS'; optOthers.textContent = 'OTHERS'; sel.appendChild(optOthers);
      var res = await fetch(API_ROOT + '/api/hotels/rooms?hotel_name=' + encodeURIComponent(HOTEL), { credentials:'include' });
      var data = await res.json();
      var nums = [];
      try { (data.room_types||[]).forEach(function(rt){ (rt.room_numbers||[]).forEach(function(rn){ if (rn && rn.room_number) nums.push(String(rn.room_number)); }); }); } catch(_) {}
      nums = Array.from(new Set(nums)).sort(function(a,b){ return String(a).localeCompare(String(b), undefined, {numeric:true}); });
      nums.forEach(function(n){ var o=document.createElement('option'); o.value=n; o.textContent=n; sel.appendChild(o); });
      sel.selectedIndex = 0;
    } catch(_) {}
  }

  function filterRoomOptions(){
    try {
      var q = (qs('room-search').value || '').toLowerCase();
      var sel = qs('mnt-room'); if (!sel) return;
      Array.from(sel.options).forEach(function(o, idx){
        if (idx < 2) { o.hidden = false; return; }
        var show = !q || String(o.textContent||'').toLowerCase().indexOf(q) !== -1;
        o.hidden = !show;
      });
    } catch(_) {}
  }

  async function loadList(){
    var hotel = HOTEL; if (!hotel){ return; }
    var roomSel = qs('mnt-room');
    var room = roomSel ? String(roomSel.value || '').trim() : '';
    // Always fetch ALL and cache; room filtering happens client-side
    var url = API_ROOT + '/api/maintenance/list?hotel_name=' + encodeURIComponent(hotel);
    var res = await fetch(url, { credentials: 'include' });
    var data = await res.json();
    var raw = (data && data.result) ? String(data.result) : '';
    var blocksAll = raw.split(/\n\s*\n/).filter(function(b){ return b.trim().length>0; });
    CACHE = { rawAll: raw, blocksAll: blocksAll, hotel: hotel };
    await populateTasksFromCache();
    renderFromCache();
  }

  function renderFromCache(){
    try {
      var sort = (qs('mnt-sort')?.value || 'recent');
      var statusFilter = (qs('mnt-status')?.value || 'ALL');
      var urgencyFilter = (qs('mnt-urgency')?.value || 'ALL');
      var roomSel = qs('mnt-room');
      var room = roomSel ? String(roomSel.value || '').trim() : '';
      var isOthers = (room === 'OTHERS');
      var allBlocks = (CACHE && Array.isArray(CACHE.blocksAll)) ? CACHE.blocksAll.slice() : [];
      // Split into active vs completed vs cancelled using explicit current_status line
      function statusOf(block){
        try {
          var m = block.match(/^\s*current_status\s*:\s*([A-Z_]+)/mi);
          if (m && m[1]) return String(m[1]).toUpperCase();
        } catch(_) {}
        return '';
      }
      var activeBlocks = [], completedBlocks = [], cancelledBlocks = [];
      allBlocks.forEach(function(b){
        var st = statusOf(b);
        if (st === 'COMPLETED') completedBlocks.push(b);
        else if (st === 'CANCELLED') cancelledBlocks.push(b);
        else activeBlocks.push(b);
      });
      // Apply status filter to active set
      if (statusFilter && statusFilter !== 'ALL') {
        var reStatus = new RegExp("\\n\\s*current_status:\\s*" + statusFilter.replace(/[-/\\^$*+?.()|[\]{}]/g,'\\$&'), 'i');
        activeBlocks = activeBlocks.filter(function(b){ return reStatus.test(b); });
      }
      // Apply urgency filter to active set (robust against emoji and formatting)
      if (urgencyFilter && urgencyFilter !== 'ALL') {
        activeBlocks = activeBlocks.filter(function(b){
          var m = b.match(/\b(URGENT|HIGH|NORMAL|LOW)\b/i);
          return m && m[1] && m[1].toUpperCase() === urgencyFilter;
        });
      }
      var outBlocks = activeBlocks;
      if (room && room !== 'ALL' && !isOthers) {
        var re = new RegExp("\\n\\s*room:\\s*" + room.replace(/[-/\\^$*+?.()|[\]{}]/g,'\\$&') + "\\s*$", 'mi');
        outBlocks = outBlocks.filter(function(b){ return re.test(b); });
      } else if (isOthers) {
        outBlocks = outBlocks.filter(function(b){ return /\n\s*room:\s*N\/A/i.test(b); });
      }
      if (sort === 'oldest') {
        outBlocks.reverse();
      } else if (sort === 'urgency') {
        var rank = { 'URGENT': 1, 'HIGH': 2, 'NORMAL': 3, 'LOW': 4 };
        outBlocks.sort(function(a,b){
          var ua = (a.match(/\|\s*(URGENT|HIGH|NORMAL|LOW)/) || [,'NORMAL'])[1];
          var ub = (b.match(/\|\s*(URGENT|HIGH|NORMAL|LOW)/) || [,'NORMAL'])[1];
          return (rank[ua]||9) - (rank[ub]||9);
        });
      }
      var html = outBlocks.map(function(block){
        // Bold the explicit "task id:" line only
        var lines = block.split(/\n/);
        lines = lines.map(function(line){
          if (/^\s*task id:\s*/i.test(line)) {
            return '<span class="font-semibold">' + line.replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</span>';
          }
          return line.replace(/</g,'&lt;').replace(/>/g,'&gt;');
        });
        return lines.join('\n');
      }).join('\n\n');
      qs('mnt-list').innerHTML = html || 'No data';
      // Completed: apply room + sort only (ignore status/urgency filters)
      var completedFiltered = completedBlocks.slice();
      if (room && room !== 'ALL' && !isOthers) {
        var reC = new RegExp("\\n\\s*room:\\s*" + room.replace(/[-/\\^$*+?.()|[\]{}]/g,'\\$&') + "\\s*$", 'mi');
        completedFiltered = completedFiltered.filter(function(b){ return reC.test(b); });
      } else if (isOthers) {
        completedFiltered = completedFiltered.filter(function(b){ return /\n\s*room:\s*N\/A/i.test(b); });
      }
      if (sort === 'oldest') {
        completedFiltered.reverse();
      } else if (sort === 'urgency') {
        var rankC = { 'URGENT': 1, 'HIGH': 2, 'NORMAL': 3, 'LOW': 4 };
        completedFiltered.sort(function(a,b){
          var ua = (a.match(/\|\s*(URGENT|HIGH|NORMAL|LOW)/) || [,'NORMAL'])[1];
          var ub = (b.match(/\|\s*(URGENT|HIGH|NORMAL|LOW)/) || [,'NORMAL'])[1];
          return (rankC[ua]||9) - (rankC[ub]||9);
        });
      }
      var compHtml = completedFiltered.map(function(block){
        var lines = block.split(/\n/).map(function(line){
          if (/^\s*task id:\s*/i.test(line)) {
            return '<span class="font-semibold">' + line.replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</span>';
          }
          return line.replace(/</g,'&lt;').replace(/>/g,'&gt;');
        });
        return lines.join('\n');
      }).join('\n\n');
      var compEl = qs('mnt-completed'); if (compEl) compEl.innerHTML = compHtml || 'No completed requests.';
      // Cancelled: apply room + sort only (ignore status/urgency filters)
      var cancelledFiltered = cancelledBlocks.slice();
      if (room && room !== 'ALL' && !isOthers) {
        var reK = new RegExp("\\n\\s*room:\\s*" + room.replace(/[-/\\^$*+?.()|[\]{}]/g,'\\$&') + "\\s*$", 'mi');
        cancelledFiltered = cancelledFiltered.filter(function(b){ return reK.test(b); });
      } else if (isOthers) {
        cancelledFiltered = cancelledFiltered.filter(function(b){ return /\n\s*room:\s*N\/A/i.test(b); });
      }
      if (sort === 'oldest') {
        cancelledFiltered.reverse();
      } else if (sort === 'urgency') {
        var rankK = { 'URGENT': 1, 'HIGH': 2, 'NORMAL': 3, 'LOW': 4 };
        cancelledFiltered.sort(function(a,b){
          var ua = (a.match(/\|\s*(URGENT|HIGH|NORMAL|LOW)/) || [,'NORMAL'])[1];
          var ub = (b.match(/\|\s*(URGENT|HIGH|NORMAL|LOW)/) || [,'NORMAL'])[1];
          return (rankK[ua]||9) - (rankK[ub]||9);
        });
      }
      var cancHtml = cancelledFiltered.map(function(block){
        var lines = block.split(/\n/).map(function(line){
          if (/^\s*task id:\s*/i.test(line)) {
            return '<span class="font-semibold">' + line.replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</span>';
          }
          return line.replace(/</g,'&lt;').replace(/>/g,'&gt;');
        });
        return lines.join('\n');
      }).join('\n\n');
      var cancEl = qs('mnt-cancelled'); if (cancEl) cancEl.innerHTML = cancHtml || 'No cancelled requests.';
    } catch(_) {
      // fallback to direct load
      qs('mnt-list').textContent = (CACHE.rawAll || '') || 'No data';
    }
  }

  async function populateTasksFromCache(){
    try {
      var sel = qs('upd-task'); if (!sel) return;
      var hotel = HOTEL; if (!hotel) return;
      if (!CACHE || !Array.isArray(CACHE.blocksAll) || !CACHE.blocksAll.length) return;
      // Helper: extract current_status
      function statusOf(block){
        try { var m = block.match(/^\s*current_status\s*:\s*([A-Z_]+)/mi); if (m && m[1]) return String(m[1]).toUpperCase(); } catch(_) {}
        return '';
      }
      // Filter to active (exclude COMPLETED/CANCELLED)
      var blocks = CACHE.blocksAll.filter(function(b){ var st = statusOf(b); return st !== 'COMPLETED' && st !== 'CANCELLED'; });
      // Apply room filter selection
      var roomSel = qs('mnt-room'); var room = roomSel ? String(roomSel.value||'').trim() : '';
      var isOthers = (room === 'OTHERS');
      if (room && room !== 'ALL' && !isOthers) {
        var re = new RegExp("\\n\\s*room:\\s*" + room.replace(/[-/\\^$*+?.()|[\]{}]/g,'\\$&') + "\\s*$", 'mi');
        blocks = blocks.filter(function(b){ return re.test(b); });
      } else if (isOthers) {
        blocks = blocks.filter(function(b){ return /\n\s*room:\s*N\/A/i.test(b); });
      }
      // Extract unique IDs in order
      var ids = [];
      blocks.forEach(function(block){
        var m = block.match(/TASK ID:\s*([A-Z0-9\- _]+)/i);
        if (m && m[1]) ids.push(m[1].trim());
      });
      ids = Array.from(new Set(ids));
      sel.innerHTML = '';
      ids.forEach(function(id){ var o=document.createElement('option'); o.value=id; o.textContent=id; sel.appendChild(o); });
    } catch(_) {}
  }

  async function sendUpdate(){
    var hotel = HOTEL; if (!hotel){ alert('Hotel required'); return; }
    var taskSel = qs('upd-task');
    var task = (taskSel && taskSel.value) ? taskSel.value.trim() : '';
    var status = qs('upd-status').value;
    var by = '';
    var notes = qs('upd-notes').value;
    var reso = qs('upd-resolution').value;
    var assignedTo = '';
    try { assignedTo = (qs('upd-assign-to')?.value || '').trim(); } catch(_) {}
    if (!task || !status){ alert('Task and status are required'); return; }
    var payload = { hotel_name: hotel, task_id: task, status: status, progress_notes: notes, resolution_description: reso };
    if (assignedTo) payload.assigned_to_phone = assignedTo;
    // assigned_at removed; backend uses current timestamp when needed
    var res = await fetch(API_ROOT + '/api/maintenance/update', { method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
    var data = await res.json();
    if (!res.ok || data.error){ alert(data.error || 'Update failed'); return; }
    alert('Updated');
    loadList();
  }

  async function init(){
    await initHotel();
    await triggerPmNow();
    try {
      var roomEl = qs('mnt-room');
      var roomSearch = qs('room-search');
      var sortEl = qs('mnt-sort');
      if (roomEl) roomEl.addEventListener('change', function(){ populateTasksFromCache(); renderFromCache(); });
      if (roomSearch) roomSearch.addEventListener('input', debounce(filterRoomOptions, 150));
      if (sortEl) sortEl.addEventListener('change', function(){ renderFromCache(); });
      var statusEl = qs('mnt-status'); if (statusEl) statusEl.addEventListener('change', function(){ renderFromCache(); });
      var urgEl = qs('mnt-urgency'); if (urgEl) urgEl.addEventListener('change', function(){ renderFromCache(); });
      var upd = qs('upd-send'); if (upd) upd.addEventListener('click', function(e){ e.preventDefault(); sendUpdate(); });
      // Schedules
      var sref = qs('sched-refresh'); if (sref) sref.addEventListener('click', function(){ loadSchedules(); });
      var sadd = qs('sched-add'); if (sadd) sadd.addEventListener('click', function(){ openScheduleModal(''); });
      var sclose = qs('sched-close'); if (sclose) sclose.addEventListener('click', function(){ qs('sched-modal').classList.add('hidden'); });
      var ssave = qs('sched-save'); if (ssave) ssave.addEventListener('click', function(){ saveSchedule(); });
      var sshow = qs('sched-show-inactive'); if (sshow) sshow.addEventListener('change', function(){ loadSchedules(); });
    } catch(_) {}
    await populateRooms();
    loadSchedules();
    loadList();
  }

  document.addEventListener('DOMContentLoaded', init);
})();


