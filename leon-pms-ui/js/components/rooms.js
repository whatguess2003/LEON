// Rooms Management Component
class RoomsComponent {
    constructor(app) {
        this.app = app;
        this.selectedRooms = [];
        this.editingRoom = null;
        this.sortBy = 'room_number';
        this.filterBy = 'all';
        this.viewMode = 'grid';
        this.hotelsLoaded = false;
        this.hotels = [];
        this.rooms = [];
        this.isFiltered = false;
        this._lastFilteredRooms = null;
        // Add local/production API URL switcher
        const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
        this.apiBase = isLocal ? 'http://localhost:8002/api' : 'https://api.inap.ai/api';
        if (!this.app) {
            this.createStandaloneMode();
        }
        this.initialize();
    }

    openOperationalStatusModal(hotelName, roomType, roomNumber) {
        try {
            const modal = document.createElement('div');
            modal.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50';
            modal.innerHTML = `
                <div class="bg-white rounded-lg p-6 w-full max-w-md">
                    <div class="flex justify-between items-center mb-4">
                        <h3 class="text-lg font-semibold">Room ${roomNumber} Operational Status</h3>
                        <button onclick="this.closest('.fixed').remove()" class="text-gray-400 hover:text-gray-600"><i class="fas fa-times"></i></button>
                    </div>

                    <div id="ooo-status" class="mb-3 text-sm text-gray-700">Loading current operational info...</div>

                    <div class="space-y-2 mb-2">
                        <div>
                            <button class="w-full flex items-center justify-center p-3 rounded-lg border border-red-200 bg-red-50 text-red-700 hover:bg-red-100" onclick="rooms.showOutOfOrderForm('${hotelName}','${roomNumber}')">
                                <i class="fas fa-wrench mr-2"></i> Mark OUT OF ORDER
                            </button>
                            <div id="ooo-form" class="hidden p-3 rounded-lg border mt-2">
                                <div class="grid grid-cols-2 gap-2 mb-2">
                                    <div>
                                        <label class="block text-xs text-gray-600">Start Date</label>
                                        <input id="ooo-start" type="date" class="border rounded w-full px-2 py-1" />
                                    </div>
                                    <div>
                                        <label class="block text-xs text-gray-600">End Date</label>
                                        <input id="ooo-end" type="date" class="border rounded w-full px-2 py-1" />
                                    </div>
                                </div>
                                <div class="mb-2">
                                    <label class="block text-xs text-gray-600">Reason</label>
                                    <input id="ooo-reason" class="border rounded w-full px-2 py-1" placeholder="e.g., AC maintenance" />
                                </div>
                                <div class="flex justify-end gap-2">
                                    <button class="px-3 py-1 text-xs rounded bg-gray-100 text-gray-700" onclick="document.getElementById('ooo-form').classList.add('hidden')">Cancel</button>
                                    <button class="px-3 py-1 text-xs rounded bg-red-600 text-white" onclick="rooms.submitOutOfOrderForm('${hotelName}','${roomNumber}')"><i class="fas fa-save mr-1"></i>Confirm</button>
                                </div>
                            </div>
                        </div>
                        <div>
                            <button class="w-full flex items-center justify-center p-3 rounded-lg border border-green-200 bg-green-50 text-green-700 hover:bg-green-100" onclick="rooms.showAvailableBlocks('${hotelName}','${roomNumber}')">
                                <i class="fas fa-check-circle mr-2"></i> Mark AVAILABLE
                            </button>
                            <div id="avail-blocks" class="hidden p-3 rounded-lg border mt-2 bg-gray-50">
                                <div class="text-xs text-gray-600 mb-2">Out-of-Order date blocks for this room:</div>
                                <div id="avail-blocks-list" class="space-y-2"></div>
                            </div>
                        </div>
                    </div>

                    <div class="space-y-2 mt-2">
                        <div>
                            <button class="w-full flex items-center justify-center p-3 rounded-lg border border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100" onclick="rooms.showCreateMaintenance('${hotelName}','${roomNumber}')">
                                <i class="fas fa-tools mr-2"></i> Create Maintenance Request
                            </button>
                            <div id="create-mnt-form" class="hidden p-3 rounded-lg border mt-2">
                                <div class="space-y-2">
                                    <input id="im-issue" class="border rounded w-full px-3 py-2" placeholder="Issue description"/>
                                    <select id="im-urgency" class="border rounded w-full px-3 py-2">
                                        <option value="LOW">LOW</option>
                                        <option value="NORMAL" selected>NORMAL</option>
                                        <option value="HIGH">HIGH</option>
                                        <option value="URGENT">URGENT</option>
                                    </select>
                                    <div class="flex justify-end gap-2">
                                        <button class="px-3 py-1 text-xs rounded bg-gray-100 text-gray-700" onclick="rooms.openCascade(null)">Cancel</button>
                                        <button class="px-3 py-1 text-xs rounded bg-blue-600 text-white" onclick="rooms.submitMaintenanceInline('${hotelName}','${roomNumber}')"><i class="fas fa-plus-circle mr-1"></i>Create</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        
                    </div>

                    <div class="mt-4 border rounded p-3 bg-gray-50">
                        <div id="maintenance-list" class="text-xs text-gray-700">Loading maintenance...</div>
                    </div>
                </div>`;
            document.body.appendChild(modal);
            // Load existing out-of-order windows and maintenance requests
            this.loadOperationalInfo(hotelName, roomNumber).catch(()=>{
                const tgt = document.getElementById('ooo-status');
                if (tgt) tgt.textContent = 'Unable to load current info.';
            });
        } catch(_) {}
    }

    async loadOperationalInfo(hotelName, roomNumber) {
        try {
            const apiRoot = this.apiBase.endsWith('/api') ? this.apiBase.slice(0, -4) : this.apiBase;
            // Today's Out-of-Order status and reason
            let oooHtml = '';
            try {
                const today = new Date().toISOString().slice(0,10);
                const cal = await fetch(`${apiRoot}/api/assignments/calendar?hotel_name=${encodeURIComponent(hotelName)}&start_date=${today}&end_date=${today}&room_numbers=${encodeURIComponent(roomNumber)}`, { credentials: 'include' });
                const calData = await cal.json();
                const roomsArr = this._extractRoomsWithEntries(calData);
                const roomObj = Array.isArray(roomsArr) ? roomsArr.find(r => String(r.room_number) === String(roomNumber)) : null;
                const entries = roomObj && roomObj.entries ? roomObj.entries : {};
                const todayEntry = entries && entries[today];
                const st = (todayEntry && todayEntry.status || '').toLowerCase();
                if (st === 'out-of-order' || st === 'out_of_order' || st === 'outoforder') {
                    const raw = todayEntry && todayEntry.notes ? String(todayEntry.notes) : '';
                    const reason = this.sanitizeOoOReason(raw);
                    oooHtml = `<div class="mb-2 flex items-center gap-2"><span class="inline-block w-2 h-2 rounded-full bg-red-500"></span><div><strong>Out of Order:</strong> ${this.escapeHtml(reason || 'Unavailable')}</div></div>`;
                } else {
                    oooHtml = '<div class="mb-2 flex items-center gap-2"><span class="inline-block w-2 h-2 rounded-full bg-green-500"></span><div><strong>Available</strong></div></div>';
                }
            } catch(_) {
                oooHtml = '<div class="mb-2 flex items-center gap-2"><span class="inline-block w-2 h-2 rounded-full bg-green-500"></span><div><strong>Available</strong></div></div>';
            }
            // Mount OoO status
            const statusTarget = document.getElementById('ooo-status');
            if (statusTarget) statusTarget.innerHTML = oooHtml;

            // Maintenance requests list filtered by room (hide completed)
            try {
                const res = await fetch(`${apiRoot}/api/maintenance/list?hotel_name=${encodeURIComponent(hotelName)}&room_number=${encodeURIComponent(roomNumber)}`, { credentials: 'include' });
                const data = await res.json();
                let raw = String(data.result || '');
                if (raw) {
                    // Drop completed blocks for modal view
                    const blocks = raw.split(/\n\s*\n/).filter(Boolean);
                    const filtered = blocks.filter(b => !/\n\s*current_status:\s*COMPLETED/i.test(b) && (/(TASK ID:|room:)/i.test(b)));
                    raw = filtered.join('\n\n');
                }
                const html = raw ? `<pre class="text-xs bg-white p-2 rounded border">${this.escapeHtml(raw)}</pre>` : '<div class="text-gray-500">No maintenance requests.</div>';
                const listTarget = document.getElementById('maintenance-list');
                if (listTarget) listTarget.innerHTML = html;
            } catch(_) {
                const listTarget = document.getElementById('maintenance-list');
                if (listTarget) listTarget.innerHTML = '<div class="text-gray-500">No maintenance data.</div>';
            }
            return '';
        } catch(_) { return ''; }
    }

    showOutOfOrderForm(hotelName, roomNumber) {
        try {
            this.openCascade('ooo-form');
        } catch(_) {}
    }

