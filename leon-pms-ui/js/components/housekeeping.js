// Housekeeping rich UI logic (static demo wiring; integrate real APIs later)
(function(){
  const qs = (s, r=document) => r.querySelector(s);
  const qsa = (s, r=document) => Array.from(r.querySelectorAll(s));
  const host = window.location.hostname;
  const isLocal = host === 'localhost' || host === '127.0.0.1' || host === '::1';
  const API_BASE = isLocal ? 'http://localhost:8002' : 'https://api.inap.ai';
  let HOTEL_TZ = 'UTC';

  function fmtWithTzMaybe(iso){
    try{
      if (!iso) return '';
      const hasTz = /Z|[\+\-]\d{2}:?\d{2}$/.test(String(iso));
      if (window.app && typeof window.app.formatWithTz === 'function' && hasTz){
        return window.app.formatWithTz(iso, 'UTC', { dateStyle: 'short', timeStyle: 'short' });
      }
      return String(iso);
    }catch(_){ return String(iso); }
  }

  function kpi(id, val){ const el = qs('#'+id); if (el) el.textContent = val; }

  function card(room){
    const div = document.createElement('div');
    div.className = 'p-2 rounded-md border bg-white shadow-sm hover:shadow transition cursor-grab text-xs';
    div.draggable = true;
    // attach identifiers for DnD updates
    if (room.cleaning_id) div.dataset.cleaningId = String(room.cleaning_id);
    if (room.number) div.dataset.roomNumber = String(room.number);
    if (room.status) div.dataset.status = String(room.status);
    if (room.staff) div.dataset.assignedTo = String(room.staff);
    div.innerHTML = `
      <div class="flex items-center justify-between">
        <div class="font-semibold text-gray-800">${room.number}</div>
        <span class="text-gray-500">${room.type}</span>
      </div>
      <div class="mt-1 text-gray-600 flex items-center gap-2">
        <i class="fas fa-user-nurse text-blue-500"></i>
        <span>${room.staff || 'Unassigned'}</span>
        <span class="mx-1">•</span>
        <span>${room.duration || '—'}</span>
      </div>
    `;
    // Click-to-update status (manual)
    div.addEventListener('click', async (e) => {
      e.stopPropagation();
      const hotel = await resolveHotel();
      const rn = div.dataset.roomNumber;
      const cid = div.dataset.cleaningId;
      const current = (div.dataset.status || '').toLowerCase();
      const next = window.prompt(`Set status for Room ${rn} (assigned|in_progress|quality_check|completed):`, current || 'assigned');
      if (!next) return;
      let staff = div.dataset.assignedTo || '';
      if (!staff || next === 'in_progress') {
        staff = await selectStaff(hotel) || staff || '';
      }
      try {
        if (cid) {
          await fetch(`${API_BASE}/api/housekeeping/update_progress`, { method:'POST', headers:{'Content-Type':'application/json'}, credentials:'include', body: JSON.stringify({ hotel_name: hotel, cleaning_id: cid, new_status: next, staff_phone: staff, notes: '' }) });
        } else if (next === 'in_progress') {
          await fetch(`${API_BASE}/api/housekeeping/start_cleaning`, { method:'POST', headers:{'Content-Type':'application/json'}, credentials:'include', body: JSON.stringify({ hotel_name: hotel, room_number: rn, staff_phone: staff }) });
        } else {
          // cannot set inspected/ready without a task
          alert('Create a cleaning task first (move to Cleaning or set in_progress).');
          return;
        }
        await init();
      } catch(_) {}
    });
    return div;
  }

  function renderBoard(data){
    // Support new and legacy column IDs
    const colDirty = qs('#col-dirty');
    const colAssigned = qs('#col-assigned') || qs('#col-dirty');
    const colInProg = qs('#col-in-progress') || qs('#col-cleaning');
    const colQC = qs('#col-quality-check') || qs('#col-inspected');
    const colDone = qs('#col-completed') || qs('#col-ready');
    const cols = { dirty: colDirty, assigned: colAssigned, in_progress: colInProg, quality_check: colQC, completed: colDone };
    // Adapt legacy headings if present
    const legacyMap = [
      { el: colAssigned, label: 'Assigned' },
      { el: colInProg, label: 'In Progress' },
      { el: colQC, label: 'Quality Check' },
      { el: colDone, label: 'Completed' },
    ];
    legacyMap.forEach(({ el, label }) => {
      if (!el) return;
      const heading = el.previousElementSibling;
      if (heading && heading.tagName === 'H3') {
        const icon = heading.querySelector('span');
        const iconHTML = icon ? icon.outerHTML : '';
        heading.innerHTML = `${iconHTML}${label}`;
      }
    });
    Object.values(cols).forEach(c=>{ if (c) c.innerHTML=''; });
    (data.rooms || []).forEach(r => {
      const key = (r.status || '').toLowerCase();
      const col = cols[key];
      col && col.appendChild(card(r));
    });
    // KPIs (exact cleaning_status). Support both new and legacy KPI IDs
    const countDirty = data.rooms.filter(r=>r.status==='dirty').length;
    const countAssigned = data.rooms.filter(r=>r.status==='assigned').length;
    const countInProg = data.rooms.filter(r=>r.status==='in_progress').length;
    const countQC = data.rooms.filter(r=>r.status==='quality_check').length;
    const countDone = data.rooms.filter(r=>r.status==='completed').length;
    const setKpi = (id, val, labelText) => {
      const el = qs('#'+id);
      if (el) {
        el.textContent = val;
        const label = el.parentElement && el.parentElement.querySelector('.text-sm');
        if (label && labelText) label.textContent = labelText;
      }
    };
    setKpi('kpi-dirty', countDirty, 'Dirty');
    setKpi('kpi-assigned', countAssigned, 'Assigned');
    setKpi('kpi-in-progress', countInProg, 'In Progress');
    setKpi('kpi-quality-check', countQC, 'Quality Check');
    setKpi('kpi-completed', countDone, 'Completed');
    // Legacy KPI IDs fallback
    // keep current dirty count for legacy id if present
    setKpi('kpi-dirty', countDirty, 'Dirty');
    setKpi('kpi-cleaning', countInProg, 'In Progress');
    setKpi('kpi-inspected', countQC, 'Quality Check');
    setKpi('kpi-ready', countDone, 'Completed');
  }

  function renderStaff(data){
    const root = qs('#hk-staff-list'); if (!root) return;
    root.innerHTML = '';
    data.staff.forEach(s => {
      const row = document.createElement('div');
      row.className = 'p-3 rounded-lg border bg-white flex items-center justify-between';
      row.innerHTML = `
        <div class="flex items-center gap-3">
          <div class="w-8 h-8 rounded-full bg-green-100 text-green-700 flex items-center justify-center"><i class="fas fa-user-nurse"></i></div>
          <div>
            <div class="font-semibold text-gray-800">${s.name}</div>
            <div class="text-xs text-gray-500">${s.phone}</div>
          </div>
        </div>
        <div class="text-sm">
          <span class="px-2 py-1 rounded bg-blue-100 text-blue-700">${s.tasks} tasks</span>
        </div>
      `;
      root.appendChild(row);
    });
  }

  function renderActivity(data){
    const root = qs('#hk-activity'); if (!root) return;
    root.innerHTML = '';
    data.activity.forEach(a => {
      const row = document.createElement('div');
      row.className = 'flex items-center gap-3 text-sm';
      row.innerHTML = `
        <span class="w-2 h-2 rounded-full ${a.color}"></span>
        <span class="text-gray-700">${a.text}</span>
        <span class="text-gray-400 text-xs ml-auto">${fmtWithTzMaybe(a.time)}</span>
      `;
      root.appendChild(row);
    });
    const nowIso = new Date().toISOString();
    const ts = fmtWithTzMaybe(nowIso);
    const lu = qs('#hk-last-updated'); if (lu) lu.textContent = `Updated ${ts}`;
  }

  async function selectStaff(hotel){
    try {
      const res = await fetch(`${API_BASE}/api/housekeeping/on_duty?hotel_name=${encodeURIComponent(hotel)}`, { credentials: 'include' });
      const data = await res.json();
      const onDuty = (data.on_duty || []).map(s => s.staff_phone);
      let staff = null;
      if (onDuty.length === 1) staff = onDuty[0];
      if (onDuty.length > 1) {
        staff = window.prompt(`Enter staff phone from on-duty list:\n${onDuty.join('\n')}`, onDuty[0] || '');
      }
      if (!staff) {
        // fallback to available housekeeping staff
        const res2 = await fetch(`${API_BASE}/api/staff/available?hotel_name=${encodeURIComponent(hotel)}&department=housekeeping`, { credentials: 'include' });
        const data2 = await res2.json();
        const avail = (data2.staff || []).map(s => `${s.staff_phone} (${s.staff_name || ''})`);
        staff = window.prompt(`Enter staff phone (available):\n${avail.join('\n')}`, (data2.staff && data2.staff[0] && data2.staff[0].staff_phone) || '');
      }
      return staff;
    } catch(_) { return null; }
  }

  function enableDnD(){
    const mapIdToStatus = {
      'col-assigned': 'assigned',
      'col-in-progress': 'in_progress',
      'col-quality-check': 'quality_check',
      'col-completed': 'completed',
      // legacy IDs
      'col-dirty': 'assigned',
      'col-cleaning': 'in_progress',
      'col-inspected': 'quality_check',
      'col-ready': 'completed'
    };
    ;['col-assigned','col-in-progress','col-quality-check','col-completed','col-dirty','col-cleaning','col-inspected','col-ready'].forEach(id => {
      const el = qs('#'+id); if (!el) return;
      new Sortable(el, { group: { name:'hk', pull:true, put:true }, animation: 150, ghostClass:'bg-gray-50', onEnd: async (evt) => {
        try {
          const item = evt.item;
          if (!item) return;
          const cleaningId = item.dataset.cleaningId;
          const assignedTo = item.dataset.assignedTo || '';
          const toColId = evt.to && evt.to.id;
          const newStatus = mapIdToStatus[toColId] || null;
          if (!newStatus) return;
          const hotel = await resolveHotel();
          if (!cleaningId) {
            // All columns are cleaning_status. Items must have cleaning_id. Ignore otherwise.
            return;
          }
          // If moving to in_progress and no staff, ask
          let staffPhone = assignedTo;
          if (newStatus === 'in_progress' && (!staffPhone || staffPhone === '—')) {
            const rn = item.dataset.roomNumber;
            const chosen = await selectStaff(hotel);
            if (!chosen) return;
            staffPhone = chosen;
          }
          {
            await fetch(`${API_BASE}/api/housekeeping/update_progress`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
              body: JSON.stringify({ hotel_name: hotel, cleaning_id: cleaningId, new_status: newStatus, staff_phone: staffPhone || assignedTo, notes: '' })
            });
          }
          // Refresh board after update
          await init();
        } catch(_) {}
      } });
    });
  }

  // No fake data; render empty on failure

  async function resolveHotel(){
    const params = new URLSearchParams(window.location.search);
    const fromQuery = params.get('hotel');
    if (fromQuery) return fromQuery.toUpperCase();
    try {
      const res = await fetch(`${API_BASE}/api/auth/me`, { credentials: 'include' });
      if (res.ok) {
        const data = await res.json();
        if (data.hotel_name) return String(data.hotel_name).toUpperCase();
      }
    } catch (_) {}
    return '';
  }

  async function init(){
    const hotel = await resolveHotel();
    if (!hotel) return;
    
    try {
      // Use UTC for all display formatting
      HOTEL_TZ = 'UTC';
      // Exact cleaning_status sets
      const [assignedRes, inProgRes, qcRes, doneRes, dirtyRes, staffRes, actRes] = await Promise.all([
        fetch(`${API_BASE}/api/housekeeping/by_status?hotel_name=${encodeURIComponent(hotel)}&status=assigned`, { credentials: 'include' }),
        fetch(`${API_BASE}/api/housekeeping/by_status?hotel_name=${encodeURIComponent(hotel)}&status=in_progress`, { credentials: 'include' }),
        fetch(`${API_BASE}/api/housekeeping/by_status?hotel_name=${encodeURIComponent(hotel)}&status=quality_check`, { credentials: 'include' }),
        fetch(`${API_BASE}/api/housekeeping/by_status?hotel_name=${encodeURIComponent(hotel)}&status=completed`, { credentials: 'include' }),
        fetch(`${API_BASE}/api/housekeeping/by_status?hotel_name=${encodeURIComponent(hotel)}&status=dirty`, { credentials: 'include' }),
        fetch(`${API_BASE}/api/housekeeping/staff_workload?hotel_name=${encodeURIComponent(hotel)}`, { credentials: 'include' }),
        fetch(`${API_BASE}/api/housekeeping/activity?hotel_name=${encodeURIComponent(hotel)}&limit=30`, { credentials: 'include' })
      ]);
      const [assigned, inProg, quality, done, dirty, staffW, activity] = await Promise.all([
        assignedRes.json(), inProgRes.json(), qcRes.json(), doneRes.json(), dirtyRes.json(), staffRes.json(), actRes.json()
      ]);
      const toRooms = (arr, status) => (arr || []).map(x => ({ number: String(x.room_number || x.room || '').trim(), type: '—', status, staff: x.assigned_to || x.staff_phone || '—', duration: '—', cleaning_id: x.cleaning_id }));
      const roomsDirtyArr = (dirty.items || []).map(r => ({ number: String(r.room_number || r.room || '').trim(), type: '—', status: 'dirty', staff: '—', duration: '—', cleaning_id: '' }));
      const roomsAssigned = toRooms(assigned.items, 'assigned');
      const roomsInProg = toRooms(inProg.items, 'in_progress');
      const roomsQC = toRooms(quality.items, 'quality_check');
      const roomsDone = toRooms(done.items, 'completed');
      const allRooms = [...roomsDirtyArr, ...roomsAssigned, ...roomsInProg, ...roomsQC, ...roomsDone];
      kpi('kpi-dirty', roomsDirtyArr.length);
      kpi('kpi-assigned', roomsAssigned.length);
      kpi('kpi-in-progress', roomsInProg.length);
      kpi('kpi-quality-check', roomsQC.length);
      kpi('kpi-completed', roomsDone.length);
      renderBoard({ rooms: allRooms, staff: [], activity: [] });
      renderStaff({ staff: (staffW.staff || []).map(s => ({ name: s.name, phone: s.phone, tasks: s.tasks })) });
      renderActivity({ activity: (activity.activity || []) });
    } catch (e) {
      kpi('kpi-assigned', 0);
      kpi('kpi-in-progress', 0);
      kpi('kpi-quality-check', 0);
      kpi('kpi-completed', 0);
      renderBoard({ rooms: [], staff: [], activity: [] });
      renderStaff({ staff: [] });
      renderActivity({ activity: [] });
    }
    enableDnD();
    const autoBtn = qs('#assign-auto');
    if (autoBtn) autoBtn.onclick = async () => {
      const hotelName = await resolveHotel();
      if (!hotelName) return;
      const dutyRes = await fetch(`${API_BASE}/api/housekeeping/on_duty`, { credentials: 'include' });
      const duty = dutyRes.ok ? await dutyRes.json() : {};
      const staff = (duty.on_duty || []).map(s => s.staff_phone).filter(Boolean);
      if (!staff.length) { alert('No housekeeping staff on duty'); return; }
      const dirtyRes = await fetch(`${API_BASE}/api/housekeeping/by_status?status=dirty`, { credentials: 'include' });
      const dirty = dirtyRes.ok ? await dirtyRes.json() : {};
      const rooms = dirty.items || [];
      if (!rooms.length) { alert('No dirty rooms'); return; }
      let assigned = 0;
      for (let i = 0; i < rooms.length; i++) {
        const room = rooms[i];
        const phone = staff[i % staff.length];
        const resp = await fetch(`${API_BASE}/api/housekeeping/start_cleaning`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ hotel_name: hotelName, room_number: room.room_number, staff_phone: phone, task_type: 'standard_cleaning', priority: 'normal', notes: '' })
        });
        if (resp.ok) assigned += 1;
      }
      alert(`Assigned ${assigned} room${assigned === 1 ? '' : 's'}`);
      await init();
    };
  }

  document.addEventListener('DOMContentLoaded', init);
})();


