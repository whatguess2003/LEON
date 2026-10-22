// Dashboard Component
class DashboardComponent {
    constructor(app) {
        this.app = app;
        this.charts = {};
        this.refreshInterval = null;
        this.initialize();
    }
    
    initialize() {
        this.renderDashboard();
        this.setupAutoRefresh();
        this.initializeCharts();
        this.loadDashboardData();
    }
    
    renderDashboard() {
        const dashboardContent = document.getElementById('dashboard-content');
        if (!dashboardContent) return;
        
        dashboardContent.innerHTML = `
            <!-- Dashboard Header -->
            <div class="flex justify-between items-center mb-8">
                <div>
                    <h1 class="text-3xl font-bold text-gray-900">Hotel Dashboard</h1>
                    <p class="text-gray-600 mt-1">Welcome back, ${this.app.user.name}. Here's what's happening today.</p>
                </div>
                <div class="flex space-x-4">
                    <button onclick="dashboard.refreshData()" 
                            class="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 transition-colors flex items-center">
                        <i class="fas fa-sync-alt mr-2"></i>
                        Refresh
                    </button>
                    <button onclick="dashboard.askLeonForInsights()" 
                            class="bg-green-600 text-white px-4 py-2 rounded-lg hover:bg-green-700 transition-colors flex items-center">
                        <i class="fas fa-robot mr-2"></i>
                        Ask LEON
                    </button>
                </div>
            </div>
            
            <!-- Key Performance Indicators -->
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
                <!-- Total Rooms -->
                <div class="card p-6">
                    <div class="flex items-center justify-between">
                        <div>
                            <p class="text-sm font-medium text-gray-500 uppercase tracking-wide">Total Rooms</p>
                            <p class="text-2xl font-bold text-gray-900 mt-2" id="total-rooms">-</p>
                        </div>
                        <div class="w-12 h-12 bg-blue-100 rounded-lg flex items-center justify-center">
                            <i class="fas fa-bed text-blue-600"></i>
                        </div>
                    </div>
                    <div class="mt-4">
                        <span class="text-sm text-gray-500">Across all properties</span>
                    </div>
                </div>
                
                <!-- Occupancy Rate -->
                <div class="card p-6">
                    <div class="flex items-center justify-between">
                        <div>
                            <p class="text-sm font-medium text-gray-500 uppercase tracking-wide">Occupancy Rate</p>
                            <p class="text-2xl font-bold text-gray-900 mt-2" id="occupancy-rate">-</p>
                        </div>
                        <div class="w-12 h-12 bg-green-100 rounded-lg flex items-center justify-center">
                            <i class="fas fa-chart-line text-green-600"></i>
                        </div>
                    </div>
                    <div class="mt-4">
                        <span class="text-sm text-green-600" id="occupancy-trend">+2.5% from yesterday</span>
                    </div>
                </div>
                
                <!-- Today's Check-ins -->
                <div class="card p-6">
                    <div class="flex items-center justify-between">
                        <div>
                            <p class="text-sm font-medium text-gray-500 uppercase tracking-wide">Today's Check-ins</p>
                            <p class="text-2xl font-bold text-gray-900 mt-2" id="todays-checkins">-</p>
                        </div>
                        <div class="w-12 h-12 bg-yellow-100 rounded-lg flex items-center justify-center">
                            <i class="fas fa-sign-in-alt text-yellow-600"></i>
                        </div>
                    </div>
                    <div class="mt-4">
                        <span class="text-sm text-gray-500" id="checkin-status">0 completed, 0 pending</span>
                    </div>
                </div>
                
                <!-- Revenue Today -->
                <div class="card p-6">
                    <div class="flex items-center justify-between">
                        <div>
                            <p class="text-sm font-medium text-gray-500 uppercase tracking-wide">Revenue Today</p>
                            <p class="text-2xl font-bold text-gray-900 mt-2" id="revenue-today">-</p>
                        </div>
                        <div class="w-12 h-12 bg-purple-100 rounded-lg flex items-center justify-center">
                            <i class="fas fa-dollar-sign text-purple-600"></i>
                        </div>
                    </div>
                    <div class="mt-4">
                        <span class="text-sm text-purple-600" id="revenue-trend">+15% from yesterday</span>
                    </div>
                </div>
            </div>
            
            <!-- Charts and Quick Actions Row -->
            <div class="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
                <!-- Occupancy Chart -->
                <div class="lg:col-span-2 card p-6">
                    <div class="flex items-center justify-between mb-4">
                        <h3 class="text-lg font-semibold text-gray-900">Occupancy Trend</h3>
                        <select id="chart-period" class="border rounded-lg px-3 py-1 text-sm">
                            <option value="7">Last 7 days</option>
                            <option value="30">Last 30 days</option>
                            <option value="90">Last 90 days</option>
                        </select>
                    </div>
                    <div class="relative">
                        <canvas id="occupancy-chart" height="200"></canvas>
                    </div>
                </div>
                
                <!-- Quick Actions -->
                <div class="card p-6">
                    <h3 class="text-lg font-semibold text-gray-900 mb-4">Quick Actions</h3>
                    <div class="space-y-3">
                        <button onclick="dashboard.quickAction('new-booking')" 
                                class="w-full bg-blue-50 hover:bg-blue-100 text-blue-700 font-medium py-3 px-4 rounded-lg transition-colors flex items-center">
                            <i class="fas fa-plus mr-3"></i>
                            New Booking
                        </button>
                        <button onclick="dashboard.quickAction('check-in')" 
                                class="w-full bg-green-50 hover:bg-green-100 text-green-700 font-medium py-3 px-4 rounded-lg transition-colors flex items-center">
                            <i class="fas fa-sign-in-alt mr-3"></i>
                            Check-in Guest
                        </button>
                        <button onclick="dashboard.quickAction('room-status')" 
                                class="w-full bg-yellow-50 hover:bg-yellow-100 text-yellow-700 font-medium py-3 px-4 rounded-lg transition-colors flex items-center">
                            <i class="fas fa-bed mr-3"></i>
                            Update Room Status
                        </button>
                        <button onclick="dashboard.quickAction('maintenance')" 
                                class="w-full bg-red-50 hover:bg-red-100 text-red-700 font-medium py-3 px-4 rounded-lg transition-colors flex items-center">
                            <i class="fas fa-tools mr-3"></i>
                            Report Maintenance
                        </button>
                        <button onclick="dashboard.quickAction('leon-chat')" 
                                class="w-full bg-purple-50 hover:bg-purple-100 text-purple-700 font-medium py-3 px-4 rounded-lg transition-colors flex items-center">
                            <i class="fas fa-robot mr-3"></i>
                            Ask LEON
                        </button>
                    </div>
                </div>
            </div>
            
            <!-- Recent Activity and Alerts -->
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
                <!-- Recent Activity -->
                <div class="card p-6">
                    <h3 class="text-lg font-semibold text-gray-900 mb-4">Recent Activity</h3>
                    <div class="space-y-4" id="recent-activity">
                        <!-- Activity items will be populated here -->
                    </div>
                    <div class="mt-4 text-center">
                        <button onclick="dashboard.viewAllActivity()" 
                                class="text-blue-600 hover:text-blue-800 text-sm font-medium">
                            View All Activity
                        </button>
                    </div>
                </div>
                
                <!-- Alerts and Notifications -->
                <div class="card p-6">
                    <h3 class="text-lg font-semibold text-gray-900 mb-4">Alerts & Notifications</h3>
                    <div class="space-y-3" id="alerts-list">
                        <!-- Alerts will be populated here -->
                    </div>
                    <div class="mt-4 text-center">
                        <button onclick="dashboard.viewAllAlerts()" 
                                class="text-blue-600 hover:text-blue-800 text-sm font-medium">
                            View All Alerts
                        </button>
                    </div>
                </div>
            </div>
            
            <!-- Room Status Overview -->
            <div class="card p-6">
                <div class="flex items-center justify-between mb-4">
                    <h3 class="text-lg font-semibold text-gray-900">Room Status Overview</h3>
                    <div class="flex space-x-2">
                        <button onclick="dashboard.filterRooms('all')" 
                                class="px-3 py-1 text-sm bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors">
                            All Rooms
                        </button>
                        <button onclick="dashboard.filterRooms('available')" 
                                class="px-3 py-1 text-sm bg-green-100 text-green-700 rounded-lg hover:bg-green-200 transition-colors">
                            Available
                        </button>
                        <button onclick="dashboard.filterRooms('occupied')" 
                                class="px-3 py-1 text-sm bg-yellow-100 text-yellow-700 rounded-lg hover:bg-yellow-200 transition-colors">
                            Occupied
                        </button>
                        <button onclick="dashboard.filterRooms('maintenance')" 
                                class="px-3 py-1 text-sm bg-red-100 text-red-700 rounded-lg hover:bg-red-200 transition-colors">
                            Maintenance
                        </button>
                    </div>
                </div>
                <div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8 gap-4" id="room-grid">
                    <!-- Room cards will be populated here -->
                </div>
            </div>
        `;
    }
    
