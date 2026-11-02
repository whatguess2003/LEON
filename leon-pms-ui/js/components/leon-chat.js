// LEON Chat Component (singleton across pages)
if (window.__LEON_CHAT_INITED) {
    // Already initialized on this browser session; skip re-creating UI
    // But expose existing instance for page integrations
    window.leonChat = window.leonChat || window.__LEON_CHAT_INSTANCE__;
} else {
window.__LEON_CHAT_INITED = true;
class LeonChatComponent {
    constructor() {
        this.chatOpen = false;
        // Add local/production API URL switcher
        const host = window.location.hostname;
        const isLocal = host === 'localhost' || host === '127.0.0.1' || host === '::1';
        this.leonChatUrl = isLocal ? 'http://localhost:8002' : 'https://api.inap.ai';
        // Default LEON avatar asset; will be upgraded to WEBP/PNG if present
        this.leonAvatar = this.getDefaultLeonAvatar();
        this.resolveLeonAvatar().then((url)=>{ this.leonAvatar = url || this.leonAvatar; this.updateLeonAvatarImages(); }).catch(()=>{});
        // Suggestion bubble config
        this._suggestTimer = null;
        this._suggestHideTimer = null;
        this._suggestLockUntil = 0; // prevent auto-suggest from overriding page intros
        this._lastSuggestAt = 0;     // track last shown suggestion time
        this._minGapMs = 12000;      // min gap between suggestions
        this._maxGapMs = 30000;      // safety ceiling to avoid long silence
        // No generic fallback suggestions (strict: no fallback logic)
        this.suggestMessages = [];
        // Page-specific suggestion map removed (AI-generated suggestions only)
        this.pageSuggestionMap = {};
        // Render charts inline? Disabled for mobile-first; use full report page instead
        this.renderInlineCharts = false;
        this._depsLoaded = false;
        // Render shell immediately to restore open state fast after navigation
        this.renderChatButton();
        try {
            const openFlag = sessionStorage.getItem('LEON_CHAT_OPEN') === '1';
            if (openFlag) {
                this.chatOpen = true;
                const widget = document.getElementById('leon-chat-widget');
                if (widget) widget.classList.add('open');
            }
        } catch(_) {}
        this.initialize();
    }
    
    initialize() {
        this.injectBaseStyles();
        this.renderChatButton();
        this.setupEventListeners();
        setTimeout(() => { this.loadChatHistory(); this.watchWhatsApp(); }, 0);
        this.loadQueue();
        if (this.chatOpen) this.refreshOpenChat();
    }

    // Dynamically load Markdown and Mermaid for rich message rendering
    ensureDependencies() {
        const loaders = [];
        if (!window.marked) {
            loaders.push(this.loadScript('https://cdn.jsdelivr.net/npm/marked/marked.min.js'));
        }
        if (!window.mermaid) {
            loaders.push(this.loadScript('https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js'));
        }
        // Optional: basic styling for code blocks
        this.injectBaseStyles();
        return Promise.all(loaders).then(() => {
            try { window.mermaid && window.mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' }); } catch (_) {}
        });
    }

    getDefaultLeonAvatar() {
        try {
            const svg = "<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 64 64'><defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0%' stop-color='#1d4ed8'/><stop offset='100%' stop-color='#06b6d4'/></linearGradient></defs><circle cx='32' cy='32' r='32' fill='url(#g)'/><text x='50%' y='54%' text-anchor='middle' fill='#ffffff' font-family='Inter, Arial, sans-serif' font-size='28' font-weight='700'>L</text></svg>";
            return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg);
        } catch(_) { return ''; }
    }

    resolveLeonAvatar() {
        const tryLoad = (url) => new Promise((res)=>{ try { const i=new Image(); i.onload=()=>res(url); i.onerror=()=>res(null); i.src=url; } catch(_) { res(null); } });
        return (async () => {
            const webp = await tryLoad('assets/leon-avatar.webp'); if (webp) return webp;
            const png = await tryLoad('assets/leon-avatar.png'); if (png) return png;
            const jpg = await tryLoad('assets/leon-avatar.jpg'); if (jpg) return jpg;
            return this.getDefaultLeonAvatar();
        })();
    }

    async loadProfileForChat() {
        try {
            const isLocal = location.hostname==='localhost'||location.hostname==='127.0.0.1';
            const api = isLocal ? 'http://localhost:8002' : 'https://api.inap.ai';
            const res = await fetch(`${api}/api/auth/me`, { credentials: 'include' });
            if (!res.ok) return;
            const data = await res.json();
            this.managerName = (data.manager_name || '').toString();
            this.managerInitials = this.computeInitials(this.managerName);
        } catch(_) {}
    }

    computeInitials(name) {
        try {
            const parts = String(name||'').trim().split(/\s+/).filter(Boolean);
            if (!parts.length) return '';
            const first = parts[0][0] || '';
            const last = parts.length>1 ? (parts[parts.length-1][0]||'') : '';
            return (first+last).toUpperCase();
        } catch(_) { return ''; }
    }

    generateGradientFromString(s) {
        try {
            let hash = 0; const str = String(s||'');
            for (let i=0;i<str.length;i++) { hash = str.charCodeAt(i) + ((hash<<5)-hash); }
            const h1 = (hash % 360 + 360) % 360;
            const h2 = ((hash>>3) % 360 + 360) % 360;
            return `linear-gradient(135deg, hsl(${h1},70%,50%), hsl(${h2},70%,50%))`;
        } catch(_) { return 'linear-gradient(135deg,#475569,#0ea5e9)'; }
    }

    loadScript(src) {
        return new Promise((resolve, reject) => {
            const s = document.createElement('script');
            s.src = src;
            s.async = true;
            s.onload = resolve;
            s.onerror = reject;
            document.head.appendChild(s);
        });
    }

    injectBaseStyles() {
        if (document.getElementById('leon-chat-base-styles')) return;
        const style = document.createElement('style');
        style.id = 'leon-chat-base-styles';
        style.textContent = `
            /* Size the widget itself so width/height are guaranteed */
            #leon-chat-widget { width: 420px !important; max-width: 92vw !important; height: 90vh !important; max-height: 94vh !important; display:none; flex-direction: column; position: relative; transform: translateY(100%); transition: transform .25s ease; font-size: 14px; background: #efeae2; }
            #leon-chat-widget.open { display:flex; transform: translateY(0); }
            #queue-widget { display:none; width: 320px; max-height: 70vh; overflow:auto; background:#fff; border-radius:12px; box-shadow:0 12px 24px rgba(0,0,0,.15); margin-bottom:12px; }
            #queue-widget.open { display:block; }
            #leon-chat-widget.leon-chat-fullscreen { width: min(900px, 95vw) !important; height: min(85vh, 900px) !important; }
            .chat-widget { max-width: 95vw; }
            /* Floating position and stacking above iframe */
            .floating-chat { position: fixed; bottom: 20px; right: 20px; z-index: 99999; }
            .floating-chat, .floating-chat * { pointer-events: auto; }
            #leon-chat-widget > div { min-height: 0; }
            #leon-chat-widget, #leon-chat-widget p, #leon-chat-widget button, #leon-chat-widget textarea, #leon-chat-widget h3, #leon-pins, #leon-screen, #leon-guide { font-size: 14px; }
            .leon-msg { max-width: 85%; font-size: 14px; line-height: 1.35; }
            /* Markdown normalization inside assistant bubbles */
            .leon-msg h1, .leon-msg h2, .leon-msg h3, .leon-msg h4, .leon-msg h5, .leon-msg h6 { margin: 0.25rem 0; font-weight: 600; }
            .leon-msg h1 { font-size: 1rem; }
            .leon-msg h2 { font-size: 0.95rem; }
            .leon-msg h3 { font-size: 0.92rem; }
            .leon-msg h4, .leon-msg h5, .leon-msg h6 { font-size: 0.9rem; }
            .leon-msg p, .leon-msg li { font-size: 12px; margin: 0.25rem 0; }
            .leon-msg pre { background:#0b1020; color:#e5e7eb; padding:10px; border-radius:8px; overflow:auto; font-size: 10px; }
            .leon-msg code { background:#f3f4f6; padding:2px 4px; border-radius:4px; font-size: 10px; }
            .leon-msg table { border-collapse: collapse; width: 100%; font-size: 12px; }
            .leon-msg table th, .leon-msg table td { border: 1px solid #e5e7eb; padding: 6px; }
            .leon-msg ul, .leon-msg ol { padding-left: 1rem; }
            /* Chat bubbles */
            .leon-user .leon-bubble { background:#d9fdd3; color:#111b21; border-radius: 8px; border-bottom-right-radius: 2px; box-shadow: 0 1px 0.5px rgba(11,20,26,.13); }
            .leon-assistant .leon-bubble { background:#ffffff; color:#111b21; border:0; border-radius: 8px; border-bottom-left-radius: 2px; box-shadow: 0 1px 0.5px rgba(11,20,26,.13); }
            .leon-time { float: right; margin: 4px 0 0 8px; font-size: 11px; color: #667781; }
            .leon-bubble .flex { clear: both; }
            .wa-action { display: block; width: 100%; background: transparent; border: 0; border-top: 1px solid #e9edef; color: #00a884; padding: 10px 0; font-size: 15px; text-align: center; }
            .wa-list { background: #fff; border-radius: 8px; border-bottom-left-radius: 2px; box-shadow: 0 1px 0.5px rgba(11,20,26,.13); max-width: 92%; }
            .wa-list-header { font-weight: 700; font-size: 16px; padding: 8px 10px 0; color: #111b21; }
            .wa-list-body { font-size: 15px; padding: 4px 10px 0; color: #111b21; }
            .wa-list-footer { display: flex; justify-content: space-between; font-size: 12px; color: #667781; padding: 6px 10px 8px; }
            .leon-approval { background:#fff; border-radius:8px; border-bottom-left-radius:2px; box-shadow:0 1px 0.5px rgba(11,20,26,.13); max-width:92%; margin:0 0 8px 4px; overflow:hidden; }
            .leon-approval h4 { font-size:14px; font-weight:600; margin:0; padding:10px 12px 0; color:#111b21; }
            .leon-approval p { font-size:13px; margin:0; padding:6px 12px 10px; color:#111b21; white-space:pre-wrap; }
            .leon-approval-actions { display:flex; border-top:1px solid #e9edef; }
            .leon-approval-actions button { flex:1; background:transparent; border:0; color:#00a884; padding:10px 0; font-size:14px; cursor:pointer; }
            .leon-approval-actions button + button { border-left:1px solid #e9edef; }
            .leon-approval-actions button.reject { color:#667781; }
            .leon-approval-status { font-size:12px; color:#667781; padding:8px 12px 10px; border-top:1px solid #e9edef; }
            .leon-toolbar button { font-size:12px; }
            .leon-copy-btn { opacity:0.7; }
            .leon-copy-btn:hover { opacity:1; }
            /* Mermaid sizing */
            .leon-msg .mermaid { font-size: 12px; }
            /* Chat input textarea font normalization */
            .leon-input-row { display: grid; grid-template-columns: 1fr auto; column-gap: 8px; align-items: center; }
            #chat-input { font-size: 14px; width: 100%; max-width: 100%; box-sizing: border-box; background: #fff; border-radius: 24px; }
            #leon-chat-widget .leon-input-row button { width: 42px; height: 42px; border-radius: 9999px; background: #00a884; border: 0; color: #fff; }
            .leon-textarea { min-height: 36px; max-height: 200px; resize: vertical; }
            /* Suggestion bubble */
            .leon-suggest { position:absolute; bottom: calc(100% + 10px); right: 0; background:#2563eb; color:#ffffff; border:0; box-shadow:0 8px 18px rgba(37,99,235,0.35); border-radius:9999px; padding:8px 12px; font-size:12px; white-space:nowrap; z-index:100000; opacity:0; transform: translateY(4px); transition: opacity .15s ease, transform .15s ease; }
            .leon-suggest.show { opacity:1; transform: translateY(0); }
            .leon-suggest::after { content:''; position:absolute; top:100%; right:14px; border-width:6px; border-style:solid; border-color:#2563eb transparent transparent transparent; }

            #leon-thread, #chat-messages { background-color: #efeae2 !important; background-image: radial-gradient(#e4dcd0 1.2px, transparent 1.2px) !important; background-size: 16px 16px !important; }
        `;
        document.head.appendChild(style);
    }

    async loadChatHistory() {
        const chatMessages = document.getElementById('chat-messages');
        if (!chatMessages) return;
        try {
            // Fetch server-side dialog_history projection
            const resp = await fetch(`${this.leonChatUrl}/api/chat/history`, { credentials: 'include' });
            if (resp && resp.ok) {
                const data = await resp.json();
                const items = (data && data.items) || [];
                // Clear existing UI and local cache
                chatMessages.innerHTML = '';
                localStorage.removeItem('leon_chat_history');
                // Re-render from backend history
                items.forEach(msg => {
                    if (msg.source_ref) this.seenWhatsApp(msg.source_ref);
                    this.addChatMessage(String(msg.text || ''), String(msg.sender || 'leon'), true);
                });
                this.restoreFunctionList();
                return;
            }
        } catch(_) {}
        // Fallback: load from local cache if backend not available
        try {
        const history = JSON.parse(localStorage.getItem('leon_chat_history') || '[]');
        history.forEach(msg => this.addChatMessage(msg.text, msg.sender, true));
        this.restoreFunctionList();
        } catch(_) {}
    }

    seenWhatsApp(sourceRef) {
        this.waSeen = this.waSeen || {};
        if (sourceRef) this.waSeen[sourceRef] = true;
    }

    watchWhatsApp() {
        if (this.waTimer) return;
        this.waTimer = setInterval(() => this.pullWhatsApp(), 3000);
    }

    async pullWhatsApp() {
        try {
            const resp = await fetch(`${this.leonChatUrl}/api/chat/history`, { credentials: 'include' });
            if (!resp || !resp.ok) return;
            const data = await resp.json();
            ((data && data.items) || []).forEach(msg => {
                if (msg.channel !== 'whatsapp' || !msg.source_ref || (this.waSeen || {})[msg.source_ref]) return;
                this.seenWhatsApp(msg.source_ref);
                this.addChatMessage(String(msg.text || ''), String(msg.sender || 'leon'), true);
            });
        } catch (_) {}
    }

    restoreFunctionList() {
        try {
            const saved = sessionStorage.getItem('leon_function_list');
            if (saved) this.mountWhatsAppList(JSON.parse(saved));
        } catch (_) {}
    }

    saveChatMessage(message, sender) {
        let history = JSON.parse(localStorage.getItem('leon_chat_history') || '[]');
        history.push({ text: message, sender: sender });
        // Keep only the last 50 messages
        if (history.length > 50) {
            history = history.slice(history.length - 50);
        }
        localStorage.setItem('leon_chat_history', JSON.stringify(history));
    }

    async clearChatHistory() {
        try {
            await fetch(`${this.leonChatUrl}/api/chat/clear`, { method: 'POST', credentials: 'include' });
        } catch(_) {}
        localStorage.removeItem('leon_chat_history');
        sessionStorage.removeItem('leon_function_list');
        const chatMessages = document.getElementById('chat-messages');
        if (chatMessages) chatMessages.innerHTML = '';
    }

    renderChatButton() {
        if (document.getElementById('leon-chat-toggle')) return;
        const chatButton = document.createElement('div');
        chatButton.className = 'floating-chat';
        chatButton.innerHTML = `
            <div id="queue-widget" class="p-3">
                <h3 class="text-sm font-semibold">Require approval</h3>
                <div id="queue-approvals" class="mt-2 space-y-2"></div>
                <h3 class="text-sm font-semibold mt-4">Require action</h3>
                <div id="queue-tasks" class="mt-2 space-y-2"></div>
            </div>
            <div class="flex items-end justify-end">
                <button id="queue-toggle" type="button" aria-label="Require approval and require action" class="relative w-14 h-14 mr-3 rounded-full shadow-lg bg-gray-800 text-white flex items-center justify-center">
                    <i class="fas fa-clipboard-list"></i>
                    <span id="queue-count" class="absolute top-0 right-0 bg-red-600 text-white text-xs rounded-full px-1">0</span>
                </button>
                <button id="leon-chat-toggle" type="button" class="w-20 h-20 rounded-full shadow-lg overflow-hidden border border-white bg-white flex items-center justify-center">
                    <img id="leon-chat-toggle-avatar" src="${this.leonAvatar}" alt="LEON" class="w-full h-full object-cover"/>
                </button>
            </div>
            <div class="chat-widget mt-4" id="leon-chat-widget">
                <header class="px-3 py-2 flex items-center flex-shrink-0" style="background:#f0f2f5">
                    <button id="leon-info-open" type="button" class="flex items-center flex-1 text-left">
                        <div class="w-10 h-10 rounded-full text-white flex items-center justify-center text-sm font-semibold flex-shrink-0" style="background:#00a884">L</div>
                        <div class="ml-3">
                            <div class="text-sm font-semibold" style="color:#111b21">Leon</div>
                            <div id="leon-progress" class="text-xs" style="color:#667781">online</div>
                        </div>
                    </button>
                    <button id="leon-chat-close" type="button" class="text-sm flex-shrink-0" style="color:#54656f">Close</button>
                </header>
                <div id="leon-pins" class="hidden px-3 py-2 text-sm flex-shrink-0" style="background:#fff;border-bottom:1px solid #e9edef"></div>
                <div id="leon-thread" class="flex-1 flex flex-col min-h-0 relative">
                    <div id="leon-screen" class="hidden px-3 pt-3"></div>
                    <div class="flex-1 overflow-y-auto px-2 py-3" id="chat-messages"></div>
                    <div id="leon-sheet" class="hidden absolute inset-0 flex flex-col justify-end" style="background:rgba(11,20,26,.45);z-index:6">
                        <div class="bg-white flex flex-col" style="max-height:78%;border-top-left-radius:16px;border-top-right-radius:16px">
                            <div class="relative py-3 border-b flex-shrink-0">
                                <div id="leon-sheet-title" class="text-center text-sm font-semibold"></div>
                                <button id="leon-sheet-close" type="button" class="absolute right-3 top-2 w-8 h-8 rounded-full" style="background:#f0f2f5;color:#54656f">×</button>
                            </div>
                            <div id="leon-sheet-rows" class="overflow-y-auto"></div>
                            <div class="text-center text-xs py-3 flex-shrink-0" style="color:#8696a0">Tap an item to select it</div>
                        </div>
                    </div>
                    <div id="leon-info" class="hidden absolute inset-0 bg-white overflow-y-auto">
                        <div class="px-3 py-3 flex items-center" style="background:#f0f2f5">
                            <button id="leon-info-back" type="button" class="text-sm mr-3" style="color:#54656f">Back</button>
                            <div class="text-sm font-semibold">Leon</div>
                        </div>
                        <div class="p-4">
                            <h2 class="text-xs font-semibold text-gray-700">To-do</h2>
                            <div id="leon-todos" class="mt-2"></div>
                            <h2 class="text-xs font-semibold text-gray-700 mt-4">Highlights</h2>
                            <div id="leon-highlights" class="mt-2"></div>
                        </div>
                    </div>
                </div>
                <div class="px-2 py-2 flex-shrink-0" style="background:#f0f2f5">
                    <div class="leon-input-row">
                        <textarea id="chat-input" rows="1" placeholder="Type a message" class="flex-1 border-0 px-4 py-2 text-sm resize-none leon-textarea"></textarea>
                        <button id="leon-chat-send" type="button" aria-label="Send"><i class="fas fa-paper-plane"></i></button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(chatButton);
        document.getElementById('leon-chat-toggle').addEventListener('click', () => this.toggleChat());
        document.getElementById('leon-chat-close').addEventListener('click', () => this.closeChat());
        document.getElementById('leon-chat-send').addEventListener('click', () => this.sendMessage());
        document.getElementById('chat-input').addEventListener('keydown', (event) => this.handleInputKeydown(event));
        document.getElementById('queue-toggle').addEventListener('click', () => this.toggleQueue());
        document.getElementById('leon-info-open').addEventListener('click', () => {
            document.getElementById('leon-info').classList.remove('hidden');
        });
        document.getElementById('leon-info-back').addEventListener('click', () => {
            document.getElementById('leon-info').classList.add('hidden');
        });
        document.getElementById('leon-sheet-close').addEventListener('click', () => {
            document.getElementById('leon-sheet').classList.add('hidden');
        });
        this.queueState = { hotel: '', approvals: [], tasks: [] };
    }

    toggleChat() {
        this.chatOpen = !this.chatOpen;
        const widget = document.getElementById('leon-chat-widget');
        if (widget) {
            widget.style.display = '';
            widget.style.transform = '';
            widget.classList.toggle('open', this.chatOpen);
        }
        if (this.chatOpen) {
            this.closeQueue();
            this.refreshOpenChat();
        }
        try { sessionStorage.setItem('LEON_CHAT_OPEN', this.chatOpen ? '1' : '0'); } catch(_) {}
    }

    toggleQueue() {
        const widget = document.getElementById('queue-widget');
        if (!widget) return;
        const open = !widget.classList.contains('open');
        widget.classList.toggle('open', open);
        if (open) {
            this.closeChat();
            this.loadQueue();
        }
    }

    closeQueue() {
        const widget = document.getElementById('queue-widget');
        if (widget) widget.classList.remove('open');
    }

    queuePost(path, body) {
        return fetch(this.leonChatUrl + '/api' + path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(body),
        });
    }

    async loadGuide() {
        try {
            const response = await fetch(this.leonChatUrl + '/api/guide', { credentials: 'include' });
            const data = await response.json();
            if (!response.ok) return;
            this.renderGuide(data || {});
        } catch (_) {}
    }

    renderGuide(data) {
        const todosBox = document.getElementById('leon-todos');
        const factsBox = document.getElementById('leon-highlights');
        const progress = document.getElementById('leon-progress');
        if (!todosBox || !factsBox) return;
        const todos = (data && data.todos) || [];
        const highlights = (data && data.highlights) || [];
        todosBox.innerHTML = '';
        factsBox.innerHTML = '';
        todos.forEach((row) => {
            const line = document.createElement('div');
            line.className = 'text-sm mt-2 ' + (row.state === 'later' ? 'text-gray-400' : row.state === 'now' ? 'text-gray-900 font-semibold' : 'text-green-700');
            line.textContent = (row.label || '') + (row.detail ? ' — ' + row.detail : '');
            todosBox.appendChild(line);
        });
        highlights.forEach((row) => {
            const chip = document.createElement('span');
            chip.className = 'inline-block bg-blue-50 text-blue-800 rounded-full px-2 py-1 mr-1 mb-1 text-xs';
            chip.textContent = (row.label || '') + ': ' + (row.value || '');
            factsBox.appendChild(chip);
        });
        if (progress && !progress.textContent) progress.textContent = 'online';
    }

    async loadQueue() {
        try {
            const response = await fetch(this.leonChatUrl + '/api/desk/queue', { credentials: 'include' });
            const data = await response.json();
            if (!response.ok) return;
            this.queueState = {
                hotel: data.hotel_name || '',
                approvals: data.approvals || [],
                tasks: data.tasks || [],
            };
            this.renderQueue();
        } catch (_) {}
    }

    renderQueue() {
        const state = this.queueState || { approvals: [], tasks: [] };
        const count = document.getElementById('queue-count');
        if (count) count.textContent = String(state.approvals.length + state.tasks.length);
        const approvals = document.getElementById('queue-approvals');
        const tasks = document.getElementById('queue-tasks');
        if (!approvals || !tasks) return;
        const esc = (value) => String(value || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        approvals.innerHTML = state.approvals.map((action, index) => `
            <div class="border rounded p-2">
                <p class="text-sm">${esc(action.guest_name)} ${esc(action.guest_phone)}</p>
                <p class="text-sm">${esc(action.label || '')}</p>
                <button type="button" data-index="${index}" class="queue-approve mt-2 bg-blue-600 text-white px-3 py-1 rounded text-sm">Approve</button>
            </div>
        `).join('');
        tasks.innerHTML = state.tasks.map((item, index) => `
            <div class="border rounded p-2">
                <p class="text-sm">${esc(item.guest_name)} ${esc(item.guest_phone)}</p>
                <p class="text-sm">${esc(item.task || '')}</p>
                <button type="button" data-index="${index}" class="queue-done mt-2 bg-gray-700 text-white px-3 py-1 rounded text-sm">Done</button>
            </div>
        `).join('');
        approvals.querySelectorAll('.queue-approve').forEach(button => {
            button.addEventListener('click', () => this.approveQueue(state.approvals[Number(button.dataset.index)], button));
        });
        tasks.querySelectorAll('.queue-done').forEach(button => {
            const item = state.tasks[Number(button.dataset.index)];
            button.addEventListener('click', () => this.doneQueue(item, button));
        });
    }

    async doneQueue(item, button) {
        button.disabled = true;
        const response = await this.queuePost('/guests/tasks/done', {
            hotel_name: this.queueState.hotel,
            guest_phone: item.guest_phone,
            task: item.task,
        });
        if (!response.ok) {
            button.disabled = false;
            return false;
        }
        await this.loadQueue();
        return true;
    }

    async approveQueue(action, button) {
        const nights = (value) => String(value || '').split(',').map(part => part.trim()).filter(Boolean);
        const hotel = this.queueState.hotel;
        const runners = {
            cancel_booking: () => this.queuePost('/bookings/cancel', { booking_id: action.booking_id }),
            create_booking: () => this.queuePost('/bookings/create', {
                guest_phone: action.guest_phone,
                room_type: action.room_type,
                stay_dates: nights(action.stay_dates),
                rooms_booked: Number(action.rooms_booked || 1),
            }),
            modify_booking: () => {
                const body = { booking_id: action.booking_id };
                if (action.new_stay_dates) body.new_stay_dates = nights(action.new_stay_dates);
                if (action.new_room_type) body.new_room_type = action.new_room_type;
                if (action.new_rooms_booked) body.new_rooms_booked = Number(action.new_rooms_booked);
                return this.queuePost('/bookings/modify', body);
            },
            check_in: () => this.queuePost('/assignments/check_in', { hotel_name: hotel, booking_id: action.booking_id, room_number: action.room_number }),
            check_out: () => this.queuePost('/assignments/check_out', { hotel_name: hotel, booking_id: action.booking_id, room_number: action.room_number }),
            reassign_room: () => this.queuePost('/assignments/reassign', {
                hotel_name: hotel,
                booking_id: action.booking_id,
                stay_date: action.stay_date,
                old_room_number: action.old_room_number,
                new_room_number: action.new_room_number,
            }),
            record_payment: () => this.queuePost('/payments/update', {
                hotel_name: hotel,
                booking_id: action.booking_id,
                payment_amount: Number(action.payment_amount),
                payment_method: action.payment_method,
            }),
            record_refund: () => this.queuePost('/payments/refund', {
                hotel_name: hotel,
                booking_id: action.booking_id,
                payment_seq: Number(action.payment_seq),
            }),
            update_guest_profile: () => {
                const body = { guest_phone: action.guest_phone };
                ['guest_name', 'email', 'country', 'special_request'].forEach(key => {
                    if (action[key]) body[key] = action[key];
                });
                return this.queuePost('/guest_profile/update', body);
            },
        };
        const run = runners[action.action];
        if (!run) return false;
        button.disabled = true;
        const response = await run();
        const data = await response.json();
        if (!response.ok || data.error) {
            button.disabled = false;
            return false;
        }
        await this.queuePost('/guests/approval/clear', {
            hotel_name: hotel,
            guest_phone: action.guest_phone,
            label: action.label,
        });
        await this.loadQueue();
        return true;
    }

    refreshOpenChat() {
        this.loadGuide();
        this.loadDesk(true);
    }

    async loadDesk(build) {
        try {
            const response = await fetch(this.leonChatUrl + '/api/chat/desk' + (build ? '?build=1' : ''), { credentials: 'include' });
            const data = await response.json();
            if (!response.ok) return;
            this.pinStore = data.stored || [];
            this.renderPins(data.pins || [], data.pin_count || 0);
            const next = JSON.stringify(data.screen || null);
            if (next !== this.screenKey) {
                this.screenKey = next;
                this.renderScreen(data.screen);
            }
        } catch (_) {}
    }

    renderPins(pins, count) {
        const box = document.getElementById('leon-pins');
        if (!box) return;
        box.innerHTML = '';
        const rows = this.pinsOpen ? (this.pinStore || []) : (pins || []);
        if (!rows.length) {
            box.classList.add('hidden');
            return;
        }
        box.classList.remove('hidden');
        rows.forEach((row) => {
            const line = document.createElement('div');
            line.className = 'flex items-start justify-between text-xs mt-1';
            const label = document.createElement('span');
            label.textContent = (row.category || '') + ' — ' + (row.text || '');
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'text-gray-500 ml-2 flex-shrink-0';
            button.textContent = 'Unpin';
            button.addEventListener('click', () => this.unpinMessage(row.text));
            line.appendChild(label);
            line.appendChild(button);
            box.appendChild(line);
        });
        const extra = (count || 0) - (pins || []).length;
        if (extra > 0) {
            const more = document.createElement('button');
            more.type = 'button';
            more.className = 'text-xs text-gray-500 mt-1';
            more.textContent = this.pinsOpen ? 'Show three' : (extra + ' more');
            more.addEventListener('click', () => {
                this.pinsOpen = !this.pinsOpen;
                this.renderPins(pins, count);
            });
            box.appendChild(more);
        }
    }

    mountWhatsAppList(screen) {
        const thread = document.getElementById('chat-messages');
        if (!thread || !screen) return;
        const previous = thread.querySelector('[data-leon-list]');
        if (previous) previous.remove();
        const bubble = document.createElement('div');
        bubble.className = 'wa-list';
        bubble.dataset.leonList = '1';
        if (screen.header) {
            const header = document.createElement('div');
            header.className = 'wa-list-header';
            header.textContent = screen.header;
            bubble.appendChild(header);
        }
        if (screen.body) {
            const body = document.createElement('div');
            body.className = 'wa-list-body';
            body.textContent = screen.body;
            bubble.appendChild(body);
        }
        const footer = document.createElement('div');
        footer.className = 'wa-list-footer';
        const foot = document.createElement('span');
        foot.textContent = screen.footer || '';
        const time = document.createElement('span');
        const clock = new Date();
        time.textContent = String(clock.getHours()).padStart(2, '0') + ':' + String(clock.getMinutes()).padStart(2, '0');
        footer.appendChild(foot);
        footer.appendChild(time);
        bubble.appendChild(footer);
        const open = document.createElement('button');
        open.type = 'button';
        open.className = 'wa-action';
        open.innerHTML = '<i class="fas fa-list"></i> ' + (screen.button || 'Open');
        open.addEventListener('click', () => this.openListSheet(screen));
        bubble.appendChild(open);
        const row = document.createElement('div');
        row.className = 'flex mb-2 px-1';
        row.appendChild(bubble);
        thread.appendChild(row);
        thread.scrollTop = thread.scrollHeight;
        try { sessionStorage.setItem('leon_function_list', JSON.stringify(screen)); } catch (_) {}
    }

    openListSheet(screen) {
        const sheet = document.getElementById('leon-sheet');
        const title = document.getElementById('leon-sheet-title');
        const rows = document.getElementById('leon-sheet-rows');
        if (!sheet || !rows) return;
        if (title) title.textContent = screen.button || '';
        rows.innerHTML = '';
        (screen.sections || []).forEach((section) => {
            if (section.title) {
                const name = document.createElement('div');
                name.className = 'px-4 pt-4 pb-1 text-sm font-semibold';
                name.style.color = '#111b21';
                name.textContent = section.title;
                rows.appendChild(name);
            }
            (section.rows || []).forEach((item) => {
                const choice = document.createElement('button');
                choice.type = 'button';
                choice.className = 'block w-full text-left px-4 py-3';
                choice.style.borderTop = '1px solid #e9edef';
                choice.style.background = '#fff';
                const name = document.createElement('div');
                name.className = 'text-sm';
                name.style.color = '#111b21';
                name.textContent = item.title || '';
                choice.appendChild(name);
                if (item.description) {
                    const detail = document.createElement('div');
                    detail.className = 'text-xs mt-1';
                    detail.style.color = '#667781';
                    detail.textContent = item.description;
                    choice.appendChild(detail);
                }
                choice.addEventListener('click', () => this.chooseListRow(screen, item));
                rows.appendChild(choice);
            });
        });
        sheet.classList.remove('hidden');
    }

    async openMenuAction(action) {
        try {
            const response = await fetch(this.leonChatUrl + '/api/chat/menu', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: action }),
            });
            const data = await response.json();
            if (response.ok && data.screen && data.screen.button) this.mountWhatsAppList(data.screen);
        } catch (_) {}
    }

    chooseListRow(screen, item) {
        const sheet = document.getElementById('leon-sheet');
        if (sheet) sheet.classList.add('hidden');
        const quote = [item.title || '', item.description || ''].filter(Boolean).join('\n');
        if (quote) this.addChatMessage(quote, 'user');
        if (item.kind === 'menu') {
            this.openMenuAction(item.id);
            return;
        }
        if (item.kind === 'approval' || item.kind === 'task') this.chooseScreenRow(screen, item, { disabled: false });
    }

    renderScreen(screen) {
        const box = document.getElementById('leon-screen');
        if (!box) return;
        if (screen && screen.button && screen.card !== 'sales') {
            box.classList.add('hidden');
            this.mountWhatsAppList(screen);
            return;
        }
        box.innerHTML = '';
        const sections = (screen && screen.sections) || [];
        if (!sections.length) {
            box.classList.add('hidden');
            return;
        }
        box.classList.remove('hidden');
        const card = document.createElement('div');
        card.className = 'bg-white rounded-lg px-3 pt-2 shadow';
        if (screen.header) {
            const header = document.createElement('div');
            header.className = 'font-semibold text-sm';
            header.textContent = screen.header;
            card.appendChild(header);
        }
        if (screen.body) {
            const body = document.createElement('div');
            body.className = 'text-sm mt-1';
            body.textContent = screen.body;
            card.appendChild(body);
        }
        if (screen.footer) {
            const footer = document.createElement('div');
            footer.className = 'text-xs text-gray-500 mt-2';
            footer.textContent = screen.footer;
            card.appendChild(footer);
        }
        const rows = sections.reduce((all, section) => all.concat(section.rows || []), []);
        const notice = rows.length && rows.every((item) => item.kind === 'pin' || item.kind === 'read' || item.kind === 'dismiss');
        if (notice) {
            const choices = document.createElement('div');
            choices.className = 'mt-2';
            rows.forEach((item) => {
                const choice = document.createElement('button');
                choice.type = 'button';
                choice.className = 'wa-action';
                choice.textContent = item.title || '';
                choice.addEventListener('click', () => this.chooseScreenRow(screen, item, choice));
                choices.appendChild(choice);
            });
            card.appendChild(choices);
            box.appendChild(card);
            return;
        }
        const list = document.createElement('div');
        list.className = screen.button ? 'hidden mt-2' : 'mt-2';
        sections.forEach((section) => {
            if (section.title) {
                const title = document.createElement('div');
                title.className = 'text-xs text-gray-500 mt-2';
                title.textContent = section.title;
                list.appendChild(title);
            }
            (section.rows || []).forEach((item) => {
                const choice = document.createElement('button');
                choice.type = 'button';
                choice.className = 'block w-full text-left border-t py-2';
                const name = document.createElement('div');
                name.className = 'text-sm';
                name.textContent = item.title || '';
                const detail = document.createElement('div');
                detail.className = 'text-xs text-gray-500';
                detail.textContent = item.description || '';
                choice.appendChild(name);
                choice.appendChild(detail);
                choice.addEventListener('click', () => this.chooseScreenRow(screen, item, choice));
                list.appendChild(choice);
            });
        });
        if (screen.button) {
            const open = document.createElement('button');
            open.type = 'button';
            open.className = 'mt-2 w-full border-t pt-2 text-green-600 text-sm';
            open.textContent = screen.button;
            open.addEventListener('click', () => list.classList.toggle('hidden'));
            card.appendChild(open);
        }
        card.appendChild(list);
        box.appendChild(card);
    }

    async chooseScreenRow(screen, item, button) {
        this.queueState = this.queueState || { approvals: [], tasks: [] };
        this.queueState.hotel = screen.hotel_name || this.queueState.hotel || '';
        if (item.kind === 'pin' || item.kind === 'read' || item.kind === 'dismiss') {
            const response = await this.queuePost('/chat/card', { card: screen.card || 'sales', id: item.id });
            if (!response.ok) return;
            await this.loadDesk();
            return;
        }
        const record = item.record || {};
        let ok = false;
        if (item.kind === 'task') ok = await this.doneQueue(record, button);
        if (item.kind === 'approval') ok = await this.approveQueue(record, button);
        if (!ok) return;
        const response = await this.queuePost('/chat/screen/drop', { id: item.id });
        if (!response.ok) return;
        const data = await response.json();
        this.screenKey = JSON.stringify(data.screen || null);
        this.renderScreen(data.screen);
    }

    showPinChoices(text, tools) {
        const existing = tools.querySelector('.pin-choices');
        if (existing) {
            existing.remove();
            return;
        }
        const row = document.createElement('div');
        row.className = 'pin-choices mt-1';
        ['urgent', 'to-do', 'follow-up', 'insights'].forEach((category) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'text-xs border rounded px-2 py-1 mr-1';
            button.textContent = category;
            button.addEventListener('click', () => this.pinMessage(text, category));
            row.appendChild(button);
        });
        tools.appendChild(row);
    }

    async pinMessage(text, category) {
        const response = await this.queuePost('/chat/pin', { text: text, category: category });
        if (!response.ok) return;
        await this.loadDesk();
    }

    async unpinMessage(text) {
        const response = await this.queuePost('/chat/unpin', { text: text });
        if (!response.ok) return;
        await this.loadDesk();
    }

    openChat() {
        this.chatOpen = true;
        const widget = document.getElementById('leon-chat-widget');
        const appFrame = document.getElementById('app-frame');
        if (widget) widget.classList.add('open');
        if (appFrame) appFrame.style.pointerEvents = 'none';
    }

    closeChat() {
        this.chatOpen = false;
        const widget = document.getElementById('leon-chat-widget');
        const appFrame = document.getElementById('app-frame');
        if (widget) {
            widget.style.display = '';
            widget.style.transform = '';
            widget.classList.remove('open');
        }
        if (appFrame) appFrame.style.pointerEvents = 'auto';
    }

    startSuggestionCycle() {
        // Clear any existing
        try { if (this._suggestTimer) { clearTimeout(this._suggestTimer); this._suggestTimer = null; } } catch(_) {}
        const schedule = () => {
            // Compute dynamic delay based on last suggestion time
            const now = Date.now();
            const since = now - (this._lastSuggestAt || 0);
            // If just showed one, extend next gap; if long silence, shorten
            let base = 14000; // neutral baseline
            if (since < 4000) base = 22000; // avoid back-to-back
            if (since > 30000) base = 9000;  // wake up faster after long silence
            const jitter = 4000 + Math.floor(Math.random()*6000); // 4-10s jitter
            let delay = base + jitter;
            // Clamp within [minGap, maxGap]
            delay = Math.max(this._minGapMs, Math.min(delay, this._maxGapMs));
            this._suggestTimer = setTimeout(()=>{
                if (document.hidden) { schedule(); return; }
                if (this.chatOpen) { schedule(); return; }
                // Respect page-intro lock window
                if (this._suggestLockUntil && Date.now() < this._suggestLockUntil) { schedule(); return; }
                this.showSuggestion();
                schedule();
            }, delay);
        };
        schedule();
    }

    showSuggestion() {
        try {
            const el = document.getElementById('leon-chat-suggest');
            if (!el) return;
            // Support override text and options via arguments
            const override = arguments && arguments[0];
            const opts = (arguments && arguments[1]) || {};
            const msg = override || this.getContextualSuggestion();
            if (!msg) return; // do not show any fallback
            el.textContent = msg;
            el.style.display = 'block';
            // trigger transition
            requestAnimationFrame(()=>{ el.classList.add('show'); });
            // auto-hide (linger longer)
            const linger = typeof opts.lingerMs === 'number' ? opts.lingerMs : 8000;
            this.hideSuggestion(linger);
            this._lastSuggestAt = Date.now();
        } catch(_) {}
    }

    hideSuggestion(delayMs) {
        try {
            if (this._suggestHideTimer) { clearTimeout(this._suggestHideTimer); this._suggestHideTimer = null; }
            const el = document.getElementById('leon-chat-suggest');
            if (!el) return;
            const hideNow = () => { el.classList.remove('show'); setTimeout(()=>{ el.style.display = 'none'; }, 150); };
            if (typeof delayMs === 'number') {
                this._suggestHideTimer = setTimeout(hideNow, delayMs);
            } else {
                hideNow();
            }
        } catch(_) {}
    }
    
    toggleSize() {
        const widget = document.getElementById('leon-chat-widget');
        if (!widget) return;
        // Toggle a class on the root widget itself rather than inner container
        widget.classList.toggle('leon-chat-fullscreen');
    }

    // Determine current SPA route and pick a context-aware suggestion
    getContextualSuggestion() {
        try {
            const key = this.getCurrentPageKey();
            const list = (key && this.pageSuggestionMap[key]) ? this.pageSuggestionMap[key] : [];
            if (list && list.length) return list[Math.floor(Math.random() * list.length)];
        } catch(_) {}
        return null; // strict: no fallback message
    }

    getCurrentPageKey() {
        try {
            // Prefer SPA iframe src; fallback to top-level path
            const frame = document.getElementById('app-frame');
            let src = '';
            if (frame) {
                src = frame.getAttribute('src') || '';
            }
            if (!src) {
                src = (window.location && window.location.pathname) ? window.location.pathname : '';
            }
            src = String(src).split('?')[0];
            const parts = src.split('/');
            const file = parts[parts.length - 1] || '';
            return file.toLowerCase();
        } catch(_) { return ''; }
    }

    // Public: show a page description immediately on navigation
    showPageDescription(route) {
        try {
            const key = (route || this.getCurrentPageKey() || '').toLowerCase();
            const msg = this.getPageDescriptionFor(key);
            // Try AI-generated suggestion to augment the intro
            this.fetchAISuggestion(key).then((aiMsg)=>{
                const intro = aiMsg || msg;
                if (intro) {
                    const lingerMs = 10000;
                    this._suggestLockUntil = Date.now() + lingerMs + 500;
                    this.showSuggestion(intro, { lingerMs });
                }
            }).catch(()=>{
                if (msg) {
                    const lingerMs = 10000;
                    this._suggestLockUntil = Date.now() + lingerMs + 500;
                    this.showSuggestion(msg, { lingerMs });
                }
            });
        } catch(_) {}
    }

    getPageDescriptionFor(key) {
        try {
            const file = (key || '').split('/').pop();
            const map = {
                'dashboard.html': 'Dashboard gives a live snapshot of KPIs, occupancy, revenue, and alerts. Open performance dashboards and revenue analytics to act fast.',
                'rooms.html': 'Room Management lets you create and edit room types, update specifications, and manage physical room numbers across your portfolio.',
                'inventory.html': 'Inventory & Pricing allows nightly quantity and price control, bulk updates for date ranges, and quick checks on pricing status.',
                'bookings.html': 'Bookings enables quick create, search, modify, and cancel flows, plus confirmations and payment status checks for guests.',
                'guests.html': 'Guests provides profile create/update, phone-based search, loyalty management, and guest analytics to personalize service.',
                'housekeeping.html': 'Housekeeping shows readiness and dirty rooms, supports task creation, and tracks cleaning progress by room or floor.',
                'operations.html': 'Operations centralizes assignments, relocations, out-of-order, maintenance, and fast updates to keep the hotel running smoothly.',
                'analytics.html': 'Analytics offers occupancy and revenue insights, performance dashboards, financial reconciliations, and channel reports.',
                'assignments.html': 'Assignments lets you view and optimize room assignments for arrivals and in-house guests with quick rebalancing.',
                'activities.html': 'Activity Logs summarize recent operational updates; filter by bookings, inventory, payments, and more to audit actions.'
            };
            return map[file] || '';
        } catch(_) { return ''; }
    }

    async fetchAISuggestion(routeKey) {
        try {
            const isLocal = location.hostname==='localhost'||location.hostname==='127.0.0.1' || location.hostname==='::1';
            const api = isLocal ? 'http://localhost:8002' : 'https://api.inap.ai';
            const res = await fetch(`${api}/api/chat/suggest`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ route: routeKey, context: { from: 'chat-widget' } })
            });
            if (!res.ok) return null;
            const data = await res.json();
            const txt = (data && data.text) ? String(data.text).trim() : '';
            return txt || null;
        } catch(_) { return null; }
    }

    handleInputKeydown(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            this.sendMessage();
        }
    }
    
    async sendMessage() {
        const chatInput = document.getElementById('chat-input');
        const message = chatInput.value.trim();
        
        if (!message) return;
        
        // Client-side quick navigation removed: only server decides

        this.addChatMessage(message, 'user');
        chatInput.value = '';
        this.addThinkingMessage();
        
        try {
            const token = localStorage.getItem('authToken');
            // Start chat request (do not await before navigation)
            const chatPromise = fetch(`${this.leonChatUrl}/api/chat/start`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    // No Authorization header needed; cookie will be sent
                },
                body: JSON.stringify({
                    message: message,
                    staff_id: 'Hotel Manager',
                    context: { 
                        current_page: window.location.pathname,
                        report_context: this.getReportContext(),
                        report_active_section: sessionStorage.getItem('LEON_REPORT_ACTIVE_SECTION') || null
                    }
                }),
                credentials: 'include' // <-- ensure cookies are sent
            });

            // Fire-and-forget: call simple navigation API without blocking
            fetch(`${this.leonChatUrl}/api/navigation/simple`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-NAV-CLIENT': 'leon-chat' },
                body: JSON.stringify({ instruction: message, context: { current_page: window.location.pathname }, source: 'leon-chat' }),
                credentials: 'include'
            }).then(r => r.json()).then(navData => {
                try {
                    if (navData && navData.route && navData.source === 'leon-chat') {
                        if (window.spaNavigate) {
                            window.spaNavigate(navData.route, navData.params || {});
                        } else {
                            const params = new URLSearchParams(navData.params || {});
                            const url = navData.route + (params.toString()?`?${params.toString()}`:'');
                            window.location.href = url;
                        }
                    }
                } catch(_) {}
            }).catch(()=>{});
            const resp = await chatPromise;
            this.removeThinkingMessage();
            if (resp && resp.ok) {
                const data = await resp.json().catch(() => null);
                if (data.final_message) {
                    this.addChatMessage(data.final_message, 'leon');
                }
                (data.approvals || []).forEach(item => this.mountApproval(item));
                if (data.screen && data.screen.button && (data.screen.card === 'function' || data.screen.card === 'menu')) {
                    this.mountWhatsAppList(data.screen);
                }
                await this.loadDesk();
                // If backend hints a refresh (tools executed), reload current page in SPA frame
                try {
                    if (data.refresh) {
                        if (window.spaNavigateEx) {
                            const key = this.getCurrentPageKey();
                            window.spaNavigateEx(key || 'dashboard.html', {}, { reload: true });
                        } else {
                            window.location.reload();
                        }
                    }
                } catch(_) {}
            } else {
                this.addChatMessage('Sorry, I\'m having trouble connecting right now.', 'leon');
            }
        } catch (error) {
            this.removeThinkingMessage();
            this.addChatMessage('Sorry, I\'m having trouble connecting right now.', 'leon');
        }
    }

    // quickNavRoute removed
    
    sendQuickMessage(message) {
        document.getElementById('chat-input').value = message;
        this.sendMessage();
    }
    
    // Modify addChatMessage to optionally skip saving (for history load)
    addChatMessage(message, sender, skipSave) {
        const chatMessages = document.getElementById('chat-messages');
        const isUser = sender === 'user';
        const row = document.createElement('div');
        row.className = 'flex mb-2 px-1' + (isUser ? ' justify-end' : '');
        const container = document.createElement('div');
        container.className = 'leon-msg ' + (isUser ? 'leon-user' : 'leon-assistant');

        const bubble = document.createElement('div');
        bubble.className = 'leon-bubble px-2 py-1 text-sm';
        bubble.dataset.raw = message;

        // Detect inline LEON report payload and strip it from display text
        let reportPayload = null;
        // Inline navigation disabled; no navigation triggers
        let displayText = message;
        if (!isUser) {
            const extracted = this.extractLeonReportFromMessage(message);
            if (extracted) {
                reportPayload = extracted.report;
                displayText = extracted.cleanText || '';
                // Persist latest report context for follow-up questions
                try {
                    this.lastReportPayload = reportPayload;
                    sessionStorage.setItem('LEON_REPORT_DATA', JSON.stringify(reportPayload));
                } catch (_) {}
            }
        }

        if (!isUser && this._depsLoaded && window.marked) {
            try {
                bubble.innerHTML = window.marked.parse(displayText);
            } catch (_) {
                bubble.innerHTML = `<p class="text-sm">${this.escapeHtml(displayText)}</p>`;
            }
        } else {
            bubble.innerHTML = `<p class="text-sm">${this.escapeHtml(displayText).replace(/\n/g, '<br>')}</p>`;
        }

        let tools = null;
        if (!isUser) {
            tools = document.createElement('div');
            tools.className = 'flex justify-end mt-1';
            const copy = document.createElement('button');
            copy.type = 'button';
            copy.className = 'text-xs text-gray-500 leon-copy-btn';
            copy.textContent = 'Copy';
            copy.addEventListener('click', () => navigator.clipboard.writeText(bubble.dataset.raw || ''));
            const pin = document.createElement('button');
            pin.type = 'button';
            pin.className = 'text-xs text-gray-500 ml-2';
            pin.textContent = 'Pin';
            pin.addEventListener('click', () => this.showPinChoices(message, tools));
            tools.appendChild(copy);
            tools.appendChild(pin);
        }

        const time = document.createElement('span');
        time.className = 'leon-time';
        const clock = new Date();
        const hour = String(clock.getHours()).padStart(2, '0');
        const minute = String(clock.getMinutes()).padStart(2, '0');
        time.textContent = hour + ':' + minute;
        bubble.appendChild(time);
        if (!isUser && tools) bubble.appendChild(tools);
        container.appendChild(bubble);
        row.appendChild(container);

        chatMessages.appendChild(row);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        // Save sanitized text (no inline directives) so history load won't retrigger navigation
        if (!skipSave) this.saveChatMessage(displayText, sender);

        // Offer full report CTA when payload exists
        if (reportPayload) {
            const cta = document.createElement('div');
            cta.className = 'mt-2 flex justify-end';
            const btn = document.createElement('button');
            btn.className = 'bg-blue-600 text-white px-3 py-1 rounded text-xs hover:bg-blue-700';
            btn.textContent = 'Open Full Report';
            btn.onclick = () => this.navigateToReport(reportPayload);
            cta.appendChild(btn);
            container.appendChild(cta);
        }

        // No inline navigation triggers

        // Render Mermaid inline only if explicitly enabled
        if (this.renderInlineCharts && !isUser && this._depsLoaded && window.mermaid) {
            this.renderMermaidIn(bubble);
        }
    }

    // Extract a LEON report payload delimited by :::leon-report ... ::: containing JSON
    extractLeonReportFromMessage(message) {
        try {
            const marker = ':::leon-report';
            const start = message.indexOf(marker);
            if (start === -1) return null;
            const after = start + marker.length;
            const end = message.indexOf(':::', after);
            if (end === -1) return null;
            const jsonStr = message.substring(after, end).trim();
            const report = JSON.parse(jsonStr);
            const cleanText = (message.substring(0, start) + message.substring(end + 3)).trim();
            return { cleanText, report };
        } catch (_) {
            return null;
        }
    }

    // Inline navigation extractor removed


    navigateToReport(report) {
        try {
            const data = encodeURIComponent(JSON.stringify(report));
            const base = '/reports/index.html';
            window.location.href = `${base}?data=${data}`;
        } catch (_) {
            sessionStorage.setItem('LEON_REPORT_DATA', JSON.stringify(report));
            window.location.href = '/reports/index.html';
        }
    }

    navigateToRoute(nav) {
        try {
            if (!nav || !nav.route) return;
            const route = nav.route.startsWith('/') ? nav.route : '/' + nav.route;
            const params = new URLSearchParams();
            for (const k of ['days','hotel','start','end','booking_id','guest_phone','room_type']) {
                if (nav[k]) params.set(k, String(nav[k]));
            }
            const qs = params.toString();
            const url = qs ? `${route}?${qs}` : route;
            window.location.href = url;
        } catch (_) {}
    }

    // Navigation API is intentionally not called from chat component

    // Removed status-based navigation and quick inference navigation

    // Chat SSE listener removed; navigation handled by global nav-listener.js only

    getReportContext() {
        // Priority: latest extracted → URL param on reports page → sessionStorage
        if (this.lastReportPayload) return this.lastReportPayload;
        try {
            const params = new URLSearchParams(location.search);
            const q = params.get('data');
            if (q) {
                const parsed = JSON.parse(decodeURIComponent(q));
                if (parsed) return parsed;
            }
        } catch (_) {}
        try {
            const sess = sessionStorage.getItem('LEON_REPORT_DATA');
            if (sess) return JSON.parse(sess);
        } catch (_) {}
        return null;
    }

    mountApproval(item) {
        const chatMessages = document.getElementById('chat-messages');
        if (!chatMessages || !item || !item.approval_id) return;
        const card = document.createElement('div');
        card.className = 'leon-approval';
        card.dataset.approvalId = item.approval_id;
        const title = document.createElement('h4');
        title.textContent = item.title || 'Approval';
        const message = document.createElement('p');
        message.textContent = item.message || '';
        const note = document.createElement('div');
        note.className = 'leon-approval-status';
        note.hidden = true;
        const actions = document.createElement('div');
        actions.className = 'leon-approval-actions';
        const approve = document.createElement('button');
        approve.type = 'button';
        approve.textContent = item.approve_label || 'Approve';
        const reject = document.createElement('button');
        reject.type = 'button';
        reject.className = 'reject';
        reject.textContent = item.reject_label || 'Cancel';
        const settle = (label) => {
            actions.remove();
            note.hidden = false;
            note.textContent = label;
        };
        const post = async (path) => {
            approve.disabled = true;
            reject.disabled = true;
            note.hidden = true;
            try {
                const response = await fetch(this.leonChatUrl + path, { method: 'POST', credentials: 'include' });
                const data = await response.json().catch(() => ({}));
                if (!response.ok || data.error) {
                    approve.disabled = false;
                    reject.disabled = false;
                    note.hidden = false;
                    note.textContent = data.error || 'This approval could not be completed.';
                    return;
                }
                const detail = (data.result && data.result.detail) || '';
                if (data.status === 'approved') settle('Approved.');
                else if (data.status === 'failed') settle(detail || 'The hotel did not complete this.');
                else if (data.status === 'rejected') settle('Cancelled.');
                else if (data.status === 'expired') settle('This approval expired.');
                else settle(detail || data.status || 'Done.');
            } catch (_) {
                approve.disabled = false;
                reject.disabled = false;
                note.hidden = false;
                note.textContent = 'This approval could not be completed.';
            }
        };
        approve.addEventListener('click', () => post('/api/leon/actions/' + encodeURIComponent(item.approval_id) + '/approve'));
        reject.addEventListener('click', () => post('/api/leon/actions/' + encodeURIComponent(item.approval_id) + '/reject'));
        actions.appendChild(approve);
        actions.appendChild(reject);
        card.appendChild(title);
        card.appendChild(message);
        card.appendChild(actions);
        card.appendChild(note);
        chatMessages.appendChild(card);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    escapeHtml(str) {
        return str
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    renderMermaidIn(container) {
        try {
            // Replace fenced mermaid code rendered by marked as <pre><code class="language-mermaid">...</code></pre>
            const codeBlocks = container.querySelectorAll('pre code.language-mermaid');
            codeBlocks.forEach((code) => {
                const graphDef = code.textContent;
                const wrapper = document.createElement('div');
                wrapper.className = 'mermaid';
                wrapper.textContent = graphDef;
                const pre = code.closest('pre');
                pre.parentNode.replaceChild(wrapper, pre);
            });
            if (container.querySelector('.mermaid')) {
                window.mermaid.run({ querySelector: container });
            }
        } catch (_) {}
    }

    addThinkingMessage() {
        // Only add if not already present
        if (document.getElementById('leon-thinking-msg')) return;
        const chatMessages = document.getElementById('chat-messages');
        const messageElement = document.createElement('div');
        messageElement.className = 'flex items-start space-x-3';
        messageElement.id = 'leon-thinking-msg';
        messageElement.innerHTML = `
            <div class="w-8 h-8 rounded-full overflow-hidden flex items-center justify-center"><img src="${this.leonAvatar}" alt="LEON" class="w-8 h-8 object-cover"/></div>
            <div class="flex-1">
                <div class="bg-gray-100 text-gray-800 rounded-lg p-3 max-w-xs">
                    <p class="text-sm italic" id="leon-thinking-text">Thinking</p>
                </div>
                <p class="text-xs text-gray-500 mt-1">Just now</p>
            </div>
        `;
        chatMessages.appendChild(messageElement);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        // Start animation
        this.startThinkingAnimation();
    }

    startThinkingAnimation() {
        this.stopThinkingAnimation();
        let dotCount = 0;
        this._thinkingInterval = setInterval(() => {
            dotCount = (dotCount + 1) % 4;
            const text = 'Thinking' + '.'.repeat(dotCount);
            const el = document.getElementById('leon-thinking-text');
            if (el) el.textContent = text;
        }, 400);
    }

    stopThinkingAnimation() {
        if (this._thinkingInterval) {
            clearInterval(this._thinkingInterval);
            this._thinkingInterval = null;
        }
    }

    removeThinkingMessage() {
        this.stopThinkingAnimation();
        const thinkingMsg = document.getElementById('leon-thinking-msg');
        if (thinkingMsg) thinkingMsg.remove();
    }
    
    setupEventListeners() {
        document.addEventListener('keydown', (e) => {
            if (e.altKey && e.key === 'c') {
                this.toggleChat();
            }
        });
    }

    initDrag(containerEl, handleEl) {
        this.dragging = false;
        this._dragMove = null;
        this._dragEnd = null;
        // Restore saved position (also done at render time)
        try { this.applySavedPosition(containerEl); } catch(_) {}
        const startDrag = (clientX, clientY) => {
            this.dragging = true;
            // Close widget while dragging to avoid overlay issues
            try { this.closeChat(); } catch(_) {}
            const rect = containerEl.getBoundingClientRect();
            this._dragOffsetX = clientX - rect.left;
            this._dragOffsetY = clientY - rect.top;
            const onMove = (mx, my) => {
                const vw = Math.max(document.documentElement.clientWidth, window.innerWidth || 0);
                const vh = Math.max(document.documentElement.clientHeight, window.innerHeight || 0);
                const w = rect.width;
                const h = rect.height;
                let x = mx - this._dragOffsetX;
                let y = my - this._dragOffsetY;
                x = Math.max(0, Math.min(vw - w, x));
                y = Math.max(0, Math.min(vh - h, y));
                containerEl.style.left = x + 'px';
                containerEl.style.top = y + 'px';
                containerEl.style.right = 'auto';
                containerEl.style.bottom = 'auto';
            };
            const onMouseMove = (e) => { onMove(e.clientX, e.clientY); };
            const onMouseUp = () => {
                this.dragging = false;
                document.removeEventListener('mousemove', onMouseMove);
                document.removeEventListener('mouseup', onMouseUp);
                // Save position
                try {
                    const left = parseInt(containerEl.style.left || '0', 10) || 0;
                    const top = parseInt(containerEl.style.top || '0', 10) || 0;
                    localStorage.setItem('LEON_CHAT_POS', JSON.stringify({ x: left, y: top }));
                } catch(_) {}
            };
            const onTouchMove = (e) => {
                if (!e.touches || !e.touches.length) return;
                onMove(e.touches[0].clientX, e.touches[0].clientY);
            };
            const onTouchEnd = () => {
                this.dragging = false;
                document.removeEventListener('touchmove', onTouchMove);
                document.removeEventListener('touchend', onTouchEnd);
                try {
                    const left = parseInt(containerEl.style.left || '0', 10) || 0;
                    const top = parseInt(containerEl.style.top || '0', 10) || 0;
                    localStorage.setItem('LEON_CHAT_POS', JSON.stringify({ x: left, y: top }));
                } catch(_) {}
            };
            document.addEventListener('mousemove', onMouseMove);
            document.addEventListener('mouseup', onMouseUp);
            document.addEventListener('touchmove', onTouchMove, { passive: false });
            document.addEventListener('touchend', onTouchEnd);
        };
        // Mouse
        handleEl.addEventListener('mousedown', (e) => {
            // Only start drag if left-click
            if (e.button !== 0) return;
            e.preventDefault();
            startDrag(e.clientX, e.clientY);
        });
        // Touch
        handleEl.addEventListener('touchstart', (e) => {
            if (!e.touches || !e.touches.length) return;
            // prevent scroll hijack while dragging
            e.preventDefault();
            startDrag(e.touches[0].clientX, e.touches[0].clientY);
        }, { passive: false });
    }

    applySavedPosition(containerEl){
        const saved = JSON.parse(localStorage.getItem('LEON_CHAT_POS') || 'null');
        if (saved && typeof saved.x === 'number' && typeof saved.y === 'number') {
            containerEl.style.left = saved.x + 'px';
            containerEl.style.top = saved.y + 'px';
            containerEl.style.right = 'auto';
            containerEl.style.bottom = 'auto';
        }
    }

    updateLeonAvatarImages() {
        try {
            const head = document.getElementById('leon-chat-header-avatar');
            if (head && head.tagName === 'IMG') head.src = this.leonAvatar;
            document.querySelectorAll('img.leon-assistant-avatar').forEach((img)=>{ img.src = this.leonAvatar; });
            const toggle = document.getElementById('leon-chat-toggle-avatar');
            if (toggle) toggle.src = this.leonAvatar;
        } catch(_) {}
    }
}

// Initialize LEON Chat
if (!window.__LEON_CHAT_INSTANCE__) {
    window.__LEON_CHAT_INSTANCE__ = new LeonChatComponent();
}
window.leonChat = window.__LEON_CHAT_INSTANCE__;
}