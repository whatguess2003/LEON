const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
const apiUrl = isLocal ? 'http://localhost:8002/api' : 'https://api.inap.ai/api';

class GuestComponent {
    constructor() {
        this.apiUrl = apiUrl;
        this.currentHotel = '';
        this.record = null;
        this.initialize();
    }
    
    async initialize() {
        await this.loadManagerContext();
        document.getElementById('guest-search-btn').addEventListener('click', () => this.search());
        document.getElementById('guest-search').addEventListener('keydown', (event) => {
            if (event.key === 'Enter') this.search();
        });
        document.getElementById('guest-add').addEventListener('click', () => {
            document.getElementById('guest-add-form').classList.toggle('hidden');
        });
        document.getElementById('add-save').addEventListener('click', () => this.createGuest());
        await this.search();
    }
    
    async loadManagerContext() {
        const box = document.getElementById('hotel-context-info');
        const hotel = String(window.leonHotel ? window.leonHotel() : '').toUpperCase();
        if (hotel) {
            this.currentHotel = hotel;
            box.textContent = this.currentHotel;
            box.className = 'mt-2 text-sm text-green-600 bg-green-50 px-3 py-1 rounded-full inline-block';
            return;
        }
        const params = new URLSearchParams(window.location.search);
        this.currentHotel = String(params.get('hotel_name') || params.get('hotel') || '').toUpperCase();
        box.textContent = this.currentHotel || 'Sign in to choose a hotel';
    }

    async search() {
        const list = document.getElementById('guest-results');
        if (!this.currentHotel) {
            list.textContent = 'Sign in to search guests.';
            return;
        }
        const q = document.getElementById('guest-search').value || '';
        const response = await fetch(`${this.apiUrl}/guests?hotel_name=${encodeURIComponent(this.currentHotel)}&q=${encodeURIComponent(q)}`, { credentials: 'include' });
        const data = await response.json();
        if (!response.ok || data.error) {
            list.textContent = data.error || 'Search failed';
            return;
        }
        const guests = data.guests || [];
        if (!guests.length) {
            list.textContent = 'No guests found';
            return;
        }
        list.innerHTML = guests.map(guest => `
            <button type="button" data-phone="${this.esc(guest.guest_phone)}" class="guest-pick w-full text-left border rounded px-3 py-2 hover:bg-gray-50">
                <div class="font-medium">${this.esc(guest.guest_name)}</div>
                <div class="text-sm text-gray-500">${this.esc(guest.guest_phone)} ${this.esc(guest.email)}</div>
            </button>
        `).join('');
        list.querySelectorAll('.guest-pick').forEach(button => {
            button.addEventListener('click', () => this.openGuest(button.dataset.phone));
        });
    }

    async openGuest(phone) {
        const response = await fetch(`${this.apiUrl}/guests/record?hotel_name=${encodeURIComponent(this.currentHotel)}&guest_phone=${encodeURIComponent(phone)}`, { credentials: 'include' });
                const data = await response.json();
        if (!response.ok || data.error) {
            document.getElementById('guest-detail').textContent = data.error || 'Could not load the guest';
            return;
        }
        this.record = data;
        this.renderRecord();
    }

