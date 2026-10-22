// Inventory Management Component - DATE-BASED HOTEL INVENTORY
class InventoryComponent {
    constructor() {
        const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
        this.apiBase = isLocal ? 'http://localhost:8002/api' : 'https://api.inap.ai/api';
        this.inventoryData = []; // Date-based inventory
        this.roomTypes = [];
        this.managerContext = null;
        this.currentHotel = null;
        const __now = new Date();
        const __pad = (n) => (n < 10 ? '0' + n : '' + n);
        const __todayLocal = `${__now.getFullYear()}-${__pad(__now.getMonth() + 1)}-${__pad(__now.getDate())}`;
        this.selectedDateRange = {
            start: __todayLocal, // Today (local)
            months: 3
        };
        this.pendingRoomType = '';
        this.pendingScroll = null;
        try {
            const savedView = sessionStorage.getItem('leon_inventory_view');
            if (savedView) {
                sessionStorage.removeItem('leon_inventory_view');
                const view = JSON.parse(savedView);
                if (view.start) this.selectedDateRange.start = view.start;
                if (view.months) this.selectedDateRange.months = view.months;
                this.pendingRoomType = view.roomType || '';
                if (typeof view.scrollY === 'number') this.pendingScroll = view.scrollY;
            }
        } catch (_) {}
        
        this.initialize();
    }
    
    getCurrentHotelId() {
        try {
            const cookies = document.cookie.split(';').map(c => c.trim());
            const kv = cookies.find(c => c.startsWith('leon_hotel='));
            if (!kv) return '';
            const val = decodeURIComponent(kv.split('=')[1] || '');
            if (!val) return '';
            return val.toLowerCase().replace(/\s+/g, '_');
        } catch (_) {
            return '';
        }
    }

    async refreshInventoryFromApi() {
        if (this._refreshing) return this._refreshing;
        this._refreshing = this._refreshInventoryFromApi();
        try {
            return await this._refreshing;
        } finally {
            this._refreshing = null;
        }
    }

    async _refreshInventoryFromApi() {
        try {
            const startDate = new Date(this.selectedDateRange.start + 'T00:00:00');
            const endDate = new Date(startDate.getFullYear(), startDate.getMonth(), startDate.getDate());
            endDate.setMonth(endDate.getMonth() + (this.selectedDateRange.months || 3));
            const fmt = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
            const startISO = fmt(startDate);
            const endISO = fmt(endDate);
            const url = `${this.apiBase}/inventory?start_date=${encodeURIComponent(startISO)}&end_date=${encodeURIComponent(endISO)}`;
            const resp = await fetch(url, { credentials: 'include' });
            if (!resp.ok) throw new Error('fetch inventory failed');
            const data = await resp.json();
            this.roomTypes = (data.room_types || []).map(item => ({
                room_type: item.room_type,
                pricing: item.pricing || [],
                quantities: item.quantities || []
            }));
            // Rebuild inventoryData from pricing + quantities
            const inventory = [];
            for (const roomType of this.roomTypes) {
                const pricing = roomType.pricing || [];
                const quantities = roomType.quantities || [];
                for (let d = new Date(startDate.getFullYear(), startDate.getMonth(), startDate.getDate()); d <= endDate; d.setDate(d.getDate() + 1)) {
                    const dateStr = `${d.getFullYear()}-${(d.getMonth()+1).toString().padStart(2,'0')}-${d.getDate().toString().padStart(2,'0')}`;
                    const priceObj = pricing.find(p => String(p.date).slice(0, 10) === dateStr) || {};
                    const qtyObj = quantities.find(q => String(q.date).slice(0, 10) === dateStr) || {};
                    inventory.push({
                        date: dateStr,
                        room_type: roomType.room_type,
                        available_quantity: typeof qtyObj.active_inventory === 'number' ? qtyObj.active_inventory : 0,
                        room_count: typeof qtyObj.total_inventory === 'number' ? qtyObj.total_inventory : 0,
                        price: priceObj.base_price != null ? priceObj.base_price : (priceObj.price != null ? priceObj.price : null),
                        dynamic_price: priceObj.dynamic_price != null ? priceObj.dynamic_price : null,
                        push_dynamic: !!priceObj.push_dynamic,
                        pricing_name: priceObj.pricing_name || '',
                        currency: priceObj.currency || ''
                    });
                }
            }
            this.inventoryData = inventory;
            this._loadedAt = Date.now();
            this.updateSummaryCards();
            this.populateRoomTypeSelect();
            this.renderCalendarView();
        } catch (e) {
            console.error('refreshInventoryFromApi error:', e);
            this.showEmptyInventoryState();
        }
    }