    apiBase() {
        const host = window.location.hostname;
        const local = host === 'localhost' || host === '127.0.0.1';
        return local ? 'http://localhost:8002/api' : 'https://api.inap.ai/api';
    }

    async loadDashboardData() {
        try {
            const api = this.apiBase();
            const meRes = await fetch(`${api}/auth/me`, { credentials: 'include' });
            const me = meRes.ok ? await meRes.json() : {};
            this.hotel = String(me.hotel_name || '').toUpperCase();
            const [roomsRes, bookingsRes, analyticsRes, maintRes] = await Promise.all([
                fetch(`${api}/hotels/rooms?hotel_name=${encodeURIComponent(this.hotel)}`, { credentials: 'include' }),
                fetch(`${api}/bookings-calendar`, { credentials: 'include' }),
                fetch(`${api}/analytics?days=7`, { credentials: 'include' }),
                fetch(`${api}/maintenance/list?hotel_name=${encodeURIComponent(this.hotel)}`, { credentials: 'include' })
            ]);
            const roomsBody = roomsRes.ok ? await roomsRes.json() : {};
            const rooms = (roomsBody.room_types || []).flatMap(rt => (rt.room_numbers || []).map(rn => Object.assign({ room_type: rt.room_type }, rn)));
            const bookings = bookingsRes.ok ? ((await bookingsRes.json()).bookings || []) : [];
            const analytics = analyticsRes.ok ? await analyticsRes.json() : {};
            const maint = maintRes.ok ? await maintRes.json() : {};
            this.rooms = rooms;
            this.bookings = bookings;
            this.analytics = analytics;
            this.maintenanceText = String(maint.result || '');
            this.updateKPIs(null, analytics, rooms, bookings);
            this.updateRecentActivity(bookings);
            this.updateAlerts();
            this.updateRoomGrid(rooms);
            this.updateCharts(analytics);
        } catch (error) {
            console.error('Error loading dashboard data:', error);
        }
    }
    