    async submitOutOfOrderForm(hotelName, roomNumber) {
        try {
            const start = document.getElementById('ooo-start')?.value || '';
            const end = document.getElementById('ooo-end')?.value || '';
            const reason = document.getElementById('ooo-reason')?.value || '';
            if (!start || !end || !reason) { this.showNotification('Please fill start, end, and reason.', 'error'); return; }
            const apiRoot = this.apiBase.endsWith('/api') ? this.apiBase.slice(0, -4) : this.apiBase;
            const res = await fetch(`${apiRoot}/api/assignments/mark_out_of_order`, {
                method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ hotel_name: hotelName, room_number: roomNumber, start_date: start, end_date: end, reason })
            });
            const data = await res.json();
            if (!res.ok || data.error) throw new Error(data.error || 'failed');
            this.showNotification('Room marked out of order', 'success');
            // Refresh modal sections with soft animation
            try {
                const tgt = document.getElementById('op-status-existing');
                if (tgt) {
                    tgt.textContent = 'Refreshing...';
                    const html = await this.loadOperationalInfo(hotelName, roomNumber);
                    tgt.innerHTML = html || '<span class="text-gray-500">No current operational holds.</span>';
                    this._fadeIn(tgt);
                }
                const f = document.getElementById('ooo-form');
                if (f) f.classList.add('hidden');
                const availWrap = document.getElementById('avail-blocks');
                if (availWrap && !availWrap.classList.contains('hidden')) {
                    await this.showAvailableBlocks(hotelName, roomNumber);
                }
            } catch(_) {}
            // Refresh underlying data
            this.loadRoomsData();
        } catch(e) { this.showNotification('Failed: '+ (e.message||e), 'error'); }
    }

    async showAvailableBlocks(hotelName, roomNumber) {
        try {
            this.openCascade('avail-blocks');
            const wrap = document.getElementById('avail-blocks');
            const list = document.getElementById('avail-blocks-list');
            if (!wrap || !list) return;
            wrap.classList.remove('hidden');
            list.innerHTML = '<div class="text-gray-500 text-xs">Loading...</div>';
            // Load 90-day window assignments and group contiguous out-of-order dates
            const apiRoot = this.apiBase.endsWith('/api') ? this.apiBase.slice(0, -4) : this.apiBase;
            const today = new Date();
            const start = today.toISOString().slice(0,10);
            const end = new Date(today.getFullYear(), today.getMonth(), today.getDate()+365).toISOString().slice(0,10);
            let blocks = [];
            // Fetch calendar entries for this room and range
            const r2 = await fetch(`${apiRoot}/api/assignments/calendar?hotel_name=${encodeURIComponent(hotelName)}&start_date=${start}&end_date=${end}&room_numbers=${encodeURIComponent(roomNumber)}`, { credentials: 'include' });
            const data2 = await r2.json();
            // Extract room entries (supports different shapes)
            const roomsArr = this._extractRoomsWithEntries(data2);
            const roomObj = Array.isArray(roomsArr) ? roomsArr.find(r => String(r.room_number) === String(roomNumber)) : null;
            const entries = roomObj && roomObj.entries ? roomObj.entries : {};
            // Prefer backend-joined linear ranges if provided
            if (roomObj && Array.isArray(roomObj.ooo_linear) && roomObj.ooo_linear.length) {
                blocks = roomObj.ooo_linear.map(b => ({ start: String(b.start), end: String(b.end), reason: b && b.reason ? String(b.reason) : '' }));
            }
            // Helper to add days
            const addDays = (iso, n) => {
                const d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() + n); return d.toISOString().slice(0,10);
            };
            // If backend did not provide ooo_linear, derive from per-day entries
            if (!blocks.length) {
                const dates = Object.keys(entries).sort();
                let i = 0;
                while (i < dates.length) {
                    const d = dates[i];
                    const st = (entries[d] && entries[d].status || '').toLowerCase();
                    if (st === 'out-of-order' || st === 'out_of_order' || st === 'outoforder') {
                        let startBlock = d;
                        let endBlock = d;
                        let j = i + 1;
                        // Capture first non-empty notes within the block
                        let reason = (entries[d] && entries[d].notes) ? String(entries[d].notes) : '';
                        while (j < dates.length) {
                            const nextDate = dates[j];
                            const prevPlus1 = addDays(endBlock, 1);
                            const nextStatus = (entries[nextDate] && entries[nextDate].status || '').toLowerCase();
                            if (nextDate === prevPlus1 && (nextStatus === 'out-of-order' || nextStatus === 'out_of_order' || nextStatus === 'outoforder')) {
                                endBlock = nextDate;
                                if (!reason && entries[nextDate] && entries[nextDate].notes) {
                                    reason = String(entries[nextDate].notes);
                                }
                                j += 1;
                            } else {
                                break;
                            }
                        }
                        blocks.push({ start: startBlock, end: endBlock, reason });
                        i = j;
                    } else {
                        i += 1;
                    }
                }
            }
            // If blocks empty, synthesize minimal UI hint
            if (!blocks.length) {
                list.innerHTML = '<div class="text-gray-500 text-xs">No out-of-order blocks found in the next 365 days.</div>';
                return;
            }
            list.innerHTML = blocks.map(b => {
                const raw = b && b.reason ? String(b.reason) : '';
                const reasonSan = this.sanitizeOoOReason(raw);
                const reason = reasonSan ? this.escapeHtml(reasonSan) : '';
                return `
                <div class="flex items-center justify-between bg-white border rounded p-2">
                    <div class="text-xs">
                        <div><strong>${b.start}</strong> → <strong>${b.end}</strong></div>
                        ${reason ? `<div class=\"text-[11px] text-gray-500\">${reason}</div>` : ''}
                    </div>
                    <button class="px-2 py-1 text-xs rounded bg-green-600 text-white" onclick="rooms.markAvailableRange('${hotelName}','${roomNumber}','${b.start}','${b.end}')"><i class="fas fa-check mr-1"></i>Mark Available</button>
                </div>`;
            }).join('');
            this._fadeIn(list);
        } catch(_) {}
    }

    openCascade(id) {
        try {
            // Close all cascade panels
            const ids = ['ooo-form', 'avail-blocks', 'create-mnt-form'];
            ids.forEach(pid => {
                const el = document.getElementById(pid);
                if (el) el.classList.add('hidden');
            });
            if (!id) return;
            const tgt = document.getElementById(id);
            if (tgt) tgt.classList.remove('hidden');
        } catch(_) {}
    }

    showCreateMaintenance(hotelName, roomNumber) { this.openCascade('create-mnt-form'); }
    showUpdateMaintenance(hotelName) { window.spaNavigate('maintenance.html', {}); }

    async markAvailableRange(hotelName, roomNumber, start, end) {
        try {
            const apiRoot = this.apiBase.endsWith('/api') ? this.apiBase.slice(0, -4) : this.apiBase;
            const res = await fetch(`${apiRoot}/api/assignments/mark_available`, {
                method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ hotel_name: hotelName, room_number: roomNumber, start_date: start, end_date: end })
            });
            const data = await res.json();
            if (!res.ok || data.error) throw new Error(data.error || 'failed');
            this.showNotification(`Room marked available for ${start} → ${end}`, 'success');
            // Refresh the available blocks list in place with a soft animation
            await this.showAvailableBlocks(hotelName, roomNumber);
            // Also refresh underlying data in the background
            this.loadRoomsData();
        } catch(e) { this.showNotification('Failed: '+ (e.message||e), 'error'); }
    }

    _fadeIn(el) {
        try {
            el.style.opacity = '0';
            el.style.transition = 'opacity 200ms ease';
            // Force reflow
            void el.offsetWidth;
            el.style.opacity = '1';
        } catch(_) {}
    }

    _extractRoomsWithEntries(data) {
        try {
            if (!data) return [];
            if (Array.isArray(data.rooms)) return data.rooms;
            if (data.items && Array.isArray(data.items.rooms)) return data.items.rooms;
            // Some shapes embed rooms under a property
            for (const k of Object.keys(data)) {
                const v = data[k];
                if (v && Array.isArray(v) && v.length && v[0] && typeof v[0] === 'object' && 'room_number' in v[0] && 'entries' in v[0]) {
                    return v;
                }
            }
        } catch(_) {}
        return [];
    }

    sanitizeOoOReason(reason) {
        try {
            if (!reason) return '';
            let r = String(reason);
            // Strip common prefixes like "Out-of-order:" (case-insensitive, optional hyphens/spaces)
            r = r.replace(/^\s*out\s*-?\s*of\s*-?\s*order\s*:\s*/i, '');
            r = r.trim();
            return r;
        } catch(_) { return ''; }
    }

    openCreateMaintenance(hotelName, roomNumber) {
        const modal = document.createElement('div');
        modal.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50';
        modal.innerHTML = `
            <div class="bg-white rounded-lg p-6 w-full max-w-md">
                <div class="flex justify-between items-center mb-4">
                    <h3 class="text-lg font-semibold">Create Maintenance Request</h3>
                    <button onclick="this.closest('.fixed').remove()" class="text-gray-400 hover:text-gray-600"><i class="fas fa-times"></i></button>
                </div>
                <div class="space-y-3">
                    <input id="mnt-issue" class="border rounded w-full px-3 py-2" placeholder="Issue description"/>
                    <select id="mnt-urgency" class="border rounded w-full px-3 py-2">
                        <option value="LOW">LOW</option>
                        <option value="NORMAL" selected>NORMAL</option>
                        <option value="HIGH">HIGH</option>
                        <option value="URGENT">URGENT</option>
                    </select>
                    <button class="w-full bg-blue-600 text-white rounded px-3 py-2" onclick="rooms.submitMaintenance('${hotelName}','${roomNumber}')"><i class="fas fa-plus-circle mr-1"></i>Create</button>
                    <div class="border-t pt-3 mt-3">
                        <h4 class="text-sm font-semibold mb-2">Update Maintenance Progress</h4>
                        <input id="mnt-task-id" class="border rounded w-full px-3 py-2 mb-2" placeholder="Task ID (e.g., MTAC2025-08-21)"/>
                        <select id="mnt-status" class="border rounded w-full px-3 py-2 mb-2">
                            <option value="ASSIGNED">ASSIGNED</option>
                            <option value="IN_PROGRESS">IN_PROGRESS</option>
                            <option value="COMPLETED">COMPLETED</option>
                            <option value="ON_HOLD">ON_HOLD</option>
                            <option value="CANCELLED">CANCELLED</option>
                        </select>
                        <input id="mnt-updated-by" class="border rounded w-full px-3 py-2 mb-2" placeholder="Updated by (staff phone, e.g., +60123456789)"/>
                        <textarea id="mnt-notes" class="border rounded w-full px-3 py-2 mb-2" rows="2" placeholder="Progress notes (optional)"></textarea>
                        <textarea id="mnt-resolution" class="border rounded w-full px-3 py-2 mb-2" rows="2" placeholder="Resolution description (if completed)"></textarea>
                        <button class="w-full bg-indigo-600 text-white rounded px-3 py-2" onclick="rooms.submitMaintenanceUpdate('${hotelName}')"><i class="fas fa-save mr-1"></i>Update</button>
                    </div>
                </div>
            </div>`;
        document.body.appendChild(modal);
    }

    async submitMaintenance(hotelName, roomNumber) {
        try {
            const issue = document.getElementById('mnt-issue')?.value || '';
            let urgency = document.getElementById('mnt-urgency')?.value || 'NORMAL';
            if (String(urgency).toUpperCase() === 'CRITICAL') urgency = 'URGENT';
            if (!issue) { alert('Issue required'); return; }
            const apiRoot = this.apiBase.endsWith('/api') ? this.apiBase.slice(0, -4) : this.apiBase;
            const res = await fetch(`${apiRoot}/api/maintenance/create`, {
                method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ hotel_name: hotelName, room_number: roomNumber, issue, urgency })
            });
            const data = await res.json();
            if (!res.ok || data.error) throw new Error(data.error || 'failed');
            this.showNotification('Maintenance request created', 'success');
            // Refresh modal content softly
            try {
                const el = document.getElementById('maintenance-list');
                if (el) {
                    el.textContent = 'Refreshing maintenance...';
                    // Reuse loader to update sections
                    await this.loadOperationalInfo(hotelName, roomNumber);
                    this._fadeIn(el);
                }
            } catch(_) {}
        } catch(e) { this.showNotification('Failed: ' + (e.message||e), 'error'); }
    }

    async submitMaintenanceInline(hotelName, roomNumber) {
        try {
            const issue = document.getElementById('im-issue')?.value || '';
            let urgency = document.getElementById('im-urgency')?.value || 'NORMAL';
            if (String(urgency).toUpperCase() === 'CRITICAL') urgency = 'URGENT';
            if (!issue) { this.showNotification('Issue required', 'error'); return; }
            const apiRoot = this.apiBase.endsWith('/api') ? this.apiBase.slice(0, -4) : this.apiBase;
            const res = await fetch(`${apiRoot}/api/maintenance/create`, {
                method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ hotel_name: hotelName, room_number: roomNumber, issue, urgency })
            });
            const data = await res.json();
            if (!res.ok || data.error) throw new Error(data.error || 'failed');
            this.showNotification('Maintenance request created', 'success');
            await this.loadOperationalInfo(hotelName, roomNumber);
        } catch(e) { this.showNotification('Failed: ' + (e.message||e), 'error'); }
    }

    async submitMaintenanceUpdate(hotelName) {
        try {
            const taskId = document.getElementById('mnt-task-id')?.value || '';
            const status = document.getElementById('mnt-status')?.value || '';
            const updatedBy = document.getElementById('mnt-updated-by')?.value || '';
            const notes = document.getElementById('mnt-notes')?.value || '';
            const resolution = document.getElementById('mnt-resolution')?.value || '';
            if (!taskId || !status || !updatedBy) { this.showNotification('Task ID, status, and updated-by are required.', 'error'); return; }
            const apiRoot = this.apiBase.endsWith('/api') ? this.apiBase.slice(0, -4) : this.apiBase;
            const res = await fetch(`${apiRoot}/api/maintenance/update`, {
                method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ hotel_name: hotelName, task_id: taskId, status, updated_by_phone: updatedBy, progress_notes: notes, resolution_description: resolution })
            });
            const data = await res.json();
            if (!res.ok || data.error) throw new Error(data.error || 'failed');
            this.showNotification('Maintenance updated', 'success');
        } catch(e) { this.showNotification('Failed: ' + (e.message||e), 'error'); }
    }

    async submitMaintenanceUpdateInline(hotelName) {
        try {
            const taskId = document.getElementById('im-task-id')?.value || '';
            const status = document.getElementById('im-status')?.value || '';
            const updatedBy = document.getElementById('im-updated-by')?.value || '';
            const notes = document.getElementById('im-notes')?.value || '';
            const resolution = document.getElementById('im-resolution')?.value || '';
            if (!taskId || !status || !updatedBy) { this.showNotification('Task ID, status, and updated-by are required.', 'error'); return; }
            const apiRoot = this.apiBase.endsWith('/api') ? this.apiBase.slice(0, -4) : this.apiBase;
            const res = await fetch(`${apiRoot}/api/maintenance/update`, {
                method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ hotel_name: hotelName, task_id: taskId, status, updated_by_phone: updatedBy, progress_notes: notes, resolution_description: resolution })
            });
            const data = await res.json();
            if (!res.ok || data.error) throw new Error(data.error || 'failed');
            this.showNotification('Maintenance updated', 'success');
            await this.loadOperationalInfo(hotelName);
        } catch(e) { this.showNotification('Failed: ' + (e.message||e), 'error'); }
    }
    showNotification(message, type = 'info') {
        try {
            if (window?.app && typeof window.app.showNotification === 'function') {
                window.app.showNotification(message, type);
                return;
            }
        } catch (_) {}
        console[type === 'error' ? 'error' : 'log'](message);
        try { alert(message); } catch (_) {}
    }
    
    createStandaloneMode() {
        // Create standalone API methods
        this.app = {
            hotelsLoaded: false,
            hotels: [],
            getCurrentHotelId: () => this.getCurrentHotelId(),
            loadRooms: (hotelId) => this.loadRooms(hotelId),
            showNotification: (message, type) => this.showNotification(message, type)
        };
    }
    
    async apiRequest(endpoint, options = {}) {
        try {
            const response = await fetch(`${this.apiBase}${endpoint}`, {
                headers: {
                    'Content-Type': 'application/json',
                    ...options.headers
                },
                credentials: 'include', // <-- ensure cookies are sent
                ...options
            });
            
            if (!response.ok) {
                throw new Error(`API Error: ${response.status}`);
            }
            
            return await response.json();
        } catch (error) {
            console.error('API Request Error:', error);
            throw error;
        }
    }
    
    async loadHotels() {
        try {
            const data = await this.apiRequest('/hotels');
            this.hotels = data.hotels || [];
            this.hotelsLoaded = true;
            console.log('✅ Hotels loaded:', this.hotels.length);
        } catch (error) {
            console.error('Error loading hotels:', error);
        }
    }
    
    getCurrentHotelId() {
        try {
            const params = new URLSearchParams(window.location.search || '');
            const hotelParam = params.get('hotel');
            if (hotelParam && hotelParam.trim()) return hotelParam.trim();
        } catch (_) {}
        try {
            const m = document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/);
            if (m && m[1]) return decodeURIComponent(m[1]);
        } catch (_) {}
        return this.hotelName || '';
    }
    
    async loadRooms(hotelId) {
        try {
            const data = await this.apiRequest(`/hotels/${hotelId}/rooms`);
            this.rooms = data.rooms || [];
            console.log('✅ Rooms loaded:', this.rooms.length);
            return this.rooms;
        } catch (error) {
            console.error('Error loading rooms:', error);
            return [];
        }
    }
    
    async loadManagerContext() {
        try {
            const response = await fetch(`${this.apiBase.replace('/api', '')}/api/auth/active`);
            if (response.ok) {
                const data = await response.json();
                if (data.success && data.manager) {
                    this.managerContext = data.manager;
                    this.currentHotel = data.manager.hotel_name;
                    console.log('✅ Manager context loaded:', this.managerContext);
                } else {
                    console.warn('No active manager found');
                    window.location.href = 'login.html';
                }
            } else {
                window.location.href = 'login.html';
            }
        } catch (error) {
            console.error('Error loading manager context:', error);
            window.location.href = 'login.html';
        }
    }

    async initialize() {
        this.renderRoomsView();
        this.loadRoomsData();
        this.setupEventListeners();
    }
    
    renderRoomsView() {
        const roomsContent = document.getElementById('rooms-content');
        if (!roomsContent) return;
        
        roomsContent.innerHTML = `
            <!-- Rooms Header -->
            <div class="flex justify-between items-center mb-6">
                <div>
                    <h1 class="text-3xl font-bold text-gray-900">Room Management</h1>
                    <p class="text-gray-600 mt-1">Manage room status, assignments, and properties</p>
                </div>
                <div class="flex space-x-3">
                    <button onclick="rooms.addNewRoom()" 
                            class="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 transition-colors flex items-center">
                        <i class="fas fa-plus mr-2"></i>
                        Add Room
                    </button>
                    <button onclick="rooms.bulkActions()" 
                            class="bg-green-600 text-white px-4 py-2 rounded-lg hover:bg-green-700 transition-colors flex items-center">
                        <i class="fas fa-tasks mr-2"></i>
                        Bulk Actions
                    </button>
                </div>
            </div>
            
            <!-- Room Status Summary -->
            <div class="grid grid-cols-2 md:grid-cols-6 gap-4 mb-6">
                <div class="card p-4 text-center">
                    <div class="text-2xl font-bold text-gray-900" id="total-rooms">-</div>
                    <div class="text-sm text-gray-600">Total Rooms</div>
                </div>
                <div class="card p-4 text-center">
                    <div class="text-2xl font-bold text-green-600" id="available-rooms">-</div>
                    <div class="text-sm text-gray-600">Available</div>
                </div>
                <div class="card p-4 text-center">
                    <div class="text-2xl font-bold text-yellow-600" id="occupied-rooms">-</div>
                    <div class="text-sm text-gray-600">Occupied</div>
                </div>
                <div class="card p-4 text-center">
                    <div class="text-2xl font-bold text-orange-600" id="dirty-rooms">-</div>
                    <div class="text-sm text-gray-600">Dirty</div>
                </div>
                <div class="card p-4 text-center">
                    <div class="text-2xl font-bold text-red-600" id="maintenance-rooms">-</div>
                    <div class="text-sm text-gray-600">Maintenance</div>
                </div>
                <div class="card p-4 text-center">
                    <div class="text-2xl font-bold text-gray-600" id="blocked-rooms">-</div>
                    <div class="text-sm text-gray-600">Blocked</div>
                </div>
            </div>
            
            <!-- Rooms Grid/List View -->
            <div class="card p-6">
                <div id="rooms-container" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                    <!-- Rooms will be populated here -->
                </div>
            </div>
            
            <!-- Bulk Actions Panel -->
            <div id="bulk-actions-panel" class="fixed bottom-4 left-1/2 transform -translate-x-1/2 bg-white border rounded-lg shadow-lg p-4 hidden">
                <div class="flex items-center space-x-4">
                    <span class="text-sm text-gray-600" id="selected-count">0 rooms selected</span>
                    <button onclick="rooms.bulkStatusUpdate()" class="bg-blue-600 text-white px-3 py-1 rounded text-sm">
                        Update Status
                    </button>
                    <button onclick="rooms.bulkAssignHousekeeping()" class="bg-green-600 text-white px-3 py-1 rounded text-sm">
                        Assign Housekeeping
                    </button>
                    <button onclick="rooms.clearSelection()" class="bg-gray-600 text-white px-3 py-1 rounded text-sm">
                        Clear Selection
                    </button>
                </div>
            </div>
        `;
    }
    
    async loadRoomsData() {
        try {
            console.log('🏨 Loading rooms data...');
            // Fetch canonical room portfolio structure (context-locked)
            const hotelName = await this.ensureHotel();
            this.hotelName = hotelName;
            if (!hotelName) {
                this.roomTypes = [];
                this.roomNumbers = [];
                this.renderRoomTypesAndRoomNumbers();
                return;
            }
            const query = `/hotels/rooms?hotel_name=${encodeURIComponent(hotelName)}`;
            const data = await this.apiRequest(query);
            console.log('API response:', data);
            this.roomTypes = data.room_types || [];
            this.roomNumbers = this.roomTypes.flatMap(rt => (rt.room_numbers || []).map(rn => ({...rn, room_type: rt.room_type})));
            console.log('Set roomTypes:', this.roomTypes);
            console.log('Set roomNumbers:', this.roomNumbers);
            await this.enrichWithReadinessBulk();
            this.updateRoomsSummary(this.roomNumbers);
            this.renderRoomTypesAndRoomNumbers();
        } catch (error) {
            console.error('Error loading rooms:', error);
            this.showNotification('Error loading rooms: ' + (error?.message || error), 'error');
        }
    }   

    renderRoomTypesAndRoomNumbers() {
        const container = document.getElementById('rooms-container');
        if (!container) return;
        console.log('Rendering room types:', this.roomTypes);
        let html = '';
        this.roomTypes.forEach(rt => {
            const key = String(rt.room_type || '').toUpperCase();
            html += `<div class="mb-6 p-4 border rounded-lg bg-white">
                <h2 class="text-xl font-bold mb-2">${rt.room_type}</h2>
                <div class="mb-2 text-gray-600">${rt.description || ''}</div>`;
            // Removed capacity, size, view, and amenities lines per request
            html += `<div class="mt-3 flex items-center justify-end gap-4">
                        <button class="text-blue-600 hover:text-blue-800 text-sm" data-room-type="${this.escapeHtml(rt.room_type || '')}" onclick="rooms.editRooms(this)">
                            <i class="fas fa-door-open mr-1"></i>Edit room
                        </button>
                        <button class="text-blue-600 hover:text-blue-800 text-sm" onclick="rooms.editRoomTypeSpecs('${(rt.room_type || '').replace(/'/g, "\'")}')">
                            <i class="fas fa-edit mr-1"></i>Edit specifications
                        </button>
                      </div>`;
            html += `<div class="mt-2">`;
            // Use canonical room_numbers for this type
            const roomNumbers = rt.room_numbers || [];
            if (roomNumbers.length > 0) {
                html += `<div class="grid grid-cols-2 md:grid-cols-4 gap-2">`;
                roomNumbers.forEach(room => {
                    const st = String(room.room_status || '').toLowerCase();
                    const colorClass = st === 'clean' ? 'bg-green-50 border-green-200 text-green-700'
                        : st === 'occupied' ? 'bg-yellow-50 border-yellow-200 text-yellow-700'
                        : st === 'dirty' ? 'bg-yellow-50 border-yellow-200 text-yellow-700'
                        : st === 'blocked' ? 'bg-red-50 border-red-200 text-red-700'
                        : 'bg-gray-50 border-gray-200 text-gray-700';
                    const shown = room.room_number;
                    html += `<div class="p-2 border rounded ${colorClass}">
                        <button class="font-semibold underline" onclick="rooms.openOperationalStatusModal('${(rt.hotel_name||'').toUpperCase() || this.getCurrentHotelId()}','${String(rt.room_type||'').toUpperCase()}','${String(room.room_number)}'); return false;">${this.escapeHtml(shown)}</button>
                        <div class="text-xs">Status: ${room.room_status}</div>
                    </div>`;
                });
                html += `</div>`;
            } else {
                html += `<div class="text-gray-400 italic">No room numbers created</div>`;
            }
            html += `</div></div>`;
        });
        container.innerHTML = html;
    }
    

    editRoomTypeSpecs(room_type) {
        const rt = (this.roomTypes || []).find(x => String(x.room_type) === String(room_type));
        const specs = (rt && rt.specifications) || {};
        const fields = [
            { key: 'room_size_sqm', label: 'ROOM SIZE (SQM)', type: 'number' },
            { key: 'max_occupancy', label: 'MAX OCCUPANCY', type: 'number' },
            { key: 'bed_type', label: 'BED TYPE', type: 'text' },
            { key: 'bed_count', label: 'BED COUNT', type: 'number' },
            { key: 'wifi', label: 'WIFI', type: 'bool' },
            { key: 'air_conditioning', label: 'AIR CONDITIONING', type: 'bool' },
            { key: 'private_bathroom', label: 'PRIVATE BATHROOM', type: 'bool' },
            { key: 'room_view', label: 'ROOM VIEW', type: 'text' },
            { key: 'price_tier', label: 'PRICE TIER', type: 'text' },
            { key: 'floor_number', label: 'FLOOR NUMBER', type: 'number' },
            { key: 'window_type', label: 'WINDOW TYPE', type: 'text' },
            { key: 'bathroom_type', label: 'BATHROOM TYPE', type: 'text' },
            { key: 'soundproofing', label: 'SOUNDPROOFING', type: 'bool' },
            { key: 'smart_features', label: 'SMART FEATURES', type: 'text' },
            { key: 'kitchenette', label: 'KITCHENETTE', type: 'bool' },
            { key: 'minibar', label: 'MINIBAR', type: 'bool' },
            { key: 'workspace_type', label: 'WORKSPACE TYPE', type: 'text' },
            { key: 'accessibility_features', label: 'ACCESSIBILITY FEATURES', type: 'text' },
            { key: 'renovation_year', label: 'RENOVATION YEAR', type: 'number' },
            { key: 'flooring_type', label: 'FLOORING TYPE', type: 'text' },
            { key: 'lighting', label: 'LIGHTING', type: 'text' },
            { key: 'air_purifier', label: 'AIR PURIFIER', type: 'bool' },
            { key: 'pet_friendly', label: 'PET FRIENDLY', type: 'bool' },
            { key: 'connecting_rooms', label: 'CONNECTING ROOMS', type: 'bool' },
            { key: 'max_children', label: 'MAX CHILDREN', type: 'number' },
            { key: 'crib_available', label: 'CRIB AVAILABLE', type: 'bool' },
        ];
        let grid = '';
        fields.forEach(f => {
            const id = `rt-${f.key}`;
            const val = specs[f.key];
            if (f.type === 'bool') {
                const sel = `<select id="${id}" class="border rounded px-2 py-1 w-full"><option value="">N/A</option><option value="true" ${val===true?'selected':''}>YES</option><option value="false" ${val===false?'selected':''}>NO</option></select>`;
                grid += `<div><label class=\"block text-xs text-gray-600\">${f.label}</label>${sel}</div>`;
            } else {
                const typeAttr = f.type === 'number' ? 'number' : 'text';
                const step = f.key === 'room_size_sqm' ? ' step="0.1"' : '';
                grid += `<div><label class=\"block text-xs text-gray-600\">${f.label}</label><input type=\"${typeAttr}\" id=\"${id}\" class=\"border rounded px-2 py-1 w-full\" value=\"${val ?? ''}\"${step}></div>`;
            }
        });
        const desc = String(rt?.description || '');
        const html = `
            <h3 class="text-lg font-semibold mb-3">Edit ${room_type} specifications</h3>
            <form id="edit-rt-form" class="space-y-3">
                <div class="grid grid-cols-4 gap-3">${grid}</div>
                <div class="mt-2">
                    <label class="block text-xs text-gray-600">DESCRIPTION</label>
                    <textarea id="rt-description" class="border rounded px-2 py-1 w-full" rows="3">${this.escapeHtml(desc)}</textarea>
                </div>
                <div class="flex justify-between items-center space-x-2 mt-2">
                    <button type="button" class="bg-red-600 text-white px-3 py-1 rounded" onclick="rooms.confirmDeleteRoomType('${room_type}')">DELETE ROOM TYPE</button>
                    <button type="button" class="bg-gray-200 text-gray-800 px-3 py-1 rounded" onclick="document.getElementById('simple-modal').remove()">Cancel</button>
                    <button type="submit" class="bg-blue-600 text-white px-3 py-1 rounded">Save</button>
                </div>
            </form>`;
        this.showSimpleModal(html);
        const form = document.getElementById('edit-rt-form');
        form.onsubmit = async (e) => {
            e.preventDefault();
            const specifications = {};
            fields.forEach(f => {
                const el = document.getElementById(`rt-${f.key}`);
                if (!el) return;
                let v = el.value;
                if (f.type === 'bool') {
                    if (v === 'true') specifications[f.key] = true;
                    else if (v === 'false') specifications[f.key] = false;
                } else if (f.type === 'number') {
                    if (v === '' || v == null) return;
                    const num = f.key === 'room_size_sqm' ? parseFloat(v) : parseInt(v, 10);
                    if (!Number.isNaN(num)) specifications[f.key] = num;
                } else {
                    v = (v || '').trim();
                    if (v) specifications[f.key] = v.toUpperCase();
                }
            });
            const descriptionVal = String(document.getElementById('rt-description')?.value || '').trim();
            if (descriptionVal) specifications.description = descriptionVal;
            try {
                const res = await fetch(`${this.apiBase}/room_types/manage`, {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ room_type: room_type, specifications })
                });
                const out = await res.json();
                if (!res.ok || out.error) throw new Error(out.error || 'Failed');
                this.showNotification('Room specifications updated', 'success');
                document.getElementById('simple-modal').remove();
                this.loadRoomsData();
            } catch (e) {
                this.showNotification('Failed to update: ' + (e.message || e), 'error');
            }
        };
    }

    async confirmDeleteRoomType(room_type) {
        try {
            if (!confirm('Delete this room type and all its rooms?')) return;
            const hotelName = this.getCurrentHotelId();
            const res = await fetch(`${this.apiBase}/room_types/delete`, {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ hotel_name: hotelName, room_type: room_type })
            });
            const out = await res.json();
            if (!res.ok || out.error) throw new Error(out.error || 'Failed');
            this.showNotification('Room type deleted', 'success');
            const modal = document.getElementById('simple-modal'); if (modal) modal.remove();
            this.loadRoomsData();
        } catch (e) {
            this.showNotification('Failed to delete room type: ' + (e.message||e), 'error');
        }
    }

    async enrichWithReadinessBulk() {
        const cookieHotel = (document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)?.[1] ? decodeURIComponent(document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)[1]) : '');
        const hotelName = this.getCurrentHotelId ? this.getCurrentHotelId() : cookieHotel;
        try {
            const apiRoot = this.apiBase.endsWith('/api') ? this.apiBase.slice(0, -4) : this.apiBase;
            const res = await fetch(`${apiRoot}/api/rooms/readiness_status_all?hotel_name=${encodeURIComponent(hotelName)}`, { credentials: 'include' });
            const data = await res.json();
            const map = new Map();
            (data.items || []).forEach(x => map.set(String(x.room_number), String(x.readiness_status).toLowerCase()));
            this.roomNumbers.forEach(rn => {
                const st = map.get(String(rn.room_number));
                if (st) rn.room_status = st;
            });
        } catch (e) {
            // fail silently
        }
    }
    
    updateRoomsSummary(rooms) {
        const statusCounts = {
            total: rooms.length,
            clean: rooms.filter(r => r.room_status === 'clean').length,
            occupied: rooms.filter(r => r.room_status === 'occupied').length,
            cleaning: rooms.filter(r => r.room_status === 'cleaning').length,
            dirty: rooms.filter(r => r.room_status === 'dirty').length,
            maintenance: rooms.filter(r => r.room_status === 'maintenance').length,
            blocked: rooms.filter(r => r.room_status === 'blocked').length
        };
        const setIf = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
        setIf('total-rooms', statusCounts.total);
        setIf('clean-rooms', statusCounts.clean);
        setIf('occupied-rooms', statusCounts.occupied);
        setIf('cleaning-rooms', statusCounts.cleaning);
        setIf('dirty-rooms', statusCounts.dirty);
        setIf('maintenance-rooms', statusCounts.maintenance);
        setIf('blocked-rooms', statusCounts.blocked);
    }
    
    renderRooms(rooms) {
        const container = document.getElementById('rooms-container');
        if (!container) return;
        
        const roomsHTML = rooms.map(room => this.renderRoomCard(room)).join('');
        container.innerHTML = roomsHTML;
        
        // Make rooms draggable for assignments
        this.initializeDragAndDrop();
    }
    
    renderRoomCard(room) {
        const statusClass = this.getRoomStatusClass(room.room_status);
        const statusText = this.getRoomStatusText(room.room_status);
        
        return `
            <div class="room-card border rounded-lg p-4 transition-all hover:shadow-md cursor-pointer ${statusClass}"
                 data-room-id="${room.room_id}" onclick="rooms.selectRoom('${room.room_id}')">
                <div class="flex items-center justify-between mb-3">
                    <div class="flex items-center space-x-2">
                        <input type="checkbox" class="room-checkbox" data-room-id="${room.room_id}" 
                               onchange="rooms.toggleRoomSelection('${room.room_id}', this.checked)">
                        <h3 class="text-lg font-semibold text-gray-900">${room.room_number}</h3>
                    </div>
                    <button onclick="rooms.editRoom('${room.room_id}'); event.stopPropagation();" 
                            class="text-gray-400 hover:text-gray-600">
                        <i class="fas fa-edit"></i>
                    </button>
                </div>
                
                <div class="space-y-2">
                    <div class="flex items-center justify-between">
                        <span class="text-sm text-gray-600">Floor ${room.floor || 'N/A'}</span>
                        <span class="status-badge ${statusClass}">${statusText}</span>
                    </div>
                    
                    <div class="text-sm text-gray-600">
                        <i class="fas fa-bed mr-1"></i>
                        ${room.room_type || 'Standard Room'}
                    </div>
                    
                    ${room.guest_name ? `
                        <div class="text-sm text-blue-600">
                            <i class="fas fa-user mr-1"></i>
                            ${room.guest_name}
                        </div>
                    ` : ''}
                    
                    ${room.checkout_date ? `
                        <div class="text-sm text-gray-600">
                            <i class="fas fa-calendar mr-1"></i>
                            Check-out: ${this.app.formatDate(room.checkout_date)}
                        </div>
                    ` : ''}
                </div>
                
                <div class="mt-3 flex space-x-2">
                    <button onclick="rooms.quickStatusUpdate('${room.room_id}'); event.stopPropagation();" 
                            class="flex-1 bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs py-1 px-2 rounded transition-colors">
                        Quick Status
                    </button>
                    <button onclick="rooms.assignGuest('${room.room_id}'); event.stopPropagation();" 
                            class="flex-1 bg-blue-100 hover:bg-blue-200 text-blue-700 text-xs py-1 px-2 rounded transition-colors">
                        Assign Guest
                    </button>
                </div>
            </div>
        `;
    }
    
    getRoomStatusClass(status) {
        switch (String(status || '').toLowerCase()) {
            case 'occupied': return 'bg-yellow-50 border-yellow-200';
            case 'clean': return 'bg-green-50 border-green-200';
            case 'dirty':
            case 'cleaning': return 'bg-orange-50 border-orange-200';
            case 'maintenance': return 'bg-red-50 border-red-200';
            case 'blocked': return 'bg-gray-50 border-gray-200';
            default: return 'bg-gray-50 border-gray-200';
        }
    }
    
    getRoomStatusText(status) {
        switch (String(status || '').toLowerCase()) {
            case 'occupied': return 'Occupied';
            case 'clean': return 'Clean';
            case 'dirty': return 'Dirty';
            case 'cleaning': return 'Cleaning';
            case 'maintenance': return 'Maintenance';
            case 'blocked': return 'Blocked';
            default: return 'Unknown';
        }
    }
    
    selectRoom(roomId) {
        // Remove previous selection
        document.querySelectorAll('.room-card').forEach(card => {
            card.classList.remove('ring-2', 'ring-blue-500');
        });
        
        // Add selection to clicked room
        const roomCard = document.querySelector(`[data-room-id="${roomId}"]`);
        if (roomCard) {
            roomCard.classList.add('ring-2', 'ring-blue-500');
        }
        
        this.showRoomDetails(roomId);
    }
    
    showRoomDetails(roomId) {
        const room = this.roomNumbers.find(r => r.room_id === roomId);
        if (!room) return;
        
        // Create room details modal
        const modal = document.createElement('div');
        modal.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50';
        modal.innerHTML = `
            <div class="bg-white rounded-lg p-6 w-full max-w-2xl max-h-[90vh] overflow-y-auto">
                <div class="flex justify-between items-center mb-4">
                    <h2 class="text-xl font-semibold">Room ${room.room_number} Details</h2>
                    <button onclick="this.closest('.fixed').remove()" class="text-gray-400 hover:text-gray-600">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
                
                <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div>
                        <h3 class="font-semibold mb-2">Room Information</h3>
                        <div class="space-y-2 text-sm">
                            <div><strong>Number:</strong> ${room.room_number}</div>
                            <div><strong>Floor:</strong> ${room.floor || 'N/A'}</div>
                            <div><strong>Type:</strong> ${room.room_type || 'Standard'}</div>
                            <div><strong>Status:</strong> ${this.getRoomStatusText(room.room_status)}</div>
                        </div>
                    </div>
                    
                    <div>
                        <h3 class="font-semibold mb-2">Guest Information</h3>
                        <div class="space-y-2 text-sm">
                            ${room.guest_name ? `
                                <div><strong>Guest:</strong> ${room.guest_name}</div>
                                <div><strong>Check-in:</strong> ${this.app.formatDate(room.checkin_date)}</div>
                                <div><strong>Check-out:</strong> ${this.app.formatDate(room.checkout_date)}</div>
                            ` : '<div class="text-gray-500">No guest assigned</div>'}
                        </div>
                    </div>
                </div>
                
                <div class="mt-6 flex space-x-3">
                    <button onclick="rooms.editRoom('${room.room_id}'); this.closest('.fixed').remove();" 
                            class="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700">
                        Edit Room
                    </button>
                    <button onclick="rooms.quickStatusUpdate('${room.room_id}'); this.closest('.fixed').remove();" 
                            class="bg-green-600 text-white px-4 py-2 rounded-lg hover:bg-green-700">
                        Update Status
                    </button>
                    <button onclick="rooms.assignGuest('${room.room_id}'); this.closest('.fixed').remove();" 
                            class="bg-purple-600 text-white px-4 py-2 rounded-lg hover:bg-purple-700">
                        Assign Guest
                    </button>
                </div>
            </div>
        `;
        
        document.body.appendChild(modal);
    }
    
    quickStatusUpdate(roomId) {
        const room = this.roomNumbers.find(r => r.room_id === roomId);
        if (!room) return;
        
        const statusOptions = [
            { value: 'CLEAN_VACANT', label: 'Available (Clean)' },
            { value: 'DIRTY_VACANT', label: 'Dirty (Needs Cleaning)' },
            { value: 'OCCUPIED', label: 'Occupied' },
            { value: 'MAINTENANCE', label: 'Maintenance Required' },
            { value: 'BLOCKED', label: 'Blocked' }
        ];
        
        const modal = document.createElement('div');
        modal.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50';
        modal.innerHTML = `
            <div class="bg-white rounded-lg p-6 w-full max-w-md">
                <h3 class="text-lg font-semibold mb-4">Update Room ${room.room_number} Status</h3>
                <div class="space-y-3">
                    ${statusOptions.map(option => `
                        <button onclick="rooms.updateRoomStatus('${roomId}', '${option.value}'); this.closest('.fixed').remove();" 
                                class="w-full text-left p-3 rounded-lg border hover:bg-gray-50 transition-colors ${room.room_status === option.value ? 'bg-blue-50 border-blue-200' : ''}">
                            ${option.label}
                        </button>
                    `).join('')}
                </div>
                <div class="mt-4 flex justify-end">
                    <button onclick="this.closest('.fixed').remove()" 
                            class="bg-gray-200 text-gray-800 px-4 py-2 rounded-lg hover:bg-gray-300">
                        Cancel
                    </button>
                </div>
            </div>
        `;
        
        document.body.appendChild(modal);
    }
    
    async updateRoomStatus(roomId, newStatus) {
        try {
            const hotelId = this.getCurrentHotelId();
            await this.app.updateRoomStatus(hotelId, roomId, newStatus);
            this.loadRoomsData(); // Refresh rooms data
        } catch (error) {
            console.error('Error updating room status:', error);
            this.app.showNotification('Error updating room status', 'error');
        }
    }
    
    toggleRoomSelection(roomId, selected) {
        if (selected) {
            this.selectedRooms.push(roomId);
        } else {
            this.selectedRooms = this.selectedRooms.filter(id => id !== roomId);
        }
        
        this.updateBulkActionsPanel();
    }
    
    updateBulkActionsPanel() {
        const panel = document.getElementById('bulk-actions-panel');
        const count = document.getElementById('selected-count');
        
        if (this.selectedRooms.length > 0) {
            panel.classList.remove('hidden');
            count.textContent = `${this.selectedRooms.length} room${this.selectedRooms.length > 1 ? 's' : ''} selected`;
        } else {
            panel.classList.add('hidden');
        }
    }
    
    clearSelection() {
        this.selectedRooms = [];
        document.querySelectorAll('.room-checkbox').forEach(checkbox => {
            checkbox.checked = false;
        });
        this.updateBulkActionsPanel();
    }
    
    // Refactor search, sort, filter, and selection logic to use this.roomNumbers only
    filterRooms(filter) {
        this.filterBy = filter;
        if (filter === 'all') {
            // Restore original per-room_type section view
            this.isFiltered = false;
            this._lastFilteredRooms = null;
            this.renderRoomTypesAndRoomNumbers();
            return;
        }
        const norm = String(filter || '').toLowerCase();
        const filteredRooms = this.roomNumbers.filter(room => {
            const status = String(room.room_status || '').toLowerCase();
            if (!norm) return true;
            if (norm === 'available') return status === 'clean';
            if (norm === 'dirty') return status === 'dirty' || status === 'cleaning';
            return status.indexOf(norm) !== -1;
        });
        this.isFiltered = true;
        this._lastFilteredRooms = filteredRooms;
        this.renderRooms(filteredRooms);
    }

    sortRooms(sortBy) {
        this.sortBy = sortBy;
        const sortedRooms = [...this.roomNumbers].sort((a, b) => {
            const aVal = a[sortBy] || '';
            const bVal = b[sortBy] || '';
            return aVal.toString().localeCompare(bVal.toString());
        });
        this.renderRooms(sortedRooms);
    }

    searchRooms(query) {
        if (!query.trim()) {
            this.renderRooms(this.roomNumbers);
            return;
        }
        const filteredRooms = this.roomNumbers.filter(room => 
            (room.room_number || '').toLowerCase().includes(query.toLowerCase()) ||
            (room.guest_name && room.guest_name.toLowerCase().includes(query.toLowerCase())) ||
            (room.room_type && room.room_type.toLowerCase().includes(query.toLowerCase()))
        );
        this.renderRooms(filteredRooms);
    }

    setViewMode(mode) {
        this.viewMode = mode;
        const container = document.getElementById('rooms-container');
        if (mode === 'list') {
            container.className = 'space-y-2';
        } else {
            container.className = 'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4';
        }
        if (this.isFiltered && Array.isArray(this._lastFilteredRooms)) {
            this.renderRooms(this._lastFilteredRooms);
        } else {
            this.renderRoomTypesAndRoomNumbers();
        }
    }
    
    askLeonAboutRooms() {
        if (!this.app.chatOpen) {
            this.app.toggleChat();
        }
        
        setTimeout(() => {
            const chatInput = document.getElementById('chat-input');
            if (chatInput) {
                chatInput.value = 'Show me room status summary and any recommendations for room management';
                this.app.sendMessageToLeon();
            }
        }, 500);
    }
    
    initializeDragAndDrop() {
        // Initialize drag and drop for room assignments
        const roomCards = document.querySelectorAll('.room-card');
        roomCards.forEach(card => {
            card.draggable = true;
            card.addEventListener('dragstart', this.handleDragStart.bind(this));
            card.addEventListener('dragover', this.handleDragOver.bind(this));
            card.addEventListener('drop', this.handleDrop.bind(this));
        });
    }
    
    handleDragStart(e) {
        e.dataTransfer.setData('text/plain', e.target.dataset.roomId);
    }
    
    handleDragOver(e) {
        e.preventDefault();
    }
    
    handleDrop(e) {
        e.preventDefault();
        const draggedRoomId = e.dataTransfer.getData('text/plain');
        const targetRoomId = e.target.closest('.room-card')?.dataset.roomId;
        
        if (draggedRoomId && targetRoomId && draggedRoomId !== targetRoomId) {
            // Handle room reassignment logic
            console.log('Reassigning room:', draggedRoomId, 'to:', targetRoomId);
        }
    }
    
    setupEventListeners() {
        // Add any additional event listeners here
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.clearSelection();
            }
        });
    }
    
    editRooms(button) {
        const roomType = button && button.getAttribute('data-room-type');
        this.addNewRoom(roomType || '');
    }

    appendEditRoomRow(wrap, room, saved) {
        const row = document.createElement('div');
        row.className = 'flex items-center gap-2';
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'add-room-number border rounded-lg px-3 py-2 w-full';
        input.placeholder = 'Room number';
        if (saved) {
            input.value = room.room_number;
            input.setAttribute('data-room-number', room.room_number);
        } else {
            input.setAttribute('data-new', '1');
        }
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'text-red-600 text-sm font-semibold flex-shrink-0';
        remove.textContent = 'Remove';
        remove.onclick = () => this.removeEditRoom(row, input.getAttribute('data-room-number') || '', !!input.getAttribute('data-room-number'));
        row.appendChild(input);
        row.appendChild(remove);
        wrap.appendChild(row);
    }

    async removeEditRoom(row, roomNumber, saved) {
        const roomType = String(this.editingRoomType || '').trim().toUpperCase();
        if (!saved) {
            row.remove();
            return;
        }
        if (!window.confirm('Remove ' + roomNumber + '?')) return;
        const hotelName = String(this.getCurrentHotelId() || '').toUpperCase();
        try {
            const res = await fetch(`${this.apiBase}/rooms/remove`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ hotel_name: hotelName, room_type: roomType, room_number: roomNumber })
            });
            const out = await res.json();
            if (!res.ok || (out && out.error)) throw new Error(out?.error || 'Remove failed');
            window.location.reload();
        } catch (err) {
            this.showNotification(err.message, 'error');
        }
    }

    // Edit the room numbers of one room type
    addNewRoom(roomType) {
        const modal = document.getElementById('add-room-modal');
        modal.classList.remove('hidden');
        const title = document.getElementById('add-room-title');
        const submit = document.getElementById('add-room-submit');
        if (title) title.textContent = 'EDIT ROOM';
        if (submit) submit.textContent = 'SAVE';
        this.editingRoomType = roomType || '';
        const typeRow = (this.roomTypes || []).find(rt => String(rt.room_type) === String(roomType));
        const existing = ((typeRow && typeRow.room_numbers) || []).filter(room => room && room.room_number);
        document.getElementById('add-room-form').reset();
        const wrap = document.getElementById('add-room-numbers');
        if (wrap) {
            wrap.innerHTML = '';
            existing.forEach(room => this.appendEditRoomRow(wrap, room, true));
        }
        const more = document.getElementById('add-room-more');
        if (more) {
            more.onclick = (ev) => {
                ev.preventDefault();
                this.appendEditRoomRow(wrap, {}, false);
            };
        }
        const form = document.getElementById('add-room-form');
        form.onsubmit = async (e) => {
            e.preventDefault();
            const inputs = Array.from(document.querySelectorAll('#add-room-numbers .add-room-number'));
            const room_type = String(this.editingRoomType || '').trim().toUpperCase();
            if (!room_type) { this.showNotification('Room type is required', 'error'); return; }
            const hotelName = String(this.getCurrentHotelId() || '').toUpperCase();
            const room_numbers = [];
            const changes = [];
            const originals = {};
            const seen = {};
            let duplicate = '';
            inputs.forEach(input => {
                const value = input.value.trim().toUpperCase();
                const original = String(input.getAttribute('data-room-number') || '').trim().toUpperCase();
                if (!value) { duplicate = 'required'; return; }
                if (seen[value]) { duplicate = value; return; }
                seen[value] = true;
                if (original) {
                    originals[original] = true;
                    if (value !== original) changes.push({ room_number: original, new_room_number: value });
                } else {
                    room_numbers.push(value);
                }
            });
            if (duplicate === 'required') { this.showNotification('Room number is required', 'error'); return; }
            if (duplicate) { this.showNotification('Room number ' + duplicate + ' is already used', 'error'); return; }
            (this.roomTypes || []).forEach(rt => {
                const same = String(rt.room_type || '').trim().toUpperCase() === room_type;
                (rt.room_numbers || []).forEach(room => {
                    const key = String(room.room_number || '').trim().toUpperCase();
                    if (!key || (same && originals[key])) return;
                    if (seen[key]) duplicate = key;
                });
            });
            if (duplicate) { this.showNotification('Room number ' + duplicate + ' is already used', 'error'); return; }
            if (!changes.length && !room_numbers.length) {
                document.getElementById('add-room-modal').classList.add('hidden');
                return;
            }
            try {
                try {
                    const s = document.getElementById('add-room-status');
                    const t = document.getElementById('add-room-status-text');
                    const sp = document.getElementById('add-room-spinner');
                    if (s && t && sp) { s.classList.remove('hidden'); sp.classList.remove('hidden'); t.textContent = 'PROCESSING...'; t.classList.remove('text-green-700'); t.classList.add('text-blue-700'); }
                } catch(_) {}
                if (changes.length) {
                    const renamedRes = await fetch(`${this.apiBase}/rooms/rename`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        credentials: 'include',
                        body: JSON.stringify({ hotel_name: hotelName, room_type, rooms: changes })
                    });
                    const renamedOut = await renamedRes.json();
                    if (!renamedRes.ok || (renamedOut && renamedOut.error)) { throw new Error(renamedOut?.error || 'Save failed'); }
                }
                if (!room_numbers.length) {
                    try { sessionStorage.setItem('rooms_reopen_modal', 'add_room_success'); } catch(_) {}
                    window.location.reload();
                    return;
                }
                const addedRes = await fetch(`${this.apiBase}/rooms/add`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify({ hotel_name: hotelName, room_type, room_numbers })
                });
                const addedOut = await addedRes.json();
                if (!addedRes.ok || (addedOut && addedOut.error)) { throw new Error(addedOut?.error || 'Add room failed'); }
                // Full refresh while keeping modal static after reload
                try { sessionStorage.setItem('rooms_reopen_modal', 'add_room_success'); } catch(_) {}
                window.location.reload();
            } catch (err) {
                try {
                    const s = document.getElementById('add-room-status');
                    const t = document.getElementById('add-room-status-text');
                    const sp = document.getElementById('add-room-spinner');
                    if (s && t && sp) { s.classList.remove('hidden'); sp.classList.add('hidden'); t.textContent = err.message; t.classList.remove('text-blue-700'); t.classList.add('text-red-700'); }
                } catch (_) {}
                this.showNotification(err.message, 'error');
            }
        };
    }

    // Edit Room Modal
    editRoom(room_id) {
        const room = this.roomNumbers.find(r => r.room_id === room_id);
        if (!room) return;

        // Simple modal for demonstration (replace with a real modal in production)
        const room_type = prompt("Edit room type:", room.room_type);
        if (room_type === null) return;
        const floor = prompt("Edit floor:", room.floor);
        if (floor === null) return;

        const hotelName = this.getCurrentHotelId();
        this.apiRequest(`/hotels/${hotelName}/rooms/${room.room_number}`, {
            method: 'PUT',
            body: JSON.stringify({ room_type, floor })
        })
        .then(() => {
            this.showNotification('Room updated!', 'success');
            this.loadRoomsData();
        })
        .catch(err => this.showNotification('Failed to update room: ' + err.message, 'error'));

        // Add Delete Room option
        setTimeout(() => {
            if (confirm("Delete this room?")) {
                this.deleteRoom(room.room_number);
            }
        }, 100);
    }

    // Delete Room
    deleteRoom(room_number) {
        const hotelName = this.getCurrentHotelId();
        this.apiRequest(`/hotels/${hotelName}/rooms/${room_number}`, {
            method: 'DELETE'
        })
        .then(() => {
            this.showNotification('Room deleted!', 'success');
            this.loadRoomsData();
        })
        .catch(err => this.showNotification('Failed to delete room: ' + err.message, 'error'));
    }
    
    assignGuest(roomId) {
        const room = this.roomNumbers.find(r => r.room_id === roomId || String(r.room_number) === String(roomId));
        const number = room ? room.room_number : roomId;
        window.location.href = `assignments.html?hotel=${encodeURIComponent(this.getCurrentHotelId() || '')}`;
        this.showNotification(`Open Stay View to assign room ${number}`, 'info');
    }
    
    bulkActions() {
        if (!this.selectedRooms.length) {
            this.showNotification('Select rooms first', 'info');
            return;
        }
        this.updateBulkActionsPanel();
    }
    
    async bulkStatusUpdate() {
        if (!this.selectedRooms.length) { this.showNotification('Select rooms first', 'info'); return; }
        const next = window.prompt('Readiness for the selected rooms (clean, dirty, cleaning, occupied)', 'dirty');
        if (!next) return;
        const hotel = await this.ensureHotel();
        let updated = 0;
        for (const roomId of this.selectedRooms) {
            const room = this.roomNumbers.find(r => r.room_id === roomId || String(r.room_number) === String(roomId));
            if (!room) continue;
            const resp = await fetch(`${this.apiBase || (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1' ? 'http://localhost:8002/api' : 'https://api.inap.ai/api')}/rooms/readiness_status/update`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ hotel_name: hotel, room_number: room.room_number, new_status: next, notes: '' })
            });
            if (resp.ok) updated += 1;
        }
        this.showNotification(`Updated ${updated} room${updated === 1 ? '' : 's'}`, 'success');
        this.clearSelection();
        this.loadRoomsData();
    }
    
    async bulkAssignHousekeeping() {
        if (!this.selectedRooms.length) { this.showNotification('Select rooms first', 'info'); return; }
        const staff = window.prompt('Staff phone');
        if (!staff) return;
        const hotel = await this.ensureHotel();
        const api = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1' ? 'http://localhost:8002/api' : 'https://api.inap.ai/api';
        let assigned = 0;
        for (const roomId of this.selectedRooms) {
            const room = this.roomNumbers.find(r => r.room_id === roomId || String(r.room_number) === String(roomId));
            if (!room) continue;
            const resp = await fetch(`${api}/housekeeping/create_task`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ hotel_name: hotel, room_number: room.room_number, staff_phone: staff, task_type: 'standard_cleaning', priority: 'normal', notes: '' })
            });
            if (resp.ok) assigned += 1;
        }
        this.showNotification(`Assigned ${assigned} room${assigned === 1 ? '' : 's'}`, 'success');
        this.clearSelection();
    }

    async ensureHotel() {
        const current = this.getCurrentHotelId();
        if (current) return String(current).toUpperCase();
        const api = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1' ? 'http://localhost:8002/api' : 'https://api.inap.ai/api';
        const res = await fetch(`${api}/auth/me`, { credentials: 'include' });
        if (!res.ok) return '';
        const data = await res.json();
        return String(data.hotel_name || '').toUpperCase();
    }

    // Room Type Management
    showRoomTypesModal() {
        // Show the Manage Room Types modal
        const modal = document.getElementById('manage-room-types-modal');
        modal.classList.remove('hidden');
        this.renderRoomTypesManagement();
    }

    async renderRoomTypesManagement() {
        const hotelName = this.getCurrentHotelId();
        const content = document.getElementById('manage-room-types-content');
        content.innerHTML = '<div class="text-center py-4 text-gray-500">Loading room types...</div>';
        try {
            const data = await this.apiRequest(`/hotels/${hotelName}/room_types`);
            let html = '<h2 class="text-xl font-bold mb-4">Manage Room Types</h2>';
            html += '<button onclick="rooms.addRoomTypeModal()" class="bg-blue-600 text-white px-3 py-1 rounded mb-4">Add Room Type</button>';
            html += '<ul>';
            (data.room_types || []).forEach(rt => {
                html += `<li class="mb-2 border-b pb-2 flex items-center justify-between">
                    <div>
                        <b>${rt.room_type}</b> (Capacity: ${rt.specifications?.max_occupancy || '-'}): ${rt.description || ''}
                    </div>
                    <div>
                        <button onclick="rooms.editRoomTypeModal('${rt.room_type}')" class="ml-2 text-blue-600">Edit</button>
                        <button onclick="rooms.deleteRoomType('${rt.room_type}')" class="ml-2 text-red-600">Delete</button>
                    </div>
                </li>`;
            });
            html += '</ul>';
            content.innerHTML = html;
        } catch (err) {
            content.innerHTML = '<div class="text-red-600">Failed to load room types: ' + err.message + '</div>';
        }
    }

    addRoomTypeModal() {
        const content = document.getElementById('manage-room-types-content');
        content.innerHTML = `
            <h2 class="text-xl font-bold mb-4">Add Room Type</h2>
            <form id="add-room-type-form" class="space-y-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700">Room Type Name</label>
                    <input type="text" id="add-room-type-name" class="border rounded-lg px-3 py-2 w-full" required />
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700">Description</label>
                    <input type="text" id="add-room-type-desc" class="border rounded-lg px-3 py-2 w-full" />
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700">Capacity</label>
                    <input type="number" id="add-room-type-capacity" class="border rounded-lg px-3 py-2 w-full" min="1" />
                </div>
                <div class="flex justify-end">
                    <button type="submit" class="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700">Add</button>
                    <button type="button" onclick="rooms.renderRoomTypesManagement()" class="ml-2 bg-gray-200 text-gray-800 px-4 py-2 rounded-lg hover:bg-gray-300">Cancel</button>
                </div>
            </form>
        `;
        document.getElementById('add-room-type-form').onsubmit = async (e) => {
            e.preventDefault();
            const room_type = document.getElementById('add-room-type-name').value.trim();
            const description = document.getElementById('add-room-type-desc').value.trim();
            const capacity = document.getElementById('add-room-type-capacity').value.trim();
            if (!room_type) {
                this.showNotification('Room type name is required', 'error');
                return;
            }
            const hotelName = this.getCurrentHotelId();
            try {
                await this.apiRequest(`/hotels/${hotelName}/room_types`, {
                    method: 'POST',
                    body: JSON.stringify({ room_type, description, capacity })
                });
                this.showNotification('Room type added!', 'success');
                this.renderRoomTypesManagement();
            } catch (err) {
                this.showNotification('Failed to add room type: ' + err.message, 'error');
            }
        };
    }

    editRoomTypeModal(room_type) {
        // Fetch current data for the room type
        const hotelName = this.getCurrentHotelId();
        this.apiRequest(`/hotels/${hotelName}/room_types`)
            .then(data => {
                const rt = (data.room_types || []).find(r => r.room_type === room_type);
                if (!rt) return;
                const content = document.getElementById('manage-room-types-content');
                content.innerHTML = `
                    <h2 class="text-xl font-bold mb-4">Edit Room Type</h2>
                    <form id="edit-room-type-form" class="space-y-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700">Room Type Name</label>
                            <input type="text" id="edit-room-type-name" class="border rounded-lg px-3 py-2 w-full" value="${rt.room_type}" required />
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700">Description</label>
                            <input type="text" id="edit-room-type-desc" class="border rounded-lg px-3 py-2 w-full" value="${rt.description || ''}" />
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700">Capacity</label>
                            <input type="number" id="edit-room-type-capacity" class="border rounded-lg px-3 py-2 w-full" min="1" value="${rt.specifications?.max_occupancy || ''}" />
                        </div>
                        <div class="flex justify-end">
                            <button type="submit" class="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700">Save</button>
                            <button type="button" onclick="rooms.renderRoomTypesManagement()" class="ml-2 bg-gray-200 text-gray-800 px-4 py-2 rounded-lg hover:bg-gray-300">Cancel</button>
                        </div>
                    </form>
                `;
                document.getElementById('edit-room-type-form').onsubmit = async (e) => {
                    e.preventDefault();
                    const new_room_type = document.getElementById('edit-room-type-name').value.trim();
                    const description = document.getElementById('edit-room-type-desc').value.trim();
                    const capacity = document.getElementById('edit-room-type-capacity').value.trim();
                    if (!new_room_type) {
                        this.showNotification('Room type name is required', 'error');
                        return;
                    }
                    try {
                        await this.apiRequest(`/hotels/${hotelName}/room_types/${room_type}`, {
                            method: 'PUT',
                            body: JSON.stringify({ room_type: new_room_type, description, capacity })
                        });
                        this.showNotification('Room type updated!', 'success');
                        this.renderRoomTypesManagement();
                    } catch (err) {
                        this.showNotification('Failed to update room type: ' + err.message, 'error');
                    }
                };
            });
    }

    deleteRoomType(room_type) {
        if (!confirm('Delete this room type?')) return;
        const hotelName = this.getCurrentHotelId();
        this.apiRequest(`/hotels/${hotelName}/room_types/${room_type}`, {
            method: 'DELETE'
        })
        .then(() => {
            this.showNotification('Room type deleted!', 'success');
            this.renderRoomTypesManagement();
        })
        .catch(err => this.showNotification('Failed to delete room type: ' + err.message, 'error'));
    }

    showSimpleModal(html) {
        let modal = document.getElementById('simple-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'simple-modal';
            modal.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50';
            document.body.appendChild(modal);
        }
        modal.innerHTML = `<div class="bg-white rounded-lg p-6 w-full max-w-lg relative">
            <button onclick="document.getElementById('simple-modal').remove()" class="absolute top-2 right-2 text-gray-400 hover:text-gray-600"><i class="fas fa-times"></i></button>
            ${html}
        </div>`;
    }

    escapeHtml(str) {
        try {
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;');
        } catch (_) {
            return '';
        }
    }
}

// Initialize rooms component
document.addEventListener('DOMContentLoaded', () => {
    document.addEventListener('alpine:init', () => {
        window.rooms = new RoomsComponent(window.app);
    });
}); 