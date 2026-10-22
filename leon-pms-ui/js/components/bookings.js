// Booking Management Component
class BookingComponent {
    constructor() {
        const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
        const apiUrl = isLocal ? 'http://localhost:8002/api' : 'https://api.inap.ai/api';
        this.apiUrl = apiUrl;
        this.currentDate = new Date();
        this.bookings = [];
        this.filteredBookings = [];
        this.managerContext = null;
        this.currentHotel = null;
        
        this.initialize();
    }
    
    // UTC date helpers
    formatYmdLocal(date) {
        const y = date.getUTCFullYear();
        const m = String(date.getUTCMonth() + 1).padStart(2, '0');
        const d = String(date.getUTCDate()).padStart(2, '0');
        return `${y}-${m}-${d}`;
    }

    parseYmdLocal(ymd) {
        if (!ymd) return null;
        const parts = String(ymd).split('-');
        if (parts.length !== 3) return null;
        const y = parseInt(parts[0], 10);
        const m = parseInt(parts[1], 10);
        const d = parseInt(parts[2], 10);
        if (!y || !m || !d) return null;
        return new Date(Date.UTC(y, m - 1, d));
    }

    async initialize() {
        if (!document.getElementById('booking-calendar')) return;
        await this.loadBookings();
        await this.preloadRoomTypes();
        this.updateSummaryCards();
        this.renderCalendar();
        this.renderBookingsTable();
        this.setupEventListeners();
    }