    updateKPIs(hotelData, analytics, rooms, bookings) {
        // Total Rooms
        document.getElementById('total-rooms').textContent = rooms.length;
        
        // Occupancy Rate
        const occupiedRooms = rooms.filter(room => String(room.room_status || '').toLowerCase() === 'occupied').length;
        const occupancyRate = rooms.length > 0 ? Math.round((occupiedRooms / rooms.length) * 100) : 0;
        document.getElementById('occupancy-rate').textContent = `${occupancyRate}%`;
        const trend = document.getElementById('occupancy-trend');
        if (trend) trend.textContent = `${occupiedRooms} occupied of ${rooms.length}`;
        
        const today = new Date();
        const iso = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
        const todayArrivals = bookings.filter(booking => String(booking.check_in || '').slice(0, 10) === iso && String(booking.status || '').toLowerCase() !== 'cancelled');
        document.getElementById('todays-checkins').textContent = todayArrivals.length;
        const todayRevenue = todayArrivals.reduce((sum, booking) => sum + Number(booking.amount || 0), 0);
        document.getElementById('revenue-today').textContent = `RM${todayRevenue.toLocaleString()}`;
        const revenueTrend = document.getElementById('revenue-trend');
        if (revenueTrend) revenueTrend.textContent = 'From arrivals today';
        document.getElementById('checkin-status').textContent = `${todayArrivals.length} arriving`;
        const countOf = (name) => rooms.filter(room => String(room.room_status || '').toLowerCase() === name).length;
        const availableEl = document.getElementById('available-count');
        const occupiedEl = document.getElementById('occupied-count');
        const maintenanceEl = document.getElementById('maintenance-count');
        if (availableEl) availableEl.textContent = countOf('clean');
        if (occupiedEl) occupiedEl.textContent = occupiedRooms;
        if (maintenanceEl) maintenanceEl.textContent = countOf('dirty') + countOf('cleaning');
    }
    
