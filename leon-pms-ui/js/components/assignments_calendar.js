(() => {
	const apiBase = (location.hostname === 'localhost' || location.hostname === '127.0.0.1')
		? 'http://localhost:8002/api'
		: 'https://api.inap.ai/api';

	const DAY_COL_WIDTH = 104; // fixed px width per day column
    const LEFT_COL_WIDTH = 120; // room number column width (wider to fit nav buttons)
	const BAR_GAP_PX = 4; // wider gap on both ends for narrower bars
	const LABEL_TEXT_INSET_PX = 6; // fixed inner margin for label text within the bar
const VERTICAL_GAP_PX = 8; // total vertical gap (top+bottom); bar height = row height - gap

// Collapsed room types state
const _collapsedTypes = new Set();

function renamedRoom(roomNumber) {
	return String(roomNumber || '').trim().toUpperCase();
}

async function loadRoomLabels() {
	return;
}

	// --- Date helpers (use browser timezone/local dates) ---
	function toLocalISODate(d) {
		try {
			const y = d.getFullYear();
			const m = String(d.getMonth() + 1).padStart(2, '0');
			const day = String(d.getDate()).padStart(2, '0');
			return `${y}-${m}-${day}`;
		} catch (_) { return new Date().toISOString().slice(0,10); }
	}

	function parseLocalDate(dateStr) {
		// Expects YYYY-MM-DD, returns Date at local midnight
		try {
			const [y, m, d] = String(dateStr || '').split('-').map(x => parseInt(x, 10));
			return new Date(y, (m - 1), d, 0, 0, 0, 0);
		} catch(_) { const now = new Date(); now.setHours(0,0,0,0); return now; }
	}

	function shiftLocalISO(dateStr, days) {
		try {
			const base = parseLocalDate(dateStr);
			base.setDate(base.getDate() + (days || 0));
			return toLocalISODate(base);
		} catch(_) { return dateStr; }
	}

	// Visual-only: convert UTC ISO string to browser local time (YYYY-MM-DD HH:MM)
	function toLocalYmdHmFromUTCString(isoLike) {
		try {
			let s = String(isoLike || '');
			// Trim fractional seconds to 3 digits for JS Date
			s = s.replace(/(\.\d{3})\d+/, '$1');
			// If no timezone marker, assume UTC
			if (s && !/(Z|[+-]\d{2}:?\d{2})$/i.test(s)) s = s + 'Z';
			const d = new Date(s);
			if (!(d instanceof Date) || isNaN(d)) return String(isoLike || '');
			const y = d.getFullYear();
			const m = String(d.getMonth() + 1).padStart(2, '0');
			const day = String(d.getDate()).padStart(2, '0');
			const hh = String(d.getHours()).padStart(2, '0');
			const mm = String(d.getMinutes()).padStart(2, '0');
			return `${y}-${m}-${day} ${hh}:${mm}`;
		} catch(_) { return String(isoLike || ''); }
	}

	// Simple in-memory cache for booking previews
	const _previewCache = new Map(); // booking_id -> data or Promise

// Cache for available rooms by (hotel|roomType|stayDate)
const _availableRoomsCache = new Map();
function invalidateAvailableRoomsCache(hotel, roomType, stayDate){
    try {
        const key = `${String(hotel||'').toUpperCase()}|${String(roomType||'').toUpperCase()}|${String(stayDate||'').slice(0,10)}`;
        _availableRoomsCache.delete(key);
    } catch(_){ /* no-op */ }
}

// Index of checked-in bookings by date for quick coloring (date -> Set(bookingId))
let _checkinsIndex = new Map();
async function fetchCheckins(params){
    try{
        const q = new URLSearchParams({
            hotel_name: String(params.hotel_name||''),
            start_date: String(params.start_date||''),
            end_date: String(params.end_date||''),
            room_type: String(params.room_type||'') || undefined,
            room_numbers: String(params.room_numbers||'') || undefined
        });
        const res = await fetch(`${apiBase}/assignments/calendar/checkins?${q.toString()}`, { credentials:'include' });
        const j = await res.json().catch(()=>({}));
        const idx = new Map();
        if (j && Array.isArray(j.rooms)){
            for (const r of j.rooms){
                const halves = (r && r.halves) || {};
                for (const [d, h] of Object.entries(halves)){
                    const chk = (h && (h.checkin || h.check_in));
                    if (chk && chk.booking_id){
                        const keyD = String(d);
                        if (!idx.has(keyD)) idx.set(keyD, new Set());
                        idx.get(keyD).add(String(chk.booking_id));
                    }
                }
            }
        }
        _checkinsIndex = idx;
    }catch(_){ _checkinsIndex = new Map(); }
}

// Read latest assigned room just-in-time for a booking/stay_date
async function getLatestAssignedRoom(hotel, bookingId, stayDate) {
    try {
        const qs = new URLSearchParams({
            hotel_name: String(hotel||'').toUpperCase(),
            booking_id: String(bookingId||''),
            stay_date: String(stayDate||'')
        }).toString();
        const res = await fetch(`${apiBase}/assignments/detail?${qs}`, { credentials: 'include' });
        const j = await res.json().catch(() => ({}));
        const rooms = (j && j.check && Array.isArray(j.check.today_rooms)) ? j.check.today_rooms : [];
        const r = rooms.length ? String(rooms[0]).toUpperCase() : '';
        if (r && r !== 'NO ROOM') return r;
    } catch(_) {}
    try {
        const el = document.querySelector('#assign-edit-modal #det-room-number');
        const t = el ? (el.textContent || '') : '';
        const r2 = String(t).toUpperCase();
        return (r2 && r2 !== 'NO ROOM') ? r2 : '';
    } catch(_) { return ''; }
}

// removed busy overlay logic (cosmetic)
async function fetchAvailableRoomsCached(hotel, roomType, stayDate) {
    try {
        const key = `${String(hotel||'').toUpperCase()}|${String(roomType||'').toUpperCase()}|${String(stayDate||'').slice(0,10)}`;
        if (_availableRoomsCache.has(key)) {
            const cached = _availableRoomsCache.get(key);
            return Array.isArray(cached) ? cached : await Promise.resolve(cached);
        }
        const p = (async () => {
            const res = await fetch(`${apiBase}/hotels/available-rooms?hotel_name=${encodeURIComponent(String(hotel||'').toUpperCase())}&room_type=${encodeURIComponent(String(roomType||'').toUpperCase())}&stay_date=${encodeURIComponent(String(stayDate||'').slice(0,10))}`, { credentials: 'include' });
            const j = await res.json().catch(()=>({ available: [] }));
            return Array.isArray(j.available) ? j.available : [];
        })();
        _availableRoomsCache.set(key, p);
        const list = await p;
        _availableRoomsCache.set(key, list);
        return list;
    } catch(_) { return []; }
}

	function scheduleFullReloadAndReopen(bookingId, stayDate, roomType, roomNumber) {
		try {
			const modalOpen = !!document.getElementById('assign-edit-modal') && document.getElementById('assign-edit-modal').style.display === 'flex';
			if (modalOpen) {
				const keptRoom = roomNumber || (document.getElementById('assign-edit-modal').dataset.roomNumber || '');
				const payload = { bookingId: String(bookingId||''), stayDate: String(stayDate||''), roomType: String(roomType||'').toUpperCase(), roomNumber: String(keptRoom||'').toUpperCase() };
				sessionStorage.setItem('leon_reopen_modal', JSON.stringify(payload));
			}
		} catch(_) {}
		try {
			const ev = new Event('beforeunload'); window.dispatchEvent(ev);
		} catch(_) {}
	}

	async function fetchPreviewsBulk(bookingIds) {
		if (!bookingIds || bookingIds.length === 0) return {};
		const unique = Array.from(new Set(bookingIds.filter(Boolean)));
		if (unique.length === 0) return {};
		try {
			const res = await fetch(`${apiBase}/assignments/preview_bulk`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				credentials: 'include',
				body: JSON.stringify({ booking_ids: unique })
			});
			if (!res.ok) throw new Error(`API ${res.status}`);
			const data = await res.json();
			return (data && data.previews) || {};
		} catch (_) {
			return {};
		}
	}

	function ensureTooltip(container) {
		if (container._assignTooltip) return container._assignTooltip;
		const tip = document.createElement('div');
		tip.className = 'assign-tooltip';
		tip.style.position = 'fixed';
		tip.style.zIndex = '9999';
		tip.style.pointerEvents = 'none';
		tip.style.background = 'rgba(255,255,255,0.98)';
		tip.style.border = '1px solid rgba(0,0,0,0.1)';
		tip.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
		tip.style.padding = '6px 8px';
		tip.style.fontSize = '12px';
		tip.style.borderRadius = '4px';
		tip.style.color = '#111827';
		tip.style.display = 'none';
		document.body.appendChild(tip);
		container._assignTooltip = tip;
		return tip;
	}

	function positionTooltipAtSegment(seg, tip) {
		const segRect = seg.getBoundingClientRect();
		// Anchor BELOW the bar centered (viewport-based)
		let left = segRect.left + (segRect.width / 2) - (tip.offsetWidth / 2);
		let top = segRect.bottom + 8;
		// Clamp to viewport
		const vw = Math.max(document.documentElement.clientWidth, window.innerWidth || 0);
		const vh = Math.max(document.documentElement.clientHeight, window.innerHeight || 0);
		left = Math.max(8, Math.min(left, vw - tip.offsetWidth - 8));
		top = Math.max(8, Math.min(top, vh - tip.offsetHeight - 8));
		tip.style.left = `${left}px`;
		tip.style.top = `${top}px`;
	}

	function renderPreviewHtml(pre) {
		if (!pre || pre.error) return '<div class="text-red-600">Not available</div>';
		const bid = pre.booking_id || '';
		const phone = pre.guest_phone || '';
		const email = pre.guest_email || '';
		const total = typeof pre.total_price === 'number' ? pre.total_price.toFixed(2) : (pre.total_price || '');
		const notes = pre.booking_notes || '';
		const cin = pre.check_in_date || '';
		const cout = pre.check_out_date || '';
		return `
			<div style="min-width:200px; max-width:280px;">
				<div style="font-weight:600; margin-bottom:4px;">${bid}</div>
				<div>Guest: ${phone}</div>
				${email ? `<div>Email: ${email}</div>` : ''}
				${total ? `<div>Total: ${total}</div>` : ''}
				${cin || cout ? `<div>Stay: ${cin || '-'} → ${cout || '-'}</div>` : ''}
				${notes ? `<div style="margin-top:4px;">Notes: ${notes}</div>` : ''}
			</div>
		`;
	}

    // Expose reopen helper for SPA
    try {
        window.AssignmentsCalendar = window.AssignmentsCalendar || {};
        window.AssignmentsCalendar.reopen = function(p){ try { openEditModal(p && p.bookingId, '', p && p.stayDate, p && p.roomType, p && p.roomNumber); } catch(_) {} };
        window.addEventListener('leon:reopen-modal', function(ev){ try { var d=ev.detail||{}; openEditModal(d.bookingId, '', d.stayDate, d.roomType, d.roomNumber); } catch(_) {} });
        // Local fallback to reopen from sessionStorage after hard reload
        document.addEventListener('DOMContentLoaded', function(){
            try {
                var raw = sessionStorage.getItem('leon_reopen_modal');
                if (raw) {
                    sessionStorage.removeItem('leon_reopen_modal');
                    try { var p = JSON.parse(raw)||{}; setTimeout(function(){ try { openEditModal(p.bookingId, '', p.stayDate, p.roomType, p.roomNumber); } catch(_) {} }, 300); } catch(_) {}
                }
            } catch(_) {}
        });
    } catch(_) {}

	// --- Inline Edit Modal (lightweight, assignments page) ---
	function ensureEditModal() {
		if (document.getElementById('assign-edit-modal')) return document.getElementById('assign-edit-modal');
		const modal = document.createElement('div');
		modal.id = 'assign-edit-modal';
		modal.style.position = 'fixed';
		modal.style.inset = '0';
		modal.style.background = 'rgba(0,0,0,0.5)';
		modal.style.display = 'none';
		modal.style.alignItems = 'center';
		modal.style.justifyContent = 'center';
		modal.style.zIndex = '10000';
		modal.innerHTML = `
			<div class="bg-white rounded-lg shadow-lg w-full max-w-3xl p-4 relative max-h-screen overflow-y-auto">
				<button id="assign-edit-close" class="absolute top-2 right-2 text-gray-500 hover:text-gray-700">✕</button>
				<div class="mb-2">
					<div class="flex items-center justify-between">
						<h3 class="text-lg font-semibold"><span id="assign-bid"></span></h3>
						<span id="assign-status" class="text-xs px-2 py-0.5 rounded bg-gray-100 text-gray-700"></span>
					</div>
					<div class="text-sm text-gray-700"><span id="assign-guest"></span></div>
				</div>
				<!-- Tabs -->
					<div class="mb-3 flex flex-wrap gap-2 border-b pb-2">
						<button id="tab-assign" class="px-3 py-1 rounded bg-blue-600 text-white">Summary</button>
						<button id="tab-booking" class="px-3 py-1 rounded bg-gray-200 text-gray-800 hover:bg-gray-300">Booking</button>
						<button id="tab-guest" class="px-3 py-1 rounded bg-gray-200 text-gray-800 hover:bg-gray-300">Guest</button>
						<button id="tab-stay" class="px-3 py-1 rounded bg-gray-200 text-gray-800 hover:bg-gray-300">Stay</button>
						<button id="tab-payment" class="px-3 py-1 rounded bg-gray-200 text-gray-800 hover:bg-gray-300">Payment</button>
					</div>
				<div id="pane-assign">
					<div class="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
						<div>Booking ID: <span id="det-booking-id" class="font-medium"></span></div>
						<div>Status: <span id="det-booking-status"></span></div>
						<div>Room: <span id="det-room-number"></span></div>
						<div>Room Type: <span id="det-room-type"></span></div>
						<div class="col-span-2">Stay: <span id="assign-stayrange"></span></div>
						<div>Nights: <span id="det-nights"></span> · Rooms: <span id="det-rooms"></span></div>
						<div>Rate Plan: <span id="sum-rate-plan"></span></div>
						<div>Stay Status: <span id="det-stay-status"></span></div>
						<div>Total: <span id="det-total"></span> <span id="det-currency" class="text-gray-500"></span></div>
						<div>Payment: <span id="det-payment-status"></span></div>
						<div>Paid: <span id="det-amount-paid"></span></div>
						<div>Remaining: <span id="det-remaining"></span></div>
					</div>
					<div class="mt-4 flex items-center justify-between gap-2 flex-wrap">
						<div class="flex gap-2">
							<button id="assign-checkin" class="hidden bg-green-600 text-white px-3 py-1 rounded hover:bg-green-700">Check In</button>
							<button id="assign-undo-checkin" class="hidden bg-gray-600 text-white px-3 py-1 rounded hover:bg-gray-700">Undo Check-in</button>
							<button id="assign-checkout" class="hidden bg-yellow-700 text-white px-3 py-1 rounded hover:bg-yellow-800">Check Out</button>
						</div>
						<div class="flex gap-2 flex-wrap justify-end">
							<button id="doc-registration" type="button" class="bg-gray-700 text-white px-3 py-1 rounded hover:bg-gray-800">Registration Card</button>
							<button id="doc-invoice" type="button" class="bg-gray-700 text-white px-3 py-1 rounded hover:bg-gray-800">Invoice</button>
							<button id="doc-receipt" type="button" class="bg-gray-700 text-white px-3 py-1 rounded hover:bg-gray-800">Receipt</button>
						</div>
					</div>
				</div>
				<div id="pane-guest" class="hidden text-sm space-y-1">
					<div>Name: <input id="det-guest-name" class="border rounded px-1 py-0.5 w-full" /></div>
					<div class="flex items-center flex-wrap gap-2">Phone: <span id="det-guest-phone"></span><button id="det-verify-phone" type="button" class="hidden px-2 py-0.5 text-xs bg-blue-600 text-white rounded">Verified phone</button></div>
					<div>Email: <input id="det-guest-email" class="border rounded px-1 py-0.5 w-full" /></div>
					<div>Country: <select id="det-guest-country" class="border rounded px-1 py-0.5 w-full"></select></div>
					<div>Date of Birth: <input id="det-guest-dob" type="date" class="border rounded px-1 py-0.5" /></div>
					<div>Gender: <select id="det-guest-gender" class="border rounded px-1 py-0.5 w-full"></select></div>
					<div>Company: <input id="det-guest-company" class="border rounded px-1 py-0.5 w-full" /></div>
					<div>Address: <input id="det-guest-address" class="border rounded px-1 py-0.5 w-full" /></div>
					<div>ID type: <select id="det-guest-identity" class="border rounded px-1 py-0.5 w-full"></select></div>
					<div>ID number: <input id="det-guest-id-detail" class="border rounded px-1 py-0.5 w-full" /></div>
					<div>Special Request: <input id="det-guest-request" class="border rounded px-1 py-0.5 w-full" /></div>
					<label class="inline-flex items-center gap-1 mt-1"><input id="det-guest-vip" type="checkbox" /> VIP</label>
					<div class="flex justify-end mt-3">
						<button id="assign-save" class="bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-700">Save</button>
					</div>
				</div>
				<div id="pane-stay" class="hidden text-sm">
					<div class="flex items-center justify-between">
						<div>Status: <span id="det-stay-status-detail"></span></div>
						<button id="det-stay-secondary" class="bg-blue-600 text-white px-2 py-1 rounded hover:bg-blue-700 text-xs"></button>
					</div>
					<div>Room Number: <span id="det-room-number-detail"></span></div>
					<div>Assigned At: <span id="det-assigned-at"></span></div>
					<div>Checked In At: <span id="det-checked-in-at"></span></div>
					<div>Checked Out At: <span id="det-checked-out-at"></span></div>
				</div>
				<!-- Booking Pane -->
				<div id="pane-booking" class="hidden">
					<div class="text-sm mb-3">
						<div>Rate Plan:
							<select id="det-rate-plan" class="border rounded px-1 py-0.5 ml-1"></select>
						</div>
						<div id="det-rate-plan-note" class="text-xs text-gray-500"></div>
						<label for="assign-notes" class="font-medium mt-2 block">Notes</label>
						<textarea id="assign-notes" class="w-full border rounded p-2 mt-1" rows="3" placeholder="Update booking notes..."></textarea>
						<div class="flex justify-end mt-2">
							<button id="assign-save-rate" class="bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-700">Save</button>
						</div>
					</div>
					<div class="flex items-center justify-between mb-2">
						<span id="eb-status" class="text-xs px-2 py-0.5 rounded bg-gray-100 text-gray-700"></span>
						<div class="hidden"></div>
					</div>
					<div class="grid grid-cols-2 gap-3 mb-2 text-sm">
						<div>
							<label class="font-medium">Check-in</label>
							<input id="eb-checkin" type="date" class="w-full border rounded px-2 py-1" />
						</div>
						<div>
							<label class="font-medium">Check-out</label>
							<input id="eb-checkout" type="date" class="w-full border rounded px-2 py-1" />
						</div>
					</div>
					<div class="grid grid-cols-2 gap-3 mb-2 text-sm">
						<div>
							<label class="font-medium">Room Type</label>
							<select id="eb-room-type" class="w-full border rounded px-2 py-1"></select>
						</div>
						<div>
							<label class="font-medium">Rooms</label>
							<select id="eb-rooms" class="w-full border rounded px-2 py-1"></select>
							<div id="eb-availability-hint" class="text-xs text-gray-500 mt-1"></div>
						</div>
					</div>
					<div id="eb-cancel-alert" class="hidden border border-red-300 bg-red-50 text-red-800 rounded p-3 mb-3">
						<div class="font-semibold mb-2">This process is irreversible. Are you sure you want to cancel this booking?</div>
						<div class="flex flex-wrap gap-2">
							<button id="eb-cancel-proceed" class="bg-red-600 text-white px-3 py-1 rounded hover:bg-red-700">PROCEED TO CANCEL BOOKING</button>
							<button id="eb-cancel-dismiss" class="bg-gray-200 text-gray-800 px-3 py-1 rounded hover:bg-gray-300">DO NOT CANCEL BOOKING</button>
						</div>
					</div>
					<div id="eb-staydates" class="space-y-1 mb-3" style="border: 1px solid rgba(229,231,235,0.9); border-radius: 6px; padding: 6px; height: 220px; overflow-y: auto; background: #fff;"></div>
					<div class="flex justify-between gap-2">
						<button id="eb-cancel-btn" class="bg-red-600 text-white px-3 py-1 rounded hover:bg-red-700">Cancel Booking</button>
						<div class="flex gap-2">
							<button id="eb-save-btn" class="bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-700">Save</button>
						</div>
					</div>
				</div>
				<!-- Payment Pane -->
				<div id="pane-payment" class="hidden">
					<div class="grid grid-cols-2 gap-4 text-sm">
						<!-- LEFT COLUMN: Summary, Edit, Ledger stacked -->
						<div class="space-y-4">
							<div class="border rounded p-2 bg-white">
							<div class="font-semibold text-base mb-1">Payment Summary</div>
								<div>Status: <span id="pay-status"></span></div>
								<div>Total: <span id="pay-total"></span> <span id="pay-currency" class="text-gray-500"></span></div>
								<div>Amount Paid: <span id="pay-paid"></span></div>
								<div>Remaining: <span id="pay-remaining"></span></div>
							</div>
							<div class="border rounded p-2 bg-white">
							<div class="font-semibold text-base mb-1">Edit Payment</div>
								<div class="grid grid-cols-2 gap-2 text-xs">
									<div>
										<label class="block mb-1">Method</label>
										<select id="pay-edit-method" class="w-full border rounded px-2 py-1">
											<option value="CARD">CARD</option>
											<option value="CASH">CASH</option>
											<option value="TRANSFER">TRANSFER</option>
											<option value="ONLINE">ONLINE</option>
											<option value="OTHER">OTHER</option>
										</select>
									</div>
									<div>
										<label class="block mb-1">Amount Paid</label>
										<input id="pay-edit-paid" type="number" step="0.01" class="w-full border rounded px-2 py-1" />
									</div>
								</div>
								<div class="flex justify-end mt-2">
									<button id="pay-edit-save" class="bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-700">Save Payment</button>
								</div>
								<div id="pay-edit-msg" class="text-xs text-gray-500 mt-1"></div>
							</div>
						<div class="border rounded p-2 bg-white">
							<div class="font-semibold text-base mb-1">Payment Received</div>
							<div id="pay-ledger-rec" class="space-y-1 text-xs"></div>
						</div>
						<div class="border rounded p-2 bg-white">
							<div class="font-semibold text-base mb-1">Payment Refunded</div>
							<div id="pay-ledger-ref" class="space-y-1 text-xs"></div>
						</div>
						</div>
						<!-- RIGHT COLUMN: Pricing -->
						<div class="border rounded p-2 bg-white">
							<div class="font-semibold text-base mb-1">Pricing</div>
							<div class="flex items-center gap-2 mb-2">
								<button id="pay-refresh" class="bg-gray-200 text-gray-800 px-2 py-1 rounded hover:bg-gray-300">Refresh Breakdown</button>
							</div>
							<pre id="pay-breakdown" class="text-xs bg-gray-50 p-2 rounded border overflow-auto max-h-48 hidden">-</pre>
							<div id="pay-breakdown-ui" class="text-sm space-y-2"></div>
							<div class="mt-2">
								<label class="block text-xs font-medium mb-1">Override Total Amount</label>
								<div class="flex items-center gap-2">
									<input id="pay-override-amount" type="number" step="0.01" class="border rounded px-2 py-1 w-40" placeholder="0.00" />
									<button id="pay-apply" class="bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-700">Apply</button>
								</div>
								<div id="pay-status-msg" class="text-xs text-gray-500 mt-1"></div>
							</div>
						</div>
					</div>
				</div>
				<!-- Room Pane removed -->
		`;
		document.body.appendChild(modal);
		modal.querySelector('#assign-edit-close').addEventListener('click', () => { modal.style.display = 'none'; });
		// Delegate booking-cancel actions so buttons always work
		try {
			if (!modal._cancelDelegates) {
				modal.addEventListener('click', async (ev) => {
					const t = ev.target;
					if (!t) return;
					const btnCancel = t.closest && t.closest('#eb-cancel-btn');
					const btnDismiss = t.closest && t.closest('#eb-cancel-dismiss');
					const btnProceed = t.closest && t.closest('#eb-cancel-proceed');
					const alertBox = modal.querySelector('#eb-cancel-alert');
					if (btnCancel) {
						ev.preventDefault(); ev.stopPropagation();
						try { if (alertBox) alertBox.classList.remove('hidden'); } catch(_) {}
						return;
					}
					if (btnDismiss) {
						ev.preventDefault(); ev.stopPropagation();
						try { if (alertBox) alertBox.classList.add('hidden'); } catch(_) {}
						return;
					}
					if (btnProceed) {
						ev.preventDefault(); ev.stopPropagation();
						try {
							const apiBaseLocal = (location.hostname === 'localhost' || location.hostname === '127.0.0.1') ? 'http://localhost:8002/api' : 'https://api.inap.ai/api';
							const bid = modal.dataset && modal.dataset.bookingId ? modal.dataset.bookingId : (modal.querySelector('#eb-booking-id')?.value || '');
							if (!bid) { alert('Missing booking_id'); return; }
							const resp = await fetch(`${apiBaseLocal}/bookings/cancel`, { method:'POST', headers:{'Content-Type':'application/json'}, credentials:'include', body: JSON.stringify({ booking_id: bid }) });
							const out = await resp.json().catch(()=>({}));
							if (!resp.ok || out.error) { alert(out.error || 'Failed to cancel booking'); return; }
							try { if (alertBox) alertBox.classList.add('hidden'); } catch(_) {}
							alert('Booking cancelled');
							modal.style.display = 'none';
							try { load(); } catch(_) { window.location.reload(); }
						} catch(e) { alert(e.message || 'Failed to cancel'); }
						return;
					}
				});
				modal._cancelDelegates = true;
			}
		} catch(_) {}
		// Wire simple tab toggle
		try {
            const btnA = modal.querySelector('#tab-assign');
            const btnB = modal.querySelector('#tab-booking');
            const btnR = null;
            const btnP = modal.querySelector('#tab-payment');
			const paneA = modal.querySelector('#pane-assign');
			const paneB = modal.querySelector('#pane-booking');
            const paneR = null;
            const paneP = modal.querySelector('#pane-payment');
			const activate = (which) => {
				const tabs = [
					['assign', '#tab-assign', '#pane-assign'],
					['booking', '#tab-booking', '#pane-booking'],
					['guest', '#tab-guest', '#pane-guest'],
					['stay', '#tab-stay', '#pane-stay'],
					['payment', '#tab-payment', '#pane-payment']
				];
				tabs.forEach(([name, btnSel, paneSel]) => {
					const btn = modal.querySelector(btnSel);
					const pane = modal.querySelector(paneSel);
					const on = name === which;
					if (pane) pane.classList.toggle('hidden', !on);
					if (btn) btn.className = on ? 'px-3 py-1 rounded bg-blue-600 text-white' : 'px-3 py-1 rounded bg-gray-200 text-gray-800 hover:bg-gray-300';
				});
			};
			if (btnA && !btnA._wired2) { btnA.addEventListener('click', () => activate('assign')); btnA._wired2 = true; }
			if (btnB && !btnB._wired2) { btnB.addEventListener('click', () => activate('booking')); btnB._wired2 = true; }
			const btnG = modal.querySelector('#tab-guest');
			const btnS = modal.querySelector('#tab-stay');
			if (btnG && !btnG._wired2) { btnG.addEventListener('click', () => activate('guest')); btnG._wired2 = true; }
			if (btnS && !btnS._wired2) { btnS.addEventListener('click', () => activate('stay')); btnS._wired2 = true; }
			if (btnR && !btnR._wired2) { btnR.addEventListener('click', () => activate('room')); btnR._wired2 = true; }
            if (btnP && !btnP._wired2) { btnP.addEventListener('click', () => activate('payment')); btnP._wired2 = true; }
			activate('assign');
		} catch(_) {}
        return modal;
	}

	// --- Full Booking Edit Modal (embedded copy for assignments page) ---
	function ensureFullBookingModal() {
		if (document.getElementById('edit-booking-modal')) return document.getElementById('edit-booking-modal');
		const modal = document.createElement('div');
		modal.id = 'edit-booking-modal';
		modal.className = 'fixed inset-0 bg-black bg-opacity-50 hidden';
		modal.style.zIndex = '10050';
		modal.innerHTML = `
			<div class="bg-white rounded-lg shadow-xl w-full max-w-2xl mx-auto mt-10 p-4 relative">
				<button id="eb-close" class="absolute top-2 right-2 text-gray-500 hover:text-gray-700">✕</button>
				<h3 class="text-lg font-semibold mb-2">Edit Booking</h3>
				<div class="flex items-center justify-between mb-2">
					<div><span class="text-sm text-gray-500">Booking ID</span>
						<input id="eb-booking-id" class="border rounded px-2 py-1 ml-2 text-sm" readonly />
					</div>
					<span id="eb-status" class="text-xs px-2 py-0.5 rounded bg-gray-100 text-gray-700"></span>
				</div>
				<div class="grid grid-cols-2 gap-3 mb-2">
					<div>
						<label class="text-sm font-medium">Check-in</label>
						<input id="eb-checkin" type="date" class="w-full border rounded px-2 py-1" />
					</div>
					<div>
						<label class="text-sm font-medium">Check-out</label>
						<input id="eb-checkout" type="date" class="w-full border rounded px-2 py-1" />
					</div>
				</div>
				<div class="grid grid-cols-2 gap-3 mb-2">
					<div>
						<label class="text-sm font-medium">Room Type</label>
						<select id="eb-room-type" class="w-full border rounded px-2 py-1"></select>
					</div>
					<div>
						<label class="text-sm font-medium">Rooms</label>
						<select id="eb-rooms" class="w-full border rounded px-2 py-1"></select>
						<div id="eb-availability-hint" class="text-xs text-gray-500 mt-1"></div>
					</div>
				</div>
				<div class="mb-3">
					<label class="text-sm font-medium">Notes</label>
					<textarea id="eb-notes" class="w-full border rounded px-2 py-1" rows="3"></textarea>
				</div>
				<div id="eb-staydates" class="space-y-1 mb-3"></div>
				<div class="flex justify-end gap-2">
					<button id="eb-cancel-btn" class="hidden bg-red-600 text-white px-3 py-1 rounded hover:bg-red-700">Cancel Booking</button>
					<button id="eb-save-btn" class="bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-700">Save</button>
				</div>
			</div>
		`;
		document.body.appendChild(modal);
		modal.querySelector('#eb-close').addEventListener('click', () => { modal.classList.add('hidden'); });
		return modal;
	}

	async function openFullBookingModal({ bookingId, hotelName, checkIn, checkOut, currentNotes }) {
		const modal = ensureFullBookingModal();
		const apiBaseLocal = (location.hostname === 'localhost' || location.hostname === '127.0.0.1') ? 'http://localhost:8002/api' : 'https://api.inap.ai/api';
		const apiRoot = apiBaseLocal.replace('/api','');
		const bidEl = modal.querySelector('#eb-booking-id');
		const stChip = modal.querySelector('#eb-status');
		const cinEl = modal.querySelector('#eb-checkin');
		const coutEl = modal.querySelector('#eb-checkout');
		const rtSel = modal.querySelector('#eb-room-type');
		const roomsSel = modal.querySelector('#eb-rooms');
		const notesEl = modal.querySelector('#eb-notes');
		const stayWrap = modal.querySelector('#eb-staydates');
		bidEl.value = bookingId || '';
		stChip.textContent = 'CONFIRMED'; stChip.className = 'text-xs px-2 py-0.5 rounded bg-green-100 text-green-800';
		cinEl.value = (checkIn || '').slice(0,10);
		coutEl.value = (checkOut || '').slice(0,10);
		notesEl.value = currentNotes || '';
		// Rooms 1..10 default 1
		roomsSel.innerHTML = ''; for (let i=1;i<=10;i++){ const o=document.createElement('option'); o.value=String(i); o.textContent=String(i); if (i===1) o.selected=true; roomsSel.appendChild(o); }
		// Load room types
		try {
			const hotel = String(hotelName || '').toUpperCase();
			const resp = await fetch(`${apiRoot}/api/hotels/rooms?hotel_name=${encodeURIComponent(hotel)}`, { credentials:'include' });
			const data = await resp.json().catch(()=>({}));
			const rts = (data && data.room_types) ? data.room_types : [];
			rtSel.innerHTML = '';
			rts.forEach(rt => { const opt=document.createElement('option'); opt.value=String(rt.room_type||'').toUpperCase(); opt.textContent=String(rt.room_type||''); rtSel.appendChild(opt); });
		} catch(_) {}
		// Derive stay date list from range
		const parseLocal = (s) => { try { const [y,m,d]=String(s||'').split('-').map(v=>parseInt(v,10)); return new Date(y, (m-1), d); } catch(_) { return null; } };
		const fmtLocal = (d) => { const y=d.getFullYear(); const m=String(d.getMonth()+1).padStart(2,'0'); const dd=String(d.getDate()).padStart(2,'0'); return `${y}-${m}-${dd}`; };
		const renderDerived = () => {
			stayWrap.innerHTML = '';
			const a = parseLocal(cinEl.value); const b = parseLocal(coutEl.value);
			if (!(a instanceof Date) || !(b instanceof Date) || isNaN(a) || isNaN(b) || b <= a) return;
			for (let d=new Date(a); d<b; d.setDate(d.getDate()+1)) {
				const row = document.createElement('div'); row.className = 'flex items-center gap-2';
				row.innerHTML = `<input type="date" class="nb-date border rounded px-3 py-2 flex-1" value="${fmtLocal(d)}" disabled />`;
				stayWrap.appendChild(row);
			}
		};
		['change','input'].forEach(evt => { cinEl.addEventListener(evt, renderDerived); coutEl.addEventListener(evt, renderDerived); });
		renderDerived();
		// Wire save -> /bookings/modify
		const saveBtn = modal.querySelector('#eb-save-btn');
		if (!saveBtn._wired) {
			saveBtn.addEventListener('click', async () => {
				try {
					const a = parseLocal(cinEl.value); const b = parseLocal(coutEl.value);
					const stay_dates = []; if (a && b && b > a) { for (let d=new Date(a); d<b; d.setDate(d.getDate()+1)) stay_dates.push(fmtLocal(d)); }
					const payload = {
						booking_id: bidEl.value,
						new_rooms_booked: parseInt(roomsSel.value||'1',10)||1,
						new_stay_dates: stay_dates,
						booking_notes: notesEl.value || '',
						new_room_type: (rtSel.value||'').toUpperCase() || undefined
					};
					const resp = await fetch(`${apiBaseLocal}/bookings/modify`, { method:'POST', headers:{'Content-Type':'application/json'}, credentials:'include', body: JSON.stringify(payload) });
					const out = await resp.json().catch(()=>({}));
					if (!resp.ok || out.error) throw new Error(out.error || 'Failed to modify booking');
					reloadSamePage();
				} catch(e) { alert(e.message || 'Failed to save'); }
			});
			saveBtn._wired = true;
		}
				// Wire Cancel Booking: show confirmation alert like bookings page
				try {
					const cancelBtn = modal.querySelector('#eb-cancel-btn');
					const alertBox = modal.querySelector('#eb-cancel-alert');
					const btnProceed = modal.querySelector('#eb-cancel-proceed');
					const btnDismiss = modal.querySelector('#eb-cancel-dismiss');
                    if (cancelBtn && !cancelBtn._wired) {
                        cancelBtn.addEventListener('click', (e) => {
                            e.preventDefault(); e.stopPropagation();
                            try { if (alertBox) alertBox.classList.remove('hidden'); } catch(_) {}
                        });
                        cancelBtn._wired = true;
                    }
                    if (btnDismiss && !btnDismiss._wired) {
                        btnDismiss.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); try { if (alertBox) alertBox.classList.add('hidden'); } catch(_) {} });
                        btnDismiss._wired = true;
                    }
                    if (btnProceed && !btnProceed._wired) {
                        btnProceed.addEventListener('click', async (e) => {
                            e.preventDefault(); e.stopPropagation();
							try {
								const apiBaseLocal = (location.hostname === 'localhost' || location.hostname === '127.0.0.1') ? 'http://localhost:8002/api' : 'https://api.inap.ai/api';
								const bid = (modal.querySelector('#eb-booking-id')?.value || bookingId);
								const resp = await fetch(`${apiBaseLocal}/bookings/cancel`, { method:'POST', headers:{'Content-Type':'application/json'}, credentials:'include', body: JSON.stringify({ booking_id: bid }) });
								const out = await resp.json().catch(()=>({}));
								if (!resp.ok || out.error) { alert(out.error || 'Failed to cancel booking'); return; }
								try { if (alertBox) alertBox.classList.add('hidden'); } catch(_) {}
								alert('Booking cancelled');
								modal.style.display = 'none';
								try { load(); } catch(_) { window.location.reload(); }
							} catch(e) { alert(e.message || 'Failed to cancel'); }
						});
						btnProceed._wired = true;
					}
				} catch(_) {}
		modal.classList.remove('hidden');
	}

	// --- Reassign Modal (drag-drop confirm) ---
	function ensureReassignModal() {
		if (document.getElementById('reassign-modal')) return document.getElementById('reassign-modal');
		const modal = document.createElement('div');
		modal.id = 'reassign-modal';
		modal.style.position = 'fixed';
		modal.style.inset = '0';
		modal.style.background = 'rgba(0,0,0,0.5)';
		modal.style.display = 'none';
		modal.style.alignItems = 'center';
		modal.style.justifyContent = 'center';
		modal.style.zIndex = '10000';
		modal.innerHTML = `
			<div class="bg-white rounded-lg shadow-lg w-full max-w-md p-4 relative">
				<button id="reassign-close" class="absolute top-2 right-2 text-gray-500 hover:text-gray-700">✕</button>
				<h3 class="text-lg font-semibold mb-3">Reassign Room</h3>
				<div class="space-y-2 text-sm">
					<div><span class="font-medium">Booking:</span> <span id="ra-bid"></span></div>
					<div><span class="font-medium">Stay Date:</span> <span id="ra-date"></span></div>
					<div><span class="font-medium">From:</span> <span id="ra-old"></span></div>
					<div class="flex items-center gap-2">
						<label class="font-medium" for="ra-new">To</label>
						<select id="ra-new" class="border rounded px-2 py-1 flex-1"><option value="">-- select --</option></select>
					</div>
					<div id="ra-status" class="text-xs text-gray-500"></div>
				</div>
				<div class="mt-4 flex justify-end gap-2">
					<button id="ra-cancel" class="bg-gray-200 text-gray-800 px-3 py-1 rounded hover:bg-gray-300">Cancel</button>
					<button id="ra-confirm" class="bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-700">Confirm</button>
				</div>
			</div>
		`;
		document.body.appendChild(modal);
		const close = () => { modal.style.display = 'none'; };
		modal.querySelector('#reassign-close').addEventListener('click', close);
		modal.querySelector('#ra-cancel').addEventListener('click', close);
		return modal;
	}

	async function openReassignModal(payload) {
		const { bookingId, targetDate, oldRoomNumber, targetRoomNumber, roomType } = payload || {};
		const modal = ensureReassignModal();
		const bidEl = modal.querySelector('#ra-bid');
		const dateEl = modal.querySelector('#ra-date');
		const oldEl = modal.querySelector('#ra-old');
		const newInput = modal.querySelector('#ra-new');
		const statusEl = modal.querySelector('#ra-status');
		const btn = modal.querySelector('#ra-confirm');
		bidEl.textContent = bookingId || '';
		dateEl.textContent = targetDate || '';
		oldEl.textContent = (oldRoomNumber && String(oldRoomNumber).trim()) ? renamedRoom(oldRoomNumber) : 'NO ROOM';
		newInput.value = targetRoomNumber || '';
		statusEl.textContent = '';
		// Persist original old room for accurate payloads
		try { modal.dataset.oldRoom = (oldRoomNumber && String(oldRoomNumber).trim()) ? String(oldRoomNumber).toUpperCase() : ''; } catch(_) {}
		// Persist room type context if provided (for cases when Details modal is not open)
		try { if (roomType) { modal.dataset.roomType = String(roomType).toUpperCase(); } } catch(_) {}
		// Populate selectable rooms (same room_type, available at targetDate)
		try {
			const params = getParamsFromUI();
			const hotel = String(params.hotel_name || '').toUpperCase();
			// We can reuse the calendar payload to infer room_type by looking up the current row when opening; fallback to modal.dataset.roomType when available
			let roomType = '';
			try { roomType = (document.getElementById('eb-room-type') && document.getElementById('eb-room-type').value) ? String(document.getElementById('eb-room-type').value).toUpperCase() : ''; } catch(_) {}
			if (!roomType) { try { roomType = (document.querySelector('#det-room-type') && document.querySelector('#det-room-type').textContent) ? String(document.querySelector('#det-room-type').textContent).toUpperCase() : ''; } catch(_) {}
			}
			if (!roomType) { try { roomType = (modal.dataset && modal.dataset.roomType) ? String(modal.dataset.roomType).toUpperCase() : ''; } catch(_) {}
			}
            const apiRoot = apiBase.replace('/api','');
            const d = String(targetDate || '').slice(0,10);
            if (newInput && newInput.tagName === 'SELECT') {
                newInput.innerHTML = '';
                // Add NO ROOM option to allow unassignment
                (function(){ const opt = document.createElement('option'); opt.value = ''; opt.textContent = 'NO ROOM'; newInput.appendChild(opt); })();
                const landed = String(targetRoomNumber || '').toUpperCase();
                if (hotel && roomType && d) {
                    // Use dedicated API for available rooms
                    try {
                        const resp = await fetch(`${apiBase}/hotels/available-rooms?hotel_name=${encodeURIComponent(hotel)}&room_type=${encodeURIComponent(roomType)}&stay_date=${encodeURIComponent(d)}`, { credentials:'include' });
                        const j = await resp.json().catch(()=>({ available: [] }));
                        const list = Array.isArray(j.available) ? j.available : [];
                        list.forEach(v => { const val = String(v||'').toUpperCase(); if (!val) return; const opt = document.createElement('option'); opt.value = val; opt.textContent = renamedRoom(val); newInput.appendChild(opt); });
                    } catch(_) {}
                }
                // Preselect landed room number regardless of date position; if missing, insert it temporarily
                if (landed) {
                    const found = Array.from(newInput.options).some(o => o.value === landed);
                    if (!found) {
                        const opt = document.createElement('option'); opt.value = landed; opt.textContent = renamedRoom(landed); newInput.insertBefore(opt, newInput.firstChild);
                    }
                    newInput.value = landed;
                } else {
                    // Default to NO ROOM when no landed room (empty)
                    newInput.value = '';
                }
            }
		} catch(_) {}
		if (!btn._wired) {
			btn.addEventListener('click', async () => {
				try {
					const params = getParamsFromUI();
					const hotel = String(params.hotel_name || '').toUpperCase();
					// Prefer persisted original old room; fallback to visible label
					let fromNow = '';
					try {
						const persisted = (modal.dataset && modal.dataset.oldRoom) ? String(modal.dataset.oldRoom).toUpperCase() : '';
						if (persisted && persisted !== 'NO ROOM') {
							fromNow = persisted;
						} else {
							const cur = (oldEl.textContent || '').trim();
							fromNow = (cur && cur.toUpperCase() !== 'NO ROOM') ? cur.toUpperCase() : '';
						}
					} catch(_) {}
					const body = {
						hotel_name: hotel,
						booking_id: bookingId,
						stay_date: targetDate,
						old_room_number: String(fromNow || ''),
						new_room_number: String(newInput.value || '').toUpperCase()
					};
					statusEl.textContent = 'Reassigning...';
					statusEl.className = 'text-xs text-gray-500';
					const resp = await fetch(`${apiBase}/assignments/reassign`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify(body) });
					const data = await resp.json().catch(() => ({}));
					if (!resp.ok || data.error) {
						statusEl.textContent = data.error || 'Failed to reassign';
						statusEl.className = 'text-xs text-red-600';
						return;
					}
                    modal.style.display = 'none';
                    // If edit modal is open, refresh it immediately before full reload and warm cache
                    try {
                        const em = document.getElementById('assign-edit-modal');
                        if (em && em.style.display === 'flex') {
                            // removed busy overlay
                            let rt = '';
                            try { rt = (em.dataset && em.dataset.roomType) ? em.dataset.roomType : ''; } catch(_){ rt=''; }
                            try { await openEditModal(String(bookingId||''), '', String(targetDate||''), rt); } catch(_) {}
                            try {
                                const params = getParamsFromUI();
                                const hotelUp = String((params && params.hotel_name) || '').toUpperCase();
                                if (hotelUp && rt) { invalidateAvailableRoomsCache(hotelUp, rt, String(targetDate||'')); await fetchAvailableRoomsCached(hotelUp, rt, String(targetDate||'').slice(0,10)); }
                            } catch(_) {}
                            // removed busy overlay
                        }
                    } catch(_) {}
                    try {
                        // Always refresh calendar; only set reopen flag if modal open
                        scheduleFullReloadAndReopen(String(bookingId||''), String(targetDate||''), (modal && modal.dataset && modal.dataset.roomType) ? modal.dataset.roomType : '');
                        try { load(); } catch(_) { window.location.reload(); }
                    } catch(_) { try { load(); } catch(__) { window.location.reload(); } }
				} catch (e) {
					statusEl.textContent = e.message || 'Failed to reassign';
					statusEl.className = 'text-xs text-red-600';
				}
			});
			btn._wired = true;
		}
		modal.style.display = 'flex';
	}