    renderRecord() {
        const guest = this.record.guest || {};
        const detail = document.getElementById('guest-detail');
        const counts = this.bookingCounts();
        detail.innerHTML = `
            <h2 class="text-xl font-semibold mb-3">${this.esc(guest.guest_name)} <span class="text-sm text-gray-500">${this.esc(guest.guest_phone)}</span></h2>
            <div class="flex flex-wrap border-b mb-4">
                <button type="button" id="tab-summary" class="px-3 py-2 text-sm border-b-2 border-blue-600 text-blue-600">Summary</button>
                <button type="button" id="tab-messages" class="px-3 py-2 text-sm text-gray-600">Messages</button>
                <button type="button" id="tab-biography" class="px-3 py-2 text-sm text-gray-600">Biography</button>
                <button type="button" id="tab-bookings" class="px-3 py-2 text-sm text-gray-600">Bookings</button>
            </div>
            <div id="pane-summary">
                ${this.bookingSection('In house', this.record.current)}
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4">
                    ${this.summaryCard('Confirmed', counts.confirmed)}
                    ${this.summaryCard('Departed', counts.departed)}
                    ${this.summaryCard('Cancelled', counts.cancelled)}
                    ${this.summaryCard('Total spent', (this.record.currency || '') + ' ' + counts.spent.toFixed(2))}
                </div>
                <div class="mt-4 border rounded p-3">
                    <div class="flex items-center justify-between mb-2">
                        <h3 class="text-sm font-semibold">AI summary</h3>
                        <button id="ai-run" class="bg-blue-600 text-white px-3 py-1 rounded text-sm">Summarize</button>
                    </div>
                    <div id="ai-summary" class="text-sm whitespace-pre-wrap">${this.esc(guest.ai_summary) || 'No summary yet.'}</div>
                    <p id="ai-when" class="text-xs text-gray-500 mt-2">${guest.ai_summary_at ? 'Saved ' + this.esc(guest.ai_summary_at) : ''}</p>
                    <p id="ai-status" class="text-sm mt-2"></p>
                </div>
            </div>
            <div id="pane-messages" class="hidden">
                <p class="text-sm text-gray-600 mb-3">Email, WhatsApp, and voice recordings for this guest are kept here.</p>
                <div class="flex space-x-2 mb-3">
                    <button type="button" data-channel="" class="msg-filter px-3 py-1 rounded text-sm bg-blue-600 text-white">All</button>
                    <button type="button" data-channel="email" class="msg-filter px-3 py-1 rounded text-sm bg-gray-100 text-gray-700">Email</button>
                    <button type="button" data-channel="whatsapp" class="msg-filter px-3 py-1 rounded text-sm bg-gray-100 text-gray-700">WhatsApp</button>
                    <button type="button" data-channel="voice" class="msg-filter px-3 py-1 rounded text-sm bg-gray-100 text-gray-700">Voice</button>
                </div>
                <div id="msg-list" class="space-y-2"></div>
                <div id="wa-compose" class="hidden mt-3 border rounded p-3">
                    <label class="text-sm text-gray-700 block" for="wa-template-pick">WhatsApp template</label>
                    <select id="wa-template-pick" class="border rounded w-full px-2 py-1 mt-1 text-sm"></select>
                    <p id="wa-template-sentence" class="text-sm text-gray-900 mt-2 whitespace-pre-wrap"></p>
                    <div class="flex justify-end mt-2">
                        <button type="button" id="wa-template" class="bg-green-600 text-white px-3 py-1 rounded text-sm">Send template</button>
                    </div>
                    <p id="wa-window" class="text-sm mt-3"></p>
                    <label class="text-sm text-gray-700 mt-3 block" for="wa-body">WhatsApp message</label>
                    <textarea id="wa-body" rows="3" class="border rounded w-full p-2 mt-1" placeholder="Write a message after the guest replies"></textarea>
                    <label class="text-sm text-gray-700 mt-2 block" for="wa-upload">Upload</label>
                    <div class="flex items-center mt-1">
                        <input id="wa-file" type="file" accept="image/*,audio/*,video/*,application/pdf" class="hidden">
                        <button type="button" id="wa-upload" class="bg-gray-100 text-gray-700 px-3 py-1 rounded text-sm">Choose file</button>
                        <span id="wa-file-name" class="text-sm text-gray-600 ml-2"></span>
                    </div>
                    <div class="flex justify-end mt-2">
                        <button type="button" id="wa-send" class="bg-blue-600 text-white px-3 py-1 rounded text-sm">Send</button>
                    </div>
                    <p id="wa-status" class="text-sm mt-2"></p>
                </div>
            </div>
            <div id="pane-biography" class="hidden">
                <div class="flex justify-end mb-3">
                    <button id="g-edit" class="bg-blue-600 text-white px-3 py-1 rounded text-sm">Edit</button>
                </div>
                <div id="g-view" class="grid grid-cols-1 md:grid-cols-2 gap-2 text-sm mb-4">
                    ${this.fact('Email', guest.email)}
                    ${this.fact('Country', guest.country || guest.nationality)}
                    ${this.fact('Date of birth', guest.date_of_birth)}
                    ${this.fact('Gender', guest.gender)}
                    ${this.fact('Company', guest.company)}
                    ${this.fact('Address', guest.address)}
                    ${this.fact('ID type', guest.identity_type)}
                    ${this.fact('ID number', guest.identity_number)}
                    ${this.fact('Special request', guest.special_request)}
                    ${this.fact('VIP', guest.vip ? 'Yes' : 'No')}
                </div>
                <div id="g-form" class="hidden">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <label class="text-sm">Name<input id="g-name" class="border rounded px-2 py-1 w-full" value="${this.esc(guest.guest_name)}"></label>
                        <label class="text-sm">Email<input id="g-email" class="border rounded px-2 py-1 w-full" value="${this.esc(guest.email)}"></label>
                        <label class="text-sm">Country<select id="g-country" class="border rounded px-2 py-1 w-full"></select></label>
                        <label class="text-sm">Date of birth<input id="g-dob" type="date" class="border rounded px-2 py-1 w-full" value="${this.esc(guest.date_of_birth)}"></label>
                        <label class="text-sm">Gender<select id="g-gender" class="border rounded px-2 py-1 w-full"></select></label>
                        <label class="text-sm">Company<input id="g-company" class="border rounded px-2 py-1 w-full" value="${this.esc(guest.company)}"></label>
                        <label class="text-sm md:col-span-2">Address<input id="g-address" class="border rounded px-2 py-1 w-full" value="${this.esc(guest.address)}"></label>
                        <label class="text-sm">ID type<select id="g-id-type" class="border rounded px-2 py-1 w-full"></select></label>
                        <label class="text-sm">ID number<input id="g-id-number" class="border rounded px-2 py-1 w-full" value="${this.esc(guest.identity_number)}"></label>
                        <label class="text-sm md:col-span-2">Special request<input id="g-request" class="border rounded px-2 py-1 w-full" value="${this.esc(guest.special_request)}"></label>
                        <label class="text-sm flex items-center space-x-2"><input id="g-vip" type="checkbox" ${guest.vip ? 'checked' : ''}> <span>VIP</span></label>
                    </div>
                    <button id="g-save" class="mt-3 bg-blue-600 text-white px-4 py-2 rounded">Save</button>
                    <p id="g-status" class="text-sm mt-2"></p>
                </div>
            </div>
            <div id="pane-bookings" class="hidden space-y-4">
                ${this.bookingSection('In house', this.record.current)}
                ${this.bookingSection('Future', this.record.future)}
                ${this.bookingSection('Past', this.record.past)}
            </div>
        `;
        document.getElementById('tab-summary').addEventListener('click', () => this.showTab('summary'));
        document.getElementById('tab-messages').addEventListener('click', () => this.showTab('messages'));
        document.getElementById('tab-biography').addEventListener('click', () => this.showTab('biography'));
        document.getElementById('tab-bookings').addEventListener('click', () => this.showTab('bookings'));
        document.getElementById('g-edit').addEventListener('click', () => this.showEditForm());
        document.getElementById('ai-run').addEventListener('click', () => this.summarizeGuest());
        document.querySelectorAll('.msg-filter').forEach(button => {
            button.addEventListener('click', () => this.renderMessages(button.dataset.channel || ''));
        });
        document.getElementById('wa-template-pick').addEventListener('change', () => this.showWhatsappTemplate());
        document.getElementById('wa-template').addEventListener('click', () => this.sendWhatsappTemplate());
        document.getElementById('wa-send').addEventListener('click', () => this.sendWhatsapp());
        document.getElementById('wa-upload').addEventListener('click', () => {
            const input = document.getElementById('wa-file');
            if (input && !input.disabled) input.click();
        });
        document.getElementById('wa-file').addEventListener('change', () => {
            const input = document.getElementById('wa-file');
            const chosen = input && input.files && input.files[0];
            const name = document.getElementById('wa-file-name');
            if (name) name.textContent = chosen ? chosen.name : '';
        });
        this.renderMessages(this.messageChannel || '');
        if (this.viewTab) this.showTab(this.viewTab);
    }