    updateRecentActivity(bookings) {
        const recentActivity = document.getElementById('recent-activity');
        if (!recentActivity) return;
        
        // Get recent bookings and activities
        const recentBookings = bookings.slice(0, 5);
        
        const activityHTML = recentBookings.map(booking => {
            return `
                <div class="flex items-center space-x-3">
                    <div class="w-8 h-8 bg-blue-100 rounded-full flex items-center justify-center">
                        <i class="fas fa-calendar-check text-blue-600 text-sm"></i>
                    </div>
                    <div class="flex-1">
                        <p class="text-sm font-medium text-gray-900">
                            ${booking.guest_name || booking.guest_phone || 'Guest'}
                        </p>
                        <p class="text-xs text-gray-500">${booking.check_in || ''} · ${booking.room_number || ''}</p>
                    </div>
                    <div class="text-right">
                        <p class="text-sm font-medium text-gray-900">RM${Number(booking.amount || 0).toLocaleString()}</p>
                    </div>
                </div>
            `;
        }).join('');
        
        recentActivity.innerHTML = activityHTML || '<p class="text-gray-500 text-sm">No recent activity</p>';
    }
    
    updateAlerts() {
        const alertsList = document.getElementById('alerts-list');
        if (!alertsList) return;
        
        const dirty = (this.rooms || []).filter(room => String(room.room_status || '').toLowerCase() === 'dirty').length;
        const cleaning = (this.rooms || []).filter(room => String(room.room_status || '').toLowerCase() === 'cleaning').length;
        const arrivals = document.getElementById('todays-checkins') ? document.getElementById('todays-checkins').textContent : '0';
        const alerts = [];
        if (dirty) alerts.push({ type: 'warning', message: `${dirty} room${dirty === 1 ? '' : 's'} dirty`, urgent: false });
        if (cleaning) alerts.push({ type: 'info', message: `${cleaning} room${cleaning === 1 ? '' : 's'} being cleaned`, urgent: false });
        alerts.push({ type: 'info', message: `${arrivals} arrival${arrivals === '1' ? '' : 's'} today`, urgent: false });
        if (this.maintenanceText) alerts.push({ type: 'error', message: 'Open maintenance requests are on the Maintenance page', urgent: true });
        
        const alertsHTML = alerts.map(alert => {
            const iconClass = alert.type === 'warning' ? 'fa-exclamation-triangle text-yellow-600' :
                             alert.type === 'error' ? 'fa-times-circle text-red-600' :
                             'fa-info-circle text-blue-600';
            const bgClass = alert.urgent ? 'bg-red-50 border-red-200' : 'bg-gray-50 border-gray-200';
            
            return `
                <div class="flex items-center space-x-3 p-3 rounded-lg border ${bgClass}">
                    <i class="fas ${iconClass}"></i>
                    <div class="flex-1">
                        <p class="text-sm text-gray-900">${alert.message}</p>
                    </div>
                    ${alert.urgent ? '<span class="text-xs bg-red-100 text-red-800 px-2 py-1 rounded-full">Urgent</span>' : ''}
                </div>
            `;
        }).join('');
        
        alertsList.innerHTML = alertsHTML;
    }
    