async function fillRatePlans(modal, data) {
    const select = modal.querySelector('#det-rate-plan');
    const note = modal.querySelector('#det-rate-plan-note');
    if (!select) return;
    const params = getParamsFromUI();
    const hotel = String((params && params.hotel_name) || (data && data.hotel_name) || '').toUpperCase();
    const current = String((data && data.booking && data.booking.rate_plan) || '').toUpperCase();
    let plans = [];
    try {
        const resp = await fetch(`${apiBase}/rate_plans?hotel_name=${encodeURIComponent(hotel)}`, { credentials: 'include' });
        const body = await resp.json().catch(() => ({}));
        plans = Array.isArray(body.rate_plans) ? body.rate_plans : [];
    } catch (_) { plans = []; }
    select.innerHTML = '<option value=""></option>' + plans.map((plan) => {
        const name = String(plan.rate_plan || '').toUpperCase();
        return `<option value="${name}">${name}</option>`;
    }).join('');
    if (current && !plans.some((plan) => String(plan.rate_plan || '').toUpperCase() === current)) {
        select.insertAdjacentHTML('beforeend', `<option value="${current}">${current}</option>`);
    }
    select.value = current;
    const showNote = () => {
        const chosen = plans.find((plan) => String(plan.rate_plan || '').toUpperCase() === select.value);
        const text = chosen ? String(chosen.description || '') : (select.value === current ? String((data.booking && data.booking.rate_plan_description) || '') : '');
        if (note) note.textContent = text;
        const summary = modal.querySelector('#sum-rate-plan');
        if (summary) summary.textContent = select.value ? (text ? `${select.value} · ${text}` : select.value) : '';
    };
    showNote();
    if (!select._wired) {
        select.addEventListener('change', showNote);
        select._wired = true;
    }
}