    showTab(name) {
        ['summary', 'messages', 'biography', 'bookings'].forEach(id => {
            document.getElementById('pane-' + id).classList.toggle('hidden', id !== name);
            const button = document.getElementById('tab-' + id);
            button.className = id === name
                ? 'px-3 py-2 text-sm border-b-2 border-blue-600 text-blue-600'
                : 'px-3 py-2 text-sm text-gray-600';
        });
    }

    bookingCounts() {
        const future = this.record.future || [];
        const past = this.record.past || [];
        const current = this.record.current || [];
        const cancelled = [] .concat(current, future, past).filter(booking => this.isCancelled(booking));
        const confirmed = future.filter(booking => !this.isCancelled(booking));
        const departed = past.filter(booking => !this.isCancelled(booking));
        const spentRows = [] .concat(current, confirmed, departed).filter(booking => !this.isCancelled(booking));
        const spent = spentRows.reduce((sum, booking) => sum + Number(booking.total_amount || 0), 0);
        return { confirmed: confirmed.length, departed: departed.length, cancelled: cancelled.length, spent: spent };
    }

    isCancelled(booking) {
        return String(booking.booking_status || '').toLowerCase() === 'cancelled';
    }

    summaryCard(label, value) {
        return `<div class="border rounded p-3"><div class="text-xs text-gray-500">${label}</div><div class="text-lg font-semibold">${this.esc(value)}</div></div>`;
    }