    updateRoomGrid(rooms) {
        const roomGrid = document.getElementById('room-grid');
        if (!roomGrid) return;
        
        const roomsHTML = rooms.map(room => {
            const statusClass = this.getRoomStatusClass(room.room_status);
            const statusText = this.getRoomStatusText(room.room_status);
            
            return `
                <div class="room-card p-4 border rounded-lg cursor-pointer hover:shadow-md transition-all ${statusClass}"
                     onclick="dashboard.selectRoom('${room.room_number}')">
                    <div class="text-center">
                        <div class="text-lg font-semibold text-gray-900">${room.room_number}</div>
                        <div class="text-sm text-gray-600">${room.room_type || 'Standard'}</div>
                        <div class="status-badge mt-2">${statusText}</div>
                    </div>
                </div>
            `;
        }).join('');
        
        roomGrid.innerHTML = roomsHTML;
    }
    
    getRoomStatusClass(status) {
        switch (status) {
            case 'occupied': return 'bg-yellow-50 border-yellow-200';
            case 'clean': return 'bg-green-50 border-green-200';
            case 'dirty': return 'bg-orange-50 border-orange-200';
            case 'cleaning': return 'bg-red-50 border-red-200';
            default: return 'bg-gray-50 border-gray-200';
        }
    }
    
    getRoomStatusText(status) {
        switch (status) {
            case 'occupied': return 'Occupied';
            case 'clean': return 'Available';
            case 'dirty': return 'Dirty';
            case 'cleaning': return 'Cleaning';
            default: return 'Unknown';
        }
    }
    