function stayDocHtml(title, rows) {
    const body = rows.map((row) => `<tr><th>${row[0]}</th><td>${row[1]}</td></tr>`).join('');
    return `<h1>${title}</h1><table>${body}</table>`;
}

function cardText(value) {
    return String(value || '').split('&').join('&amp;').split('<').join('&lt;').split('>').join('&gt;').split('\n').join('<br>');
}

function openPrint(title, inner) {
    const win = window.open('', '_blank');
    if (!win) { alert('Allow pop-ups to print this document'); return; }
    win.document.write(`<!doctype html><html><head><title>${title}</title><style>body{font-family:Arial,sans-serif;padding:24px;color:#111}.letterhead{text-align:center;border-bottom:2px solid #111;margin:0 0 16px;padding:0 0 10px}.lh-name{font-size:22px;font-weight:700;letter-spacing:.04em}.lh-line{font-size:12px;margin-top:2px}h1{font-size:18px;margin:0 0 12px}h2{font-size:15px;margin:18px 0 6px}p{font-size:13px;line-height:1.4}table{width:100%;border-collapse:collapse}th,td{border:1px solid #ddd;padding:6px 8px;text-align:left;font-size:13px}th{width:180px;background:#f7f7f7}.line{margin-top:48px;border-top:1px solid #111;width:220px;padding-top:4px;font-size:12px}</style></head><body>${inner}<div class="line">Signature</div><script>window.print()<\\/script></body></html>`);
    win.document.close();
}

function letterheadHtml(hotel, details) {
    const kt = details || {};
    const place = [kt.address, kt.city, kt.state_region, kt.country].map((part) => String(part || '').trim()).filter(Boolean);
    const reach = [kt.contact_phone, kt.contact_email].map((part) => String(part || '').trim()).filter(Boolean);
    const lines = [];
    if (place.length) lines.push(`<div class="lh-line">${cardText(place.join(', '))}</div>`);
    if (reach.length) lines.push(`<div class="lh-line">${cardText(reach.join(' · '))}</div>`);
    return `<header class="letterhead"><div class="lh-name">${cardText(hotel)}</div>${lines.join('')}</header>`;
}

async function loadHotelLetterhead(hotel) {
    let details = {};
    try {
        const resp = await fetch(`${apiBase}/property/templates?hotel_name=${encodeURIComponent(hotel)}`, { credentials: 'include' });
        const body = await resp.json().catch(() => ({}));
        details = (body && body.keywords_template) || {};
    } catch (_) { details = {}; }
    return letterheadHtml(hotel, details);
}

function staySheet(modal) {
    const text = (id) => {
        const el = modal.querySelector(id);
        if (!el) return '';
        return ('value' in el && el.tagName !== 'SPAN') ? String(el.value || '') : String(el.textContent || '');
    };
    return {
        hotel: String((getParamsFromUI() || {}).hotel_name || ''),
        booking: text('#det-booking-id'),
        guest: text('#det-guest-name'),
        phone: text('#det-guest-phone'),
        email: text('#det-guest-email'),
        country: text('#det-guest-country'),
        dob: text('#det-guest-dob'),
        gender: text('#det-guest-gender'),
        company: text('#det-guest-company'),
        address: text('#det-guest-address'),
        identity: text('#det-guest-identity'),
        idDetail: text('#det-guest-id-detail'),
        request: text('#det-guest-request'),
        vip: modal.querySelector('#det-guest-vip') && modal.querySelector('#det-guest-vip').checked ? 'Yes' : 'No',
        roomType: text('#det-room-type'),
        room: text('#det-room-number'),
        checkIn: text('#eb-checkin'),
        checkOut: text('#eb-checkout'),
        nights: text('#det-nights'),
        ratePlan: text('#det-rate-plan'),
        rateNote: text('#det-rate-plan-note'),
        total: text('#det-total'),
        currency: text('#det-currency')
    };
}

function wireStayDocuments(modal) {
    const card = modal.querySelector('#doc-registration');
    const invoice = modal.querySelector('#doc-invoice');
    const receipt = modal.querySelector('#doc-receipt');
    if (card && !card._wired) {
        card.addEventListener('click', async () => {
            const s = staySheet(modal);
            let clauses = '';
            try {
                const resp = await fetch(`${apiBase}/property/registration_card?hotel_name=${encodeURIComponent(s.hotel)}`, { credentials: 'include' });
                const body = await resp.json().catch(() => ({}));
                if (body.house_rules) clauses += `<h2>House Rules</h2><p>${cardText(body.house_rules)}</p>`;
                if (body.terms) clauses += `<h2>Terms and Conditions</h2><p>${cardText(body.terms)}</p>`;
            } catch (_) { clauses = ''; }
            const head = await loadHotelLetterhead(s.hotel);
            openPrint('Registration Card', head + stayDocHtml('Registration Card', [
                ['Guest', s.guest], ['Phone', s.phone], ['Email', s.email], ['Country', s.country],
                ['Date of Birth', s.dob], ['Gender', s.gender], ['Company', s.company], ['Address', s.address],
                ['ID', `${s.identity} ${s.idDetail}`.trim()], ['VIP', s.vip], ['Special Request', s.request],
                ['Booking', s.booking], ['Room Type', s.roomType], ['Room', s.room], ['Rate Plan', `${s.ratePlan} ${s.rateNote}`.trim()],
                ['Check-in', s.checkIn], ['Check-out', s.checkOut]
            ]) + clauses);
        });
        card._wired = true;
    }
    if (invoice && !invoice._wired) {
        invoice.addEventListener('click', async () => {
            const s = staySheet(modal);
            let lines = '';
            try {
                const resp = await fetch(`${apiBase}/pricing/breakdown?booking_id=${encodeURIComponent(s.booking)}`, { credentials: 'include' });
                const body = await resp.json().catch(() => ({}));
                const rows = Array.isArray(body.lines) ? body.lines : [];
                lines = rows.map((row) => `<tr><td>${row.stay_date || ''}</td><td>${s.roomType} x ${row.rooms_booked || 1}</td><td>${row.line_total != null ? row.line_total : row.unit_price}</td></tr>`).join('');
                if (body.taxes != null) lines += `<tr><td></td><td>Tax</td><td>${body.taxes}</td></tr>`;
            } catch (_) { lines = ''; }
            const head = await loadHotelLetterhead(s.hotel);
            openPrint('Invoice', `${head}<h1>Invoice</h1><p>${cardText(s.guest)} · ${cardText(s.booking)}</p><p>${cardText(s.checkIn)} to ${cardText(s.checkOut)} · ${cardText(s.roomType)} ${cardText(s.room)} · ${cardText(s.ratePlan)}</p><table><tr><th>Date</th><th>Room</th><th>Amount</th></tr>${lines}<tr><th colspan="2">Total</th><td>${cardText(s.currency)} ${cardText(s.total)}</td></tr></table>`);
        });
        invoice._wired = true;
    }
    if (receipt && !receipt._wired) {
        receipt.addEventListener('click', async () => {
            const s = staySheet(modal);
            let rows = '';
            try {
                const resp = await fetch(`${apiBase}/payments/ledger?booking_id=${encodeURIComponent(s.booking)}`, { credentials: 'include' });
                const body = await resp.json().catch(() => ({}));
                const entries = Array.isArray(body.entries) ? body.entries : [];
                rows = entries.filter((entry) => String(entry.payment_status || '').toLowerCase() !== 'refunded').map((entry) => `<tr><td>${entry.payment_date || ''}</td><td>${entry.payment_ref || entry.payment_seq}</td><td>${entry.payment_method || ''}</td><td>${entry.payment_amount}</td></tr>`).join('');
            } catch (_) { rows = ''; }
            const head = await loadHotelLetterhead(s.hotel);
            openPrint('Payment Receipt', `${head}<h1>Payment Receipt</h1><p>${cardText(s.guest)} · ${cardText(s.booking)} · ${cardText(s.room)} ${cardText(s.roomType)}</p><table><tr><th>Date</th><th>Reference</th><th>Method</th><th>Amount</th></tr>${rows || '<tr><td colspan="4">No payment recorded</td></tr>'}</table>`);
        });
        receipt._wired = true;
    }
}