    renderMessages(channel) {
        this.messageChannel = channel || '';
        const list = document.getElementById('msg-list');
        const compose = document.getElementById('wa-compose');
        const rows = (this.record.messages || []).filter(message => !channel || message.channel === channel);
        document.querySelectorAll('.msg-filter').forEach(button => {
            const selected = (button.dataset.channel || '') === channel;
            button.className = 'msg-filter px-3 py-1 rounded text-sm ' + (selected ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700');
        });
        if (compose) compose.classList.toggle('hidden', channel !== 'whatsapp');
        if (channel === 'whatsapp') {
            this.applyWhatsappWindow();
            this.loadWhatsappTemplates();
        }
        if (!rows.length) {
            const empty = {
                '': 'Email, WhatsApp, and voice recordings will appear here once they are connected.',
                email: 'No email saved for this guest.',
                whatsapp: 'No WhatsApp messages saved for this guest.',
                voice: 'No voice recordings saved for this guest.'
            };
            list.innerHTML = `<p class="text-sm text-gray-500">${empty[channel] || 'No messages saved.'}</p>`;
            return;
        }
        if (channel === 'whatsapp') {
            list.innerHTML = `<div class="border rounded p-3 max-h-96 overflow-y-auto space-y-2 bg-gray-50">${rows.map(message => this.whatsappBubble(message)).join('')}</div>`;
            const thread = list.firstChild;
            if (thread) thread.scrollTop = thread.scrollHeight;
            this.hydrateMedia(list);
            return;
        }
        const directionLabel = { in: 'In', out: 'Out' };
        const channelLabel = { email: 'Email', whatsapp: 'WhatsApp', voice: 'Voice' };
        list.innerHTML = rows.map(message => `
            <div class="border rounded p-3 text-sm">
                <div class="flex justify-between text-gray-500">
                    <span>${this.esc(channelLabel[message.channel] || message.channel)} · ${this.esc(directionLabel[message.direction] || message.direction)}</span>
                    <span>${this.esc(message.sent_at)}</span>
                </div>
                <div class="mt-1 text-gray-900 whitespace-pre-wrap">${this.esc(message.body)}</div>
            </div>
        `).join('');
    }

    applyWhatsappWindow() {
        const windowState = (this.record && this.record.whatsapp_window) || { open: false, until: '' };
        const note = document.getElementById('wa-window');
        const field = document.getElementById('wa-body');
        const send = document.getElementById('wa-send');
        if (!note) return;
        note.textContent = windowState.open ? 'Free text open until ' + windowState.until : 'Free text closed';
        note.className = 'text-sm mt-3 ' + (windowState.open ? 'text-green-700' : 'text-gray-500');
        if (field) {
            field.disabled = !windowState.open;
            field.classList.toggle('opacity-50', !windowState.open);
        }
        if (send) {
            send.disabled = !windowState.open;
            send.classList.toggle('opacity-50', !windowState.open);
        }
        const file = document.getElementById('wa-file');
        const upload = document.getElementById('wa-upload');
        if (file) file.disabled = !windowState.open;
        if (upload) {
            upload.disabled = !windowState.open;
            upload.classList.toggle('opacity-50', !windowState.open);
        }
    }

    whatsappBubble(message) {
        const outbound = message.direction === 'out';
        const status = message.status ? ' · ' + message.status : '';
        const media = message.media_mime
            ? `<div class="mt-1" data-media="${this.apiUrl}/guests/whatsapp/media?source_ref=${encodeURIComponent(message.source_ref || '')}&guest_phone=${encodeURIComponent(message.guest_phone || '')}&hotel_name=${encodeURIComponent(this.currentHotel || '')}" data-mime="${this.esc(message.media_mime)}" data-name="${this.esc(message.media_name || '')}"></div>`
            : '';
        return `
            <div class="flex ${outbound ? 'justify-end' : 'justify-start'}">
                <div class="max-w-md rounded px-3 py-2 text-sm ${outbound ? 'bg-green-100' : 'bg-white border'}">
                    ${media}
                    <div class="whitespace-pre-wrap text-gray-900">${this.esc(message.body)}</div>
                    <div class="text-xs text-gray-500 mt-1">${this.esc(message.sent_at)}${this.esc(status)}</div>
                </div>
            </div>
        `;
    }

    mediaTag(mime) {
        const tags = [
            ['image/', 'img'],
            ['video/', 'video'],
            ['audio/', 'audio'],
            ['application/pdf', 'iframe'],
        ];
        const match = tags.find(row => String(mime || '').toLowerCase().startsWith(row[0]));
        return match ? match[1] : '';
    }

    async hydrateMedia(root) {
        const nodes = root.querySelectorAll('[data-media]');
        const classes = { img: 'max-w-full rounded', video: 'max-w-full', iframe: 'w-full h-64' };
        for (const node of nodes) {
            const response = await fetch(node.dataset.media, { credentials: 'include' });
            if (!response.ok) continue;
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const tag = this.mediaTag(node.dataset.mime || '');
            if (tag) {
                const element = document.createElement(tag);
                element.src = url;
                element.className = classes[tag] || '';
                if (tag === 'video' || tag === 'audio') element.controls = true;
                node.appendChild(element);
            }
            const link = document.createElement('a');
            link.href = url;
            link.download = node.dataset.name || 'file';
            link.textContent = 'Download';
            link.className = 'text-blue-600 text-sm block mt-1';
            node.appendChild(link);
        }
    }

    async sendWhatsapp() {
        const guest = (this.record && this.record.guest) || {};
        const field = document.getElementById('wa-body');
        const status = document.getElementById('wa-status');
        const fileInput = document.getElementById('wa-file');
        const chosen = fileInput && fileInput.files && fileInput.files[0];
        if (chosen) {
            status.textContent = 'Sending file...';
            const form = new FormData();
            form.append('guest_phone', guest.guest_phone);
            form.append('hotel_name', this.currentHotel);
            form.append('caption', String((field && field.value) || '').trim());
            form.append('file', chosen, chosen.name);
            const response = await fetch(`${this.apiUrl}/guests/whatsapp/media`, { method: 'POST', credentials: 'include', body: form });
            const data = await response.json();
            if (!response.ok || data.error) {
                status.textContent = data.error || 'WhatsApp send failed';
                return;
            }
            fileInput.value = '';
            const chosenName = document.getElementById('wa-file-name');
            if (chosenName) chosenName.textContent = '';
            if (field) field.value = '';
            this.viewTab = 'messages';
            this.messageChannel = 'whatsapp';
            await this.openGuest(guest.guest_phone);
            return;
        }
        const text = String((field && field.value) || '').trim();
        if (!text) {
            status.textContent = 'Write a message first';
            return;
        }
        status.textContent = 'Sending...';
        const response = await fetch(`${this.apiUrl}/guests/whatsapp`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ guest_phone: guest.guest_phone, hotel_name: this.currentHotel, body: text })
        });
        const data = await response.json();
        if (!response.ok || data.error) {
            status.textContent = data.error || 'WhatsApp send failed';
            return;
        }
        field.value = '';
        this.viewTab = 'messages';
        this.messageChannel = 'whatsapp';
        await this.openGuest(guest.guest_phone);
    }

    async loadWhatsappTemplates() {
        const guest = (this.record && this.record.guest) || {};
        const select = document.getElementById('wa-template-pick');
        const sentence = document.getElementById('wa-template-sentence');
        if (!select || !guest.guest_phone) return;
        const response = await fetch(`${this.apiUrl}/guests/whatsapp/templates?guest_phone=${encodeURIComponent(guest.guest_phone)}&hotel_name=${encodeURIComponent(this.currentHotel)}`, { credentials: 'include' });
        const data = await response.json();
        this.whatsappTemplates = (!response.ok || data.error) ? [] : (data.templates || []);
        select.innerHTML = '';
        this.whatsappTemplates.forEach(item => {
            const option = document.createElement('option');
            option.value = item.key;
            option.textContent = item.template_name;
            select.appendChild(option);
        });
        if (!this.whatsappTemplates.length) sentence.textContent = (data && data.error) || 'No active WhatsApp template';
        this.showWhatsappTemplate();
    }

    showWhatsappTemplate() {
        const select = document.getElementById('wa-template-pick');
        const sentence = document.getElementById('wa-template-sentence');
        if (!select || !sentence) return;
        const chosen = (this.whatsappTemplates || []).find(item => item.key === select.value);
        if (chosen) sentence.textContent = chosen.sentence;
    }

    async sendWhatsappTemplate() {
        const guest = (this.record && this.record.guest) || {};
        const status = document.getElementById('wa-status');
        const select = document.getElementById('wa-template-pick');
        const template = select ? select.value : '';
        if (!template) {
            status.textContent = 'No active WhatsApp template';
            return;
        }
        status.textContent = 'Sending template...';
        const response = await fetch(`${this.apiUrl}/guests/whatsapp/template`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ guest_phone: guest.guest_phone, hotel_name: this.currentHotel, template: template })
        });
        const data = await response.json();
        if (!response.ok || data.error) {
            status.textContent = data.error || 'WhatsApp template failed';
            return;
        }
        this.viewTab = 'messages';
        this.messageChannel = 'whatsapp';
        await this.openGuest(guest.guest_phone);
    }

    async summarizeGuest() {
        const guest = this.record.guest || {};
        const status = document.getElementById('ai-status');
        status.textContent = 'Writing summary...';
        const response = await fetch(`${this.apiUrl}/guests/summarize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ guest_phone: guest.guest_phone, hotel_name: this.currentHotel })
        });
        const data = await response.json();
        if (!response.ok || data.error) {
            status.textContent = data.error || 'Summary failed';
            return;
        }
        guest.ai_summary = data.ai_summary;
        guest.ai_summary_at = data.ai_summary_at;
        document.getElementById('ai-summary').textContent = data.ai_summary;
        document.getElementById('ai-when').textContent = 'Saved ' + data.ai_summary_at;
        status.textContent = 'Saved';
    }

    fact(label, value) {
        return `<div><span class="text-gray-500">${label}</span><div class="text-gray-900">${this.esc(value)}</div></div>`;
    }

    showEditForm() {
        const guest = this.record.guest || {};
        document.getElementById('g-view').classList.add('hidden');
        document.getElementById('g-form').classList.remove('hidden');
        document.getElementById('g-edit').classList.add('hidden');
        this.fillSelect('g-country', (window.COUNTRIES_ISO3166 || []).map(row => row.name), guest.country || guest.nationality);
        this.fillSelect('g-gender', ['Male', 'Female'], guest.gender);
        this.fillSelect('g-id-type', ['IC', 'MyKad', 'Passport'], guest.identity_type);
        document.getElementById('g-save').addEventListener('click', () => this.saveGuest());
    }

    bookingSection(title, bookings) {
        const rows = bookings || [];
        const currency = this.record.currency || '';
        const body = rows.length ? rows.map(booking => `
            <div class="border rounded p-3">
                <div class="flex justify-between text-sm">
                    <span class="font-medium">${this.esc(booking.booking_id)}</span>
                    <span>${this.esc(booking.booking_status)} · ${currency} ${Number(booking.total_amount || 0).toFixed(2)}</span>
                </div>
                <div class="text-sm text-gray-600">${this.esc(booking.check_in)} to ${this.esc(booking.check_out)} · ${this.esc(booking.room_type)}</div>
                <div class="mt-2 text-sm">
                    ${(booking.rooms || []).length ? (booking.rooms || []).map(room => `
                        <div>${this.esc(room.room_number)} · ${this.esc(room.stay_status)} · ${this.esc(room.check_in)} to ${this.esc(room.check_out)}</div>
                    `).join('') : '<div class="text-gray-500">No room assigned</div>'}
                </div>
            </div>
        `).join('') : '<p class="text-sm text-gray-500">None</p>';
        return `<div><h3 class="font-semibold mb-2">${title}</h3><div class="space-y-2">${body}</div></div>`;
    }

    fillSelect(id, values, current) {
        const select = document.getElementById(id);
        const chosen = String(current || '');
        const options = [''].concat(values);
        const known = options.find(value => value.toUpperCase() === chosen.toUpperCase());
        if (chosen && !known) options.push(chosen);
        select.innerHTML = options.map(value => `<option value="${this.esc(value)}">${this.esc(value)}</option>`).join('');
        select.value = known || chosen;
    }

    async saveGuest() {
        const guest = this.record.guest || {};
        const country = document.getElementById('g-country').value;
        const payload = {
            guest_phone: guest.guest_phone,
            guest_name: document.getElementById('g-name').value,
            email: document.getElementById('g-email').value,
            country: country,
            nationality: country,
            date_of_birth: document.getElementById('g-dob').value,
            gender: document.getElementById('g-gender').value,
            company: document.getElementById('g-company').value,
            address: document.getElementById('g-address').value,
            identity_type: document.getElementById('g-id-type').value,
            identity_number: document.getElementById('g-id-number').value,
            special_request: document.getElementById('g-request').value,
            vip: document.getElementById('g-vip').checked
        };
        const status = document.getElementById('g-status');
        const response = await fetch(`${this.apiUrl}/guest_profile/update`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok || data.error) {
            status.textContent = data.error || 'Save failed';
            return;
        }
        status.textContent = 'Saved';
        await this.search();
        await this.openGuest(guest.guest_phone);
    }

    async createGuest() {
        const payload = {
            guest_phone: document.getElementById('add-phone').value,
            guest_name: document.getElementById('add-name').value,
            email: document.getElementById('add-email').value
        };
        const response = await fetch(`${this.apiUrl}/guest_profile/create`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok || data.error) {
            alert(data.error || 'Could not create the guest');
            return;
        }
        document.getElementById('guest-search').value = payload.guest_phone;
        await this.search();
        await this.openGuest(payload.guest_phone);
    }

    esc(value) {
        return String(value == null ? '' : value).split('&').join('&amp;').split('<').join('&lt;').split('>').join('&gt;').split('"').join('&quot;');
    }
}
