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
                if (widget) {
                    widget.style.display = 'block';
                    widget.style.transform = 'translateY(0)';
                    widget.classList.add('open');
                }
            }
        } catch(_) {}
        this.initialize();
    }
    
    initialize() {
        this.ensureDependencies().then(() => {
            this._depsLoaded = true;
        this.renderChatButton();
        this.setupEventListeners();
        this.loadProfileForChat().then(()=>{ try{ this.updateHeaderAvatar(); }catch(_){} });
        // Load chat history after rendering chat button
        setTimeout(() => this.loadChatHistory(), 0);
        // Start suggestion cycle
        this.startSuggestionCycle();
        }).catch(() => {
            // Even if deps fail, render basic chat
            this.renderChatButton();
            this.setupEventListeners();
            this.loadProfileForChat().then(()=>{ try{ this.updateHeaderAvatar(); }catch(_){} });
            setTimeout(() => this.loadChatHistory(), 0);
            this.startSuggestionCycle();
        });
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
            #leon-chat-widget { width: 25vw !important; min-width: 360px !important; height: 75vh !important; max-height: 90vh !important; display:none; transform: translateY(100%); transition: transform .25s ease; }
            #leon-chat-widget.open { display:block; transform: translateY(0); }
            #leon-chat-widget.leon-chat-fullscreen { width: min(900px, 95vw) !important; height: min(85vh, 900px) !important; }
            .chat-widget { max-width: 95vw; }
            /* Floating position and stacking above iframe */
            .floating-chat { position: fixed; bottom: 20px; right: 20px; z-index: 99999; }
            .floating-chat, .floating-chat * { pointer-events: auto; }
            /* Ensure inner container fills the widget */
            #leon-chat-widget > .flex { width: 100%; height: 100%; }
            #leon-chat-widget .flex.flex-col.h-full { width: 100%; height: 100%; }
            .leon-msg { max-width: 85%; font-size: 12px; line-height: 1.5; }
            /* Markdown normalization inside assistant bubbles */
            .leon-msg h1, .leon-msg h2, .leon-msg h3, .leon-msg h4, .leon-msg h5, .leon-msg h6 { margin: 0.25rem 0; font-weight: 600; }
            .leon-msg h1 { font-size: 1rem; }
            .leon-msg h2 { font-size: 0.95rem; }
            .leon-msg h3 { font-size: 0.92rem; }
            .leon-msg h4, .leon-msg h5, .leon-msg h6 { font-size: 0.9rem; }
            .leon-msg p, .leon-msg li { font-size: 0.8rem; margin: 0.25rem 0; }
            .leon-msg pre { background:#0b1020; color:#e5e7eb; padding:10px; border-radius:8px; overflow:auto; font-size: 10px; }
            .leon-msg code { background:#f3f4f6; padding:2px 4px; border-radius:4px; font-size: 10px; }
            .leon-msg table { border-collapse: collapse; width: 100%; font-size: 12px; }
            .leon-msg table th, .leon-msg table td { border: 1px solid #e5e7eb; padding: 6px; }
            .leon-msg ul, .leon-msg ol { padding-left: 1rem; }
            /* Chat bubbles */
            .leon-user .leon-bubble { background:#bbf7d0; color:#0b0b0b; }
            .leon-assistant .leon-bubble { background:#2563eb; color:#ffffff; border:1px solid rgba(255,255,255,0.08); }
            .leon-toolbar button { font-size:12px; }
            .leon-copy-btn { opacity:0.7; }
            .leon-copy-btn:hover { opacity:1; }
            /* Mermaid sizing */
            .leon-msg .mermaid { font-size: 12px; }
            /* Chat input textarea font normalization */
            .leon-input-row { display: grid; grid-template-columns: 1fr auto; column-gap: 8px; align-items: end; }
            #chat-input { font-size: 12px; width: 100%; max-width: 100%; box-sizing: border-box; }
            #leon-chat-widget .leon-input-row button { height: 36px; }
            .leon-textarea { min-height: 36px; max-height: 200px; resize: vertical; }
            /* Suggestion bubble */
            .leon-suggest { position:absolute; bottom: calc(100% + 10px); right: 0; background:#2563eb; color:#ffffff; border:0; box-shadow:0 8px 18px rgba(37,99,235,0.35); border-radius:9999px; padding:8px 12px; font-size:12px; white-space:nowrap; z-index:100000; opacity:0; transform: translateY(4px); transition: opacity .15s ease, transform .15s ease; }
            .leon-suggest.show { opacity:1; transform: translateY(0); }
            .leon-suggest::after { content:''; position:absolute; top:100%; right:14px; border-width:6px; border-style:solid; border-color:#2563eb transparent transparent transparent; }

            /* WhatsApp-style background for chat area */
            #chat-messages { 
                background-color: #eef1f4 !important;
                background-image:
                  radial-gradient(#e2e8f0 1.5px, transparent 1.5px),
                  radial-gradient(#e2e8f0 1.5px, transparent 1.5px);
                background-position: 0 0, 10px 10px;
                background-size: 20px 20px;
            }
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
                items.forEach(msg => this.addChatMessage(String(msg.text || ''), String(msg.sender || 'leon'), true));
                return;
            }
        } catch(_) {}
        // Fallback: load from local cache if backend not available
        try {
        const history = JSON.parse(localStorage.getItem('leon_chat_history') || '[]');
        history.forEach(msg => this.addChatMessage(msg.text, msg.sender, true));
        } catch(_) {}
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
        const chatMessages = document.getElementById('chat-messages');
        if (chatMessages) chatMessages.innerHTML = '';
    }

    renderChatButton() {
        const chatButton = document.createElement('div');
        chatButton.className = 'floating-chat';
        chatButton.innerHTML = `
            <button id="leon-chat-toggle" class="w-20 h-20 rounded-full shadow-lg overflow-hidden border border-white/50 bg-white transition-all duration-300 flex items-center justify-center">
                <img id="leon-chat-toggle-avatar" src="${this.leonAvatar}" alt="LEON" class="w-full h-full object-cover"/>
            </button>
            <div class="chat-widget mt-4" id="leon-chat-widget">
                <div class="flex flex-col h-full leon-chat-wide">
                    <div class="bg-blue-600 text-white p-3 flex items-center justify-between">
                        <div class="flex items-center">
                            <img id="leon-chat-header-avatar" src="${this.leonAvatar}" alt="LEON" class="w-16 h-16 rounded-full mr-3"/>
                            <div>
                                <h3 class="font-semibold">LEON</h3>
                                <p class="text-xs opacity-75"><span class="inline-block w-2 h-2 bg-green-500 rounded-full mr-1"></span>Online</p>
                            </div>
                        </div>
                        <div class="flex items-center gap-2 leon-toolbar">
                            <button onclick="leonChat.toggleSize()" class="px-2 py-1 bg-white/20 hover:bg-white/30 rounded text-xs">Resize</button>
                            <button onclick="leonChat.toggleChat()" class="text-white hover:text-gray-200"><i class="fas fa-times"></i></button>
                        </div>
                    </div>
                    
                    <div class="flex-1 overflow-y-auto p-4 space-y-4 bg-white" id="chat-messages">
                        <div class="flex items-start space-x-3">
                            <div class="w-16 h-16 rounded-full flex items-center justify-center overflow-hidden">
                                <img class="leon-assistant-avatar w-16 h-16 object-cover" src="${this.leonAvatar}" alt="LEON"/>
                            </div>
                            <div class="flex-1">
                                <div class="leon-msg leon-assistant">
                                    <div class="leon-bubble rounded-lg p-3">
                                        <div class="text-sm text-gray-800">Hello! I'm LEON, your hotel management assistant. How can I help you today?</div>
                                    </div>
                                    <p class="text-xs text-gray-500 mt-1">Just now</p>
                                </div>
                            </div>
                        </div>
                    </div>
                    
                    <div class="border-t p-3 bg-white">
                        <div class="leon-input-row">
                            <textarea 
                                   id="chat-input"
                                   placeholder="Type a message... (Enter to send, Shift+Enter for newline)"
                                   class="flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 leon-textarea"
                                   onkeydown="leonChat.handleInputKeydown(event)"></textarea>
                            <button onclick="leonChat.sendMessage()" 
                                    class="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 transition-colors">
                                <i class="fas fa-paper-plane"></i>
                            </button>
                        </div>
                        
                        
                    </div>
                </div>
            </div>
            <div id="leon-chat-suggest" class="leon-suggest" style="display:none;"></div>
        `;
        
        document.body.appendChild(chatButton);
        // Hover to open/close
        try {
            chatButton.addEventListener('mouseenter', () => { this.openChat(); });
            chatButton.addEventListener('mouseleave', (e) => {
                // Close only when truly leaving the floating container
                const rel = e.relatedTarget;
                if (!chatButton.contains(rel)) { this.closeChat(); }
            });
            chatButton.addEventListener('mouseenter', ()=> this.hideSuggestion());
        } catch(_) {}
    }
    
    toggleChat() {
        // Legacy click toggle (no longer used)
        this.chatOpen = !this.chatOpen;
        const widget = document.getElementById('leon-chat-widget');
        const appFrame = document.getElementById('app-frame');
        if (this.chatOpen) {
            widget.classList.add('open');
            if (appFrame) appFrame.style.pointerEvents = 'none';
        } else {
            widget.classList.remove('open');
            if (appFrame) appFrame.style.pointerEvents = 'auto';
        }
        try { sessionStorage.setItem('LEON_CHAT_OPEN', this.chatOpen ? '1' : '0'); } catch(_) {}
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
        if (widget) widget.classList.remove('open');
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
        // Align LEON (assistant) left, USER right
        row.className = isUser ? 'flex items-start gap-3 justify-end' : 'flex items-start gap-3';
        const avatar = document.createElement('div');
        if (isUser) {
            avatar.className = 'w-12 h-12 rounded-full flex items-center justify-center overflow-hidden';
            avatar.innerHTML = '<i class="fas fa-user text-blue-600 text-lg"></i>';
        } else {
            avatar.className = 'w-8 h-8 rounded-full flex items-center justify-center overflow-hidden';
            avatar.innerHTML = '<img src="'+this.leonAvatar+'" alt="LEON" class="w-8 h-8 object-cover"/>';
        }

        const container = document.createElement('div');
        // Remove flex-1 so the bubble aligns starting right next to its own avatar
        container.className = 'leon-msg ' + (isUser ? 'leon-user' : 'leon-assistant');

        const bubble = document.createElement('div');
        bubble.className = 'leon-bubble rounded-lg p-3';
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

        // Copy button for assistant messages
        if (!isUser) {
            const tools = document.createElement('div');
            tools.className = 'flex justify-end mt-1';
            tools.innerHTML = `<button class="text-xs text-gray-500 leon-copy-btn" onclick="navigator.clipboard.writeText(this.closest('.leon-msg').querySelector('.leon-bubble').dataset.raw)">Copy</button>`;
            container.appendChild(tools);
        }

        const time = document.createElement('p');
        time.className = 'text-xs text-gray-500 mt-1';
        time.textContent = 'Just now';

        container.appendChild(bubble);
        container.appendChild(time);

        if (isUser) {
            // For user messages: message bubble before avatar, aligned to the right
            row.appendChild(container);
            row.appendChild(avatar);
        } else {
            // For LEON messages: avatar on the left, message on the right
            row.appendChild(avatar);
            row.appendChild(container);
        }

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