async function openEditModal(bookingId, initialStatus, stayDate, currentRoomType, currentRoomNumber) {
		const modal = ensureEditModal();
		wireStayDocuments(modal);
		const summaryTab = modal.querySelector('#tab-assign');
		if (summaryTab) summaryTab.click();
		const editToken = String(Date.now());
		try { modal.dataset.editToken = editToken; } catch(_) {}
		// Open immediately to avoid perceived lag; content populates after fetch
		try { modal.style.display = 'flex'; } catch(_) {}
		const bidEl = modal.querySelector('#assign-bid');
		const guestEl = modal.querySelector('#assign-guest');
		const roomsEl = modal.querySelector('#assign-todayrooms');
		const notesEl = modal.querySelector('#assign-notes');
			const statusEl = modal.querySelector('#assign-status');
		const btnIn = modal.querySelector('#assign-checkin');
		const btnUndo = modal.querySelector('#assign-undo-checkin');
		const btnOut = modal.querySelector('#assign-checkout');
		// Reset
		bidEl.textContent = bookingId || '';
		guestEl.textContent = '';
		if (roomsEl) roomsEl.textContent = '';
		notesEl.value = '';
			// Remove status from header entirely (we show status in Stay Status card instead)
			if (statusEl) { statusEl.textContent = ''; statusEl.className = 'hidden'; }
		btnIn.classList.add('hidden');
		if (btnUndo) btnUndo.classList.add('hidden');
		btnOut.classList.add('hidden');
		// Persist context for cross-tab use
		try { modal.dataset.bookingId = bookingId || ''; } catch(_) {}
		try { modal.dataset.roomType = (currentRoomType || '').toString().toUpperCase(); } catch(_) {}
		try { modal.dataset.roomNumber = (currentRoomNumber || '').toString().toUpperCase(); } catch(_) {}
		try { modal.dataset.stayDate = String(stayDate || ''); } catch(_) {}
		let currentRoomsBooked = null; // carry into Booking tab for initial selection
		// Load detail for specific stay date (required by backend)
		try {
			const url = `${apiBase}/assignments/detail?booking_id=${encodeURIComponent(bookingId)}&stay_date=${encodeURIComponent(stayDate || '')}`;
			const res = await fetch(url, { credentials: 'include' });
			const data = await res.json();
			if (modal.dataset.editToken !== editToken) return;
			if (!res.ok || data.error) throw new Error(data.error || `API ${res.status}`);
            const guest = (data.guest || {});
            const check = (data.checkin || {});
            const barRoomNow = String((modal.dataset && modal.dataset.roomNumber) || '').toUpperCase();
            const roomRows = (check && Array.isArray(check.rooms)) ? check.rooms : [];
            const thisRoom = roomRows.find((row) => String((row && row.room_number) || '').toUpperCase() === barRoomNow);
            const roomStayStatus = String((thisRoom && thisRoom.stay_status) || initialStatus || '').toLowerCase();
            // Populate Details tab
            try {
                const detId = modal.querySelector('#det-booking-id'); if (detId) detId.textContent = String(data.booking_id || '');
                const detBstat = modal.querySelector('#det-booking-status'); if (detBstat) detBstat.textContent = String((data.booking && data.booking.booking_status) || '').toUpperCase();
                const detTotal = modal.querySelector('#det-total'); if (detTotal) detTotal.textContent = String((data.payment && typeof data.payment.total_amount === 'number') ? data.payment.total_amount.toFixed(2) : (data.payment && data.payment.total_amount) || '');
                const detCur = modal.querySelector('#det-currency'); if (detCur) detCur.textContent = String((data.payment && data.payment.currency) || '');
                const setField = (id, value, asValue) => {
                    const el = modal.querySelector(id);
                    if (!el) return;
                    if (asValue) el.value = String(value || '');
                    else el.textContent = String(value || '');
                };
                setField('#det-guest-name', guest.guest_name, true);
                var referencePhone = String(guest.reference_phone || '');
                var phoneVerified = !!guest.phone_verified;
                setField('#det-guest-phone', phoneVerified ? (guest.guest_phone || referencePhone) : referencePhone, false);
                var verifyBtn = modal.querySelector('#det-verify-phone');
                if (verifyBtn) {
                    verifyBtn.classList.toggle('hidden', !(referencePhone && !phoneVerified));
                    verifyBtn.disabled = false;
                    verifyBtn.onclick = async function() {
                        verifyBtn.disabled = true;
                        try {
                            var resp = await fetch(apiBase + '/assignments/verify-phone', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                credentials: 'include',
                                body: JSON.stringify({booking_id: bookingId})
                            });
                            var body = await resp.json();
                            if (!resp.ok || body.error) throw new Error(body.error || 'Verify failed');
                            setField('#det-guest-phone', body.guest_phone || referencePhone, false);
                            verifyBtn.classList.add('hidden');
                            var header = modal.querySelector('#assign-guest');
                            if (header) header.textContent = ((body.guest_name || guest.guest_name || '') + ' (' + (body.guest_phone || '') + ')').trim();
                        } catch (err) {
                            verifyBtn.disabled = false;
                            window.alert(err.message || 'Verify failed');
                        }
                    };
                }
                setField('#det-guest-email', guest.guest_email, true);
                const setSelect = (id, values, value) => {
                    const el = modal.querySelector(id);
                    if (!el) return;
                    const chosen = String(value || '');
                    el.innerHTML = '';
                    el.appendChild(new Option('', ''));
                    values.forEach((item) => el.appendChild(new Option(item, item)));
                    const known = values.find((item) => item.toUpperCase() === chosen.toUpperCase());
                    if (chosen && !known) el.appendChild(new Option(chosen, chosen));
                    el.value = known || (chosen ? chosen : '');
                };
                const countryNames = (window.COUNTRIES_ISO3166 || []).map((row) => row.name);
                setSelect('#det-guest-country', countryNames, guest.country || guest.nationality);
                setField('#det-guest-dob', String(guest.date_of_birth || '').slice(0, 10), true);
                setSelect('#det-guest-gender', ['Male', 'Female'], guest.gender);
                setField('#det-guest-company', guest.company, true);
                setField('#det-guest-address', guest.address, true);
                setSelect('#det-guest-identity', ['IC', 'MyKad', 'Passport'], guest.identity_type);
                setField('#det-guest-id-detail', guest.identity_number, true);
                setField('#det-guest-request', guest.special_request, true);
                const vipEl = modal.querySelector('#det-guest-vip');
                if (vipEl) vipEl.checked = !!guest.vip;
                fillRatePlans(modal, data);
                const detRoomType = modal.querySelector('#det-room-type');
                if (detRoomType) {
                    const rtFromBar = (modal.dataset && modal.dataset.roomType) ? modal.dataset.roomType : '';
                    const rtFromApi = (data && data.booking && data.booking.room_type) ? String(data.booking.room_type).toUpperCase() : (data && data.room_type ? String(data.room_type).toUpperCase() : '');
                    detRoomType.textContent = rtFromBar || rtFromApi || '';
                }
                // Nights and Rooms
                try {
                    const dIn = (data.check_in_date || '').slice(0,10);
                    const dOut = (data.check_out_date || '').slice(0,10);
                    const di = dIn ? parseLocalDate(dIn) : null;
                    const doff = dOut ? parseLocalDate(dOut) : null;
                    let nights = '';
                    if (di instanceof Date && doff instanceof Date && !isNaN(di) && !isNaN(doff) && doff > di) {
                        nights = String(Math.round((doff - di) / 86400000));
                    }
                    const detN = modal.querySelector('#det-nights'); if (detN) detN.textContent = nights;
                    // Resolve rooms booked robustly from API
                    let roomsBookedRaw = null;
                    if (data && data.booking) {
                        const b = data.booking;
                        roomsBookedRaw = (typeof b.rooms_booked !== 'undefined') ? b.rooms_booked
                            : (typeof b.rooms !== 'undefined') ? b.rooms
                            : (typeof b.num_rooms !== 'undefined') ? b.num_rooms
                            : (typeof b.rooms_booked_count !== 'undefined') ? b.rooms_booked_count
                            : null;
                    }
                    if (roomsBookedRaw == null && typeof data.rooms_booked !== 'undefined') roomsBookedRaw = data.rooms_booked;
                    let roomsBookedNum = (roomsBookedRaw != null && roomsBookedRaw !== '') ? parseInt(roomsBookedRaw, 10) : NaN;
                    if ((isNaN(roomsBookedNum) || roomsBookedNum <= 0) && Array.isArray(check.today_rooms)) {
                        roomsBookedNum = check.today_rooms.length;
                    }
                    const detR = modal.querySelector('#det-rooms'); if (detR) detR.textContent = (!isNaN(roomsBookedNum) && roomsBookedNum > 0) ? String(roomsBookedNum) : '';
                    try { currentRoomsBooked = (!isNaN(roomsBookedNum) && roomsBookedNum > 0) ? roomsBookedNum : null; } catch(_) {}
                } catch(_) {}
				// Derive stay status with strict normalization (PENDING | CHECKED_IN | DEPARTED)
				let derivedStay = 'PENDING';
				try {
					if (roomStayStatus === 'checked_in') derivedStay = 'CHECKED_IN';
					else if (roomStayStatus === 'departed' || roomStayStatus === 'checked_out') derivedStay = 'DEPARTED';
				} catch(_) { derivedStay = 'PENDING'; }
                const detS = modal.querySelector('#det-stay-status');
                if (detS) {
                    try {
                        // Replace plain text with a colored inline label
                        detS.textContent = '';
                        detS.style.display = 'inline-flex';
                        detS.style.alignItems = 'center';
                        detS.style.gap = '6px';
                        const badge = document.createElement('span');
                        badge.textContent = String(derivedStay || '').replace('_', ' ');
                        badge.style.display = 'inline-block';
                        badge.style.padding = '2px 8px';
                        badge.style.borderRadius = '9999px';
                        badge.style.fontSize = '11px';
                        badge.style.fontWeight = '700';
                        badge.style.letterSpacing = '0.02em';
                        badge.style.textTransform = 'uppercase';
                        const st = String(derivedStay || '').toUpperCase();
                        if (st === 'CHECKED_IN') {
                            badge.style.backgroundColor = '#D1FAE5'; // green-100
                            badge.style.color = '#065F46'; // green-800
                            badge.style.border = '1px solid #10B981'; // green-500
                        } else if (st === 'DEPARTED') {
                            badge.style.backgroundColor = '#F3F4F6'; // gray-100
                            badge.style.color = '#374151'; // gray-700
                            badge.style.border = '1px solid #9CA3AF'; // gray-400
                        } else {
                            // PENDING (default) → orange
                            badge.style.backgroundColor = '#FFEDD5'; // orange-100
                            badge.style.color = '#9A3412'; // orange-800
                            badge.style.border = '1px solid #FB923C'; // orange-400/500
                        }
                        detS.appendChild(badge);
                        const detDetail = modal.querySelector('#det-stay-status-detail');
                        if (detDetail) detDetail.textContent = badge.textContent;
                    } catch(_) {}
                }
                // Remove status text from the header line; only show guest name (+ phone) in header
                try {
                    const guestHeader = modal.querySelector('#assign-guest');
                    if (guestHeader) {
                        // already set below using guest.guest_name and guest.guest_phone
                        // ensure no status text is concatenated anywhere in the header
                    }
                } catch(_) {}
                try { const prev = modal.querySelector('#det-status-menu'); if (prev && prev.parentNode) prev.parentNode.removeChild(prev); } catch(_) {}
            // Populate Stay Status card fields
                const detRoomNo = modal.querySelector('#det-room-number');
                if (detRoomNo) {
                    try {
                        detRoomNo.textContent = barRoomNow || '';
                    } catch(_) { detRoomNo.textContent = ''; }
                }
                const detRoomDetail = modal.querySelector('#det-room-number-detail');
                if (detRoomDetail) detRoomDetail.textContent = barRoomNow || '';
                const detAssign = modal.querySelector('#det-assigned-at'); if (detAssign) detAssign.textContent = check.assigned_at ? toLocalYmdHmFromUTCString(check.assigned_at) : '';
                const detCinAt = modal.querySelector('#det-checked-in-at'); if (detCinAt) detCinAt.textContent = check.checked_in_at ? toLocalYmdHmFromUTCString(check.checked_in_at) : '';
                const detCoutAt = modal.querySelector('#det-checked-out-at'); if (detCoutAt) detCoutAt.textContent = check.checked_out_at ? toLocalYmdHmFromUTCString(check.checked_out_at) : '';
                // Warm up available rooms cache for Assign popover (force refresh on every open)
                try {
                    const params = getParamsFromUI();
                    const hotelUp = String((params && params.hotel_name) || data.hotel_name || '').toUpperCase();
                    let roomTypeText = (document.querySelector('#det-room-type') && document.querySelector('#det-room-type').textContent) ? String(document.querySelector('#det-room-type').textContent).toUpperCase() : '';
                    if (!roomTypeText) { try { roomTypeText = String((modal && modal.dataset && modal.dataset.roomType) || '').toUpperCase(); } catch(_) {}
                    }
                    const d = String(data.stay_date||stayDate||'').slice(0,10);
                    if (hotelUp && roomTypeText && d) {
                        invalidateAvailableRoomsCache(hotelUp, roomTypeText, d);
                        // Fire and forget
                        fetchAvailableRoomsCached(hotelUp, roomTypeText, d).catch(()=>{});
                    }
                } catch(_) {}

                // Dynamic stay action
				try {
                    const actBtn = modal.querySelector('#det-stay-action');
                    const secBtn = modal.querySelector('#det-stay-secondary');
                    if (secBtn) {
                        try { secBtn.textContent='Assign'; secBtn.style.cursor = 'pointer'; } catch(_) {}
                        try {
                            secBtn.dataset.bookingId = String(data.booking_id || bookingId || '');
                            secBtn.dataset.stayDate = String(data.stay_date || stayDate || '').slice(0, 10);
                        } catch(_) {}
                        if (!secBtn._assignWired) {
                            // Open popover on hover instead of click
                            const openAssignPopover = async (ev) => {
                                try {
                                    ev.preventDefault(); ev.stopPropagation();
                                    // Remove any existing menu first
                                    try { const m = secBtn._assignMenu; if (m && m.parentNode) { m.parentNode.removeChild(m); secBtn._assignMenu = null; } } catch(_) {}
                                    const menu = document.createElement('div');
                                    menu.style.position = 'absolute';
                                    menu.style.background = '#fff';
                                    menu.style.border = '1px solid #e5e7eb';
                                    menu.style.borderRadius = '6px';
                                    menu.style.boxShadow = '0 4px 10px rgba(0,0,0,0.08)';
                                    menu.style.padding = '6px';
                                    menu.style.zIndex = '99999';
                                    // helper to maybe close when neither button nor menu hovered
                                    const maybeClose = () => {
                                        try {
                                            setTimeout(() => {
                                                try {
                                                    const overBtn = secBtn.matches(':hover');
                                                    const overMenu = menu.matches(':hover');
                                                    if (!overBtn && !overMenu) {
                                                        try { document.body.removeChild(menu); } catch(_) {}
                                                        try { document.removeEventListener('click', onDoc, false); } catch(_) {}
                                                        try { secBtn._assignMenu = null; } catch(_) {}
                                                    }
                                                } catch(_) {}
                                            }, 120);
                                        } catch(_) {}
                                    };
                                    const addOptionBtn = (label, val) => {
                                        const b = document.createElement('button');
                                        b.type = 'button';
                                        b.textContent = label;
                                        b.style.display = 'block';
                                        b.style.width = '100%';
                                        b.style.textAlign = 'left';
                                        b.style.padding = '4px 8px';
                                        b.style.fontSize = '12px';
                                        b.style.fontWeight = '600';
                                        b.style.borderRadius = '4px';
                                        b.style.transition = 'background-color 120ms ease';
                                        b.addEventListener('mouseenter', () => { b.style.backgroundColor = 'rgba(191,219,254,0.35)'; });
                                        b.addEventListener('mouseleave', () => { b.style.backgroundColor = 'transparent'; });
                                        b.addEventListener('click', async () => {
                                            try {
                                                const params = getParamsFromUI();
                                                const hotel = String(params.hotel_name||'').toUpperCase();
                                                const bidNow = String(secBtn.dataset.bookingId || modal.dataset.bookingId || '');
                                                const stayD = String(secBtn.dataset.stayDate || modal.dataset.stayDate || '').slice(0, 10);
                                                const resolveCurrentOldRoom = async () => {
                                                    try {
                                                        const url = `${apiBase}/assignments/detail?booking_id=${encodeURIComponent(bidNow)}&stay_date=${encodeURIComponent(stayD)}`;
                                                        const res = await fetch(url, { credentials: 'include' });
                                                        const det = await res.json().catch(()=>({}));
                                                        const rooms = (det && det.checkin && Array.isArray(det.checkin.today_rooms)) ? det.checkin.today_rooms : [];
                                                        const cur = rooms.length ? String(rooms[0]).toUpperCase() : '';
                                                        return (cur === 'NO ROOM') ? '' : cur;
                                                    } catch(_) { return ''; }
                                                };
                                                const clickedRoom = String((modal.dataset && modal.dataset.roomNumber) || '').toUpperCase();
                                                const oldNow = clickedRoom || await resolveCurrentOldRoom();
                                                const body = { hotel_name: hotel, booking_id: bidNow, stay_date: stayD, old_room_number: String(oldNow||''), new_room_number: String(val||'').toUpperCase() };
                                                const resp = await fetch(`${apiBase}/assignments/reassign`, { method:'POST', headers:{'Content-Type':'application/json'}, credentials:'include', body: JSON.stringify(body) });
                                                const j = await resp.json().catch(()=>({}));
                                                if (!resp.ok || j.error) { alert(j.error || 'Failed to assign'); return; }
                                                if (menu && menu.parentNode) { menu.parentNode.removeChild(menu); }
                                                // If edit modal is open, refresh it immediately
                                                try {
                                                    const em = document.getElementById('assign-edit-modal');
                                                    if (em && em.style.display === 'flex') {
                                                        let rt2 = '';
                                                        try { rt2 = (em.dataset && em.dataset.roomType) ? em.dataset.roomType : ''; } catch(_){ rt2=''; }
                                                        await openEditModal(bidNow, '', String(stayD||''), rt2);
                                                    }
                                                } catch(_) {}
                                                try { load(); } catch(_) { window.location.reload(); }
                                            } catch(e) { alert(e.message || 'Failed'); }
                                        });
                                        menu.appendChild(b);
                                    };
                                    // options list: NO ROOM + available rooms
                                    addOptionBtn('NO ROOM', '');
                                    try {
                                        const params = getParamsFromUI();
                                        const hotel = String(params.hotel_name||'').toUpperCase();
                                        let roomTypeText = (document.querySelector('#det-room-type') && document.querySelector('#det-room-type').textContent) ? String(document.querySelector('#det-room-type').textContent).toUpperCase() : '';
                                        if (!roomTypeText) { try { roomTypeText = String((modal.dataset && modal.dataset.roomType) || '').toUpperCase(); } catch(_) {}
                                        }
                                        const d = String(secBtn.dataset.stayDate || modal.dataset.stayDate || '').slice(0,10);
                                        if (hotel && roomTypeText && d) {
                                            const list = await fetchAvailableRoomsCached(hotel, roomTypeText, d);
                                            (Array.isArray(list)? list: []).forEach(v=>{ const val=String(v||'').toUpperCase(); if (!val) return; addOptionBtn(renamedRoom(val), val); });
                                        }
                                    } catch(_) {}
                                    const bRect = secBtn.getBoundingClientRect();
                                    menu.style.left = `${Math.round(bRect.left + window.scrollX)}px`;
                                    menu.style.top = `${Math.round(bRect.bottom + window.scrollY + 6)}px`;
                                    document.body.appendChild(menu);
                                    // Track current menu on the button for subsequent opens/closes
                                    try { secBtn._assignMenu = menu; } catch(_) {}
                                    const onDoc = (e2) => { try { if (menu && !menu.contains(e2.target) && e2.target !== secBtn) { document.body.removeChild(menu); document.removeEventListener('click', onDoc, false); } } catch(_){} };
                                    setTimeout(()=>document.addEventListener('click', onDoc, false), 0);
                                    // Close on mouseleave of button or menu (with small delay to allow moving between)
                                    try { secBtn.addEventListener('mouseleave', maybeClose, { passive: true }); } catch(_) {}
                                    try { menu.addEventListener('mouseleave', maybeClose, { passive: true }); } catch(_) {}
                                    try { menu.addEventListener('mouseenter', () => {}, { passive: true }); } catch(_) {}
                                } catch(_) {}
                            };
                            secBtn.addEventListener('mouseenter', openAssignPopover);
                            try { secBtn._assignWired = true; } catch(_) {}
                        }
                    }
                    if (actBtn) {
                        const s = String(derivedStay || (detS && detS.textContent) || '').toUpperCase();
                        // Preserve primary CI/CO logic (no hiding of Assign button)
                        actBtn.textContent = '';
                        actBtn.onclick = null;
                        const todayIso = toLocalISODate(new Date());
                        const isToday = (String(data.stay_date || '') === todayIso);
                        // Always provide Assign button via inline popover, regardless of status
                        if (secRef) {
                            secRef.textContent = 'Assign';
                            secRef.addEventListener('click', async (ev) => {
                                    try {
                                        ev.preventDefault(); ev.stopPropagation();
                                        const menu = document.createElement('div');
                                        menu.style.position = 'absolute';
                                        menu.style.background = '#fff';
                                        menu.style.border = '1px solid #e5e7eb';
                                        menu.style.borderRadius = '6px';
                                        menu.style.boxShadow = '0 4px 10px rgba(0,0,0,0.08)';
                                        menu.style.padding = '6px';
                                        menu.style.zIndex = '99999';
                                        const addOptionBtn = (label, val) => {
                                            const b = document.createElement('button');
                                            b.type = 'button';
                                            b.textContent = label;
                                            b.style.display = 'block';
                                            b.style.width = '100%';
                                            b.style.textAlign = 'left';
                                            b.style.padding = '4px 8px';
                                            b.style.fontSize = '12px';
                                            b.style.fontWeight = '600';
                                            b.style.borderRadius = '4px';
                                            b.style.transition = 'background-color 120ms ease';
                                            b.addEventListener('mouseenter', () => { b.style.backgroundColor = 'rgba(191,219,254,0.35)'; });
                                            b.addEventListener('mouseleave', () => { b.style.backgroundColor = 'transparent'; });
                                            b.addEventListener('click', async () => {
                                                try {
                                                    const params = getParamsFromUI();
                                                    const hotel = String(params.hotel_name||'').toUpperCase();
                                                    const bidNow = String((modal.dataset && modal.dataset.bookingId) || '');
                                                    const stayNow = String((modal.dataset && modal.dataset.stayDate) || '').slice(0, 10);
                                                    const clickedRoom = String((modal.dataset && modal.dataset.roomNumber) || '').toUpperCase();
                                                    const listedRoom = (Array.isArray(check.today_rooms) && check.today_rooms.length) ? String(check.today_rooms[0]).toUpperCase() : '';
                                                    const body = { hotel_name: hotel, booking_id: bidNow, stay_date: stayNow, old_room_number: clickedRoom || listedRoom, new_room_number: String(val||'').toUpperCase() };
                                                    const resp = await fetch(`${apiBase}/assignments/reassign`, { method:'POST', headers:{'Content-Type':'application/json'}, credentials:'include', body: JSON.stringify(body) });
                                                    const j = await resp.json().catch(()=>({}));
                                                    if (!resp.ok || j.error) { alert(j.error || 'Failed to assign'); return; }
                                                    document.body.removeChild(menu);
                                                    try { load(); } catch(_) { window.location.reload(); }
                                                } catch(e) { alert(e.message || 'Failed'); }
                                            });
                                            menu.appendChild(b);
                                        };
                                        // options list: NO ROOM + available rooms
                                        addOptionBtn('NO ROOM', '');
                                        try {
                                            const params = getParamsFromUI();
                                            const hotel = String(params.hotel_name||'').toUpperCase();
                                            let roomTypeText = (document.querySelector('#det-room-type') && document.querySelector('#det-room-type').textContent) ? String(document.querySelector('#det-room-type').textContent).toUpperCase() : '';
                                            const d = String(data.stay_date||stayDate||'').slice(0,10);
                                        if (hotel && roomTypeText && d) {
                                            const list = await fetchAvailableRoomsCached(hotel, roomTypeText, d);
                                            (Array.isArray(list)? list: []).forEach(v=>{ const val=String(v||'').toUpperCase(); if (!val) return; addOptionBtn(renamedRoom(val), val); });
                                        }
                                        } catch(_) {}
                                        const bRect = secRef.getBoundingClientRect();
                                        menu.style.left = `${Math.round(bRect.left + window.scrollX)}px`;
                                        menu.style.top = `${Math.round(bRect.bottom + window.scrollY + 6)}px`;
                                        document.body.appendChild(menu);
                                    const onDoc = (e2) => { try { if (menu && !menu.contains(e2.target) && e2.target !== secBtn) { document.body.removeChild(menu); document.removeEventListener('click', onDoc, false); } } catch(_){} };
                                    setTimeout(()=>document.addEventListener('click', onDoc, false), 0);
                                    } catch(_) {}
                            }, { once: false });
                        }
                        // Remove dynamic primary CI/CO button wiring; status changes handled by status menu
                        try { actBtn.textContent=''; actBtn.onclick=null; } catch(_) {}
                    }
                } catch(_) {}
                const detPayStat = modal.querySelector('#det-payment-status'); if (detPayStat) detPayStat.textContent = String((data.payment && data.payment.payment_status) || '');
                const detPaid = modal.querySelector('#det-amount-paid'); if (detPaid) detPaid.textContent = String((data.payment && typeof data.payment.amount_paid === 'number') ? data.payment.amount_paid.toFixed(2) : (data.payment && data.payment.amount_paid) || '');
                const detRemain = modal.querySelector('#det-remaining'); if (detRemain) detRemain.textContent = String((data.payment && typeof data.payment.remaining_balance === 'number') ? data.payment.remaining_balance.toFixed(2) : (data.payment && data.payment.remaining_balance) || '');
                const detPTot = modal.querySelector('#det-pay-total'); if (detPTot) detPTot.textContent = String((data.payment && typeof data.payment.total_amount === 'number') ? data.payment.total_amount.toFixed(2) : (data.payment && data.payment.total_amount) || '');
                const detPCur = modal.querySelector('#det-pay-currency'); if (detPCur) detPCur.textContent = String((data.payment && data.payment.currency) || '');
				// Populate Payment tab
						try {
                        const payStatus = modal.querySelector('#pay-status'); if (payStatus) payStatus.textContent = String((data.payment && data.payment.payment_status) || '');
                        const payTotal = modal.querySelector('#pay-total'); if (payTotal) payTotal.textContent = String((data.payment && typeof data.payment.total_amount === 'number') ? data.payment.total_amount.toFixed(2) : (data.payment && data.payment.total_amount) || '');
                        const payCur = modal.querySelector('#pay-currency'); if (payCur) payCur.textContent = String((data.payment && data.payment.currency) || '');
                        const payPaid = modal.querySelector('#pay-paid'); if (payPaid) payPaid.textContent = String((data.payment && typeof data.payment.amount_paid === 'number') ? data.payment.amount_paid.toFixed(2) : (data.payment && data.payment.amount_paid) || '');
                        const payRem = modal.querySelector('#pay-remaining'); if (payRem) payRem.textContent = String((data.payment && typeof data.payment.remaining_balance === 'number') ? data.payment.remaining_balance.toFixed(2) : (data.payment && data.payment.remaining_balance) || '');
                        const payMethod = modal.querySelector('#pay-method'); if (payMethod) payMethod.textContent = String((data.payment && data.payment.payment_method) || '');
                    const preBreakdown = modal.querySelector('#pay-breakdown');
                    const uiBreakdown = modal.querySelector('#pay-breakdown-ui');
                    const inputOverride = modal.querySelector('#pay-override-amount');
                    const btnApply = modal.querySelector('#pay-apply');
                    const msg = modal.querySelector('#pay-status-msg');
                    const bid = String(data.booking_id || '');
                    // Prefill edit payment fields
                    try {
                        const eMeth = modal.querySelector('#pay-edit-method'); if (eMeth && data.payment) {
                            const v = String(data.payment.payment_method || '').toUpperCase();
                            if ([...eMeth.options].some(o => o.value === v)) eMeth.value = v;
                        }
                        const ePaid = modal.querySelector('#pay-edit-paid'); if (ePaid && data.payment) ePaid.value = String((typeof data.payment.amount_paid==='number')? data.payment.amount_paid.toFixed(2): (data.payment.amount_paid||''));
                    } catch(_) {}
                    // Autoload pricing breakdown
                    try { const btn = modal.querySelector('#pay-refresh'); if (btn) btn.style.display = 'none'; } catch(_) {}
                    const refreshBreakdown = async () => {
                        try {
                            if (uiBreakdown) uiBreakdown.innerHTML = '<div class="text-gray-500">Loading…</div>';
                            const res = await fetch(`${apiBase}/pricing/breakdown?booking_id=${encodeURIComponent(bid)}`, { credentials:'include' });
                            const j = await res.json().catch(()=>({}));
                            if (!res.ok || j.error) throw new Error(j.error || 'Failed');
                            if (uiBreakdown) {
                                // Reset container
                                uiBreakdown.innerHTML = '';
                                const cur = j.currency || '';
                                const toAmt = (n) => {
                                    const v = Number(n || 0);
                                    return cur + v.toFixed(2);
                                };
                                // Nightly Breakdown
                                if (Array.isArray(j.lines) && j.lines.length) {
                                    const night = document.createElement('div'); night.className = 'border rounded p-2';
                                    const h = document.createElement('div'); h.className = 'font-semibold mb-1'; h.textContent = 'Nightly Breakdown'; night.appendChild(h);
                                    const linesLen = j.lines.length;
                                    const taxesHeader = (typeof j.taxes === 'number') ? j.taxes : (typeof j.taxes_total === 'number' ? j.taxes_total : 0);
                                    const perNightTax = linesLen > 0 ? (Number(taxesHeader || 0) / linesLen) : 0;
                                    j.lines.forEach((ln) => {
                                        const d = ln.date || ln.stay_date || '';
                                        const unit = (typeof ln.unit_price === 'number') ? ln.unit_price : ((typeof ln.base_price === 'number') ? ln.base_price : (ln.price || 0));
                                        const roomsLn = (typeof ln.rooms_booked === 'number') ? ln.rooms_booked : 1;
                                        const base = (typeof ln.line_total === 'number') ? ln.line_total : (Number(unit || 0) * Number(roomsLn || 1));
                                        const taxes = (typeof ln.taxes === 'number') ? ln.taxes : (typeof ln.tax === 'number' ? ln.tax : perNightTax);
                                        const total = (typeof ln.total === 'number') ? ln.total : (Number(base || 0) + Number(taxes || 0));
                                        const notes = (ln.notes || ln.rule || ln.label || '');
                                        const row = document.createElement('div'); row.className = 'py-1 flex items-start justify-between';
                                        const left = document.createElement('div');
                                        const dt = document.createElement('div'); dt.className = 'text-gray-800'; dt.textContent = String(d);
                                        left.appendChild(dt);
                                        if (notes) { const nt = document.createElement('div'); nt.className = 'text-xs text-gray-500'; nt.textContent = String(notes); left.appendChild(nt); }
                                        const right = document.createElement('div'); right.className = 'text-right';
                                        const l1 = document.createElement('div'); l1.className = 'text-xs text-gray-500'; l1.textContent = 'Base'; right.appendChild(l1);
                                        const v1 = document.createElement('div'); v1.textContent = toAmt(base); right.appendChild(v1);
                                        const l2 = document.createElement('div'); l2.className = 'text-xs text-gray-500'; l2.textContent = 'Taxes'; right.appendChild(l2);
                                        const v2 = document.createElement('div'); v2.textContent = toAmt(taxes); right.appendChild(v2);
                                        const l3 = document.createElement('div'); l3.className = 'text-xs text-gray-500'; l3.textContent = 'Total'; right.appendChild(l3);
                                        const v3 = document.createElement('div'); v3.className = 'font-semibold'; v3.textContent = toAmt(total); right.appendChild(v3);
                                        row.appendChild(left); row.appendChild(right);
                                        night.appendChild(row);
                                    });
                                    uiBreakdown.appendChild(night);
                                }
                                // Summary
                                const sum = document.createElement('div'); sum.className = 'border rounded p-2';
                                const sh = document.createElement('div'); sh.className = 'font-semibold mb-1'; sh.textContent = 'Summary'; sum.appendChild(sh);
                                const makeRow = (label, value) => {
                                    const r = document.createElement('div'); r.className = 'flex justify-between';
                                    const l = document.createElement('span'); l.className = 'text-gray-600'; l.textContent = label;
                                    const v = document.createElement('span'); v.className = 'font-medium'; v.textContent = value;
                                    r.appendChild(l); r.appendChild(v); return r;
                                };
                                const subtotal = (typeof j.subtotal === 'number') ? j.subtotal : 0;
                                const taxes = (typeof j.taxes === 'number') ? j.taxes : (typeof j.taxes_total === 'number' ? j.taxes_total : 0);
                                const computed = (typeof j.computed_total === 'number') ? j.computed_total : (subtotal + taxes);
                                const current = (typeof j.current_total === 'number') ? j.current_total : computed;
                                sum.appendChild(makeRow('Subtotal', toAmt(subtotal)));
                                sum.appendChild(makeRow('Taxes', toAmt(taxes)));
                                sum.appendChild(makeRow('Computed Total', toAmt(computed)));
                                sum.appendChild(makeRow('Current Total', toAmt(current)));
                                if (typeof j.tax_rate === 'number') sum.appendChild(makeRow('Tax Rate', (j.tax_rate*100).toFixed(2) + '%'));
                                uiBreakdown.appendChild(sum);
                            }
                            // Also refresh Payment Summary from ledger (reuse /api/payments/ledger)
                            try {
                                const ledRes = await fetch(`${apiBase}/payments/ledger?booking_id=${encodeURIComponent(bid)}`, { credentials:'include' });
                                const ledger = await ledRes.json().catch(()=>({}));
                                const entries = Array.isArray(ledger.entries) ? ledger.entries : [];
                                let totalPaid = 0;
                                for (const en of entries) {
                                    const amt = Number(en && en.payment_amount || 0);
                                    const st = String(en && en.payment_status || '').toLowerCase();
                                    if (!isFinite(amt)) continue;
                                    // Sum only non-refunded positive payments
                                    if (st !== 'refunded' && amt > 0) totalPaid += amt;
                                }
                                const last = entries.length ? entries[entries.length - 1] : null;
                                const totalAmount = (typeof j.current_total === 'number') ? j.current_total
                                    : (typeof j.computed_total === 'number') ? j.computed_total
                                    : (typeof (data.payment && data.payment.total_amount) === 'number') ? data.payment.total_amount
                                    : 0;
                                const paidNonNegative = Math.max(totalPaid, 0);
                                const remaining = Math.max(totalAmount - paidNonNegative, 0);
                                const status = (totalAmount > 0 && remaining <= 0) ? 'completed' : (totalPaid > 0 ? 'partial' : 'pending');
                                if (payTotal) payTotal.textContent = (function(){ try { return (j.currency||'') + Number(totalAmount||0).toFixed(2); } catch(_) { return String(totalAmount||0); } })();
                                if (payPaid) payPaid.textContent = (function(){ try { return (j.currency||'') + Number(paidNonNegative||0).toFixed(2); } catch(_) { return String(paidNonNegative||0); } })();
                                if (payRem) payRem.textContent = (function(){ try { return (j.currency||'') + Number(remaining||0).toFixed(2); } catch(_) { return String(remaining||0); } })();
                                if (payStatus) payStatus.textContent = status;
                                // payMethod hidden from summary
                                // Render entries list with refund buttons
                                try {
                                    const listRec = modal.querySelector('#pay-ledger-rec');
                                    const listRef = modal.querySelector('#pay-ledger-ref');
                                    if (listRec) listRec.innerHTML = '';
                                    if (listRef) listRef.innerHTML = '';
                                    entries.forEach((en) => {
                                        const st = String(en && en.payment_status || '').toLowerCase();
                                        const row = document.createElement('div'); row.className = 'flex items-center justify-between border rounded px-2 py-1';
                                        const left = document.createElement('div');
                                        const tsLocal = toLocalYmdHmFromUTCString(en.payment_date || '');
                                        left.textContent = `SEQ ${en.payment_seq || ''} · ${tsLocal} · ${en.payment_method || ''} · ${en.payment_amount}`;
                                        const right = document.createElement('div');
                                        if (st !== 'refunded') {
                                            const btn = document.createElement('button'); btn.className = 'bg-red-600 text-white px-2 py-0.5 rounded hover:bg-red-700'; btn.textContent = 'Refund';
                                            btn.addEventListener('click', async () => {
                                                try {
                                                    const seq = Number(en && en.payment_seq || 0);
                                                    if (!isFinite(seq) || seq <= 0) return;
                                                    const ok = confirm(`Mark payment seq ${seq} as refunded?`);
                                                    if (!ok) return;
                                                    const resR = await fetch(`${apiBase}/payments/refund`, { method:'POST', headers:{'Content-Type':'application/json'}, credentials:'include', body: JSON.stringify({ hotel_name: String(data.hotel_name||'').toUpperCase(), booking_id: bid, payment_seq: seq, refund_type: '' }) });
                                                    const jR = await resR.json().catch(()=>({}));
                                                    if (!resR.ok || jR.error) throw new Error(jR.error || 'Refund failed');
                                                    await refreshBreakdown();
                                                } catch(e) { alert(e.message || 'Refund failed'); }
                                            });
                                            right.appendChild(btn);
                                        } else {
                                            const tag = document.createElement('span'); tag.className = 'text-xs text-gray-500'; tag.textContent = 'refunded'; right.appendChild(tag);
                                        }
                                        row.appendChild(left); row.appendChild(right);
                                        if (st !== 'refunded') { if (listRec) listRec.appendChild(row); }
                                        else { if (listRef) listRef.appendChild(row); }
                                    });
                                } catch(_) {}
                            } catch(_) {}
                        } catch(e) {
                            if (uiBreakdown) uiBreakdown.innerHTML = `<div class="text-red-600 text-sm">${e.message||'Failed'}</div>`;
                        }
                    };
                    try { refreshBreakdown(); } catch(_) {}
                    // Wire apply override
                    if (btnApply && !btnApply._wired) {
                        btnApply.addEventListener('click', async () => {
                            try {
                                const amt = parseFloat((inputOverride && inputOverride.value) || '');
                                if (!isFinite(amt) || amt <= 0) { if (msg) { msg.textContent = 'Enter a valid amount'; msg.className='text-xs text-red-600 mt-1'; } return; }
                                if (msg) { msg.textContent = 'Applying…'; msg.className='text-xs text-gray-500 mt-1'; }
                                const payload = { booking_id: bid, new_total_amount: amt, hotel_name: String(data.hotel_name||'').toUpperCase() };
                                const res = await fetch(`${apiBase}/pricing/override`, { method:'POST', headers:{'Content-Type':'application/json'}, credentials:'include', body: JSON.stringify(payload) });
                                const j = await res.json().catch(()=>({}));
                                if (!res.ok || j.error) throw new Error(j.error || 'Failed');
                                if (msg) { msg.textContent = 'Override applied'; msg.className='text-xs text-green-600 mt-1'; }
                                // Refresh summary
                                try { await refreshBreakdown(); } catch(_) {}
                            } catch(e) { if (msg) { msg.textContent = e.message || 'Failed'; msg.className='text-xs text-red-600 mt-1'; } }
                        });
                        btnApply._wired = true;
                    }
                    // Wire save payment (assignments/update)
                    const btnPaySave = modal.querySelector('#pay-edit-save');
                    const msgEdit = modal.querySelector('#pay-edit-msg');
                    if (btnPaySave && !btnPaySave._wired) {
                        btnPaySave.addEventListener('click', async () => {
                            try {
                                const eMeth = modal.querySelector('#pay-edit-method');
                                const ePaid = modal.querySelector('#pay-edit-paid');
                                const method = eMeth ? String(eMeth.value||'').toUpperCase() : 'CASH';
                                const amt = ePaid && ePaid.value !== '' ? parseFloat(ePaid.value) : NaN;
                                if (!isFinite(amt) || amt <= 0) { if (msgEdit) { msgEdit.textContent = 'Enter valid amount'; msgEdit.className='text-xs text-red-600 mt-1'; } return; }
                                const payment = { payment_method: method, amount_paid: amt };
                                if (msgEdit) { msgEdit.textContent = 'Saving…'; msgEdit.className='text-xs text-gray-500 mt-1'; }
                                // Record as a ledger payment, then refresh
                                const res = await fetch(`${apiBase}/payments/update`, { method:'POST', headers:{'Content-Type':'application/json'}, credentials:'include', body: JSON.stringify({ hotel_name: String(data.hotel_name||'').toUpperCase(), booking_id: bid, payment_amount: amt, payment_method: method }) });
                                const j = await res.json().catch(()=>({}));
                                if (!res.ok || j.error) throw new Error(j.error || 'Failed');
                                if (msgEdit) { msgEdit.textContent = 'Saved'; msgEdit.className='text-xs text-green-600 mt-1'; }
                                // Refresh Detail/Payment summary
                                try { await refreshBreakdown(); } catch(_) {}
                            } catch(e) { if (msgEdit) { msgEdit.textContent = e.message || 'Failed'; msgEdit.className='text-xs text-red-600 mt-1'; } }
                        });
                        btnPaySave._wired = true;
						}
						} catch(_) {}
            // Room tab and controls removed; stay status shown in Details
            // Wire Details->Booking edit button, and Details->Payment tab
            try {
                const btnGoEdit = modal.querySelector('#det-edit-booking');
                const btnGoPay = modal.querySelector('#det-edit-payment');
                const btnA = modal.querySelector('#tab-assign');
                const btnB = modal.querySelector('#tab-booking');
                const btnP = modal.querySelector('#tab-payment');
                const paneA = modal.querySelector('#pane-assign');
                const paneB = modal.querySelector('#pane-booking');
                const paneP = modal.querySelector('#pane-payment');
				const activate = (which) => {
					if (which === 'booking') {
						paneA.classList.add('hidden'); if (modal.querySelector('#pane-payment')) modal.querySelector('#pane-payment').classList.add('hidden'); paneB.classList.remove('hidden');
                        if (btnB) btnB.className = 'px-3 py-1 rounded bg-blue-600 text-white';
                        if (btnA) btnA.className = 'px-3 py-1 rounded bg-gray-200 text-gray-800 hover:bg-gray-300';
					} else {
						paneA.classList.remove('hidden'); paneB.classList.add('hidden'); if (modal.querySelector('#pane-payment')) modal.querySelector('#pane-payment').classList.add('hidden');
                        if (btnA) btnA.className = 'px-3 py-1 rounded bg-blue-600 text-white';
                        if (btnB) btnB.className = 'px-3 py-1 rounded bg-gray-200 text-gray-800 hover:bg-gray-300';
                    }
                };
                if (btnGoEdit && !btnGoEdit._wired) {
                    btnGoEdit.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); activate('booking'); });
                    btnGoEdit._wired = true;
                }
                if (btnGoPay && !btnGoPay._wired) {
                    btnGoPay.addEventListener('click', (e) => {
                        e.preventDefault(); e.stopPropagation();
                        try {
                            if (paneA) paneA.classList.add('hidden');
                            if (paneB) paneB.classList.add('hidden');
                            if (paneR) paneR.classList.add('hidden');
                            if (paneP) paneP.classList.remove('hidden');
                            if (btnP) btnP.className = 'px-3 py-1 rounded bg-blue-600 text-white';
                            if (btnA) btnA.className = 'px-3 py-1 rounded bg-gray-200 text-gray-800 hover:bg-gray-300';
                            if (btnB) btnB.className = 'px-3 py-1 rounded bg-gray-200 text-gray-800 hover:bg-gray-300';
                            if (btnR) btnR.className = 'px-3 py-1 rounded bg-gray-200 text-gray-800 hover:bg-gray-300';
                        } catch(_) {}
                    });
                    btnGoPay._wired = true;
                }
                // Edit Room button switches to Room tab
                const btnEditRoom = modal.querySelector('#det-edit-room');
                const activateRoom = () => {
                    try {
                        paneA.classList.add('hidden'); paneB.classList.add('hidden'); if (paneR) paneR.classList.remove('hidden');
                        if (btnR) btnR.className = 'px-3 py-1 rounded bg-blue-600 text-white';
                        if (btnA) btnA.className = 'px-3 py-1 rounded bg-gray-200 text-gray-800 hover:bg-gray-300';
                        if (btnB) btnB.className = 'px-3 py-1 rounded bg-gray-200 text-gray-800 hover:bg-gray-300';
                    } catch(_) {}
                };
                if (btnEditRoom && !btnEditRoom._wired) { btnEditRoom.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); activateRoom(); }); btnEditRoom._wired = true; }
                if (btnR && !btnR._wired) { btnR.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); activateRoom(); }); btnR._wired = true; }
            } catch(_) {}
			guestEl.textContent = `${guest.guest_name || ''} ${guest.guest_phone ? '(' + guest.guest_phone + ')' : ''}`.trim();
			if (roomsEl) roomsEl.textContent = Array.isArray(check.today_rooms) ? check.today_rooms.map(renamedRoom).join(', ') : '';
			const stayRangeEl = modal.querySelector('#assign-stayrange');
			if (stayRangeEl) {
				const s = data.check_in_date || '';
				const e = data.check_out_date || '';
				stayRangeEl.textContent = (s || e) ? `${s || '-'} → ${e || '-'}` : '';
			}
			notesEl.value = (data.booking && data.booking.booking_notes) ? data.booking.booking_notes : '';
        // Pre-fill Booking tab
			try {
				const ebCin = modal.querySelector('#eb-checkin');
				const ebCout = modal.querySelector('#eb-checkout');
				const ebNotes = modal.querySelector('#eb-notes');
                if (ebCin) ebCin.value = (data.check_in_date || '').slice(0,10);
				if (ebCout) ebCout.value = (data.check_out_date || '').slice(0,10);
				if (ebNotes) ebNotes.value = (data.booking && data.booking.booking_notes) ? data.booking.booking_notes : '';
                try { modal.dataset.bookingId = bookingId || ''; } catch(_) {}
				// Populate room types for booking tab
				const hotel = String(data.hotel_name || '').toUpperCase();
				const apiRoot = apiBase.replace('/api','');
				const rtSel = modal.querySelector('#eb-room-type');
				if (rtSel) {
					fetch(`${apiRoot}/api/hotels/rooms?hotel_name=${encodeURIComponent(hotel)}`, { credentials:'include' })
						.then(r => r.json()).then(json => {
							const rts = (json && json.room_types) ? json.room_types : [];
							rtSel.innerHTML = '';
							let matched = false;
							rts.forEach(rt => {
								const opt=document.createElement('option');
								const val=String(rt.room_type||'').toUpperCase();
								opt.value=val; opt.textContent=String(rt.room_type||'');
								if (!matched && currentRoomType && val===String(currentRoomType).toUpperCase()) { opt.selected=true; matched=true; }
								rtSel.appendChild(opt);
							});
							// Fallback: if no match, keep first option selected
							if (!matched && rtSel.options && rtSel.options.length > 0) { rtSel.options[0].selected = true; }
							// Trigger immediate availability/pricing now that room types are populated
							try { renderDerived(); } catch(_) {}
						}).catch(()=>{});
				}
				// Render derived dates list + availability & pricing
				const stayWrap = modal.querySelector('#eb-staydates');
				const roomsSel = modal.querySelector('#eb-rooms');
				const hintEB = modal.querySelector('#eb-availability-hint');
				const parseLocal = (s) => { try { const [y,m,d]=String(s||'').split('-').map(v=>parseInt(v,10)); return new Date(y, (m-1), d); } catch(_) { return null; } };
				const fmtLocal = (d) => { const y=d.getFullYear(); const m=String(d.getMonth()+1).padStart(2,'0'); const dd=String(d.getDate()).padStart(2,'0'); return `${y}-${m}-${dd}`; };
                const renderDerived = async () => {
					if (!stayWrap) return;
					stayWrap.innerHTML = '';
					const a = parseLocal(ebCin && ebCin.value);
					const b = parseLocal(ebCout && ebCout.value);
					if (!(a instanceof Date) || !(b instanceof Date) || isNaN(a) || isNaN(b) || b <= a) { if (roomsSel) roomsSel.innerHTML=''; if (hintEB) hintEB.textContent=''; return; }
					const dates = [];
					for (let d=new Date(a); d<b; d.setDate(d.getDate()+1)) {
						const row = document.createElement('div'); row.className = 'flex items-center gap-2';
						const ymd = fmtLocal(d); dates.push(ymd);
						row.innerHTML = `<input type=\"date\" class=\"nb-date border rounded px-3 py-2 flex-1\" value=\"${ymd}\" disabled /><span class=\"nb-av text-xs\"></span>`;
						stayWrap.appendChild(row);
					}
					// availability + pricing
					try {
						const room_type = (rtSel && rtSel.value) ? String(rtSel.value).toUpperCase() : '';
						if (!room_type || !dates.length) { if (roomsSel) roomsSel.innerHTML=''; if (hintEB) hintEB.textContent=''; return; }
						const hotel = String(data.hotel_name || '').toUpperCase();
						const apiRoot = apiBase.replace('/api','');
						const results = await Promise.all(dates.map(d => fetch(`${apiRoot}/api/availability?hotel_name=${encodeURIComponent(hotel)}&room_type=${encodeURIComponent(room_type)}&stay_date=${encodeURIComponent(d)}`, { credentials:'include' }).then(r=>r.json()).catch(()=>({ available:0 }))));
						const priceResults = await Promise.all(dates.map(d => fetch(`${apiRoot}/api/hotels/${encodeURIComponent(hotel.replace(/\s+/g,'_'))}/pricing?room_type=${encodeURIComponent(room_type)}&start_date=${encodeURIComponent(d)}`, { credentials:'include' }).then(r=>r.json()).catch(()=>({ price:0 }))));
						let minAvail = null;
						const inputs = Array.from(stayWrap.querySelectorAll('.nb-date'));
						inputs.forEach((input, idx) => {
							const r = results[idx] || {}; const booked = (typeof r.booked === 'number') ? r.booked : 0; const av = (typeof r.available === 'number') ? r.available : 0; const inventory = Math.max(0, booked + av);
							const pr = priceResults[idx] || {}; const price = (typeof pr.price === 'number') ? pr.price : 0;
							const span = input.parentElement && input.parentElement.querySelector('.nb-av');
							if (span) { span.textContent = `inventory : ${inventory}, booked : ${booked}, available : ${av}${price ? `, price : ${price}` : ''}`; try { span.classList.remove('text-gray-500','text-red-600','text-green-600'); span.classList.add(av===0?'text-red-600':'text-green-600'); } catch(_) {} }
							if (minAvail === null) minAvail = av; else minAvail = Math.min(minAvail, av);
						});
						const cap = Math.max(0, parseInt(minAvail || 0, 10));
                        if (roomsSel) {
                            roomsSel.innerHTML='';
                            for (let i=1;i<=cap;i++){
                                const opt=document.createElement('option');
                                opt.value=String(i); opt.textContent=String(i);
                                roomsSel.appendChild(opt);
                            }
                            if (!cap){ const opt=document.createElement('option'); opt.value='0'; opt.textContent='0'; roomsSel.appendChild(opt); }
                            // Set initial selection to current rooms booked if within cap
                            if (currentRoomsBooked != null && cap > 0) {
                                const desired = Math.max(1, Math.min(cap, parseInt(currentRoomsBooked,10)||1));
                                roomsSel.value = String(desired);
                            } else if (cap > 0) {
                                roomsSel.value = '1';
                            }
                        }
                        const baseTotal = priceResults.reduce((sum, pr) => sum + (pr && typeof pr.price === 'number' ? pr.price : 0), 0);
                        if (roomsSel) roomsSel.dataset.baseTotal = String(baseTotal);
                        const selectedVal = roomsSel ? Math.max(0, parseInt(roomsSel.value || '1', 10) || 1) : 1;
                        const total = (baseTotal * selectedVal).toFixed(2);
						if (hintEB) { hintEB.textContent = `total : ${total}`; try { hintEB.classList.add('text-sm','text-green-600'); } catch(_) {} }
						if (roomsSel && !roomsSel._priceWire) {
							roomsSel.addEventListener('change', () => {
								const base = parseFloat(roomsSel.dataset.baseTotal || '0') || 0;
								const sel = Math.max(0, parseInt(roomsSel.value || '1', 10) || 1);
								const t = (base * sel).toFixed(2);
								if (hintEB) { hintEB.textContent = `total : ${t}`; }
							});
							roomsSel._priceWire = true;
						}
					} catch(_) { if (hintEB) hintEB.textContent=''; }
				};
				if (ebCin && !ebCin._wiredBk) { ebCin.addEventListener('change', renderDerived); ebCin.addEventListener('input', renderDerived); ebCin._wiredBk = true; }
				if (ebCout && !ebCout._wiredBk) { ebCout.addEventListener('change', renderDerived); ebCout.addEventListener('input', renderDerived); ebCout._wiredBk = true; }
				if (rtSel && !rtSel._wiredBk) { rtSel.addEventListener('change', renderDerived); rtSel._wiredBk = true; }
				// First render (dates prefilled)
				renderDerived();
				// Ensure network fires once layout/values stabilize
				try { setTimeout(() => { try { renderDerived(); } catch(_) {} }, 0); } catch(_) {}
				// Wire Save button for Booking tab
				try {
					const saveBtn = modal.querySelector('#eb-save-btn');
					if (saveBtn && !saveBtn._wiredBk) {
						saveBtn.addEventListener('click', async () => {
							try {
								const a = parseLocal(ebCin && ebCin.value); const b = parseLocal(ebCout && ebCout.value);
								const stay_dates = [];
								if (a instanceof Date && b instanceof Date && !isNaN(a) && !isNaN(b) && b > a) {
									for (let d = new Date(a); d < b; d.setDate(d.getDate()+1)) stay_dates.push(fmtLocal(d));
								}
								const apiBaseLocal = (location.hostname === 'localhost' || location.hostname === '127.0.0.1') ? 'http://localhost:8002/api' : 'https://api.inap.ai/api';
								const payload = {
									booking_id: String(data.booking_id || bookingId || ''),
									new_rooms_booked: Math.max(0, parseInt((roomsSel && roomsSel.value) || '1', 10) || 1),
									new_stay_dates: stay_dates,
									booking_notes: String((ebNotes && ebNotes.value) || ''),
									new_room_type: (rtSel && rtSel.value) ? String(rtSel.value).toUpperCase() : undefined
								};
								const resp = await fetch(`${apiBaseLocal}/bookings/modify`, { method:'POST', headers:{'Content-Type':'application/json'}, credentials:'include', body: JSON.stringify(payload) });
								const out = await resp.json().catch(()=>({}));
								if (!resp.ok || out.error) throw new Error(out.error || 'Failed to modify booking');
								reloadSamePage();
							} catch(e) { alert(e.message || 'Failed to save'); }
						});
						saveBtn._wiredBk = true;
					}
				} catch(_) {}
			} catch(_) {}
			const todayIso = toLocalISODate(new Date());
			const cinDay = String(data.check_in_date || '').slice(0, 10);
			const coutDay = String(data.check_out_date || '').slice(0, 10);
			const stayIncludesToday = !!(cinDay && coutDay && cinDay <= todayIso && todayIso < coutDay);
			const roomStatus = roomStayStatus || String(initialStatus || '').toLowerCase();
			if (statusEl) { statusEl.textContent = ''; statusEl.className = 'hidden'; }
			if (stayIncludesToday && barRoomNow && roomStatus !== 'checked_in' && roomStatus !== 'departed' && roomStatus !== 'checked_out') btnIn.classList.remove('hidden');
			if (roomStatus === 'checked_in') {
				if (btnUndo) btnUndo.classList.remove('hidden');
				btnOut.classList.remove('hidden');
			}
			// Show time next to status where available
			try {
				const timeHint = (roomStatus === 'checked_in' && check.checked_in_at)
					? check.checked_in_at
					: (roomStatus === 'assigned' && check.assigned_at)
						? check.assigned_at
						: '';
				// Header status is hidden; do not append timeHint here
			} catch(_) {}
			// Wire actions once
			const refreshModal = async () => {
				try {
					const rt = (modal && modal.dataset && modal.dataset.roomType) ? modal.dataset.roomType : (currentRoomType || '');
					const rn = (modal && modal.dataset && modal.dataset.roomNumber) ? modal.dataset.roomNumber : '';
					await openEditModal(bookingId, initialStatus, stayDate, rt, rn);
				} catch(_) {}
			};
			if (!btnIn._wired) {
				btnIn.addEventListener('click', async () => {
					try {
						const barRoom = String((modal.dataset && modal.dataset.roomNumber) || '').toUpperCase();
						const payload = { hotel_name: String(data.hotel_name||'').toUpperCase(), booking_id: bookingId, notes: '' };
						if (barRoom) payload.room_number = barRoom;
						const resp = await fetch(`${apiBase}/assignments/check_in`, { method: 'POST', headers: { 'Content-Type':'application/json' }, credentials:'include', body: JSON.stringify(payload) });
						const out = await resp.json();
						if (!resp.ok || out.error) throw new Error(out.error || 'Failed to check in');
                    // Refresh details immediately (blocking) with busy overlay so UI reflects state now
                    // removed busy overlay
                    await refreshModal();
                    // removed busy overlay
                    // Then refresh calendar immediately and schedule full reload to sync other caches
                    try { scheduleFullReloadAndReopen(bookingId, (data.stay_date||stayDate||''), (modal.dataset && modal.dataset.roomType) ? modal.dataset.roomType : (currentRoomType||'')); } catch(_) {}
                    try { load(); } catch(_) { try { window.location.reload(); } catch(__) {} }
					} catch(e) { alert(e.message || 'Failed to check in'); }
				});
				btnIn._wired = true;
			}
			if (!btnOut._wired) {
                btnOut.addEventListener('click', async () => {
					try {
                        // Just-in-time resolve latest room to avoid stale payload after reassign
                        const params = getParamsFromUI();
                        const hotelUp = String((params && params.hotel_name) || data.hotel_name || '').toUpperCase();
                        const barRoomOut = String((modal.dataset && modal.dataset.roomNumber) || '').toUpperCase();
                        const payload = { hotel_name: hotelUp, booking_id: bookingId, room_number: barRoomOut || undefined, notes: '' };
						const resp = await fetch(`${apiBase}/assignments/check_out`, { method: 'POST', headers: { 'Content-Type':'application/json' }, credentials:'include', body: JSON.stringify(payload) });
						const out = await resp.json();
						if (!resp.ok || out.error) throw new Error(out.error || 'Failed to check out');
                    // Refresh details immediately (blocking) with busy overlay so UI reflects state now
                    // removed busy overlay
                    await refreshModal();
                    // removed busy overlay
                    // Then refresh calendar immediately and schedule full reload to sync other caches
                    try { scheduleFullReloadAndReopen(bookingId, (data.stay_date||stayDate||''), (modal.dataset && modal.dataset.roomType) ? modal.dataset.roomType : (currentRoomType||'')); } catch(_) {}
                    try { load(); } catch(_) { try { window.location.reload(); } catch(__) {} }
					} catch(e) { alert(e.message || 'Failed to check out'); }
				});
				btnOut._wired = true;
			}
			if (btnUndo && !btnUndo._wired) {
				btnUndo.addEventListener('click', async () => {
					try {
						const params = getParamsFromUI();
						const hotelUp = String((params && params.hotel_name) || data.hotel_name || '').toUpperCase();
						const barRoom = String((modal.dataset && modal.dataset.roomNumber) || '').toUpperCase();
						const bidNow = String((modal.dataset && modal.dataset.bookingId) || bookingId || '');
						const markClean = await askMarkOldRoomClean();
						if (markClean === null) return;
						const payload = { hotel_name: hotelUp, booking_id: bidNow, room_number: barRoom, mark_clean: markClean };
						const resp = await fetch(`${apiBase}/assignments/undo_check_in`, { method: 'POST', headers: { 'Content-Type':'application/json' }, credentials:'include', body: JSON.stringify(payload) });
						const out = await resp.json().catch(() => ({}));
						if (!resp.ok || out.error) throw new Error(out.error || 'Failed to undo check-in');
						await refreshModal();
						try { scheduleFullReloadAndReopen(bidNow, (data.stay_date||stayDate||''), (modal.dataset && modal.dataset.roomType) ? modal.dataset.roomType : (currentRoomType||''), barRoom); } catch(_) {}
						try { load(); } catch(_) { try { window.location.reload(); } catch(__) {} }
					} catch(e) { alert(e.message || 'Failed to undo check-in'); }
				});
				btnUndo._wired = true;
			}
		} catch (err) {
			alert(err.message || 'Failed to load booking detail');
		}
		// Wire Save (unchanged)
		modal.querySelectorAll('#assign-save, #assign-save-rate').forEach((saveBtn) => {
		if (saveBtn._wired) return;
			saveBtn.addEventListener('click', async () => {
				try {
					const fieldVal = (id) => {
						const el = modal.querySelector(id);
						return el ? String(el.value || '') : '';
					};
					const payload = {
						booking_id: bookingId,
						booking: {
							booking_notes: notesEl.value || '',
							rate_plan: fieldVal('#det-rate-plan')
						},
						guest: {
							guest_name: fieldVal('#det-guest-name'),
							guest_email: fieldVal('#det-guest-email'),
							country: fieldVal('#det-guest-country'),
							nationality: fieldVal('#det-guest-country'),
							date_of_birth: fieldVal('#det-guest-dob'),
							gender: fieldVal('#det-guest-gender'),
							company: fieldVal('#det-guest-company'),
							address: fieldVal('#det-guest-address'),
							identity_type: fieldVal('#det-guest-identity'),
							identity_number: fieldVal('#det-guest-id-detail'),
							special_request: fieldVal('#det-guest-request'),
							vip: !!(modal.querySelector('#det-guest-vip') && modal.querySelector('#det-guest-vip').checked)
						}
					};
					const resp = await fetch(`${apiBase}/assignments/update`, {
						method: 'POST',
						headers: { 'Content-Type': 'application/json' },
						credentials: 'include',
						body: JSON.stringify(payload)
					});
					const out = await resp.json();
					if (!resp.ok || out.error) throw new Error(out.error || 'Failed');
					modal.style.display = 'none';
				} catch (e) {
					alert(e.message || 'Failed to save');
				}
			});
			saveBtn._wired = true;
		});
		// Wire Open full editor
			// Removed external open button; booking edit is now a tab inside this modal
		modal.style.display = 'flex';
	} catch (err) {
		alert(err.message || 'Failed to load booking detail');
	}
  	}
	async function fetchCalendar(params) {
		const qs = new URLSearchParams(params).toString();
		const res = await fetch(`${apiBase}/assignments/calendar?${qs}`, { credentials: 'include', cache: 'no-store' });
		if (!res.ok) throw new Error(`API ${res.status}`);
		return res.json();
	}

	function toneForEntry(entry) {
		if (!entry) return null;
		const status = String(entry.status || '').toLowerCase();
		const stay = String(entry.stay_status || '').toLowerCase();
		if (status === 'out-of-order' || status === 'out_of_order' || status === 'outoforder') {
			return { bg: '#E5E7EB', fg: '#4B5563', label: 'Blocked' };
		}
		if (stay === 'checked_in') return { bg: '#047857', fg: '#FFFFFF', label: '' };
		if (stay === 'checked_out' || stay === 'departed') return { bg: '#4B5563', fg: '#FFFFFF', label: '' };
		if (entry.booking_id) return { bg: '#FFEDD5', fg: '#9A3412', label: '' };
		return null;
	}

	function readinessGlyph(status, size) {
		const px = size || 20;
		const box = `viewBox="0 0 24 24" width="${px}" height="${px}" aria-hidden="true"`;
		const glyphs = {
			clean: `<svg ${box}><path fill="currentColor" d="M12 1.6l3.1 6.4 7 .9-5.1 4.9 1.3 6.9L12 17.4 5.7 20.7l1.3-6.9L1.9 8.9l7-.9L12 1.6z"/></svg>`,
			occupied: `<svg ${box}><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2.6"/><path d="M6.6 17.4L17.4 6.6" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/></svg>`,
			cleaning: `<svg ${box} fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h12"/><path d="M6 21h12"/><path d="M16.5 21v-3.2a2 2 0 0 0-.6-1.4L12 12l-3.9 4.4a2 2 0 0 0-.6 1.4V21"/><path d="M7.5 3v3.2a2 2 0 0 0 .6 1.4L12 12l3.9-4.4a2 2 0 0 0 .6-1.4V3"/></svg>`,
			dirty: `<svg ${box}><path fill="currentColor" d="M11 1.5h2V12h-2z"/><path fill="currentColor" d="M5.5 12h13l-1.6 3.2H7.1L5.5 12z"/><path fill="currentColor" d="M7.4 16.2h1.6l.35 6.2H7.7zm2.7 0h1.6l.2 6.2H9.9zm2.7 0h1.6l.05 6.2h-1.7zm2.7 0h1.6l-.15 6.2h-1.6z"/></svg>`
		};
		return glyphs[String(status || '').toLowerCase()] || '';
	}

	function shortDayLabel(iso) {
		try {
			return parseLocalDate(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
		} catch (_) { return String(iso || ''); }
	}

	function groupNeedsRoom(unassignedByType, days) {
		const groups = new Map();
		Object.keys(unassignedByType || {}).forEach((rtKey) => {
			const dayMap = unassignedByType[rtKey] || {};
			(days || []).forEach((d) => {
				const list = Array.isArray(dayMap[d]) ? dayMap[d] : [];
				const seen = new Map();
				list.forEach((item) => {
					const bid = String((item && item.booking_id) || '');
					if (!bid) return;
					if (!seen.has(bid)) seen.set(bid, { item: item, open: 0 });
					seen.get(bid).open += 1;
				});
				seen.forEach((pack, bid) => {
					const key = String(rtKey || '').toUpperCase() + '|' + bid;
					if (!groups.has(key)) {
						const guest = (pack.item && (pack.item.guest_name || pack.item.guest_phone)) || bid;
						groups.set(key, {
							roomType: String(rtKey || '').toUpperCase(),
							bookingId: bid,
							guest: String(guest),
							status: String((pack.item && pack.item.stay_status) || ''),
							nights: []
						});
					}
					groups.get(key).nights.push({ date: d, open: pack.open });
				});
			});
		});
		return Array.from(groups.values());
	}

	let _dragStay = null;
	let _dragStayEl = null;

	function stayDragPayload(bookingId, stayDate, oldRoomNumber, roomType, stayStatus) {
		return {
			bookingId: String(bookingId || ''),
			stayDate: String(stayDate || '').slice(0, 10),
			oldRoomNumber: String(oldRoomNumber || '').toUpperCase(),
			roomType: String(roomType || '').toUpperCase(),
			stayStatus: String(stayStatus || '').toLowerCase()
		};
	}

	function askMarkOldRoomClean() {
		return new Promise((resolve) => {
			const overlay = document.createElement('div');
			overlay.style.position = 'fixed';
			overlay.style.left = '0';
			overlay.style.top = '0';
			overlay.style.right = '0';
			overlay.style.bottom = '0';
			overlay.style.background = 'rgba(0,0,0,0.35)';
			overlay.style.zIndex = '100000';
			overlay.style.display = 'flex';
			overlay.style.alignItems = 'center';
			overlay.style.justifyContent = 'center';
			const box = document.createElement('div');
			box.className = 'bg-white rounded shadow p-4';
			box.style.minWidth = '260px';
			const text = document.createElement('div');
			text.className = 'text-sm font-semibold mb-3';
			text.textContent = 'Mark old room as clean';
			const row = document.createElement('div');
			row.className = 'flex gap-2 justify-end';
			const yes = document.createElement('button');
			yes.type = 'button';
			yes.className = 'bg-green-600 text-white px-3 py-1 rounded hover:bg-green-700';
			yes.textContent = 'Yes';
			const no = document.createElement('button');
			no.type = 'button';
			no.className = 'bg-gray-600 text-white px-3 py-1 rounded hover:bg-gray-700';
			no.textContent = 'No';
			const finish = (value) => {
				try { overlay.parentNode.removeChild(overlay); } catch (_) {}
				resolve(value);
			};
			yes.addEventListener('click', () => finish(true));
			no.addEventListener('click', () => finish(false));
			overlay.addEventListener('click', (ev) => { if (ev.target === overlay) finish(null); });
			row.appendChild(no);
			row.appendChild(yes);
			box.appendChild(text);
			box.appendChild(row);
			overlay.appendChild(box);
			document.body.appendChild(overlay);
		});
	}

	function readStayDrag(ev) {
		try {
			const raw = ev.dataTransfer.getData('text/plain');
			if (raw) return JSON.parse(raw);
		} catch (_) {}
		return _dragStay;
	}

	function markStayDrag(el, payload) {
		el.draggable = true;
		el.style.cursor = 'grab';
		el.addEventListener('dragstart', (ev) => {
			const data = (typeof payload === 'function') ? payload(ev) : payload;
			el._dragging = true;
			_dragStay = data;
			_dragStayEl = el;
			try {
				ev.dataTransfer.effectAllowed = 'move';
				ev.dataTransfer.setData('text/plain', JSON.stringify(data));
			} catch (_) {}
			document.querySelectorAll('.stay-bar').forEach((bar) => {
				if (bar !== el) bar.style.pointerEvents = 'none';
			});
			ev.stopPropagation();
		});
		el.addEventListener('dragend', () => {
			const ended = _dragStay;
			document.querySelectorAll('.stay-bar').forEach((bar) => { bar.style.pointerEvents = ''; });
			requestAnimationFrame(() => {
				if (_dragStay === ended) {
					_dragStay = null;
					_dragStayEl = null;
				}
			});
		});
	}

	function landStayBar(chip, roomNumber) {
		const home = chip && chip.parentElement;
		const start = home && home.dataset ? String(home.dataset.stayDate || '').slice(0, 10) : '';
		const wantRoom = String(roomNumber || '').toUpperCase();
		if (!chip || !start || !wantRoom) return;
		const cells = document.querySelectorAll('td[data-room-number]');
		for (let i = 0; i < cells.length; i++) {
			const td = cells[i];
			if (String(td.dataset.roomNumber || '').toUpperCase() !== wantRoom) continue;
			if (String(td.dataset.stayDate || '').slice(0, 10) !== start) continue;
			td.style.position = 'relative';
			td.style.zIndex = '2';
			td.style.overflow = 'visible';
			td.appendChild(chip);
			return;
		}
	}

	function dropAllowed(td, payload) {
		if (!payload || !payload.bookingId || !td) return false;
		const roomNumber = String(td.dataset.roomNumber || '').toUpperCase();
		const roomType = String(td.dataset.roomType || '').toUpperCase();
		const occupied = String(td.dataset.bookingId || '');
		if (!roomNumber) return false;
		if (payload.roomType && roomType && payload.roomType !== roomType) return false;
		if (String(td.dataset.blocked || '') === '1') return false;
		if (occupied && occupied !== payload.bookingId) return false;
		if (roomNumber === payload.oldRoomNumber && occupied === payload.bookingId) return false;
		return true;
	}

	async function dropStayOnCell(td, payload, chip) {
		const targetDate = String(td.dataset.stayDate || '').slice(0, 10);
		if (payload.stayDate && targetDate && targetDate !== payload.stayDate) {
			alert('Edit the booking first to change the stay dates.');
			return;
		}
		if (!dropAllowed(td, payload)) {
			const roomType = String(td.dataset.roomType || '').toUpperCase();
			const occupied = String(td.dataset.bookingId || '');
			if (payload.roomType && roomType && payload.roomType !== roomType) alert('That room is a different room type');
			else if (occupied && occupied !== payload.bookingId) alert('That room is already assigned that night');
			return;
		}
		const params = getParamsFromUI();
		const body = {
			hotel_name: String(params.hotel_name || '').toUpperCase(),
			booking_id: payload.bookingId,
			stay_date: payload.stayDate,
			old_room_number: payload.oldRoomNumber,
			new_room_number: String(td.dataset.roomNumber || '').toUpperCase()
		};
		if (String(payload.stayStatus || '').toLowerCase() === 'checked_in' && payload.oldRoomNumber) {
			const markClean = await askMarkOldRoomClean();
			if (markClean === null) return;
			body.mark_old_clean = markClean;
		}
		const moving = chip || _dragStayEl;
		if (moving) landStayBar(moving, body.new_room_number);
		const resp = await fetch(`${apiBase}/assignments/reassign`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify(body) });
		const out = await resp.json().catch(() => ({}));
		if (!resp.ok || out.error) {
			alert(out.error || 'Failed to assign');
			try { await load(); } catch (_) {}
			return;
		}
		try { await load({ silent: true }); } catch (_) {}
	}

	function openCreateBookingFromCell(td) {
		if (!window.bookingManager || typeof window.bookingManager.showNewBookingModal !== 'function') return;
		const stay = String(td.dataset.stayDate || '').slice(0, 10);
		const roomType = String(td.dataset.roomType || '').toUpperCase();
		const roomNumber = String(td.dataset.roomNumber || '').toUpperCase();
		if (!stay || !roomNumber) return;
		const start = parseLocalDate(stay);
		const next = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 1);
		window.bookingManager.showNewBookingModal({
			roomType: roomType,
			roomNumber: roomNumber,
			checkIn: stay,
			checkOut: toLocalISODate(next)
		});
	}

	function wireGridCell(td) {
		td.dataset.baseBg = td.style.background || '';
		td.addEventListener('mouseenter', () => { td.style.background = '#bfdbfe'; });
		td.addEventListener('mouseleave', () => { td.style.background = td.dataset.baseBg || ''; });
		td.addEventListener('dblclick', (ev) => {
			if (td.dataset.bookingId || td.dataset.blocked === '1' || td.querySelector('.stay-bar')) return;
			ev.preventDefault();
			ev.stopPropagation();
			openCreateBookingFromCell(td);
		});
	}

	function bindRoomDrop(td) {
		td.addEventListener('dragover', (ev) => {
			const payload = _dragStay || null;
			if (!payload) return;
			const targetDate = String(td.dataset.stayDate || '').slice(0, 10);
			const otherDate = !!(payload.stayDate && targetDate && targetDate !== payload.stayDate);
			if (!otherDate && !dropAllowed(td, payload)) return;
			ev.preventDefault();
			try { ev.dataTransfer.dropEffect = 'move'; } catch (_) {}
			if (!otherDate) td.style.boxShadow = 'inset 0 0 0 2px #2563eb';
		});
		td.addEventListener('dragleave', () => { td.style.boxShadow = ''; });
		td.addEventListener('drop', async (ev) => {
			ev.preventDefault();
			ev.stopPropagation();
			td.style.boxShadow = '';
			const payload = readStayDrag(ev);
			const chip = _dragStayEl;
			if (!payload) return;
			await dropStayOnCell(td, payload, chip);
		});
	}

	function needsRoomLines(groups) {
		const lines = [];
		(groups || []).forEach((g) => {
			const nights = g.nights || [];
			let slots = 0;
			nights.forEach((n) => { slots = Math.max(slots, Number(n.open) || 0); });
			for (let slot = 0; slot < slots; slot++) {
				const dates = nights.filter((n) => (Number(n.open) || 0) > slot).map((n) => n.date);
				lines.push({
					roomType: g.roomType,
					bookingId: g.bookingId,
					guest: g.guest,
					status: g.status,
					dates: dates
				});
			}
		});
		return lines;
	}

	function openAssignMenu(anchor, line) {
		try {
			const prev = document.getElementById('needs-assign-menu');
			if (prev && prev.parentNode) prev.parentNode.removeChild(prev);
		} catch (_) {}
		const menu = document.createElement('div');
		menu.id = 'needs-assign-menu';
		menu.style.position = 'absolute';
		menu.style.background = '#fff';
		menu.style.border = '1px solid #e5e7eb';
		menu.style.borderRadius = '6px';
		menu.style.boxShadow = '0 4px 10px rgba(0,0,0,0.08)';
		menu.style.padding = '6px';
		menu.style.zIndex = '99999';
		menu.style.minWidth = '120px';
		const note = document.createElement('div');
		note.className = 'text-xs text-gray-500 px-2 py-1';
		note.textContent = 'Loading rooms';
		menu.appendChild(note);
		const rect = anchor.getBoundingClientRect();
		menu.style.left = `${Math.round(rect.left + window.scrollX)}px`;
		menu.style.top = `${Math.round(rect.bottom + window.scrollY + 6)}px`;
		document.body.appendChild(menu);
		const close = () => { try { if (menu.parentNode) menu.parentNode.removeChild(menu); } catch (_) {} };
		const onDoc = (ev) => {
			if (menu.contains(ev.target) || ev.target === anchor) return;
			close();
			document.removeEventListener('click', onDoc, false);
		};
		setTimeout(() => document.addEventListener('click', onDoc, false), 0);
		const dates = (line.dates || []).map((d) => String(d || '').slice(0, 10)).filter(Boolean);
		const firstNight = dates[0] || '';
		(async () => {
			const params = getParamsFromUI();
			const hotel = String((params && params.hotel_name) || '').toUpperCase();
			const roomType = String(line.roomType || '').toUpperCase();
			let rooms = [];
			if (hotel && roomType && dates.length) {
				const lists = await Promise.all(dates.map((d) => fetchAvailableRoomsCached(hotel, roomType, d)));
				lists.forEach((list, idx) => {
					const have = new Set((Array.isArray(list) ? list : []).map((v) => String(v || '').toUpperCase()).filter(Boolean));
					if (idx === 0) rooms = Array.from(have);
					else rooms = rooms.filter((rn) => have.has(rn));
				});
			}
			note.remove();
			if (!rooms.length) {
				const empty = document.createElement('div');
				empty.className = 'text-xs text-gray-500 px-2 py-1';
				empty.textContent = 'No room free for every night';
				menu.appendChild(empty);
				return;
			}
			rooms.forEach((roomNumber) => {
				const b = document.createElement('button');
				b.type = 'button';
				b.textContent = renamedRoom(roomNumber);
				b.style.display = 'block';
				b.style.width = '100%';
				b.style.textAlign = 'left';
				b.style.padding = '4px 8px';
				b.style.fontSize = '12px';
				b.style.fontWeight = '600';
				b.style.borderRadius = '4px';
				b.addEventListener('mouseenter', () => { b.style.backgroundColor = 'rgba(191,219,254,0.35)'; });
				b.addEventListener('mouseleave', () => { b.style.backgroundColor = 'transparent'; });
				b.addEventListener('click', async () => {
					const body = {
						hotel_name: hotel,
						booking_id: line.bookingId,
						stay_date: firstNight,
						old_room_number: '',
						new_room_number: roomNumber
					};
					const resp = await fetch(`${apiBase}/assignments/reassign`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify(body) });
					const out = await resp.json().catch(() => ({}));
					if (!resp.ok || out.error) { alert(out.error || 'Failed to assign'); return; }
					dates.forEach((d) => invalidateAvailableRoomsCache(hotel, roomType, d));
					close();
					try { await load(); } catch (_) {}
				});
				menu.appendChild(b);
			});
		})();
	}

	function renderNeedsPanel(container, groups) {
		const lines = needsRoomLines(groups);
		if (!lines.length) return;
		const box = document.createElement('div');
		box.className = 'border-b bg-yellow-50 p-3';
		const title = document.createElement('div');
		title.className = 'text-sm font-semibold text-gray-900 mb-2';
		title.textContent = 'Needs a room';
		box.appendChild(title);
		lines.forEach((line) => {
			const row = document.createElement('div');
			row.className = 'flex items-center justify-between gap-3 py-1';
			const left = document.createElement('div');
			left.className = 'text-sm text-gray-800';
			const nights = (line.dates || []).map((d) => shortDayLabel(d)).join(', ');
			left.textContent = line.guest + ' · ' + (line.roomType || 'ROOM') + ' · ' + nights;
			const firstNight = (line.dates && line.dates[0]) ? line.dates[0] : '';
			markStayDrag(left, stayDragPayload(line.bookingId, firstNight, '', line.roomType));
			const btn = document.createElement('button');
			btn.type = 'button';
			btn.className = 'bg-blue-600 text-white text-xs px-2 py-1 rounded hover:bg-blue-700';
			btn.textContent = 'Assign';
			btn.addEventListener('click', (ev) => {
				ev.preventDefault();
				ev.stopPropagation();
				openAssignMenu(btn, line);
			});
			row.appendChild(left);
			row.appendChild(btn);
			box.appendChild(row);
		});
		container.appendChild(box);
	}

	function parkDateTools() {
		let holder = document.getElementById('date-tools');
		if (!holder) {
			holder = document.createElement('div');
			holder.id = 'date-tools';
			holder.className = 'hidden';
			document.body.appendChild(holder);
		}
		['start', 'end', 'today', 'range7', 'range14', 'bulk-checkout-past'].forEach((id) => {
			const el = document.getElementById(id);
			if (el) holder.appendChild(el);
		});
	}

	function renderTable(container, data) {
		parkDateTools();
		container.innerHTML = '';
		if (data && data.error) {
			container.innerHTML = `<div class=\"p-4 text-red-600\">${data.error}</div>`;
			return;
		}
		const daysAll = (data && data.days) || [];
		// Clamp server-extended days to the UI-selected range
		let days = daysAll;
		try {
			const ui = getParamsFromUI();
			const sReq = String(ui.start_date || '');
			const eReq = String(ui.end_date || '');
			if (sReq && eReq) {
				days = daysAll.filter(d => String(d) >= sReq && String(d) <= eReq);
				if (!Array.isArray(days) || days.length === 0) days = daysAll;
			}
		} catch(_) {}
		const rooms = ((data && data.rooms) || []).slice().sort((a, b) => {
			const ta = String((a && a.room_type) || '').toUpperCase();
			const tb = String((b && b.room_type) || '').toUpperCase();
			if (ta < tb) return -1;
			if (ta > tb) return 1;
			return renamedRoom(a && a.room_number).localeCompare(renamedRoom(b && b.room_number));
		});
		const unassignedByType = (data && data.unassigned_by_type) || {};
		const allRows = rooms;
		renderNeedsPanel(container, groupNeedsRoom(unassignedByType, days));

		// Make container horizontally scrollable and independent of headers
		try {
			container.style.overflow = 'auto';
			container.style.position = 'relative';
		} catch(_) {}

		const table = document.createElement('table');
		table.className = 'table-fixed border-separate';
		const gridLine = '1px solid #e5e7eb';
		try {
			table.style.borderSpacing = '0';
			table.style.tableLayout = 'fixed';
			table.style.position = 'relative';
            table.style.zIndex = '1';
			// Set an explicit width based on number of columns (clamped days)
			table.style.width = `${LEFT_COL_WIDTH + (days.length * DAY_COL_WIDTH)}px`;
		} catch(_) {}
		// Define explicit column widths (no header dependency)
		const colgroup = document.createElement('colgroup');
		const colLeft = document.createElement('col'); colLeft.style.width = `${LEFT_COL_WIDTH}px`; colgroup.appendChild(colLeft);
		for (let i = 0; i < days.length; i++) { const c = document.createElement('col'); c.style.width = `${DAY_COL_WIDTH}px`; colgroup.appendChild(c); }
		table.appendChild(colgroup);

		const thead = document.createElement('thead');
			const shiftRange = (deltaDays) => {
				try {
					const startEl = document.getElementById('start');
					const endEl = document.getElementById('end');
					const sVal = (startEl && startEl.value) ? startEl.value : toLocalISODate(new Date());
					const eVal = (endEl && endEl.value) ? endEl.value : toLocalISODate(new Date());
					const sDate = parseLocalDate(sVal);
					const eDate = parseLocalDate(eVal);
					const newS = toLocalISODate(new Date(sDate.getFullYear(), sDate.getMonth(), sDate.getDate() + deltaDays));
					const newE = toLocalISODate(new Date(eDate.getFullYear(), eDate.getMonth(), eDate.getDate() + deltaDays));
					if (startEl) startEl.value = newS;
					if (endEl) endEl.value = newE;
					load();
				} catch(_) { load(); }
			};
			const makeNavBtn = (label, delta) => {
				const b = document.createElement('button');
				b.textContent = label;
				b.className = 'bg-blue-600 text-white px-2 py-1 rounded hover:bg-blue-700';
				b.style.fontSize = '15px';
			b.style.marginRight = '0';
				b.addEventListener('click', () => shiftRange(delta));
				return b;
			};
		const navBar = document.createElement('div');
		navBar.className = 'inline-flex items-center justify-center flex-wrap gap-3 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2';
		navBar.style.maxWidth = '100%';
		const dateStart = document.getElementById('start');
		const dateEnd = document.getElementById('end');
		const todayBtn = document.getElementById('today');
		const range7 = document.getElementById('range7');
		const range14 = document.getElementById('range14');
		const bulkBtn = document.getElementById('bulk-checkout-past');
		const cluster = () => {
			const el = document.createElement('div');
			el.className = 'inline-flex items-center gap-2';
			return el;
		};
		const dates = cluster();
		[dateStart, dateEnd].forEach((el) => { if (el) dates.appendChild(el); });
		const arrows = cluster();
		arrows.appendChild(makeNavBtn('<', -1));
		arrows.appendChild(makeNavBtn('>', +1));
		arrows.appendChild(makeNavBtn('<<', -7));
		arrows.appendChild(makeNavBtn('>>', +7));
		const ranges = cluster();
		[todayBtn, range7, range14, bulkBtn].forEach((el) => { if (el) ranges.appendChild(el); });
		navBar.appendChild(dates);
		navBar.appendChild(arrows);
		navBar.appendChild(ranges);
		const trDate = document.createElement('tr');
		const thLeft = document.createElement('th');
		thLeft.className = 'bg-gray-100 px-2 py-1 text-sm font-medium text-gray-700';
		thLeft.style.width = `${LEFT_COL_WIDTH}px`;
		thLeft.style.borderTop = gridLine;
		thLeft.style.borderLeft = gridLine;
		thLeft.style.borderRight = gridLine;
		thLeft.style.borderBottom = gridLine;
		trDate.appendChild(thLeft);
		// Day cells
		for (let i = 0; i < days.length; i++) {
			const d = days[i];
			const th = document.createElement('th');
			th.className = 'px-1 py-1 text-sm font-medium text-gray-700 text-center';
			th.style.borderTop = gridLine;
			th.style.borderRight = gridLine;
			th.style.borderBottom = gridLine;
			// Alternating header background
		try { th.style.background = (i % 2 === 0) ? 'rgba(219,234,254,0.35)' : 'rgba(191,219,254,0.55)'; } catch(_) {}
			try {
				const todayIso = toLocalISODate(new Date());
				if (String(d) === todayIso) {
					th.textContent = 'TODAY';
					// Match the special shade used in grid for today
					th.style.background = 'rgba(96,165,250,0.40)';
				} else {
				const dt = new Date(d + 'T00:00:00Z');
				const wd = dt.toLocaleDateString('en-US', { weekday: 'short' });
				const lbl = dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
				th.textContent = `${lbl} (${wd})`;
				}
			} catch(_) {
				th.textContent = d;
			}
			trDate.appendChild(th);
			th.style.position = 'relative';
			th.style.left = `-${DAY_COL_WIDTH / 2}px`;
		}
		thead.appendChild(trDate);
		table.appendChild(thead);
		const tbody = document.createElement('tbody');

	// Build groups by room_type (header-only toggle)
	const groups = {};
	(allRows || []).forEach((r, idx) => {
		const rt = String(r.room_type || '').toUpperCase();
		if (!groups[rt]) groups[rt] = [];
		groups[rt].push({ r, idx });
	});

    Object.keys(groups).forEach(rt => {
		// Header row
        const trh = document.createElement('tr');
        trh.setAttribute('data-rt', rt);
        const th = document.createElement('td'); th.colSpan = (1 + days.length);
        th.className = 'bg-gray-50 px-2 py-1 font-semibold';
        th.style.borderLeft = gridLine;
        th.style.borderRight = gridLine;
        th.style.borderBottom = gridLine;
        try { th.style.position = 'sticky'; th.style.top = '0px'; th.style.zIndex = '120'; th.style.background = '#f9fafb'; th.style.pointerEvents = 'auto'; } catch(_) {}
        const btn = document.createElement('button'); btn.textContent = _collapsedTypes.has(rt) ? '▶' : '▼'; btn.style.marginRight = '6px';
        try { btn.style.cursor = 'pointer'; btn.style.userSelect = 'none'; } catch(_) {}
        btn.addEventListener('click', (ev) => {
            ev.preventDefault(); ev.stopPropagation();
            const willCollapse = !_collapsedTypes.has(rt);
            if (willCollapse) { _collapsedTypes.add(rt); btn.textContent = '▶'; }
            else { _collapsedTypes.delete(rt); btn.textContent = '▼'; }
            // Toggle visibility of rows for this room type without full re-render
            const rowsForType = tbody.querySelectorAll(`tr[data-room-type-row="${rt}"]`);
            rowsForType.forEach(row => { row.style.display = willCollapse ? 'none' : 'table-row'; });
            // Recompute grids/overlay after layout change
            // Ensure button remains on top and clickable
            try { th.style.zIndex = '200'; btn.style.zIndex = '210'; } catch(_) {}
        });
		const label = document.createElement('span'); label.textContent = `Room Type: ${rt || 'UNKNOWN'}`;
		th.appendChild(btn); th.appendChild(label); trh.appendChild(th); tbody.appendChild(trh);
        // rows rendered below; visibility will be controlled by state
        (groups[rt] || []).forEach(({ r, idx }) => {
            const tr = document.createElement('tr'); tr.setAttribute('data-room-index', String(idx)); tr.setAttribute('data-room-type-row', rt);
            if (_collapsedTypes.has(rt)) { try { tr.style.display = 'none'; } catch(_) {} }
				const left = document.createElement('td');
			left.className = 'px-2 py-1 sticky left-0 bg-gray-50 z-10 font-semibold';
			left.style.borderLeft = gridLine;
			left.style.borderRight = gridLine;
			left.style.borderBottom = gridLine;
			left.style.width = `${LEFT_COL_WIDTH}px`;
		left.style.fontSize = '15px';
		try { left.style.background = '#f9fafb'; } catch(_) {}
            // Hover highlight for room cell
            try {
                left.style.transition = 'background-color 120ms ease';
                left.addEventListener('mouseenter', () => { left.style.backgroundColor = 'rgba(191,219,254,0.35)'; });
                left.addEventListener('mouseleave', () => { left.style.backgroundColor = '#f9fafb'; });
            } catch(_) {}
		try {
			const entriesObj = r.entries || {};
			const hasUnassignedByType = Object.values(entriesObj).some(v => v && String(v.status || '').toLowerCase() === 'unassigned_by_type');
			left.textContent = (r.room_number && String(r.room_number).trim()) ? renamedRoom(r.room_number) : (hasUnassignedByType ? 'NO ROOM' : '');
		} catch(_) { left.textContent = renamedRoom(r.room_number); }
            const mark = document.createElement('button');
            mark.type = 'button';
            mark.className = 'rr-mark';
            mark.style.display = 'inline-flex';
            mark.style.alignItems = 'center';
            mark.style.justifyContent = 'center';
            mark.style.width = '22px';
            mark.style.height = '22px';
            mark.style.marginLeft = '6px';
            mark.style.verticalAlign = 'middle';
            mark.style.cursor = 'pointer';
            mark.style.border = '0';
            mark.style.background = 'transparent';
            mark.style.padding = '0';
            mark.style.color = '#9ca3af';
            left.appendChild(mark);
            // Click-to-change readiness status
            mark.addEventListener('click', (ev) => {
                try {
                    ev.stopPropagation(); ev.preventDefault();
                    const rn = (r.room_number || '').toString();
                    if (!rn) return;
                    const menu = document.createElement('div');
                    menu.style.position = 'absolute';
                    menu.style.background = '#fff';
                    menu.style.border = '1px solid #e5e7eb';
                    menu.style.borderRadius = '6px';
                    menu.style.boxShadow = '0 4px 10px rgba(0,0,0,0.08)';
                    menu.style.padding = '6px';
                    menu.style.zIndex = '9999';
                    const opts = ['clean','dirty','cleaning','occupied'];
                    const colorForStatus = (s) => {
                        s = String(s||'').toLowerCase();
                        if (s === 'clean') return '#10b981';
                        if (s === 'dirty') return '#f97316';
                        if (s === 'cleaning') return '#f59e0b';
                        if (s === 'occupied') return '#ef4444';
                        return '#374151';
                    };
                    opts.forEach(st => {
                        const btn = document.createElement('button');
                        try { btn.type = 'button'; } catch(_) {}
                        btn.textContent = '';
                        btn.style.display = 'flex';
                        btn.style.alignItems = 'center';
                        btn.style.gap = '8px';
                        btn.style.width = '100%';
                        btn.style.textAlign = 'left';
                        btn.style.padding = '4px 8px';
                        btn.style.fontSize = '12px';
                        btn.style.fontWeight = '600';
                        btn.style.color = colorForStatus(st);
                        btn.style.borderRadius = '4px';
                        btn.style.transition = 'background-color 120ms ease';
                        btn.addEventListener('mouseenter', () => { btn.style.backgroundColor = 'rgba(191,219,254,0.35)'; });
                        btn.addEventListener('mouseleave', () => { btn.style.backgroundColor = 'transparent'; });
                        const icon = document.createElement('span');
                        icon.style.display = 'inline-flex';
                        icon.style.color = colorForStatus(st);
                        icon.innerHTML = readinessGlyph(st, 14);
                        btn.appendChild(icon);
                        btn.appendChild(document.createTextNode(st.toUpperCase()));
                        btn.addEventListener('click', async () => {
                            try {
                                let hotelName = '';
                                try { const p = (typeof getParamsFromUI === 'function') ? getParamsFromUI() : {}; hotelName = String(p.hotel_name || '').toUpperCase(); } catch(_) {}
                                if (!hotelName) {
                                    try {
                                        const m = document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/);
                                        if (m && m[1]) hotelName = decodeURIComponent(m[1]).toUpperCase();
                                    } catch(_) {}
                                }
                                const payload = { hotel_name: hotelName, room_number: rn, new_status: st, notes: 'Manual update from calendar' };
                                const res = await fetch(`${apiBase}/rooms/readiness_status/update`, {
                                    method: 'POST', credentials: 'include',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify(payload)
                                });
                                const out = await res.json().catch(()=>({}));
                                if (!res.ok || out.error) {
                                    console.error('readiness_status/update failed', out && out.error);
                                }
                            } catch(err) { console.error(err); }
                            try { document.body.removeChild(menu); } catch(_){}
                            try { await load(); } catch(_) {}
                        });
                        menu.appendChild(btn);
                    });
                    const rect = mark.getBoundingClientRect();
                    menu.style.left = `${Math.round(rect.left + window.scrollX)}px`;
                    menu.style.top = `${Math.round(rect.bottom + window.scrollY + 4)}px`;
                    document.body.appendChild(menu);
                    const onDoc = (e2) => { try { if (menu && !menu.contains(e2.target)) { document.body.removeChild(menu); document.removeEventListener('click', onDoc, false); } } catch(_){} };
                    setTimeout(()=>document.addEventListener('click', onDoc, false), 0);
                } catch(_) {}
            });
				tr.appendChild(left);
			const entriesObj = r.entries || {};
			let coverLeft = 0;
			for (let i = 0; i < days.length; i++) {
						const d = days[i];
							const td = document.createElement('td');
				td.className = 'px-0 py-0 align-middle';
				td.style.borderRight = gridLine;
				td.style.borderBottom = gridLine;
				td.style.height = '34px';
					td.style.width = `${DAY_COL_WIDTH}px`;
					td.style.minWidth = `${DAY_COL_WIDTH}px`;
					td.style.maxWidth = `${DAY_COL_WIDTH}px`;
							td.dataset.stayDate = d;
				td.dataset.roomNumber = r.room_number || '';
							td.dataset.roomType = r.room_type || '';
				const entry = entriesObj[d] || null;
				const tone = toneForEntry(entry);
				bindRoomDrop(td);
				if (!tone && String(d) === toLocalISODate(new Date())) {
					td.style.background = 'rgba(96,165,250,0.25)';
				}
				if (coverLeft > 0) {
					coverLeft -= 1;
					const bid = entry ? String(entry.booking_id || '') : '';
					if (bid) td.dataset.bookingId = bid;
					if (entry && !bid && tone && tone.label === 'Blocked') td.dataset.blocked = '1';
					wireGridCell(td);
					tr.appendChild(td);
					continue;
				}
				if (tone && entry) {
					const bid = String(entry.booking_id || '');
					const blocked = !bid && tone.label === 'Blocked';
					if (blocked) td.dataset.blocked = '1';
					if (bid) td.dataset.bookingId = bid;
					let span = 1;
					if (bid) {
						while (i + span < days.length) {
							const nxt = entriesObj[days[i + span]] || null;
							if (!nxt || String(nxt.booking_id || '') !== bid) break;
							span += 1;
						}
					}
					const chip = document.createElement('button');
					chip.type = 'button';
					chip.className = 'stay-bar text-xs text-left truncate';
					chip.style.position = 'absolute';
					chip.style.left = '0';
					chip.style.top = '2px';
					chip.style.height = '30px';
					chip.style.width = `${span * DAY_COL_WIDTH}px`;
					chip.style.zIndex = '5';
					chip.style.background = tone.bg;
					const stayNow = String(entry.stay_status || '').toLowerCase();
					const lightName = stayNow === 'checked_in' || stayNow === 'departed' || stayNow === 'checked_out';
					chip.style.color = (lightName || tone.fg === '#FFFFFF') ? '#FFFFFF' : (bid ? '#000000' : tone.fg);
					chip.style.fontWeight = bid ? '700' : '400';
					chip.style.border = '0';
					chip.style.borderRadius = '4px 0 0 4px';
					chip.style.display = 'flex';
					chip.style.alignItems = 'center';
					chip.style.gap = '4px';
					chip.style.padding = '0 22px 0 6px';
					chip.style.clipPath = 'polygon(0 0, calc(100% - 18px) 0, 100% 50%, calc(100% - 18px) 100%, 0 100%)';
					const name = String(entry.guest_name || entry.guest_phone || tone.label || '');
					const channel = String(entry.channel || '').toUpperCase();
					const channelIcons = {
						'PMS': 'assets/pms.svg?v=hotel',
						'AGODA': 'assets/agoda.ico',
						'BOOKING.COM': 'assets/bookingdotcom.ico',
						'CTRIP': 'assets/ctrip.ico',
						'TRAVELOKA': 'assets/traveloka.ico'
					};
					const iconSrc = channelIcons[channel] || '';
					if (iconSrc) {
						const img = document.createElement('img');
						img.src = iconSrc;
						img.alt = channel;
						img.style.width = '14px';
						img.style.height = '14px';
						img.style.flex = '0 0 auto';
						img.style.borderRadius = '3px';
						img.draggable = false;
						img.style.pointerEvents = 'none';
						chip.appendChild(img);
					}
					const label = document.createElement('span');
					label.textContent = name;
					label.style.overflow = 'hidden';
					label.style.textOverflow = 'ellipsis';
					label.style.whiteSpace = 'nowrap';
					chip.appendChild(label);
					chip.title = bid ? (name + ' · ' + d) : (entry.notes ? String(entry.notes) : name);
					td.style.position = 'relative';
					td.style.zIndex = '2';
					td.style.overflow = 'visible';
					if (bid) {
						const nights = days.slice(i, i + span);
						markStayDrag(chip, (ev) => {
							const rect = chip.getBoundingClientRect();
							const idx = Math.min(span - 1, Math.max(0, Math.floor((ev.clientX - rect.left) / DAY_COL_WIDTH)));
							return stayDragPayload(bid, nights[idx] || d, r.room_number || '', r.room_type || '', entry.stay_status || entry.status || '');
						});
						chip.addEventListener('click', (ev) => {
							if (chip._dragging) { chip._dragging = false; ev.preventDefault(); ev.stopPropagation(); return; }
							ev.preventDefault();
							ev.stopPropagation();
							const rect = chip.getBoundingClientRect();
							const idx = Math.min(span - 1, Math.max(0, Math.floor((ev.clientX - rect.left) / DAY_COL_WIDTH)));
							openEditModal(bid, entry.stay_status || entry.status || '', nights[idx] || d, r.room_type || '', r.room_number || '');
						});
					}
					chip.addEventListener('mouseenter', () => { td.style.background = '#bfdbfe'; });
					chip.addEventListener('mouseleave', () => { td.style.background = td.dataset.baseBg || ''; });
					td.appendChild(chip);
					coverLeft = span - 1;
				}
				wireGridCell(td);
							tr.appendChild(td);
					}
				tbody.appendChild(tr);
        });
			});

		table.appendChild(tbody);
		const toolbarWrap = document.createElement('div');
		toolbarWrap.className = 'flex justify-center mb-3';
		toolbarWrap.style.position = 'sticky';
		toolbarWrap.style.left = '0';
		toolbarWrap.style.zIndex = '30';
		const fitToolbar = () => {
			const w = container.clientWidth;
			if (w) toolbarWrap.style.width = w + 'px';
		};
		fitToolbar();
		container._fitToolbar = fitToolbar;
		if (!container._toolbarResize) {
			container._toolbarResize = () => { if (typeof container._fitToolbar === 'function') container._fitToolbar(); };
			window.addEventListener('resize', container._toolbarResize);
		}
		toolbarWrap.appendChild(navBar);
		container.appendChild(toolbarWrap);
		container.appendChild(table);
        // Fetch bulk readiness and color markers (non-blocking)
        (async () => {
            try {
                const params = getParamsFromUI();
                const hotel = String(params.hotel_name || '').toUpperCase();
                if (!hotel) return;
                const url = `${apiBase}/rooms/readiness_status_all?hotel_name=${encodeURIComponent(hotel)}`;
                const resR = await fetch(url, { credentials: 'include' });
                const jR = await resR.json().catch(()=>({}));
                const items = (jR && jR.items) || [];
                const map = new Map();
                items.forEach(it => { try { map.set(String(it.room_number), String(it.readiness_status).toLowerCase()); } catch(_) {} });
                const colorFor = (s) => {
                    s = String(s||'').toLowerCase();
                    if (s === 'clean') return '#10b981'; // green
                    if (s === 'dirty') return '#f97316'; // orange
                    if (s === 'cleaning') return '#f59e0b'; // yellow
                    if (s === 'occupied') return '#ef4444'; // red
                    return '#9ca3af';
                };
                const rowsLive = tbody.querySelectorAll('tr[data-room-index]');
                rowsLive.forEach(row => {
                    try {
                        const cell = row.children && row.children[0];
                        if (!cell) return;
                        const rnText = cell.firstChild && cell.firstChild.nodeType === Node.TEXT_NODE ? cell.firstChild.nodeValue : cell.textContent;
                        const rn = String(rnText || '').trim();
                        const mark = cell.querySelector('button.rr-mark');
                        const status = map.get(rn) || '';
                        const clr = colorFor(status);
                        if (mark) {
                            mark.style.color = clr;
                            mark.innerHTML = readinessGlyph(status, 20);
                            mark.title = status;
                        }
                        // Match font color to readiness dot color
                        cell.style.color = clr;
                    } catch(_) {}
                });
            } catch(_) {}
        })();
	}

	function getParamsFromUI() {
		const u = new URL(location.href);
		const cookieHotelMatch = document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/);
		const cookieHotel = cookieHotelMatch ? decodeURIComponent(cookieHotelMatch[1]) : '';
		const hotel = document.getElementById('hotel')?.value || u.searchParams.get('hotel') || cookieHotel || '';
		const start = document.getElementById('start')?.value || u.searchParams.get('start') || (() => { const d = new Date(); d.setDate(d.getDate()-1); return toLocalISODate(d); })();
		const end = document.getElementById('end')?.value || u.searchParams.get('end') || (() => { const d = new Date(); d.setDate(d.getDate()+12); return toLocalISODate(d); })();
		const room_type = document.getElementById('room_type')?.value || u.searchParams.get('room_type') || '';
		const rooms = document.getElementById('rooms')?.value || u.searchParams.get('rooms') || '';
		return { hotel_name: hotel, start_date: start, end_date: end, room_type, room_numbers: rooms };
	}

	function reloadSamePage() {
		try {
			const u = new URL(location.href);
			const start = document.getElementById('start');
			const end = document.getElementById('end');
			if (start && start.value) u.searchParams.set('start', start.value);
			if (end && end.value) u.searchParams.set('end', end.value);
			history.replaceState(null, '', u.pathname + u.search + u.hash);
		} catch (_) {}
		location.reload();
	}

	async function load(opts) {
		const silent = !!(opts && opts.silent);
		const params = getParamsFromUI();
		const cal = document.getElementById('calendar');
		if (!silent) {
			parkDateTools();
			cal.innerHTML = '<div class=\"p-4 text-gray-500\">Loading...</div>';
		}
		try {
			await loadRoomLabels(params.hotel_name);
			const data = await fetchCalendar(params);
			parkDateTools();
			renderTable(cal, data);
		} catch (e) {
			parkDateTools();
			cal.innerHTML = `<div class=\"p-4 text-red-600\">${e.message}</div>`;
		}
	}

	document.addEventListener('DOMContentLoaded', () => {
		const startEl = document.getElementById('start');
		const endEl = document.getElementById('end');
		const todayBtn = document.getElementById('today');
		const range7 = document.getElementById('range7');
		const range14 = document.getElementById('range14');
		const bulkBtn = document.getElementById('bulk-checkout-past');
		// Helper to compute API base robustly
		const computeApiBase = () => {
			if (window.apiRoot) return window.apiRoot;
			try {
				const host = window.location.hostname;
				if (host === 'localhost' || host === '127.0.0.1' || host === '::1') {
					return `${window.location.protocol}//localhost:8002/api`;
				}
			} catch(_) {}
			return '/api';
		};

		if (bulkBtn) {
			const modal = document.getElementById('bulk-checkout-modal');
			const tbody = document.getElementById('bulk-tbody');
			const btnClose = document.getElementById('bulk-close');
			const btnCancel = document.getElementById('bulk-cancel');
			const btnConfirm = document.getElementById('bulk-confirm');
			const chkAll = document.getElementById('bulk-select-all');
			const openModal = () => { if (modal) { modal.classList.remove('hidden'); modal.classList.add('flex'); } };
			const closeModal = () => { if (modal) { modal.classList.add('hidden'); modal.classList.remove('flex'); } };
			bulkBtn.addEventListener('click', async () => {
				try {
					let hotelName = '';
					try { const p = (typeof getParamsFromUI === 'function') ? getParamsFromUI() : {}; hotelName = String(p.hotel_name || '').toUpperCase(); } catch(_) {}
					if (!hotelName) { const u = new URL(window.location.href); hotelName = String(u.searchParams.get('hotel') || '').toUpperCase(); }
					if (!hotelName) { alert('Missing hotel name'); return; }
					const apiBase = computeApiBase();
					// Load items (try GET then POST fallback)
					let j = { items: [] };
					let r = await fetch(`${apiBase}/assignments/past_stays?hotel_name=${encodeURIComponent(hotelName)}`, { credentials:'include' });
					if (!r.ok) {
						const r2 = await fetch(`${apiBase}/assignments/past_stays`, { method:'POST', headers:{'Content-Type':'application/json'}, credentials:'include', body: JSON.stringify({ hotel_name: hotelName }) });
						j = await r2.json().catch(()=>({ items:[] }));
					} else {
						j = await r.json().catch(()=>({ items:[] }));
					}
                    const bookings = j.bookings || [];
                    if (tbody) {
                        tbody.innerHTML = '';
                        for (const bk of bookings) {
                            const dates = Array.isArray(bk.stay_dates) ? bk.stay_dates : [];
                            const rooms = Array.isArray(bk.rooms) ? bk.rooms : [];
                            const ci = dates.length ? dates[0] : '';
                            // compute checkout = (last stay_date + 1 day)
                            let co = '';
                            if (dates.length) {
                                try {
                                    const d = new Date(dates[dates.length-1]);
                                    d.setDate(d.getDate()+1);
                                    co = d.toISOString().slice(0,10);
                                } catch(_) { co = dates[dates.length-1]; }
                            }
                            const tr = document.createElement('tr');
                            tr.innerHTML = `
                                <td class="p-2 text-center"><input type="checkbox" class="bulk-booking" data-bid="${bk.booking_id}" data-dates="${dates.join(',')}"></td>
                                <td class="p-2">${ci}</td>
                                <td class="p-2">${co}</td>
                                <td class="p-2">${bk.booking_id}</td>
                                <td class="p-2">${bk.guest_name || bk.guest_phone || ''}</td>
                                <td class="p-2">${bk.stay_status || ''} ${rooms.length ? `• Rooms: ${rooms.join(' / ')}` : ''}</td>
                            `;
                            tbody.appendChild(tr);
                        }
                    }
					if (chkAll) {
                        chkAll.checked = false;
                        chkAll.onclick = () => {
                            const boxes = tbody ? tbody.querySelectorAll('input.bulk-booking') : [];
                            boxes.forEach(b => { b.checked = chkAll.checked; });
                        };
					}
					if (btnConfirm) {
						btnConfirm.onclick = async () => {
							try {
                            const boxes = tbody ? Array.from(tbody.querySelectorAll('input.bulk-booking:checked')) : [];
                            if (!boxes.length) { alert('Select at least one booking'); return; }
                            const selected = [];
                            for (const b of boxes) {
                                const bid = b.getAttribute('data-bid');
                                const dates = (b.getAttribute('data-dates') || '').split(',').filter(Boolean);
                                for (const d of dates) selected.push({ booking_id: bid, stay_date: d });
                            }
								btnConfirm.disabled = true; btnConfirm.textContent = 'Processing...';
								const resp = await fetch(`${computeApiBase()}/assignments/bulk_checkout_selected`, {
									method: 'POST', headers:{'Content-Type':'application/json'}, credentials:'include',
									body: JSON.stringify({ hotel_name: hotelName, items: selected })
								});
								const dj = await resp.json().catch(()=>({}));
								if (!resp.ok || dj.error) { alert(`Bulk check-out failed: ${dj.error || resp.status}`); }
								else { alert(`Bulk check-out complete. Rows updated: ${dj.rows_updated ?? 'n/a'}`); closeModal(); try { if (typeof reloadCalendar==='function') reloadCalendar(); } catch(_) {} }
							} catch(e) {
								console.error(e); alert('Bulk check-out error');
							} finally {
								btnConfirm.disabled = false; btnConfirm.textContent = 'Confirm Check-out';
							}
						};
					}
					if (btnClose) btnClose.onclick = closeModal;
					if (btnCancel) btnCancel.onclick = closeModal;
					openModal();
				} catch (e) { console.error(e); alert('Failed to load past stays'); }
			});
		}
		try { startEl && startEl.addEventListener('change', load); } catch(_) {}
		try { endEl && endEl.addEventListener('change', load); } catch(_) {}
		try {
			if (todayBtn) {
				todayBtn.addEventListener('click', () => {
					try {
						const s = new Date(); s.setHours(0,0,0,0); s.setDate(s.getDate()-1);
						const e = new Date(); e.setHours(0,0,0,0); e.setDate(e.getDate()+12);
						const startIso = toLocalISODate(s);
						const endIso = toLocalISODate(e);
						if (startEl) startEl.value = startIso;
						if (endEl) endEl.value = endIso;
		load();
					} catch(_) { load(); }
				});
			}
		} catch(_) {}
		// Range quick buttons next to Today
		try {
			const applyRange = (daysCount) => {
				try {
					const sVal = (startEl && startEl.value) ? startEl.value : toLocalISODate(new Date());
					const sDate = parseLocalDate(sVal);
					const endDate = new Date(sDate.getFullYear(), sDate.getMonth(), sDate.getDate() + (daysCount - 1));
					if (endEl) endEl.value = toLocalISODate(endDate);
					load();
				} catch(_) { load(); }
			};
			range7 && range7.addEventListener('click', () => applyRange(7));
			range14 && range14.addEventListener('click', () => applyRange(14));
		} catch(_) {}
		// Initialize inputs to default range if empty, then load
		try {
			const now = new Date(); now.setHours(0,0,0,0);
			const s = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
			const e = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 12);
			const qs = new URL(location.href).searchParams;
			if (startEl && !startEl.value) startEl.value = qs.get('start') || toLocalISODate(s);
			if (endEl && !endEl.value) endEl.value = qs.get('end') || toLocalISODate(e);
		} catch(_) {}
		load();
	});
})();