    initializeCharts() {
        // Initialize occupancy chart
        const occupancyCtx = document.getElementById('occupancy-chart');
        if (occupancyCtx) {
            this.charts.occupancy = new Chart(occupancyCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Occupancy Rate',
                        data: [],
                        borderColor: '#2563eb',
                        backgroundColor: 'rgba(37, 99, 235, 0.1)',
                        fill: true,
                        tension: 0.4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            display: false
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            max: 100,
                            ticks: {
                                callback: function(value) {
                                    return value + '%';
                                }
                            }
                        }
                    }
                }
            });
        }
    }
    
    updateCharts(analytics) {
        const rows = (analytics && analytics.occupancy) || [];
        if (this.charts.occupancy) {
            this.charts.occupancy.data.labels = rows.map(row => row.date);
            this.charts.occupancy.data.datasets[0].data = rows.map(row => row.occupancy);
            this.charts.occupancy.update();
        }
    }
    
    // Quick Actions
    openPage(page) {
        try {
            if (window.parent && window.parent.spaNavigate) {
                window.parent.spaNavigate(page, {});
                return;
            }
        } catch (e) {}
        window.location.href = page;
    }

    quickAction(action) {
        const pages = {
            'new-booking': 'bookings.html',
            'check-in': 'operations.html',
            'room-status': 'rooms.html',
            'maintenance': 'maintenance.html'
        };
        if (pages[action]) this.openPage(pages[action]);
    }
    
    reportMaintenance() {
        // Show maintenance reporting modal
        const modal = document.createElement('div');
        modal.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50';
        modal.innerHTML = `
            <div class="bg-white rounded-lg p-6 w-full max-w-md">
                <h3 class="text-lg font-semibold mb-4">Report Maintenance Issue</h3>
                <form id="maintenance-form">
                    <div class="mb-4">
                        <label class="block text-sm font-medium text-gray-700 mb-2">Room Number</label>
                        <input type="text" id="maintenance-room" class="w-full border rounded-lg px-3 py-2" required>
                    </div>
                    <div class="mb-4">
                        <label class="block text-sm font-medium text-gray-700 mb-2">Issue Description</label>
                        <textarea id="maintenance-description" rows="3" class="w-full border rounded-lg px-3 py-2" required></textarea>
                    </div>
                    <div class="mb-4">
                        <label class="block text-sm font-medium text-gray-700 mb-2">Priority</label>
                        <select id="maintenance-priority" class="w-full border rounded-lg px-3 py-2">
                            <option value="low">Low</option>
                            <option value="medium">Medium</option>
                            <option value="high">High</option>
                            <option value="urgent">Urgent</option>
                        </select>
                    </div>
                    <div class="flex space-x-3">
                        <button type="button" onclick="this.closest('.fixed').remove()" 
                                class="flex-1 bg-gray-200 text-gray-800 py-2 px-4 rounded-lg hover:bg-gray-300">
                            Cancel
                        </button>
                        <button type="submit" class="flex-1 bg-blue-600 text-white py-2 px-4 rounded-lg hover:bg-blue-700">
                            Report Issue
                        </button>
                    </div>
                </form>
            </div>
        `;
        
        document.body.appendChild(modal);
        
        // Handle form submission
        document.getElementById('maintenance-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const room = document.getElementById('maintenance-room').value;
            const description = document.getElementById('maintenance-description').value;
            const priority = document.getElementById('maintenance-priority').value;
            const resp = await fetch(`${this.apiBase()}/maintenance/create`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ hotel_name: this.hotel, room_number: room, issue: description, urgency: String(priority || 'NORMAL').toUpperCase() })
            });
            const data = await resp.json();
            if (!resp.ok || data.error) { alert(data.error || 'Could not report the issue'); return; }
            modal.remove();
            alert(data.result || 'Maintenance reported');
        });
    }
    
    selectRoom(roomId) {
        this.openPage('assignments.html');
    }
    
    filterRooms(status) {
        const rooms = document.querySelectorAll('.room-card');
        rooms.forEach(room => {
            const roomStatus = room.querySelector('.status-badge').textContent.toLowerCase();
            if (status === 'all' || roomStatus.includes(status)) {
                room.style.display = 'block';
            } else {
                room.style.display = 'none';
            }
        });
    }
    
    askLeonForInsights() {
        const chat = window.leonChat;
        if (chat && chat.toggleChat) chat.toggleChat();
    }
    
    refreshData() {
        this.loadDashboardData();
    }
    
    viewAllActivity() {
        this.openPage('operations.html');
    }
    
    viewAllAlerts() {
        this.openPage('maintenance.html');
    }
    
    setupAutoRefresh() {
        // Refresh dashboard every 5 minutes
        this.refreshInterval = setInterval(() => {
            this.loadDashboardData();
        }, 300000); // 5 minutes
    }
    
    getTimeAgoWithHotelTz(isoUtc) {
        // Use API-provided timezone when available (activities endpoint uses cookie JWT; fallback to local)
        const tz = window.HOTEL_TZ || undefined;
        const now = new Date();
        const when = new Date(isoUtc);
        // Compute diff in ms, rendering is approximate regardless of tz offset
        const diff = now.getTime() - when.getTime();
        const minutes = Math.floor(diff / 60000);
        const hours = Math.floor(minutes / 60);
        const days = Math.floor(hours / 24);
        
        if (days > 0) return `${days} day${days > 1 ? 's' : ''} ago`;
        if (hours > 0) return `${hours} hour${hours > 1 ? 's' : ''} ago`;
        if (minutes > 0) return `${minutes} minute${minutes > 1 ? 's' : ''} ago`;
        return 'Just now';
    }
    
    destroy() {
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
        }
        
        // Destroy charts
        Object.values(this.charts).forEach(chart => {
            if (chart) chart.destroy();
        });
    }
}

// Initialize dashboard when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    // Wait for Alpine.js to initialize
    document.addEventListener('alpine:init', () => {
        window.dashboard = new DashboardComponent(window.app);
    });
});

// Also handle view changes
document.addEventListener('alpine:init', () => {
    Alpine.store('dashboard', {
        component: null,
        init() {
            this.component = new DashboardComponent(Alpine.store('app'));
        }
    });
}); 