    async initialize() {
        // No need to load manager context
        await this.refreshInventoryFromApi();
        await this.loadDynamicPricings();
        await this.loadKiteSetting();
        // Force render even if data empty; shows calendar scaffolding
        this.renderCalendarView();
        this.setupDateRangeControls();
        this.applyPendingScroll();
        // Auto-refresh hooks for orchestrator/external updates
        window.addEventListener('focus', () => {
            if (Date.now() - (this._loadedAt || 0) < 4000) return;
            this.refreshInventoryFromApi();
        });
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState !== 'visible') return;
            if (Date.now() - (this._loadedAt || 0) < 4000) return;
            this.refreshInventoryFromApi();
        });
        // Optional custom hook other components can dispatch
        window.addEventListener('leon:inventory-refresh', () => {
            this.refreshInventoryFromApi();
        });
    }
    
    async loadRoomTypes() {
        try {
            // No hotel context needed
            const response = await fetch(`${this.apiBase}/inventory`, { credentials: 'include' });
            if (response.ok) {
                const data = await response.json();
                this.roomTypes = (data.room_types || []).map(item => ({
                    room_type: item.room_type,
                    pricing: item.pricing || [],
                    quantities: item.quantities || []
                }));
                console.log(`✅ Loaded ${this.roomTypes.length} room types`);
            } else {
                console.error('Failed to load room types');
                this.roomTypes = [];
            }
        } catch (error) {
            console.error('Error loading room types:', error);
            this.roomTypes = [];
        }
    }

    populateRoomTypeSelect() {
        try {
            const sel = document.getElementById('room-type-select');
            if (!sel) return;
            const current = sel.value || this.pendingRoomType || '';
            sel.innerHTML = '';
            // placeholder
            const placeholder = document.createElement('option');
            placeholder.value = '';
            placeholder.textContent = 'Select room type';
            placeholder.disabled = false;
            sel.appendChild(placeholder);
            const allTypes = this.roomTypes.map(rt => rt.room_type).filter(Boolean);
            const opts = [...Array.from(new Set(allTypes))];
            opts.forEach(rt => {
                const opt = document.createElement('option');
                opt.value = rt;
                opt.textContent = rt;
                sel.appendChild(opt);
            });
            sel.value = (opts.includes(current) && current) ? current : '';
            sel.addEventListener('change', () => {
                this.renderCalendarView();
            });
            // Render once after populating options
            this.renderCalendarView();
        } catch (_) {}
    }
    
    updateHotelContextDisplay(errorMessage = null) {
        const contextElement = document.getElementById('hotel-context-info');
        if (contextElement) {
            if (errorMessage) {
                contextElement.innerHTML = `<i class="fas fa-exclamation-triangle mr-1"></i>${errorMessage}`;
                contextElement.className = 'mt-2 text-sm text-red-600 bg-red-50 px-3 py-1 rounded-full inline-block';
            } else if (this.managerContext) {
                contextElement.innerHTML = `<i class="fas fa-calendar mr-1"></i>Managing inventory for: ${this.currentHotel} (Manager: ${this.managerContext.manager_name})`;
                contextElement.className = 'mt-2 text-sm text-green-600 bg-green-50 px-3 py-1 rounded-full inline-block';
            }
        }
    }
    
    async loadInventoryData() {
        await this.refreshInventoryFromApi();
        this.renderInventoryTable();
    }

    renderCalendarView() {
        const container = document.getElementById('inventory-calendar');
        if (!container) return;
        const sel = document.getElementById('room-type-select');
        const filterType = sel && sel.value ? sel.value : null;
        let start = new Date(this.selectedDateRange.start + 'T00:00:00');
        let end = new Date(new Date(this.selectedDateRange.start + 'T00:00:00').setMonth(new Date(this.selectedDateRange.start + 'T00:00:00').getMonth() + (this.selectedDateRange.months || 3)));
        if (!(start instanceof Date) || isNaN(start.getTime())) {
            start = new Date();
        }
        if (!(end instanceof Date) || isNaN(end.getTime()) || end < start) {
            end = new Date(start.getTime() + 90 * 24 * 60 * 60 * 1000);
        }
        // Clamp to max 12 months
        const maxMonths = 12;
        const capLater = new Date(start);
        capLater.setMonth(capLater.getMonth() + maxMonths);
        const viewEnd = end > capLater ? capLater : end;

        if (!filterType) {
            container.innerHTML = '<div class="text-sm text-gray-500">Select a room type</div>';
            return;
        }
        // Build a map: date -> [{room_type, price, currency, qty}]
        const byDate = {};
        this.inventoryData.forEach(it => {
            if (it.room_type !== filterType) return;
            if (!byDate[it.date]) byDate[it.date] = [];
            byDate[it.date].push({
                room_type: it.room_type,
                price: it.price,
                dynamic_price: it.dynamic_price,
                push_dynamic: !!it.push_dynamic,
                pricing_name: it.pricing_name || '',
                currency: it.currency || '',
                qty: it.available_quantity || 0,
                room_count: it.room_count || 0
            });
        });

        // Generate calendar months between start and viewEnd
        const months = [];
        const iter = new Date(start.getFullYear(), start.getMonth(), 1);
        const endIter = new Date(viewEnd.getFullYear(), viewEnd.getMonth(), 1);
        const desiredMonths = Math.min((this.selectedDateRange.months || 3), 12);
        while (iter <= endIter && months.length < desiredMonths) {
            months.push(new Date(iter));
            iter.setMonth(iter.getMonth() + 1);
        }
        if (months.length === 0) {
            months.push(new Date(start.getFullYear(), start.getMonth(), 1));
        }

        const parts = [];
        months.forEach(monthStart => {
            const year = monthStart.getFullYear();
            const month = monthStart.getMonth();
            const monthName = monthStart.toLocaleString('en-US', { month: 'long', year: 'numeric' });
            // First day of month weekday
            const firstWeekday = new Date(year, month, 1).getDay(); // 0=Sun
            const daysInMonth = new Date(year, month + 1, 0).getDate();
            parts.push(`
                <div class="border rounded-lg p-2 bg-gray-50">
                    <div class="flex items-center justify-between mb-1 text-base">
                        <h4 class="font-bold text-gray-900">${monthName}</h4>
                    </div>
                    <div class="grid grid-cols-7 gap-1 text-xs font-semibold">
                        <div class="text-gray-600">Sun</div>
                        <div class="text-gray-600">Mon</div>
                        <div class="text-gray-600">Tue</div>
                        <div class="text-gray-600">Wed</div>
                        <div class="text-gray-600">Thu</div>
                        <div class="text-gray-600">Fri</div>
                        <div class="text-gray-600">Sat</div>
                        ${'<div></div>'.repeat(firstWeekday)}
            `);
            for (let day = 1; day <= daysInMonth; day++) {
                const d = new Date(year, month, day);
                const iso = `${d.getFullYear()}-${(d.getMonth()+1).toString().padStart(2,'0')}-${d.getDate().toString().padStart(2,'0')}`;
                const entries = byDate[iso] || [];
                const en0 = entries[0] || null;
                const money = (n) => (n != null && !Number.isNaN(Number(n))) ? String(Math.round(Number(n))) : '-';
                const dynNum = en0 && en0.dynamic_price != null ? Number(en0.dynamic_price) : null;
                const baseNum = en0 && en0.price != null ? Number(en0.price) : null;
                const dynStr = money(dynNum);
                const baseStr = money(baseNum);
                const baseForEdit = en0 ? en0.price : 0;
                const openArgs = en0 ? `${en0.qty}, ${baseForEdit}` : `0, 0`;
                const pushOn = en0 && en0.push_dynamic;
                const blocked = !!(en0 && Number(en0.room_count) > 0 && Number(en0.qty) === 0);
                const cellFill = blocked ? 'bg-red-200 border-red-400' : 'hover:bg-gray-50';
                const dynClass = pushOn ? 'bg-blue-100 text-blue-800 border-2 border-blue-600' : 'bg-blue-100 text-blue-800 border border-blue-200';
                const baseClass = pushOn ? 'bg-yellow-100 text-yellow-800 border border-yellow-200' : 'bg-yellow-100 text-yellow-800 border-2 border-yellow-600';
                const chip = 'px-1 py-1 rounded font-bold text-sm leading-5 cursor-pointer transition-colors duration-75';
                const pricingAttr = en0 && en0.pricing_name ? ` data-pricing="${this.attrText(en0.pricing_name)}"` : '';
                parts.push(`
                    <div data-stay="${iso}" data-blocked="${blocked ? '1' : '0'}"${pricingAttr} class="border rounded p-1 text-left cursor-pointer flex flex-col gap-1 ${cellFill}" onclick="openSingleUpdateModal('${iso}', '${filterType || ''}', ${openArgs})">
                        <div class="text-gray-700 font-extrabold text-lg leading-none">${day}</div>
                        <div data-price="dynamic" class="${chip} ${dynClass}" onclick="event.stopPropagation(); setPushDynamic('${iso}', true)" title="Send the dynamic price">D ${dynStr}</div>
                        <div data-price="base" class="${chip} ${baseClass}" onclick="event.stopPropagation(); setPushDynamic('${iso}', false)" title="Send the base price">B ${baseStr}</div>
                    </div>
                `);
            }
            parts.push('</div></div>');
        });
        container.innerHTML = parts.join('');
    }
    
    buildDateBasedInventory(pricingData) {
        // Convert pricing data to date-based inventory structure
        const inventory = [];
        const startDate = new Date(this.selectedDateRange.start + 'T00:00:00');
        const endDate = new Date(new Date(this.selectedDateRange.start + 'T00:00:00').setMonth(new Date(this.selectedDateRange.start + 'T00:00:00').getMonth() + (this.selectedDateRange.months || 3)));
        
        // Generate each date in the range
        for (let d = new Date(startDate.getFullYear(), startDate.getMonth(), startDate.getDate()); d <= endDate; d.setDate(d.getDate() + 1)) {
            const dateStr = `${d.getFullYear()}-${(d.getMonth()+1).toString().padStart(2,'0')}-${d.getDate().toString().padStart(2,'0')}`;
            
            // For each room type, get pricing and assume full capacity available
            this.roomTypes.forEach(roomType => {
                const pricing = pricingData.find(p => p.room_type === roomType.room_type);
                
                inventory.push({
                    date: dateStr,
                    room_type: roomType.room_type,
                    available_quantity: roomType.capacity, // Start with full capacity
                    price: pricing ? pricing.sample_price : 0.00,
                    currency: ''
                });
            });
        }
        
        return inventory;
    }
    
    setupDateRangeControls() {
        // Set up date range selector
        const startDateInput = document.getElementById('inventory-start-date');
        const endDateInput = null;
        const rangeSelect = document.getElementById('inventory-range');
        const refreshButton = null;
        
        if (startDateInput) {
            startDateInput.value = this.selectedDateRange.start;
            startDateInput.addEventListener('change', (e) => {
                this.selectedDateRange.start = e.target.value;
                this.loadInventoryData();
            });
        }
        
        if (rangeSelect) {
            rangeSelect.value = String(this.selectedDateRange.months || 3);
            rangeSelect.addEventListener('change', (e) => {
                const m = parseInt(e.target.value, 10);
                this.selectedDateRange.months = isNaN(m) ? 3 : m;
                this.loadInventoryData();
            });
        }
        
        // Auto refresh on any selection change
        const selRt = document.getElementById('room-type-select');
        if (selRt) {
            selRt.addEventListener('change', () => this.loadInventoryData());
        }
    }
    
    renderInventoryTable() {
        const tableBody = document.getElementById('inventory-table');
        if (!tableBody) {
            // Calendar-only view active; skip table rendering
            return;
        }
        
        if (this.inventoryData.length === 0) {
            this.showEmptyInventoryState();
            return;
        }
        
        // Group by date for easier display
        const groupedByDate = {};
        this.inventoryData.forEach(item => {
            if (!groupedByDate[item.date]) {
                groupedByDate[item.date] = [];
            }
            groupedByDate[item.date].push(item);
        });
        
        let tableHTML = '';
        
        Object.keys(groupedByDate).sort().forEach(date => {
            const dateItems = groupedByDate[date];
            const formattedDate = new Date(date).toLocaleDateString('en-US', {
                weekday: 'short',
                month: 'short', 
                day: 'numeric'
            });
            
            // Date header row
            tableHTML += `
                <tr class="bg-gray-50 border-b-2">
                    <td colspan="6" class="py-3 px-4 font-semibold text-gray-900">
                        📅 ${formattedDate} (${date})
                    </td>
                </tr>
            `;
            
            // Room type rows for this date
            dateItems.forEach(item => {
                tableHTML += `
                    <tr class="border-b hover:bg-gray-50">
                        <td class="py-3 px-4 pl-8">
                            <div class="font-medium text-gray-900">${item.room_type}</div>
                        </td>
                        <td class="py-3 px-4">
                            <div class="font-medium text-gray-900">${item.available_quantity}</div>
                            <div class="text-sm text-gray-500">rooms available</div>
                        </td>
                        <td class="py-3 px-4">
                            <div class="font-medium text-gray-900">${item.currency} ${item.price.toFixed(2)}</div>
                            <div class="text-sm text-gray-500">per night</div>
                        </td>
                        <td class="py-3 px-4">
                            <span class="status-badge ${item.price > 0 ? 'bg-green-100 text-green-800' : 'bg-yellow-100 text-yellow-800'}">
                                ${item.price > 0 ? 'Priced' : 'No Price'}
                            </span>
                        </td>
                        <td class="py-3 px-4">
                            <div class="flex space-x-2">
                                <button onclick="editInventoryItem('${item.date}', '${item.room_type}', ${item.available_quantity}, ${item.price})" 
                                        class="text-blue-600 hover:text-blue-800 text-sm">
                                    <i class="fas fa-edit"></i> Edit
                                </button>
                                <button onclick="copyToOtherDates('${item.date}', '${item.room_type}')" 
                                        class="text-green-600 hover:text-green-800 text-sm">
                                    <i class="fas fa-copy"></i> Copy
                                </button>
                            </div>
                        </td>
                    </tr>
                `;
            });
        });
        
        tableBody.innerHTML = tableHTML;
    }
    
    showEmptyInventoryState() {
        const hotelName = this.currentHotel || 'your hotel';
        const tableBody = document.getElementById('inventory-table');
        if (tableBody) {
            tableBody.innerHTML = `
                <tr>
                    <td colspan="6" class="text-center py-8 text-gray-500">
                        <i class="fas fa-calendar-times text-3xl mb-2"></i>
                        <p>No inventory data found for ${hotelName}</p>
                        <p class="text-sm">Create room types first, then set quantities and prices for specific dates</p>
                    </td>
                </tr>
            `;
            return;
        }
        const cal = document.getElementById('inventory-calendar');
        if (cal) {
            cal.innerHTML = `<div class="text-center py-8 text-gray-500"><i class=\"fas fa-calendar-times text-3xl mb-2\"></i><p>No inventory data found for ${hotelName}</p></div>`;
        }
    }
    
    updateSummaryCards() {
        const totalDays = this.inventoryData.length / this.roomTypes.length;
        const totalRoomNights = this.inventoryData.reduce((sum, item) => sum + item.available_quantity, 0);
        const avgPrice = this.inventoryData.length > 0 
            ? (this.inventoryData.reduce((sum, item) => sum + item.price, 0) / this.inventoryData.length).toFixed(2)
            : '0.00';
        const pricedItems = this.inventoryData.filter(item => item.price > 0).length;
        
        // Update UI
        document.getElementById('total-days') && (document.getElementById('total-days').textContent = totalDays);
        document.getElementById('total-room-nights') && (document.getElementById('total-room-nights').textContent = totalRoomNights);
        document.getElementById('avg-price') && (document.getElementById('avg-price').textContent = `${avgPrice}`);
        document.getElementById('priced-items') && (document.getElementById('priced-items').textContent = `${pricedItems}/${this.inventoryData.length}`);
    }
    
    attrText(value) {
        return String(value || '').split('&').join('&amp;').split('"').join('&quot;').split('<').join('&lt;');
    }

    async loadDynamicPricings() {
        try {
            const resp = await fetch(`${this.apiBase}/dynamic_pricing`, { credentials: 'include' });
            const data = await resp.json();
            this.dynamicPricings = (resp.ok && !data.error) ? (data.pricings || []) : [];
        } catch (e) {
            this.dynamicPricings = [];
        }
    }

    async loadKiteSetting() {
        const enabled = document.getElementById('kite-enabled');
        const sensitivity = document.getElementById('kite-sensitivity');
        const lower = document.getElementById('kite-lower');
        const upper = document.getElementById('kite-upper');
        if (!enabled) return;
        try {
            const resp = await fetch(`${this.apiBase}/kite`, { credentials: 'include' });
            const data = await resp.json();
            enabled.checked = !!data.enabled;
            if (sensitivity) sensitivity.value = data.sensitivity != null ? data.sensitivity : '';
            if (lower) lower.value = data.lower_limit != null ? data.lower_limit : '';
            if (upper) upper.value = data.upper_limit != null ? data.upper_limit : '';
        } catch (e) {
            console.error('loadKiteSetting', e);
        }
    }

    async saveKiteSetting() {
        const enabled = document.getElementById('kite-enabled').checked;
        const sensitivityRaw = document.getElementById('kite-sensitivity').value;
        const lowerRaw = document.getElementById('kite-lower').value;
        const upperRaw = document.getElementById('kite-upper').value;
        const body = {
            enabled: enabled,
            sensitivity: sensitivityRaw === '' ? null : Number(sensitivityRaw),
            lower_limit: lowerRaw === '' ? null : Number(lowerRaw),
            upper_limit: upperRaw === '' ? null : Number(upperRaw)
        };
        try {
            const resp = await fetch(`${this.apiBase}/kite`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify(body)
            });
            const data = await resp.json();
            if (!resp.ok || data.error) {
                this.showNotification('error', data.error || 'Could not save dynamic pricing');
                return;
            }
            this.showNotification('success', 'Dynamic pricing saved');
            await this.refreshInventoryFromApi();
            await this.loadKiteSetting();
        } catch (e) {
            this.showNotification('error', 'Could not save dynamic pricing');
        }
    }

    paintPush(stayDate, pushDynamic) {
        const cell = document.querySelector(`[data-stay="${stayDate}"]`);
        if (!cell) {
            this.renderCalendarView();
            return;
        }
        const chip = 'px-1 py-1 rounded font-bold text-sm leading-5 cursor-pointer transition-colors duration-75';
        const dyn = cell.querySelector('[data-price="dynamic"]');
        const base = cell.querySelector('[data-price="base"]');
        if (dyn) dyn.className = chip + ' ' + (pushDynamic ? 'bg-blue-100 text-blue-800 border-2 border-blue-600' : 'bg-blue-100 text-blue-800 border border-blue-200');
        if (base) base.className = chip + ' ' + (pushDynamic ? 'bg-yellow-100 text-yellow-800 border border-yellow-200' : 'bg-yellow-100 text-yellow-800 border-2 border-yellow-600');
    }

    async setPushDynamic(stayDate, pushDynamic, silent, pricingName) {
        let name = pricingName;
        if (name == null) {
            const cell = document.querySelector(`[data-stay="${stayDate}"]`);
            name = cell ? (cell.getAttribute('data-pricing') || '') : '';
        }
        const prior = [];
        this.inventoryData.forEach(item => {
            if (item.date !== stayDate) return;
            prior.push({ item: item, push: item.push_dynamic, pricing_name: item.pricing_name || '' });
            item.push_dynamic = !!pushDynamic;
            item.pricing_name = name || '';
        });
        this.paintPush(stayDate, !!pushDynamic);
        try {
            const resp = await fetch(`${this.apiBase}/kite/day`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ stay_date: stayDate, push_dynamic: pushDynamic, pricing_name: name || '' })
            });
            const data = await resp.json();
            if (!resp.ok || data.error) {
                prior.forEach(row => {
                    row.item.push_dynamic = row.push;
                    row.item.pricing_name = row.pricing_name;
                });
                this.paintPush(stayDate, prior.length ? !!prior[0].push : false);
                this.showNotification('error', data.error || 'Could not save the day');
                return false;
            }
            const cell = document.querySelector(`[data-stay="${stayDate}"]`);
            if (cell) {
                if (name) cell.setAttribute('data-pricing', name);
                else cell.removeAttribute('data-pricing');
            }
            if (silent) return true;
            const sent = data.aiosell && !data.aiosell.error && !data.aiosell.skipped;
            this.showNotification(sent ? 'success' : 'error', sent ? 'Rate sent to Aiosell' : (data.aiosell && data.aiosell.skipped ? 'Day saved' : 'Day saved. Aiosell did not accept the rate'));
            return true;
        } catch (e) {
            prior.forEach(row => {
                row.item.push_dynamic = row.push;
                row.item.pricing_name = row.pricing_name;
            });
            this.paintPush(stayDate, prior.length ? !!prior[0].push : false);
            this.showNotification('error', 'Could not save the day');
            return false;
        }
    }

    async updateInventoryItem(date, roomType, quantity, price) {
        try {
            // No hotel context needed
            console.log(`Updating inventory for ${roomType} on ${date} at ${price}`);
            const hotelId = this.getCurrentHotelId();
            const response = await fetch(`${this.apiBase}/hotels/${hotelId}/inventory/update-date`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                credentials: 'include',
                body: JSON.stringify({
                    room_type: roomType,
                    date: date,
                    quantity: quantity,
                    price: price
                })
            });
            
            let result = null; let bodyText = '';
            try {
                const ct = response.headers.get('content-type') || '';
                if (ct.indexOf('application/json') !== -1) {
                    result = await response.json();
                } else {
                    bodyText = await response.text();
                }
            } catch(_) {}

            const ok = response.ok || response.status === 204;
            const isSuccess = ok || (result && (result.success === true || typeof result.result !== 'undefined'));

            if (isSuccess) {
                console.log('✅ Inventory updated successfully');
                this.showNotification('success', 'Inventory updated successfully');
                return true;
            } else {
                const errMsg = (result && result.error) || bodyText || `HTTP ${response.status}`;
                console.error('Failed to update inventory:', errMsg);
                this.showNotification('error', `Failed to update inventory: ${errMsg}`);
                return false;
            }
        } catch (error) {
            console.error('Error updating inventory:', error);
            this.showNotification('error', 'Error updating inventory. Please try again.');
            return false;
        }
    }

    async updateInventoryDates(dates, roomType, quantity, price) {
        try {
            const hotelId = this.getCurrentHotelId();
            const response = await fetch(`${this.apiBase}/hotels/${hotelId}/inventory/update-dates`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    room_type: roomType,
                    dates: dates,
                    quantity: quantity,
                    price: price
                })
            });
            let result = null;
            try { result = await response.json(); } catch (_) {}
            if (response.ok && result && result.success) {
                this.showNotification('success', 'Inventory updated successfully');
                return true;
            }
            this.showNotification('error', (result && result.error) || 'Failed to update inventory');
            return false;
        } catch (error) {
            this.showNotification('error', 'Error updating inventory. Please try again.');
            return false;
        }
    }
    
    showNotification(type, message) {
        const notification = document.createElement('div');
        notification.className = `fixed top-4 right-4 z-50 p-4 rounded-lg shadow-lg transition-all duration-300 ${
            type === 'success' ? 'bg-green-500 text-white' : 'bg-red-500 text-white'
        }`;
        notification.innerHTML = `
            <div class="flex items-center">
                <i class="fas fa-${type === 'success' ? 'check-circle' : 'exclamation-circle'} mr-2"></i>
                <span>${message}</span>
                <button onclick="this.parentElement.parentElement.remove()" class="ml-4 text-white hover:text-gray-200">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        `;
        
        document.body.appendChild(notification);
        
        setTimeout(() => {
            if (notification.parentElement) {
                notification.remove();
            }
        }, 5000);
    }

    applyPendingScroll() {
        if (this.pendingScroll == null) return;
        const y = this.pendingScroll;
        this.pendingScroll = null;
        requestAnimationFrame(() => window.scrollTo(0, y));
    }

    reloadInventoryPage() {
        try {
            const startEl = document.getElementById('inventory-start-date');
            const rangeEl = document.getElementById('inventory-range');
            const roomEl = document.getElementById('room-type-select');
            sessionStorage.setItem('leon_inventory_view', JSON.stringify({
                start: startEl && startEl.value ? startEl.value : this.selectedDateRange.start,
                months: rangeEl && rangeEl.value ? parseInt(rangeEl.value, 10) : (this.selectedDateRange.months || 3),
                roomType: roomEl ? roomEl.value : (this.pendingRoomType || ''),
                scrollY: window.scrollY || 0
            }));
        } catch (_) {}
        window.location.reload();
    }
}