    async preloadRoomTypes() {
        try {
            const hotelParam = (new URLSearchParams(window.location.search || '').get('hotel') || '');
            const cookieHotel = (document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)?.[1] ? decodeURIComponent(document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)[1]) : '');
            const hotelName = (hotelParam || cookieHotel || '').toUpperCase();
            const apiRoot = this.apiUrl.replace('/api', '');
            const sel = document.getElementById('room-type-filter');
            if (!sel) return;
            sel.innerHTML = '';
            // Default option: All room types
            const allOpt = document.createElement('option');
            allOpt.value = '';
            allOpt.textContent = 'All room types';
            sel.appendChild(allOpt);
            const resp = await fetch(`${apiRoot}/api/hotels/rooms?hotel_name=${encodeURIComponent(hotelName)}`, { credentials: 'include' });
            const data = await resp.json();
            const rts = (data && data.room_types) ? data.room_types : [];
            this.roomTypeNames = rts.map(rt => String(rt.room_type || '').toUpperCase()).filter(Boolean);
            rts.forEach(rt => {
                const opt = document.createElement('option');
                const val = String(rt.room_type || '').toUpperCase();
                opt.value = val;
                opt.textContent = String(rt.room_type || '');
                sel.appendChild(opt);
            });
        } catch (e) {
            // Non-fatal
        }
    }
    
    async loadBookings() {
        try {
            const response = await fetch(`${this.apiUrl}/bookings-calendar`, { credentials: 'include' });
            if (response.ok) {
                const data = await response.json();
                this.bookings = data.bookings || [];
                this.filteredBookings = [...this.bookings];
                console.log(`âœ… Loaded ${this.bookings.length} bookings`);
                if (this.bookings.length === 0) {
                    console.log('ðŸ“ No bookings from API - showing empty state');
                }
                return;
            }
            // No fallback to mock data - show empty state
            console.log('ðŸ“ No bookings available - showing empty state');
            this.bookings = [];
            this.filteredBookings = [];
        } catch (error) {
            console.error('Error loading bookings:', error);
            this.bookings = [];
            this.filteredBookings = [];
        }
    }

    showEditBookingModal(bookingId) {
        const modal = document.getElementById('edit-booking-modal');
        if (!modal) return;
        const booking = this.bookings.find(b => String(b.id) === String(bookingId));
        if (!booking) return;
        document.getElementById('eb-booking-id').value = booking.id;
        // Show status chip
        const statusChip = document.getElementById('eb-status');
        const st = String(booking.status || '').toLowerCase();
        if (statusChip) {
            statusChip.textContent = st === 'cancelled' ? 'CANCELLED' : 'CONFIRMED';
            statusChip.className = 'status-badge ' + (st === 'cancelled' ? 'bg-red-100 text-red-800' : 'bg-green-100 text-green-800');
        }
        // Toggle cancel button and save enable based on status
        const cancelBtn = document.getElementById('eb-cancel-btn');
        const saveBtn = document.getElementById('eb-save-btn');
        const isCancelled = (st === 'cancelled');
        if (cancelBtn) cancelBtn.classList.toggle('hidden', isCancelled);
        if (saveBtn) saveBtn.classList.toggle('hidden', isCancelled);
        // Pre-fill range and wire auto-derive similar to Create modal
        const cin = document.getElementById('eb-checkin');
        const cout = document.getElementById('eb-checkout');
        cin.value = (booking.check_in || '').slice(0,10);
        cout.value = (booking.check_out || '').slice(0,10);
        const autoAdvance = () => {
            try {
                const v = (cin.value || '').trim();
                if (!v) { return; }
                if (!(cout && cout._manual)) {
                    const d = this.parseYmdLocal(v);
                    if (d && !isNaN(d)) { d.setUTCDate(d.getUTCDate() + 1); cout.value = this.formatYmdLocal(d); }
                }
            } catch(_) {}
        };
        const renderDerived = () => {
            try {
                const wrap = document.getElementById('eb-staydates');
                if (!wrap) return;
                wrap.innerHTML = '';
                const c0 = (cin.value || '').trim();
                const c1 = (cout.value || '').trim();
                if (!c0 || !c1) return;
                const start = this.parseYmdLocal(c0);
                const end = this.parseYmdLocal(c1);
                if (!(start instanceof Date) || !(end instanceof Date) || isNaN(start) || isNaN(end) || end <= start) return;
                for (let d = new Date(start.getTime()); d < end; d.setUTCDate(d.getUTCDate() + 1)) {
                    const row = document.createElement('div');
                    row.className = 'flex items-center gap-2';
                    row.innerHTML = `
                        <input type="date" class="nb-date border rounded px-3 py-2 flex-1" value="${this.formatYmdLocal(d)}" disabled />
                        <span class="nb-av text-xs"></span>
                    `;
                    wrap.appendChild(row);
                }
            } catch(_) {}
        };
        const triggerEBRefresh = () => {
            try {
                const evt = new Event('change');
                document.getElementById('eb-room-type')?.dispatchEvent(evt);
            } catch(_) {}
        };
        if (cin && !cin._wired_eb) { cin.addEventListener('change', () => { autoAdvance(); renderDerived(); triggerEBRefresh(); }); cin._wired_eb = true; }
        if (cout && !cout._wired_eb) { cout.addEventListener('change', () => { cout._manual = true; renderDerived(); triggerEBRefresh(); }); cout._wired_eb = true; }
        // Initial render
        renderDerived();
        triggerEBRefresh();
        // Rooms select 1..max(10) default to booking.rooms_booked
        const roomsSel = document.getElementById('eb-rooms');
        roomsSel.innerHTML = '';
        const maxOpt = 10;
        for (let i=1;i<=maxOpt;i++){
            const opt=document.createElement('option');
            opt.value=String(i);
            opt.textContent=String(i);
            if (i === parseInt(booking.rooms_booked || 1,10)) opt.selected = true;
            roomsSel.appendChild(opt);
        }
        // Wire availability like create modal but using EB elements (ensure room types loaded first if empty)
        (async () => {
            try {
                const rtSel = document.getElementById('eb-room-type');
                const wrap = document.getElementById('eb-staydates');
                const roomsSelEB = document.getElementById('eb-rooms');
                const hintEB = document.getElementById('eb-availability-hint');
                // Load room types if not yet populated
                if (rtSel && (!rtSel.options || rtSel.options.length === 0)) {
                    try {
                        const hotelParam = (new URLSearchParams(window.location.search || '').get('hotel') || '');
                        const cookieHotel = (document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)?.[1] ? decodeURIComponent(document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)[1]) : '');
                        const hotelName = (hotelParam || cookieHotel || '').toUpperCase();
                        const apiRoot = this.apiUrl.replace('/api', '');
                        const resp = await fetch(`${apiRoot}/api/hotels/rooms?hotel_name=${encodeURIComponent(hotelName)}`, { credentials: 'include' });
                        const data = await resp.json();
                        const rts = (data && data.room_types) ? data.room_types : [];
                        rtSel.innerHTML = '';
                        rts.forEach(rt => {
                            const opt = document.createElement('option');
                            const val = String(rt.room_type || '').toUpperCase();
                            opt.value = val;
                            opt.textContent = String(rt.room_type || '');
                            if (val === String(booking.room_number || booking.room_type || '').toUpperCase()) opt.selected = true;
                            rtSel.appendChild(opt);
                        });
                    } catch(_) {}
                }
                const getDates = () => Array.from(wrap.querySelectorAll('input[type="date"]')).map(el => el.value).filter(Boolean);
                const refreshEB = async () => {
                    const room_type = (rtSel?.value || '').toUpperCase();
                    const dates = getDates();
                    if (!room_type || !dates.length) return;
                    const hotelParam = (new URLSearchParams(window.location.search || '').get('hotel') || '');
                    const cookieHotel = (document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)?.[1] ? decodeURIComponent(document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)[1]) : '');
                    const hotelName = (hotelParam || cookieHotel || '').toUpperCase();
                    const apiRoot = this.apiUrl.replace('/api', '');
                    const results = await Promise.all(dates.map(d => fetch(`${apiRoot}/api/availability?hotel_name=${encodeURIComponent(hotelName)}&room_type=${encodeURIComponent(room_type)}&stay_date=${encodeURIComponent(d)}`, { credentials: 'include' }).then(r => r.json()).catch(() => ({ available: 0 }))));
                    const priceResults = await Promise.all(dates.map(d => fetch(`${apiRoot}/api/hotels/${encodeURIComponent(hotelName.replace(/\s+/g,'_'))}/pricing?room_type=${encodeURIComponent(room_type)}&start_date=${encodeURIComponent(d)}`, { credentials: 'include' }).then(r => r.json()).catch(() => ({ price: 0 }))));
                    let minAvail = null;
                    const dateInputs = Array.from(wrap.querySelectorAll('input[type="date"]'));
                    dateInputs.forEach((input, idx) => {
                        const r = results[idx] || {}; const booked = (typeof r.booked === 'number') ? r.booked : 0; const av = (typeof r.available === 'number') ? r.available : 0; const inventory = Math.max(0, booked + av);
                        const pr = priceResults[idx] || {}; const price = (typeof pr.price === 'number') ? pr.price : 0;
                        const span = input.parentElement && input.parentElement.querySelector('.nb-av');
                        if (span) {
                            span.textContent = `inventory : ${inventory}, booked : ${booked}, available : ${av}${price ? `, price : ${price}` : ''}`;
                            try { span.classList.remove('text-gray-500','text-red-600','text-green-600'); span.classList.add(av === 0 ? 'text-red-600' : 'text-green-600'); } catch(_) {}
                        }
                        if (minAvail === null) minAvail = av; else minAvail = Math.min(minAvail, av);
                    });
                    const cap = Math.max(0, parseInt(minAvail || 0, 10));
                    roomsSelEB.innerHTML = '';
                    for (let i=1;i<=cap;i++){ const opt=document.createElement('option'); opt.value=String(i); opt.textContent=String(i); roomsSelEB.appendChild(opt); }
                    if (!cap) { const opt=document.createElement('option'); opt.value='0'; opt.textContent='0'; roomsSelEB.appendChild(opt); }
                    // total
                    const baseTotal = priceResults.reduce((sum, pr) => sum + (pr && typeof pr.price === 'number' ? pr.price : 0), 0);
                    roomsSelEB.dataset.baseTotal = String(baseTotal);
                    const selected = Math.max(0, parseInt(roomsSelEB.value || '1', 10) || 1);
                    const total = (baseTotal * selected).toFixed(2);
                    if (hintEB) { hintEB.textContent = `total : ${total}`; try { hintEB.classList.add('text-sm','text-green-600'); } catch(_) {} }
                    if (!roomsSelEB._priceWire) {
                        roomsSelEB.addEventListener('change', () => {
                            const base = parseFloat(roomsSelEB.dataset.baseTotal || '0') || 0;
                            const sel = Math.max(0, parseInt(roomsSelEB.value || '1', 10) || 1);
                            const t = (base * sel).toFixed(2);
                            const h = document.getElementById('eb-availability-hint'); if (h) { h.textContent = `total : ${t}`; }
                        });
                        roomsSelEB._priceWire = true;
                    }
                };
                // Wire changes
                document.getElementById('eb-room-type')?.addEventListener('change', refreshEB);
                document.getElementById('eb-checkin')?.addEventListener('change', refreshEB);
                document.getElementById('eb-checkout')?.addEventListener('change', refreshEB);
                // Initial
                await refreshEB();
            } catch(_) {}
        })();
        // Notes
        document.getElementById('eb-notes').value = booking.booking_notes || '';
        const channelEl = document.getElementById('eb-channel');
        if (channelEl) channelEl.value = booking.channel || 'PMS';
        // Disable inputs when cancelled
        if (isCancelled) {
            try {
                ['eb-checkin','eb-checkout','eb-room-type','eb-rooms','eb-notes','eb-channel'].forEach(id => {
                    const el = document.getElementById(id); if (el) el.setAttribute('disabled','disabled');
                });
            } catch(_) {}
        } else {
            try {
                ['eb-checkin','eb-checkout','eb-room-type','eb-rooms','eb-notes','eb-channel'].forEach(id => {
                    const el = document.getElementById(id); if (el) el.removeAttribute('disabled');
                });
            } catch(_) {}
        }
        modal.classList.remove('hidden');
    }

    hideEditBookingModal() {
        const modal = document.getElementById('edit-booking-modal');
        if (modal) modal.classList.add('hidden');
    }

    showCancelAlert() {
        const a = document.getElementById('eb-cancel-alert'); if (a) a.classList.remove('hidden');
    }

    dismissCancelAlert() {
        const a = document.getElementById('eb-cancel-alert'); if (a) a.classList.add('hidden');
    }

    async confirmCancelBooking() {
        try {
            const bid = document.getElementById('eb-booking-id').value;
            const resp = await fetch(`${this.apiUrl}/bookings/cancel`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ booking_id: bid })
            });
            const data = await resp.json();
            if (!resp.ok || data.error) { alert(data.error || 'Failed to cancel booking'); return; }
            this.dismissCancelAlert();
            await this.loadBookings();
            this.applyFilters();
            this.hideEditBookingModal();
        } catch (e) { alert('Failed to cancel booking'); }
    }

    async submitEditBooking() {
        try {
            const bid = document.getElementById('eb-booking-id').value;
            const cin = document.getElementById('eb-checkin').value;
            const cout = document.getElementById('eb-checkout').value;
            const rooms = parseInt(document.getElementById('eb-rooms').value, 10) || 1;
            const newRoomType = String(document.getElementById('eb-room-type')?.value || '').toUpperCase() || undefined;
            const notes = document.getElementById('eb-notes').value || '';
            const channel = document.getElementById('eb-channel')?.value || '';
            // Derive stay_dates from range
            const d0 = this.parseYmdLocal(cin); const d1 = this.parseYmdLocal(cout);
            const stay_dates = [];
            if (d0 && d1 && d1 > d0) {
                let d = new Date(d0);
                while (d < d1) { stay_dates.push(this.formatYmdLocal(d)); d.setUTCDate(d.getUTCDate()+1); }
            }
            const payload = { booking_id: bid, new_rooms_booked: rooms, new_stay_dates: stay_dates, booking_notes: notes, new_room_type: newRoomType, channel: channel };
            // Call backend
            const resp = await fetch(`${this.apiUrl}/bookings/modify`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify(payload)
            });
            const data = await resp.json();
            if (!resp.ok || data.error) { alert(data.error || 'Failed to modify booking'); return; }
            window.location.reload();
        } catch (e) {
            alert('Failed to modify booking');
        }
    }
    showNewBookingModal(prefill) {
        const modal = document.getElementById('new-booking-modal');
        const preset = prefill || null;
        this._preferRoom = preset && preset.roomNumber ? String(preset.roomNumber).toUpperCase() : '';
        if (modal) {
            try { modal.style.zIndex = '10000'; } catch(_) {}
            // Preload room types from API
            try {
                const hotelParam = (new URLSearchParams(window.location.search || '').get('hotel') || '');
                const cookieHotel = (document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)?.[1] ? decodeURIComponent(document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)[1]) : '');
                const hotelName = (hotelParam || cookieHotel || '').toUpperCase();
                const apiRoot = this.apiUrl.replace('/api', '');
                fetch(`${apiRoot}/api/hotels/rooms?hotel_name=${encodeURIComponent(hotelName)}`, { credentials: 'include' })
                    .then(r => r.json()).then(data => {
                        const sel = document.getElementById('nb-room-type');
                        if (!sel) return;
                        sel.innerHTML = '';
                        const rts = (data && data.room_types) ? data.room_types : [];
                        if (Array.isArray(rts) && rts.length) {
                            rts.forEach(rt => {
                                const opt = document.createElement('option');
                                opt.value = String(rt.room_type || '').toUpperCase();
                                opt.textContent = String(rt.room_type || '');
                                sel.appendChild(opt);
                            });
                            if (preset && preset.roomType) sel.value = String(preset.roomType).toUpperCase();
                        } else {
                            const opt = document.createElement('option');
                            opt.value = '';
                            opt.textContent = 'No room types found';
                            sel.appendChild(opt);
                        }
                    }).then(() => { this.applyBookingPreset(preset); }).catch(() => { this.applyBookingPreset(preset); });
            } catch(_) {}
            this.applyBookingPreset(preset);
            // Reset and preload availability-dependent rooms select
            try {
                const roomsSel = document.getElementById('nb-rooms');
                const hint = document.getElementById('nb-availability-hint');
                if (roomsSel) roomsSel.innerHTML = '<option value="1">1</option>';
                if (hint) hint.textContent = '';
                // Clear derived stay_date rows
                const wrap = document.getElementById('nb-staydates');
                if (wrap) {
                    wrap.innerHTML = '';
                }
                // Wire derivation and auto-advance
                const cin = document.getElementById('nb-checkin');
                const cout = document.getElementById('nb-checkout');
                const wireDerive = () => this.deriveStayDatesFromRange();
                if (cin && !cin._wired) { cin.addEventListener('change', wireDerive); cin._wired = true; }
                if (cout && !cout._wired) { cout.addEventListener('change', () => { cout._manual = true; wireDerive(); }); cout._wired = true; }
                if (cin && !cin._auto) {
                    cin.addEventListener('change', () => {
                        try {
                            const v = (cin.value || '').trim();
                            if (!v) { wireDerive(); return; }
                            if (cout && !cout._manual) {
                                const d = this.parseYmdLocal(v);
                                if (d && !isNaN(d)) { d.setDate(d.getDate() + 1); cout.value = this.formatYmdLocal(d); }
                            }
                            wireDerive();
                        } catch(_) { wireDerive(); }
                    });
                    cin._auto = true;
                }
            } catch(_) {}
            // Reset guest status and disable create until profile loaded
            try {
                const status = document.getElementById('nb-guest-status'); if (status) status.textContent = '';
                const btn = document.getElementById('nb-create-btn'); if (btn) btn.disabled = true;
                const newGuestBtn = document.getElementById('nb-new-guest-btn'); if (newGuestBtn) newGuestBtn.classList.add('hidden');
            } catch(_) {}
            modal.classList.remove('hidden');
        }
    }

    applyBookingPreset(preset) {
        const cin = document.getElementById('nb-checkin');
        const cout = document.getElementById('nb-checkout');
        if (cout) cout._manual = !!(preset && preset.checkOut);
        if (!preset) return;
        const sel = document.getElementById('nb-room-type');
        if (sel && preset.roomType) sel.value = String(preset.roomType).toUpperCase();
        if (cin && preset.checkIn) cin.value = preset.checkIn;
        if (cout && preset.checkOut) cout.value = preset.checkOut;
        this.deriveStayDatesFromRange();
    }

    deriveStayDatesFromRange() {
        try {
            const checkin = (document.getElementById('nb-checkin')?.value || '').trim();
            const checkout = (document.getElementById('nb-checkout')?.value || '').trim();
            const wrap = document.getElementById('nb-staydates');
            if (!wrap) return;
            wrap.innerHTML = '';
            if (!checkin || !checkout) { this.refreshRoomsAvailability(); return; }
            const start = this.parseYmdLocal(checkin);
            const end = this.parseYmdLocal(checkout);
            if (!(start instanceof Date) || !(end instanceof Date) || isNaN(start) || isNaN(end) || end <= start) {
                this.refreshRoomsAvailability();
                return;
            }
            for (let d = new Date(start.getTime()); d < end; d.setUTCDate(d.getUTCDate() + 1)) {
                const row = document.createElement('div');
                row.className = 'space-y-1';
                row.innerHTML = `
                    <input type="date" class="nb-date border rounded px-3 py-2 w-full" value="${this.formatYmdLocal(d)}" disabled />
                    <div class="nb-av text-xs whitespace-normal"></div>
                `;
                wrap.appendChild(row);
            }
            // After render, recompute availability and rooms cap
            this.refreshRoomsAvailability();
            // Ensure room type changes also refresh availability when dates pre-exist
            try {
                const rt = document.getElementById('nb-room-type');
                if (rt && !rt._wired_av) { rt.addEventListener('change', () => this.refreshRoomsAvailability()); rt._wired_av = true; }
            } catch(_) {}
        } catch(_) {}
    }

    addStayDateField() {
        try {
            const wrap = document.getElementById('nb-staydates');
            if (!wrap) return;
            // Create a new date row
            const idx = wrap.children.length + 1;
            const row = document.createElement('div');
            row.className = 'space-y-1';
            row.innerHTML = `
                <div class="flex items-center gap-2">
                    <input type="date" class="nb-date border rounded px-3 py-2 flex-1" placeholder="YYYY-MM-DD" />
                    <button class="text-xs px-2 py-1 rounded bg-red-100 hover:bg-red-200" onclick="bookingManager.removeStayDateField(this); return false;">Remove</button>
                </div>
                <div class="nb-av text-xs whitespace-normal"></div>
            `;
            wrap.appendChild(row);
            // Reset rooms select when dates change
            const roomsSel = document.getElementById('nb-rooms');
            if (roomsSel) roomsSel.innerHTML = '<option value="1">1</option>';
            const hint = document.getElementById('nb-availability-hint'); if (hint) hint.textContent = '';
            // Wire availability on change of first date
            this._wireDatesAvailability();
        } catch(_) {}
    }

    removeStayDateField(btn) {
        try {
            const row = btn && btn.parentElement;
            if (row && row.parentElement) {
                row.parentElement.removeChild(row);
            }
            // Reset rooms on any date removal
            const roomsSel = document.getElementById('nb-rooms');
            if (roomsSel) roomsSel.innerHTML = '<option value="1">1</option>';
            const hint = document.getElementById('nb-availability-hint'); if (hint) hint.textContent = '';
            this._wireDatesAvailability();
            // Recompute availability after removal
            this.refreshRoomsAvailability();
        } catch(_) {}
    }

    _wireDatesAvailability() {
        try {
            const dates = Array.from(document.querySelectorAll('#nb-staydates .nb-date'));
            if (!dates.length) return;
            const onChange = () => this.refreshRoomsAvailability();
            dates.forEach(d => { if (!d._wired) { d.addEventListener('change', onChange); d._wired = true; } });
        } catch(_) {}
    }

    async refreshRoomsAvailability() {
        try {
            const room_type = (document.getElementById('nb-room-type')?.value || '').trim().toUpperCase();
            // Use ALL stay_dates for availability check
            const dateInputs = Array.from(document.querySelectorAll('#nb-staydates .nb-date'));
            const dates = dateInputs.map(el => (el.value || '').trim()).filter(Boolean);
            const roomsSel = document.getElementById('nb-rooms');
            const hint = document.getElementById('nb-availability-hint');
            if (!room_type || !roomsSel || !dates.length) return;
            // Guard: require guest profile loaded
            const status = document.getElementById('nb-guest-status');
            const createBtnGuard = document.getElementById('nb-create-btn');
            const hasProfile = status && status.dataset && status.dataset.loaded === '1';
            if (createBtnGuard) createBtnGuard.disabled = !hasProfile;
            const hotelParam = (new URLSearchParams(window.location.search || '').get('hotel') || '');
            const cookieHotel = (document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)?.[1] ? decodeURIComponent(document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)[1]) : '');
            const hotelName = (hotelParam || cookieHotel || '').toUpperCase();
            const apiRoot = this.apiUrl.replace('/api', '');
            // Fetch all availabilities and prices in parallel
            const results = await Promise.all(dates.map(d => fetch(`${apiRoot}/api/availability?hotel_name=${encodeURIComponent(hotelName)}&room_type=${encodeURIComponent(room_type)}&stay_date=${encodeURIComponent(d)}`, { credentials: 'include' }).then(r => r.json()).catch(() => ({ available: 0 }))));
            const priceResults = await Promise.all(dates.map(d => fetch(`${apiRoot}/api/hotels/${encodeURIComponent(hotelName.replace(/\s+/g,'_'))}/pricing?room_type=${encodeURIComponent(room_type)}&start_date=${encodeURIComponent(d)}`, { credentials: 'include' }).then(r => r.json()).catch(() => ({ price: 0 }))));
            // Update inline availability per row
            let minAvail = null;
            dateInputs.forEach((input, idx) => {
                const r = results[idx] || {};
                const booked = (typeof r.booked === 'number') ? r.booked : 0;
                const av = (typeof r.available === 'number') ? r.available : 0;
                const inventory = Math.max(0, booked + av);
                const pr = priceResults[idx] || {};
                const price = (typeof pr.price === 'number') ? pr.price : 0;
                const span = input.parentElement && input.parentElement.querySelector('.nb-av');
                if (span) {
                    span.textContent = `inventory : ${inventory}, booked : ${booked}, available : ${av}${price ? `, price : ${price}` : ''}`;
                    try {
                        span.classList.remove('text-gray-500', 'text-gray-600', 'text-red-600', 'text-green-600');
                        span.classList.add(av === 0 ? 'text-red-600' : 'text-green-600');
                    } catch(_) {}
                }
                if (minAvail === null) minAvail = av; else minAvail = Math.min(minAvail, av);
            });
            const cap = Math.max(0, parseInt(minAvail || 0, 10));
            roomsSel.innerHTML = '';
            for (let i = 1; i <= cap; i++) {
                const opt = document.createElement('option'); opt.value = String(i); opt.textContent = String(i); roomsSel.appendChild(opt);
            }
            if (!cap) {
                const opt = document.createElement('option'); opt.value = '0'; opt.textContent = '0'; roomsSel.appendChild(opt);
            }
            // Compute total pricing inline with selected rooms
            try {
                const baseTotal = priceResults.reduce((sum, pr) => sum + (pr && typeof pr.price === 'number' ? pr.price : 0), 0);
                roomsSel.dataset.baseTotal = String(baseTotal);
                roomsSel.dataset.nights = String(dates.length);
                const selected = Math.max(0, parseInt(roomsSel.value || '1', 10) || 1);
                const total = (baseTotal * selected).toFixed(2);
                if (hint) { hint.textContent = `total : ${total}`; try { hint.classList.remove('text-lg'); hint.classList.add('text-sm','text-green-600'); } catch(_) {} }
                if (!roomsSel._priceWire) {
                    roomsSel.addEventListener('change', () => {
                        const base = parseFloat(roomsSel.dataset.baseTotal || '0') || 0;
                        const sel = Math.max(0, parseInt(roomsSel.value || '1', 10) || 1);
                        const t = (base * sel).toFixed(2);
                        const h = document.getElementById('nb-availability-hint');
                        if (h) { h.textContent = `total : ${t}`; try { h.classList.remove('text-lg'); h.classList.add('text-sm','text-green-600'); } catch(_) {} }
                    });
                    roomsSel._priceWire = true;
                }
            } catch(_) { if (hint) hint.textContent = ''; }
            const btn = document.getElementById('nb-create-btn'); if (btn) btn.disabled = cap === 0;
        } catch(_) {}
    }

    hideNewBookingModal() {
        const modal = document.getElementById('new-booking-modal');
        if (modal) modal.classList.add('hidden');
    }

    groupRoomOptions() {
        return (this.roomTypeNames || []).map(name => `<option value="${name}">${name}</option>`).join('');
    }

    showGroupBookingModal() {
        const modal = document.getElementById('group-booking-modal');
        if (!modal) return;
        const phone = document.getElementById('gb-guest-phone'); if (phone) phone.value = '';
        const status = document.getElementById('gb-guest-status');
        if (status) { status.textContent = 'please load profile first'; status.dataset.loaded = '0'; }
        const notes = document.getElementById('gb-notes'); if (notes) notes.value = '';
        const btn = document.getElementById('gb-create-btn'); if (btn) btn.disabled = true;
        const newGuest = document.getElementById('gb-new-guest-btn'); if (newGuest) newGuest.classList.add('hidden');
        const lines = document.getElementById('gb-lines'); if (lines) lines.innerHTML = '';
        this.addGroupLine();
        const cin = document.getElementById('gb-checkin');
        const cout = document.getElementById('gb-checkout');
        const wire = () => this.deriveGroupStayDates();
        if (cout) { cout._manual = false; cout.value = ''; }
        if (cin) cin.value = '';
        if (cout && !cout._wired) { cout.addEventListener('change', () => { cout._manual = true; wire(); }); cout._wired = true; }
        if (cin && !cin._auto) {
            cin.addEventListener('change', () => {
                const v = (cin.value || '').trim();
                if (v && cout && !cout._manual) {
                    const d = this.parseYmdLocal(v);
                    if (d && !isNaN(d)) { d.setDate(d.getDate() + 1); cout.value = this.formatYmdLocal(d); }
                }
                wire();
            });
            cin._auto = true;
        }
        const dates = document.getElementById('gb-staydates'); if (dates) dates.innerHTML = '';
        modal.classList.remove('hidden');
    }

    hideGroupBookingModal() {
        const modal = document.getElementById('group-booking-modal');
        if (modal) modal.classList.add('hidden');
    }

    addGroupLine() {
        const wrap = document.getElementById('gb-lines');
        if (!wrap) return;
        const row = document.createElement('div');
        row.className = 'gb-line flex gap-2 items-center';
        row.innerHTML = `
            <select class="gb-room-type border rounded px-3 py-2 flex-1">${this.groupRoomOptions()}</select>
            <select class="gb-rooms border rounded px-3 py-2 w-24"><option value="1">1</option></select>
            <button type="button" class="text-xs px-2 py-1 rounded bg-red-100 hover:bg-red-200">Remove</button>
        `;
        const remove = row.querySelector('button');
        if (remove) remove.addEventListener('click', () => { if (wrap.children.length > 1) row.remove(); this.refreshGroupAvailability(); });
        const sel = row.querySelector('.gb-room-type');
        if (sel) sel.addEventListener('change', () => this.refreshGroupAvailability());
        const rooms = row.querySelector('.gb-rooms');
        if (rooms) rooms.addEventListener('change', () => this.applyGroupQuantityLimits());
        wrap.appendChild(row);
        this.refreshGroupAvailability();
    }

    deriveGroupStayDates() {
        const checkin = (document.getElementById('gb-checkin')?.value || '').trim();
        const checkout = (document.getElementById('gb-checkout')?.value || '').trim();
        const wrap = document.getElementById('gb-staydates');
        if (!wrap) return;
        wrap.innerHTML = '';
        if (!checkin || !checkout) return;
        const start = this.parseYmdLocal(checkin);
        const end = this.parseYmdLocal(checkout);
        if (!(start instanceof Date) || !(end instanceof Date) || isNaN(start) || isNaN(end) || end <= start) return;
        for (let d = new Date(start.getTime()); d < end; d.setUTCDate(d.getUTCDate() + 1)) {
            const row = document.createElement('div');
            row.className = 'space-y-1';
            row.innerHTML = `
                <input type="date" class="gb-date border rounded px-3 py-2 w-full" value="${this.formatYmdLocal(d)}" disabled />
                <div class="gb-av text-xs whitespace-normal"></div>
            `;
            wrap.appendChild(row);
        }
        this.refreshGroupAvailability();
    }

    async refreshGroupAvailability() {
        try {
            this._groupAvailToken = (this._groupAvailToken || 0) + 1;
            const token = this._groupAvailToken;
            const dateInputs = Array.from(document.querySelectorAll('#gb-staydates .gb-date'));
            const dates = dateInputs.map(el => (el.value || '').trim()).filter(Boolean);
            const lines = Array.from(document.querySelectorAll('#gb-lines .gb-line')).map(row => ({
                room_type: (row.querySelector('.gb-room-type')?.value || '').trim().toUpperCase(),
                rooms: row.querySelector('.gb-rooms')
            })).filter(line => line.room_type);
            if (!dates.length || !lines.length) return;
            const hotelParam = (new URLSearchParams(window.location.search || '').get('hotel') || '');
            const cookieHotel = (document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)?.[1] ? decodeURIComponent(document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)[1]) : '');
            const hotelName = (hotelParam || cookieHotel || '').toUpperCase();
            const apiRoot = this.apiUrl.replace('/api', '');
            const packs = await Promise.all(lines.map(async (line) => {
                const results = await Promise.all(dates.map(d => fetch(`${apiRoot}/api/availability?hotel_name=${encodeURIComponent(hotelName)}&room_type=${encodeURIComponent(line.room_type)}&stay_date=${encodeURIComponent(d)}`, { credentials: 'include' }).then(r => r.json()).catch(() => ({ available: 0, booked: 0 }))));
                const priceResults = await Promise.all(dates.map(d => fetch(`${apiRoot}/api/hotels/${encodeURIComponent(hotelName.replace(/\s+/g,'_'))}/pricing?room_type=${encodeURIComponent(line.room_type)}&start_date=${encodeURIComponent(d)}`, { credentials: 'include' }).then(r => r.json()).catch(() => ({ price: 0 }))));
                let minAvail = null;
                results.forEach(r => {
                    const av = (typeof r.available === 'number') ? r.available : 0;
                    minAvail = minAvail === null ? av : Math.min(minAvail, av);
                });
                const cap = Math.max(0, parseInt(minAvail || 0, 10));
                const nightTotal = priceResults.reduce((sum, pr) => sum + (pr && typeof pr.price === 'number' ? pr.price : 0), 0);
                return { room_type: line.room_type, rooms: line.rooms, cap, nightTotal, results, priceResults };
            }));
            if (token !== this._groupAvailToken) return;
            packs.forEach(pack => {
                if (!pack.rooms) return;
                pack.rooms.dataset.availCap = String(pack.cap);
                pack.rooms.dataset.nightTotal = String(pack.nightTotal);
            });
            dateInputs.forEach((input, idx) => {
                const box = input.parentElement && input.parentElement.querySelector('.gb-av');
                if (!box) return;
                box.innerHTML = '';
                packs.forEach(pack => {
                    const r = pack.results[idx] || {};
                    const booked = (typeof r.booked === 'number') ? r.booked : 0;
                    const av = (typeof r.available === 'number') ? r.available : 0;
                    const inventory = Math.max(0, booked + av);
                    const pr = pack.priceResults[idx] || {};
                    const price = (typeof pr.price === 'number') ? pr.price : 0;
                    const line = document.createElement('div');
                    const prefix = packs.length > 1 ? `${pack.room_type} — ` : '';
                    line.textContent = `${prefix}inventory : ${inventory}, booked : ${booked}, available : ${av}, price : ${price}`;
                    line.className = av === 0 ? 'text-red-600' : 'text-green-600';
                    box.appendChild(line);
                });
            });
            this.applyGroupQuantityLimits();
        } catch (_) {}
    }

    fillGroupRoomSelect(sel, max, chosen) {
        const keep = Math.max(0, Math.min(chosen, max));
        sel.innerHTML = '';
        const start = max > 0 ? 1 : 0;
        for (let i = start; i <= max; i++) {
            const opt = document.createElement('option');
            opt.value = String(i);
            opt.textContent = String(i);
            sel.appendChild(opt);
        }
        sel.value = String(max > 0 ? Math.max(1, keep) : 0);
    }

    applyGroupQuantityLimits() {
        const rows = Array.from(document.querySelectorAll('#gb-lines .gb-line'));
        const seen = {};
        let total = 0;
        rows.forEach(row => {
            const roomType = (row.querySelector('.gb-room-type')?.value || '').trim().toUpperCase();
            const rooms = row.querySelector('.gb-rooms');
            if (!rooms || rooms.dataset.availCap === undefined) return;
            const cap = Math.max(0, parseInt(rooms.dataset.availCap || '0', 10) || 0);
            const used = seen[roomType] || 0;
            const left = Math.max(0, cap - used);
            const wanted = parseInt(rooms.value || '1', 10) || 0;
            this.fillGroupRoomSelect(rooms, left, wanted);
            const qty = parseInt(rooms.value || '0', 10) || 0;
            seen[roomType] = used + qty;
            const nightTotal = parseFloat(rooms.dataset.nightTotal || '0') || 0;
            total += nightTotal * qty;
        });
        const box = document.getElementById('gb-total');
        if (box) box.textContent = rows.length ? `total : ${total.toFixed(2)}` : '';
    }

    async loadGroupGuest() {
        try {
            const phone = (document.getElementById('gb-guest-phone')?.value || '').trim().toUpperCase();
            const status = document.getElementById('gb-guest-status');
            const createBtn = document.getElementById('gb-create-btn');
            const newGuestBtn = document.getElementById('gb-new-guest-btn');
            const showNewGuest = (visible) => { if (newGuestBtn) newGuestBtn.classList.toggle('hidden', !visible); };
            if (!phone) { if (status) { status.textContent = 'Enter guest phone'; status.dataset.loaded = '0'; } if (createBtn) createBtn.disabled = true; showNewGuest(false); return; }
            const apiRoot = this.apiUrl.replace('/api', '');
            if (status) { status.textContent = 'Loading...'; status.dataset.loaded = '0'; }
            showNewGuest(false);
            const res = await fetch(`${apiRoot}/api/guest_profile?guest_phone=${encodeURIComponent(phone)}`, { credentials: 'include' });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || data.error) {
                const message = (data && data.error) ? String(data.error) : 'Guest not found';
                if (status) { status.textContent = message; status.dataset.loaded = '0'; }
                showNewGuest(message.indexOf('No guest profile found') >= 0);
                if (createBtn) createBtn.disabled = true;
                return;
            }
            if (status) { status.textContent = (data && data.profile) ? String(data.profile) : 'Guest profile loaded'; status.dataset.loaded = '1'; }
            showNewGuest(false);
            if (createBtn) createBtn.disabled = false;
        } catch (e) {
            const status = document.getElementById('gb-guest-status');
            const createBtn = document.getElementById('gb-create-btn');
            if (status) { status.textContent = 'Failed to load profile'; status.dataset.loaded = '0'; }
            if (createBtn) createBtn.disabled = true;
        }
    }

    submitGroupBooking = async () => {
        try {
            const guest_phone = (document.getElementById('gb-guest-phone')?.value || '').trim().toUpperCase();
            const status = document.getElementById('gb-guest-status');
            const dates = Array.from(document.querySelectorAll('#gb-staydates .gb-date')).map(el => (el.value || '').trim()).filter(Boolean);
            const lines = Array.from(document.querySelectorAll('#gb-lines .gb-line')).map(row => ({
                room_type: (row.querySelector('.gb-room-type')?.value || '').trim().toUpperCase(),
                rooms_booked: parseInt(row.querySelector('.gb-rooms')?.value || '0', 10) || 0
            })).filter(line => line.room_type && line.rooms_booked > 0);
            const booking_notes = (document.getElementById('gb-notes')?.value || '').trim();
            if (!status || status.dataset.loaded !== '1') { alert('Please load guest profile first.'); return; }
            if (!guest_phone || !dates.length || lines.length < 1) {
                alert('Guest, stay dates, and at least one room type are required.');
                return;
            }
            const res = await fetch(`${this.apiUrl}/bookings/group`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ guest_phone, stay_dates: dates, lines, booking_notes })
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || data.error) { alert(data.error || 'Failed to create group booking'); return; }
            alert(data.result || 'Group booking created');
            this.hideGroupBookingModal();
            await this.loadBookings();
            this.updateSummaryCards();
            this.renderCalendar();
            this.renderBookingsTable();
        } catch (e) {
            alert('Failed: ' + (e.message || e));
        }
    };

    submitNewBooking = async () => {
        try {
            const guest_phone = (document.getElementById('nb-guest-phone')?.value || '').trim().toUpperCase();
            const room_type = (document.getElementById('nb-room-type')?.value || '').trim().toUpperCase();
            // Collect stay_dates from fields
            const dates = Array.from(document.querySelectorAll('#nb-staydates .nb-date')).map(el => (el.value || '').trim()).filter(Boolean);
            const rooms_booked = parseInt(document.getElementById('nb-rooms')?.value || '1', 10) || 1;
            const booking_notes = (document.getElementById('nb-notes')?.value || '').trim();
            const channel = 'PMS';
            // Enforce guest profile loaded
            const status = document.getElementById('nb-guest-status');
            if (!status || status.dataset.loaded !== '1') {
                alert('Please load guest profile first.');
                return;
            }
            if (!guest_phone || !room_type || !dates.length) {
                alert('Please fill guest phone, room type, and at least one stay_date.');
                return;
            }
            // Check availability for all nights (quick guard)
            try {
                const apiRoot = this.apiUrl.replace('/api', '');
                const hotel = (new URLSearchParams(window.location.search || '').get('hotel') || (document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)?.[1] ? decodeURIComponent(document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)[1]) : '')).toUpperCase();
                const results = await Promise.all(dates.map(d => fetch(`${apiRoot}/api/availability?hotel_name=${encodeURIComponent(hotel)}&room_type=${encodeURIComponent(room_type)}&stay_date=${encodeURIComponent(d)}&rooms_needed=${rooms_booked}`, { credentials: 'include' }).then(r => r.json()).catch(() => ({ available: 0 }))));
                const insufficient = results.find(av => av && typeof av.available === 'number' && av.available < rooms_booked);
                if (insufficient) { alert('Insufficient availability across selected dates.'); return; }
            } catch(_) {}
            const stay_dates = dates;
            const payload = { guest_phone, room_type, stay_dates, rooms_booked, booking_notes, channel };
            const res = await fetch(`${this.apiUrl}/bookings/create`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify(payload)
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || data.error) {
                alert(data.error || 'Failed to create booking');
                return;
            }
            const assigned = Array.isArray(data.assigned_rooms) ? data.assigned_rooms.filter(Boolean) : [];
            const prefer = String(this._preferRoom || '').toUpperCase();
            this._preferRoom = '';
            const placed = assigned.map(r => String(r).toUpperCase());
            if (prefer && data.booking_id && placed.indexOf(prefer) < 0) {
                const hotel = (new URLSearchParams(window.location.search || '').get('hotel') || (document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)?.[1] ? decodeURIComponent(document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/)[1]) : '')).toUpperCase();
                const oldRoom = placed.length ? placed[0] : '';
                const move = await fetch(`${this.apiUrl}/assignments/reassign`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify({ hotel_name: hotel, booking_id: data.booking_id, stay_date: dates[0], old_room_number: oldRoom, new_room_number: prefer })
                });
                const moved = await move.json().catch(() => ({}));
                if (!move.ok || moved.error) {
                    alert(moved.error || 'Booking created, but the selected room could not be assigned.');
                } else if (oldRoom) {
                    const idx = placed.indexOf(oldRoom);
                    if (idx >= 0) placed[idx] = prefer;
                    else placed.unshift(prefer);
                } else {
                    placed.unshift(prefer);
                }
            }
            alert(placed.length ? ('Booking created. Assigned ' + placed.join(', ')) : 'Booking created. No room was free for every night.');
            this.hideNewBookingModal();
            if (!document.getElementById('booking-calendar')) {
                window.location.reload();
                return;
            }
            await this.loadBookings();
            this.updateSummaryCards();
            this.renderCalendar();
            this.renderBookingsTable();
        } catch (e) {
            alert('Failed: ' + (e.message || e));
        }
    }

    async loadGuestProfile() {
        try {
            const phone = (document.getElementById('nb-guest-phone')?.value || '').trim().toUpperCase();
            const status = document.getElementById('nb-guest-status');
            const createBtn = document.getElementById('nb-create-btn');
            const newGuestBtn = document.getElementById('nb-new-guest-btn');
            const showNewGuest = (visible) => { if (newGuestBtn) newGuestBtn.classList.toggle('hidden', !visible); };
            if (!phone) { if (status) { status.textContent = 'Enter guest phone'; status.dataset.loaded = '0'; } if (createBtn) createBtn.disabled = true; showNewGuest(false); return; }
            const apiRoot = this.apiUrl.replace('/api', '');
            if (status) { status.textContent = 'Loading...'; status.dataset.loaded = '0'; }
            showNewGuest(false);
            const res = await fetch(`${apiRoot}/api/guest_profile?guest_phone=${encodeURIComponent(phone)}`, { credentials: 'include' });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || data.error) {
                const message = (data && data.error) ? String(data.error) : 'Guest not found';
                if (status) { status.textContent = message; status.dataset.loaded = '0'; }
                showNewGuest(message.indexOf('No guest profile found') >= 0);
                if (createBtn) createBtn.disabled = true;
                return;
            }
            if (status) { status.textContent = (data && data.profile) ? String(data.profile) : 'Guest profile loaded'; status.dataset.loaded = '1'; try { status.style.whiteSpace = 'pre-line'; } catch(_) {} }
            showNewGuest(false);
            if (createBtn) createBtn.disabled = false;
        } catch(e) {
            const status = document.getElementById('nb-guest-status'); const createBtn = document.getElementById('nb-create-btn');
            const newGuestBtn = document.getElementById('nb-new-guest-btn'); if (newGuestBtn) newGuestBtn.classList.add('hidden');
            if (status) { status.textContent = 'Failed to load profile'; status.dataset.loaded = '0'; }
            if (createBtn) createBtn.disabled = true;
        }
    }

    showCreateGuestModal(prefillPhone = '') {
        try {
            const modal = document.getElementById('create-guest-modal');
            if (!modal) return;
            const groupOpen = document.getElementById('group-booking-modal') && !document.getElementById('group-booking-modal').classList.contains('hidden');
            const sourceId = groupOpen ? 'gb-guest-phone' : 'nb-guest-phone';
            const p = document.getElementById('cg-phone'); if (p) p.value = prefillPhone || (document.getElementById(sourceId)?.value || '');
            const n = document.getElementById('cg-name'); if (n) n.value = '';
            const e = document.getElementById('cg-email'); if (e) e.value = '';
            const c = document.getElementById('cg-nation'); if (c) c.value = '';
            const s = document.getElementById('cg-status'); if (s) s.textContent = '';
            modal.classList.remove('hidden');
        } catch(_) {}
    }

    hideCreateGuestModal() {
        const modal = document.getElementById('create-guest-modal');
        if (modal) modal.classList.add('hidden');
    }

    async submitCreateGuest() {
        try {
            const phone = (document.getElementById('cg-phone')?.value || '').trim().toUpperCase();
            const name = (document.getElementById('cg-name')?.value || '').trim().toUpperCase();
            const email = (document.getElementById('cg-email')?.value || '').trim();
            const nation = (document.getElementById('cg-nation')?.value || '').trim().toUpperCase();
            const status = document.getElementById('cg-status');
            if (!phone || !name) { if (status) status.textContent = 'guest_phone and guest_name are required'; return; }
            const payload = { guest_phone: phone, guest_name: name };
            if (email) payload.email = email;
            if (nation) payload.nationality = nation;
            const res = await fetch(`${this.apiUrl}/guest_profile/create`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify(payload) });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || data.error) { if (status) status.textContent = data.error || 'Failed to create guest'; return; }
            if (status) status.textContent = 'Guest created';
            // Reflect into booking modal: set phone, then load full profile to display
            const groupOpen = document.getElementById('group-booking-modal') && !document.getElementById('group-booking-modal').classList.contains('hidden');
            const phoneField = document.getElementById(groupOpen ? 'gb-guest-phone' : 'nb-guest-phone');
            if (phoneField) phoneField.value = phone;
            this.hideCreateGuestModal();
            if (groupOpen) await this.loadGroupGuest();
            else await this.loadGuestProfile();
        } catch(_) {
            const status = document.getElementById('cg-status'); if (status) status.textContent = 'Failed to create guest';
        }
    }
    
    generateMockBookings() {
        const mockBookings = [];
        const guests = ['Ahmad Hassan', 'Lim Wei Ming', 'Priya Sharma', 'Tan Mei Ling', 'Rajesh Kumar', 'Siti Aminah', 'David Lim', 'Fatimah Ali', 'Chen Wei', 'Aisha Rahman'];
        const rooms = ['Family Suite', 'King Suite', 'Two-bedroom apartment'];
        const statuses = ['confirmed', 'pending', 'checked-in', 'checked-out'];
        
        // Add hotel context to mock bookings
        const hotelName = this.currentHotel || 'Marina Heights';
        
        for (let i = 0; i < 18; i++) {
            const checkIn = new Date();
            checkIn.setDate(checkIn.getDate() + Math.floor(Math.random() * 30) - 15);
            const checkOut = new Date(checkIn);
            checkOut.setDate(checkOut.getDate() + Math.floor(Math.random() * 5) + 1);
            
            mockBookings.push({
                id: `booking_${i + 1}_${hotelName.replace(' ', '_').toLowerCase()}`,
                guest_name: guests[Math.floor(Math.random() * guests.length)],
                room_number: rooms[Math.floor(Math.random() * rooms.length)],
                check_in: checkIn.toISOString().split('T')[0],
                check_out: checkOut.toISOString().split('T')[0],
                status: statuses[Math.floor(Math.random() * statuses.length)],
                amount: (Math.random() * 800 + 200).toFixed(2),
                guest_phone: `+601${Math.floor(Math.random() * 100000000)}`,
                hotel: hotelName,
                notes: `Booking at ${hotelName}`
            });
        }
        
        return mockBookings;
    }
    
    updateSummaryCards() {
        const today = new Date().toISOString().split('T')[0];
        const normalizeYmd = (s) => this.formatYmdLocal(this.parseYmdLocal(String(s||'')) || new Date(NaN));
        const valid = this.bookings.filter(b => String(b.status || '').toLowerCase() !== 'cancelled');
        const numRooms = (b) => parseInt(b.rooms_booked || 1, 10) || 1;
        const todaysArrivals = valid.filter(b => normalizeYmd(b.check_in) === today).reduce((s,b)=>s+numRooms(b),0);
        const todaysDepartures = valid.filter(b => normalizeYmd(b.check_out) === today).reduce((s,b)=>s+numRooms(b),0);
        const totalBookings = this.bookings.length;
        const pendingPayments = this.bookings.filter(b => String(b.status||'').toLowerCase() === 'pending').length;
        
        document.getElementById('todays-arrivals').textContent = todaysArrivals;
        document.getElementById('todays-departures').textContent = todaysDepartures;
        document.getElementById('total-bookings').textContent = totalBookings;
        document.getElementById('pending-payments').textContent = pendingPayments;
    }
    
    renderCalendar() {
        const calendarContainer = document.getElementById('booking-calendar');
        const monthElement = document.getElementById('calendar-month');
        
        // Update month display (UTC)
        monthElement.textContent = new Date(Date.UTC(this.currentDate.getUTCFullYear(), this.currentDate.getUTCMonth(), 1)).toLocaleDateString('en-US', { 
            month: 'long', 
            year: 'numeric',
            timeZone: 'UTC'
        });
        
        // Create calendar grid (UTC)
        const firstDay = new Date(Date.UTC(this.currentDate.getUTCFullYear(), this.currentDate.getUTCMonth(), 1));
        const lastDay = new Date(Date.UTC(this.currentDate.getUTCFullYear(), this.currentDate.getUTCMonth() + 1, 0));
        const startDate = new Date(firstDay);
        startDate.setUTCDate(startDate.getUTCDate() - firstDay.getUTCDay());
        
        let calendarHTML = `
            <div class="grid grid-cols-7 gap-1 mb-2">
                <div class="text-center text-xs font-medium text-gray-500 py-2">Sun</div>
                <div class="text-center text-xs font-medium text-gray-500 py-2">Mon</div>
                <div class="text-center text-xs font-medium text-gray-500 py-2">Tue</div>
                <div class="text-center text-xs font-medium text-gray-500 py-2">Wed</div>
                <div class="text-center text-xs font-medium text-gray-500 py-2">Thu</div>
                <div class="text-center text-xs font-medium text-gray-500 py-2">Fri</div>
                <div class="text-center text-xs font-medium text-gray-500 py-2">Sat</div>
            </div>
            <div class="grid grid-cols-7 gap-1">
        `;
        
        for (let i = 0; i < 42; i++) {
            const currentDate = new Date(startDate);
            currentDate.setUTCDate(startDate.getUTCDate() + i);
            
            const isCurrentMonth = (currentDate.getUTCMonth() === this.currentDate.getUTCMonth()) && (currentDate.getUTCFullYear() === this.currentDate.getUTCFullYear());
            const todayStr = new Date().toISOString().split('T')[0];
            const isToday = (currentDate.toISOString().split('T')[0] === todayStr);
            const dateStr = currentDate.toISOString().split('T')[0];
            const normalizeYmd = (s) => this.formatYmdLocal(this.parseYmdLocal(String(s||'')) || new Date(NaN));
            
            // Count bookings for this date
            const valid = this.bookings.filter(b => String(b.status || '').toLowerCase() !== 'cancelled');
            const asSetSize = (arr) => (new Set(arr.map(b => String(b.id || '')))).size;
            const checkIns = asSetSize(valid.filter(b => normalizeYmd(b.check_in) === dateStr));
            const checkOuts = asSetSize(valid.filter(b => normalizeYmd(b.check_out) === dateStr));
            // Stay-overs: in-house (between check-in and check-out exclusive)
            const stayOvers = asSetSize(valid.filter(b => (normalizeYmd(b.check_in) < dateStr) && (normalizeYmd(b.check_out) > dateStr)));
            
            calendarHTML += `
                <div class="border rounded p-2 h-16 ${isCurrentMonth ? 'bg-white' : 'bg-gray-50'} ${isToday ? 'ring-2 ring-blue-500' : ''} hover:bg-blue-50 cursor-pointer" 
                     onclick="selectDate('${dateStr}')">
                    <div class="text-xs ${isCurrentMonth ? 'text-gray-900' : 'text-gray-400'}">${currentDate.getUTCDate()}</div>
                    <div class="flex items-center gap-1 mt-0.5">
                        ${stayOvers > 0 ? `<span class="text-xs text-blue-600"><i class="fas fa-bed"></i> ${stayOvers}</span>` : ''}
                        ${checkIns > 0 ? `<span class="text-xs text-green-600"><i class="fas fa-arrow-down"></i> ${checkIns}</span>` : ''}
                        ${checkOuts > 0 ? `<span class="text-xs text-red-600"><i class="fas fa-arrow-up"></i> ${checkOuts}</span>` : ''}
                    </div>
                </div>
            `;
        }
        
        calendarHTML += '</div>';
        calendarContainer.innerHTML = calendarHTML;
    }
    
    renderBookingsTable() {
        const tableBody = document.getElementById('bookings-table');
        
        if (this.filteredBookings.length === 0) {
            tableBody.innerHTML = `
                <tr>
                    <td colspan="9" class="text-center py-8 text-gray-500">
                        <i class="fas fa-calendar-times text-2xl mb-2"></i>
                        <p>No bookings found</p>
                    </td>
                </tr>
            `;
            return;
        }
        
        const tableHTML = this.filteredBookings.map(booking => `
            <tr class="border-b hover:bg-gray-50">
                <td class="py-3 px-4">
                    <div class="font-medium text-gray-900">${booking.guest_name}</div>
                    <div class="text-sm text-gray-500">${booking.guest_phone || ''}</div>
                </td>
                <td class="py-3 px-4 text-gray-900">${booking.channel || ''}</td>
                <td class="py-3 px-4 text-gray-900">${booking.room_number}</td>
                <td class="py-3 px-4 text-gray-900">${parseInt(booking.rooms_booked || 1, 10) || 1}</td>
                <td class="py-3 px-4 text-gray-900">${this.formatDate(booking.check_in)}</td>
                <td class="py-3 px-4 text-gray-900">${this.formatDate(booking.check_out)}</td>
                <td class="py-3 px-4">
                    <span class="status-badge ${this.getStatusClass(booking.status)}">
                        ${this.capitalizeFirst(booking.status)}
                    </span>
                </td>
                <td class="py-3 px-4 text-gray-900">RM${booking.amount}</td>
                <td class="py-3 px-4">
                    <button onclick="bookingManager.showEditBookingModal('${booking.id}')" 
                            class="text-green-600 hover:text-green-800 text-sm">
                        <i class="fas fa-edit"></i>
                    </button>
                </td>
            </tr>
        `).join('');
        
        tableBody.innerHTML = tableHTML;
    }
    
    getStatusClass(status) {
        const statusClasses = {
            'confirmed': 'bg-green-100 text-green-800',
            'pending': 'bg-yellow-100 text-yellow-800',
            'checked-in': 'bg-blue-100 text-blue-800',
            'checked-out': 'bg-gray-100 text-gray-800',
            'cancelled': 'bg-red-100 text-red-800'
        };
        return statusClasses[status] || 'bg-gray-100 text-gray-800';
    }
    
    formatDate(dateStr) {
        return new Date(dateStr + 'T00:00:00').toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric',
            year: 'numeric'
        });
    }
    
    capitalizeFirst(str) {
        return str.charAt(0).toUpperCase() + str.slice(1).replace('-', ' ');
    }
    
    applyFilters() {
        const showCancelled = !!document.getElementById('show-cancelled')?.checked;
        const dateFrom = document.getElementById('date-from').value;
        const dateTo = document.getElementById('date-to').value;
        const guestSearch = document.getElementById('guest-search').value.toLowerCase();
        const roomTypeFilter = String(document.getElementById('room-type-filter')?.value || '').toUpperCase().trim();
        
        this.filteredBookings = this.bookings.filter(booking => {
            // Cancelled toggle
            const isCancelled = String(booking.status || '').toLowerCase() === 'cancelled';
            if (!showCancelled && isCancelled) return false;
            
            // Date range filter (UTC, include check-in, check-out, and stay-over)
            if (dateFrom || dateTo) {
                const normalizeYmd = (s) => this.formatYmdLocal(this.parseYmdLocal(String(s||'')) || new Date(NaN));
                const inYmd = normalizeYmd(booking.check_in);
                const outYmd = normalizeYmd(booking.check_out);
                // Overlap logic: (inYmd <= dateTo) AND (outYmd > dateFrom)
                if (dateTo && inYmd > dateTo) return false;
                if (dateFrom && (!outYmd || outYmd <= dateFrom)) return false;
            }
            
            // Room type filter (select list; empty = all)
            if (roomTypeFilter) {
                const rt = String(booking.room_number || booking.room_type || '').toUpperCase();
                if (!rt.includes(roomTypeFilter)) return false;
            }

            // Guest name search
            if (guestSearch && !booking.guest_name.toLowerCase().includes(guestSearch)) return false;
            
            return true;
        });
        
        this.renderBookingsTable();
    }
    
    previousMonth() {
        this.currentDate = new Date(Date.UTC(this.currentDate.getUTCFullYear(), this.currentDate.getUTCMonth() - 1, 1));
        this.renderCalendar();
    }
    
    nextMonth() {
        this.currentDate = new Date(Date.UTC(this.currentDate.getUTCFullYear(), this.currentDate.getUTCMonth() + 1, 1));
        this.renderCalendar();
    }
    
    setupEventListeners() {
        // Auto-apply filters on input change
        ['show-cancelled', 'date-from', 'date-to', 'room-type-filter'].forEach(id => {
            document.getElementById(id).addEventListener('change', () => this.applyFilters());
        });
        
        document.getElementById('guest-search').addEventListener('input', () => {
            clearTimeout(this.searchTimeout);
            this.searchTimeout = setTimeout(() => this.applyFilters(), 300);
        });
    }
} 
