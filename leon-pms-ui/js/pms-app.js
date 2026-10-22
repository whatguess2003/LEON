// Main PMS Application
// Add local/production API URL switcher
const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
const LOCAL_API_BASE = 'http://localhost:8002/api';
const LOCAL_LEON_CHAT_URL = 'http://localhost:8002';
const LOCAL_WS_URL = 'ws://localhost:8002/ws';

// Default to production
let apiBase = 'https://api.inap.ai/api';
let leonChatUrl = 'https://api.inap.ai';
let wsUrl = 'wss://api.inap.ai/ws';

if (isLocal) {
  apiBase = LOCAL_API_BASE;
  leonChatUrl = LOCAL_LEON_CHAT_URL;
  wsUrl = LOCAL_WS_URL;
}

// Expose API roots globally for non-Alpine modules
try {
  window.apiRoot = apiBase;
  window.leonChatRoot = leonChatUrl;
  window.wsRoot = wsUrl;
} catch (e) {
  // no-op
}

function pmsApp() {
    return {
        // UTC-only formatting
        formatWithTz(isoUtc, tz, options = {}) {
            try {
                if (!isoUtc) return '';
                const d = new Date(isoUtc);
                const fmt = new Intl.DateTimeFormat(undefined, { timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', ...options });
                return fmt.format(d);
            } catch (e) {
                try { return new Date(isoUtc).toLocaleString(); } catch { return String(isoUtc); }
            }
        },
        // Application State
        currentView: 'dashboard',
        chatOpen: false,
        showNotifications: false,
        showToast: false,
        toastMessage: '',
        
        // User Data
        user: {
            name: 'Hotel Manager',
            role: 'Property Manager',
            hotel: 'Grand Hotel'
        },
        
        // Application Data
        rooms: [],
        bookings: [],
        guests: [],
        analytics: {},
        notifications: [],
        
        // API Configuration
        apiBase: apiBase,
        leonChatUrl: leonChatUrl,
        wsUrl: wsUrl,
        
        // LEON Chat Configuration
        leonStatus: 'online',
        
        // Initialization
        init() {
            console.log('🚀 Initializing LEON PMS Application');
            this.loadInitialData();
            this.initializeWebSocket();
            this.initializeLeonChat();
            this.setupEventListeners();
            this.checkLeonStatus();
        },
        
        // Load Initial Data
        async loadInitialData() {
            try {
                // Load hotels first
                await this.loadHotels();
                
                // Then load other data that depends on hotels
                await Promise.all([
                    this.loadNotifications(),
                    this.loadUserPreferences()
                ]);
                
                // Signal that hotels are loaded
                // this.hotelsLoaded = true; // Removed as per edit hint
                
                // Trigger rooms loading if we're on rooms page
                if (typeof window.rooms !== 'undefined') {
                    await window.rooms.loadRoomsData();
                }
            } catch (error) {
                console.error('Error loading initial data:', error);
                this.showNotification('Error loading application data', 'error');
            }
        },
        
        // API Methods
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
        },
        
        // Hotel Management
        async loadHotels() {
            try {
                const data = await this.apiRequest('/hotels');
                // this.hotels = data.hotels || []; // Removed as per edit hint
                console.log('✅ Hotels loaded:', data.hotels?.length || 0);
            } catch (error) {
                console.error('Error loading hotels:', error);
            }
        },
        
        async loadHotelData() {
            try {
                const data = await this.apiRequest(`/hotels`); // or /hotels/info if you have a dedicated endpoint
                return data;
            } catch (error) {
                console.error('Error loading hotel data:', error);
                return null;
            }
        },
        
        // Room Management
        async loadRooms() {
            try {
                const data = await this.apiRequest(`/hotels/rooms`);
                this.rooms = data.room_types?.flatMap(rt => (rt.room_numbers || []).map(rn => ({...rn, room_type: rt.room_type}))) || [];
                console.log('✅ Rooms loaded:', this.rooms.length);
                return this.rooms;
            } catch (error) {
                console.error('Error loading rooms:', error);
                return [];
            }
        },
        
        async updateRoomStatus(hotelId, roomId, status) {
            try {
                await this.apiRequest(`/hotels/${hotelId}/update-room-status`, {
                    method: 'POST',
                    body: JSON.stringify({
                        room_id: roomId,
                        status: status
                    })
                });
                
                this.showNotification(`Room ${roomId} status updated to ${status}`, 'success');
                this.loadRooms(); // Refresh rooms data
            } catch (error) {
                console.error('Error updating room status:', error);
                this.showNotification('Error updating room status', 'error');
            }
        },
        
        // Booking Management
        async loadBookings() {
            try {
                const data = await this.apiRequest(`/hotels/bookings-calendar?start_date=${this.getCurrentDate()}&days=30`);
                this.bookings = data.bookings || [];
                console.log('✅ Bookings loaded:', this.bookings.length);
                return this.bookings;
            } catch (error) {
                console.error('Error loading bookings:', error);
                return [];
            }
        },
        
        async createBooking(hotelId, bookingData) {
            try {
                const response = await this.apiRequest(`/hotels/${hotelId}/bookings`, {
                    method: 'POST',
                    body: JSON.stringify(bookingData)
                });
                
                this.showNotification('Booking created successfully', 'success');
                this.loadBookings(); // Refresh bookings
                return response;
            } catch (error) {
                console.error('Error creating booking:', error);
                this.showNotification('Error creating booking', 'error');
                throw error;
            }
        },
        
        // Guest Management
        async loadGuests() {
            try {
                const data = await this.apiRequest(`/hotels/guests`);
                this.guests = data.guests || [];
                console.log('✅ Guests loaded:', this.guests.length);
                return this.guests;
            } catch (error) {
                console.error('Error loading guests:', error);
                return [];
            }
        },
        
        // Analytics
        async loadAnalytics() {
            try {
                const data = await this.apiRequest(`/hotels/analytics?days=30`);
                this.analytics = data;
                console.log('✅ Analytics loaded');
                return this.analytics;
            } catch (error) {
                console.error('Error loading analytics:', error);
                return {};
            }
        },
        
        // LEON Chat Integration
        initializeLeonChat() {
            console.log('🤖 Initializing LEON Chat Integration');
            
            // Check if LEON chat widget exists
            if (document.getElementById('leon-chat-widget')) {
                this.renderLeonChatWidget();
            }
        },
        
        renderLeonChatWidget() {
            const chatWidget = document.getElementById('leon-chat-widget');
            if (!chatWidget) return;
            
            chatWidget.innerHTML = `
                <div class="flex flex-col h-full">
                    <!-- Chat Header -->
                    <div class="bg-blue-600 text-white p-4 flex items-center justify-between">
                        <div class="flex items-center">
                            <div class="w-8 h-8 bg-white rounded-full flex items-center justify-center mr-3">
                                <i class="fas fa-robot text-blue-600"></i>
                            </div>
                            <div>
                                <h3 class="font-semibold">LEON Assistant</h3>
                                <p class="text-xs opacity-75">Online • Ready to help</p>
                            </div>
                        </div>
                        <button @click="toggleChat()" class="text-white hover:text-gray-200">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    
                    <!-- Chat Messages -->
                    <div class="flex-1 overflow-y-auto p-4 space-y-4" id="chat-messages">
                        <div class="flex items-start space-x-3">
                            <div class="w-8 h-8 bg-blue-100 rounded-full flex items-center justify-center">
                                <i class="fas fa-robot text-blue-600 text-sm"></i>
                            </div>
                            <div class="flex-1">
                                <div class="bg-gray-100 rounded-lg p-3 max-w-xs">
                                    <p class="text-sm text-gray-800">Hello! I'm LEON, your hotel management assistant. How can I help you today?</p>
                                </div>
                                <p class="text-xs text-gray-500 mt-1">Just now</p>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Chat Input -->
                    <div class="border-t p-4">
                        <div class="flex space-x-2">
                            <input type="text" 
                                   id="chat-input"
                                   placeholder="Type a message..."
                                   class="flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                                   @keydown.enter="sendMessageToLeon()">
                            <button @click="sendMessageToLeon()" 
                                    class="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 transition-colors">
                                <i class="fas fa-paper-plane"></i>
                            </button>
                        </div>
                        
                        <!-- Quick Actions -->
                        <div class="mt-3 flex flex-wrap gap-2">
                            <button @click="sendQuickMessage('Show me today\\'s check-ins')" 
                                    class="bg-gray-100 text-gray-700 px-3 py-1 rounded-full text-xs hover:bg-gray-200 transition-colors">
                                Today's Check-ins
                            </button>
                            <button @click="sendQuickMessage('Room availability status')" 
                                    class="bg-gray-100 text-gray-700 px-3 py-1 rounded-full text-xs hover:bg-gray-200 transition-colors">
                                Room Status
                            </button>
                            <button @click="sendQuickMessage('Update room prices')" 
                                    class="bg-gray-100 text-gray-700 px-3 py-1 rounded-full text-xs hover:bg-gray-200 transition-colors">
                                Update Prices
                            </button>
                        </div>
                    </div>
                </div>
            `;
        },
        
        async sendMessageToLeon() {
            const chatInput = document.getElementById('chat-input');
            const message = chatInput.value.trim();
            
            if (!message) return;
            
            // Add user message to chat
            this.addChatMessage(message, 'user');
            chatInput.value = '';
            
            try {
                // Send message to LEON
                const response = await fetch(`${this.leonChatUrl}/api/chat`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        message: message,
                        staff_id: this.user.name,
                        context: {
                            current_view: this.currentView,
                            hotel_id: this.getCurrentHotelId(), // This will now be 'marina_heights' or 'grand_hotel'
                            user_role: this.user.role
                        }
                    })
                });
                
                if (response.ok) {
                    const data = await response.json();
                    if (Array.isArray(data.messages)) {
                        data.messages.forEach(msg => this.addChatMessage(msg, 'leon'));
                    } else if (data.message) {
                        this.addChatMessage(data.message, 'leon');
                    }
                    // Handle LEON actions
                    if (data.action) {
                        this.handleLeonAction(data.action);
                    }
                } else {
                    this.addChatMessage('Sorry, I\'m having trouble connecting right now. Please try again.', 'leon');
                }
            } catch (error) {
                console.error('Error sending message to LEON:', error);
                this.addChatMessage('Sorry, I\'m having trouble connecting right now. Please try again.', 'leon');
            }
        },
        
        sendQuickMessage(message) {
            const chatInput = document.getElementById('chat-input');
            chatInput.value = message;
            this.sendMessageToLeon();
        },
        
        addChatMessage(message, sender) {
            const chatMessages = document.getElementById('chat-messages');
            if (!chatMessages) return;
            
            const messageElement = document.createElement('div');
            messageElement.className = 'flex items-start space-x-3';
            
            const isUser = sender === 'user';
            const avatar = isUser ? 
                '<div class="w-8 h-8 bg-blue-600 rounded-full flex items-center justify-center"><i class="fas fa-user text-white text-sm"></i></div>' :
                '<div class="w-8 h-8 bg-blue-100 rounded-full flex items-center justify-center"><i class="fas fa-robot text-blue-600 text-sm"></i></div>';
            
            messageElement.innerHTML = `
                ${avatar}
                <div class="flex-1">
                    <div class="${isUser ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-800'} rounded-lg p-3 max-w-xs">
                        <p class="text-sm">${message}</p>
                    </div>
                    <p class="text-xs text-gray-500 mt-1">Just now</p>
                </div>
            `;
            
            chatMessages.appendChild(messageElement);
            chatMessages.scrollTop = chatMessages.scrollHeight;
        },
        
        handleLeonAction(action) {
            switch (action.type) {
                case 'navigate':
                    this.currentView = action.view;
                    break;
                case 'update_room_status':
                    this.updateRoomStatus(action.hotel_id, action.room_id, action.status);
                    break;
                case 'show_booking':
                    this.showBookingDetails(action.booking_id);
                    break;
                case 'refresh_data':
                    this.loadInitialData();
                    break;
                default:
                    console.log('Unknown LEON action:', action);
            }
        },
        
        async checkLeonStatus() {
            try {
                const response = await fetch(`${this.leonChatUrl}/health`);
                this.leonStatus = response.ok ? 'online' : 'offline';
            } catch (error) {
                this.leonStatus = 'offline';
            }
        },
        
        // Chat Controls
        toggleChat() {
            this.chatOpen = !this.chatOpen;
        },
        
        // Notification System
        showNotification(message, type = 'success') {
            this.toastMessage = message;
            this.showToast = true;
            
            // Auto-hide after 5 seconds
            setTimeout(() => {
                this.hideToast();
            }, 5000);
        },
        
        hideToast() {
            this.showToast = false;
        },
        
        async loadNotifications() {
            // Load notifications from API or local storage
            this.notifications = [
                { id: 1, message: 'New booking received', type: 'info', timestamp: new Date() },
                { id: 2, message: 'Room maintenance required', type: 'warning', timestamp: new Date() }
            ];
        },
        
        // WebSocket for Real-time Updates
        initializeWebSocket() {
            console.log('🔌 Initializing WebSocket for real-time updates');
            
            // Initialize WebSocket connection for real-time updates
            if (typeof WebSocket !== 'undefined') {
                try {
                    this.ws = new WebSocket(this.wsUrl); // Use this.wsUrl
                    
                    this.ws.onopen = () => {
                        console.log('✅ WebSocket connected');
                    };
                    
                    this.ws.onmessage = (event) => {
                        const data = JSON.parse(event.data);
                        this.handleWebSocketMessage(data);
                    };
                    
                    this.ws.onclose = () => {
                        console.log('❌ WebSocket disconnected');
                    };
                } catch (error) {
                    console.warn('WebSocket not available:', error);
                }
            }
        },
        
        handleWebSocketMessage(data) {
            switch (data.type) {
                case 'booking_update':
                    this.loadBookings();
                    this.showNotification('Booking updated', 'info');
                    break;
                case 'room_status_change':
                    this.loadRooms();
                    this.showNotification(`Room ${data.room_id} status changed`, 'info');
                    break;
                case 'new_notification':
                    this.notifications.unshift(data.notification);
                    this.showNotification(data.notification.message, data.notification.type);
                    break;
                default:
                    console.log('Unknown WebSocket message:', data);
            }
        },
        
        // Utility Methods
        getCurrentDate() {
            return new Date().toISOString().split('T')[0];
        },
        
        getCurrentHotelId() {
            // This method is no longer needed as hotel context is handled by LEON
            return 'marina_heights'; // Default fallback
        },
        
        formatDate(date) {
            return new Date(date).toLocaleDateString();
        },
        
        formatCurrency(amount) {
            return new Intl.NumberFormat('en-US', {
                style: 'currency',
                currency: 'USD'
            }).format(amount);
        },
        
        // User Preferences
        async loadUserPreferences() {
            try {
                const prefs = localStorage.getItem('leon-pms-preferences');
                if (prefs) {
                    const preferences = JSON.parse(prefs);
                    this.user = { ...this.user, ...preferences.user };
                }
            } catch (error) {
                console.error('Error loading user preferences:', error);
            }
        },
        
        saveUserPreferences() {
            try {
                const preferences = {
                    user: this.user,
                    currentView: this.currentView
                };
                localStorage.setItem('leon-pms-preferences', JSON.stringify(preferences));
            } catch (error) {
                console.error('Error saving user preferences:', error);
            }
        },
        
        // Event Listeners
        setupEventListeners() {
            // Auto-save preferences on view change
            this.$watch('currentView', () => {
                this.saveUserPreferences();
            });
            
            // Keyboard shortcuts
            document.addEventListener('keydown', (e) => {
                if (e.altKey) {
                    switch (e.key) {
                        case '1':
                            this.currentView = 'dashboard';
                            break;
                        case '2':
                            this.currentView = 'rooms';
                            break;
                        case '3':
                            this.currentView = 'bookings';
                            break;
                        case '4':
                            this.currentView = 'guests';
                            break;
                        case '5':
                            this.currentView = 'operations';
                            break;
                        case '6':
                            this.currentView = 'analytics';
                            break;
                        case 'c':
                            this.toggleChat();
                            break;
                    }
                }
            });
        }
    }
} 