// Global utility functions for the new date-based inventory
window.editInventoryItem = async function(date, roomType, quantity, price) {
    const newPrice = prompt(`Edit price for ${roomType} on ${date}:`, price);
    if (newPrice !== null && window.inventoryManager) {
        const saved = await window.inventoryManager.updateInventoryItem(date, roomType, null, parseFloat(newPrice));
        if (saved) window.inventoryManager.reloadInventoryPage();
    }
};

window.copyToOtherDates = async function(sourceDate, roomType) {
    const manager = window.inventoryManager;
    if (!manager) return;
    const source = (manager.inventoryData || []).find(item => item.date === sourceDate && item.room_type === roomType);
    if (!source) { alert('No settings on that date'); return; }
    const until = window.prompt(`Copy ${roomType} from ${sourceDate} through (YYYY-MM-DD)`, sourceDate);
    if (!until) return;
    const start = new Date(sourceDate + 'T00:00:00');
    const end = new Date(until + 'T00:00:00');
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end < start) { alert('Choose an end date on or after the source date'); return; }
    const cursor = new Date(start);
    cursor.setDate(cursor.getDate() + 1);
    while (cursor <= end) {
        const iso = cursor.getFullYear() + '-' + String(cursor.getMonth() + 1).padStart(2, '0') + '-' + String(cursor.getDate()).padStart(2, '0');
        await manager.updateInventoryItem(iso, roomType, null, source.price);
        cursor.setDate(cursor.getDate() + 1);
    }
    manager.reloadInventoryPage();
}; 