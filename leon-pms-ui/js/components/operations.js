// Add local/production API URL switcher
const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
const apiUrl = isLocal ? 'http://localhost:8002/api' : 'https://api.inap.ai/api';

// Operations Dashboard Component
class OperationsComponent {
    constructor() {
        this.apiUrl = apiUrl;
        this.currentTab = 'checkin';
        this.checkIns = [];
        this.checkOuts = [];
        this.housekeepingTasks = [];
        this.maintenanceIssues = [];
        this.staffTasks = [];
        
        this.initialize();
    }
    
    async initialize() {
        await this.loadOperationsData();
        this.updateSummaryCards();
        this.renderCurrentTab();
        this.setupDateDisplays();
    }
    
    async sessionHotel() {
        const res = await fetch(`${this.apiUrl}/auth/me`, { credentials: 'include' });
        if (!res.ok) return '';
        const data = await res.json();
        this.hotel = String(data.hotel_name || '').toUpperCase();
        return this.hotel;
    }

    localToday() {
        const today = new Date();
        const m = String(today.getMonth() + 1).padStart(2, '0');
        const d = String(today.getDate()).padStart(2, '0');
        return `${today.getFullYear()}-${m}-${d}`;
    }

    async loadOperationsData() {
        this.checkIns = [];
        this.checkOuts = [];
        this.housekeepingTasks = [];
        this.maintenanceIssues = [];
        this.staffTasks = [];
        this.rooms = [];
        try {
            const hotel = await this.sessionHotel();
            if (!hotel) return;
            const today = this.localToday();
            const [bookingsRes, roomsRes, assignedRes, progressRes, maintRes] = await Promise.all([
                fetch(`${this.apiUrl}/bookings-calendar`, { credentials: 'include' }),
                fetch(`${this.apiUrl}/rooms/readiness_status_all?hotel_name=${encodeURIComponent(hotel)}`, { credentials: 'include' }),
                fetch(`${this.apiUrl}/housekeeping/by_status?status=assigned`, { credentials: 'include' }),
                fetch(`${this.apiUrl}/housekeeping/by_status?status=in_progress`, { credentials: 'include' }),
                fetch(`${this.apiUrl}/maintenance/list?hotel_name=${encodeURIComponent(hotel)}`, { credentials: 'include' })
            ]);
            const bookings = bookingsRes.ok ? ((await bookingsRes.json()).bookings || []) : [];
            bookings.forEach(b => {
                const status = String(b.status || '').toLowerCase();
                if (status === 'cancelled') return;
                const row = {
                    id: b.id,
                    guest: b.guest_name || b.guest_phone || '',
                    room: b.room_number || '',
                    time: '',
                    status: 'pending',
                    phone: b.guest_phone || ''
                };
                if (String(b.check_in || '').slice(0, 10) === today) this.checkIns.push(row);
                if (String(b.check_out || '').slice(0, 10) === today) this.checkOuts.push(Object.assign({}, row));
            });
            const roomsBody = roomsRes.ok ? await roomsRes.json() : {};
            this.rooms = roomsBody.items || [];
            const assigned = assignedRes.ok ? ((await assignedRes.json()).items || []) : [];
            const progress = progressRes.ok ? ((await progressRes.json()).items || []) : [];
            this.housekeepingTasks = assigned.map(t => ({
                id: t.cleaning_id || t.room_number,
                room: t.room_number,
                task: 'Cleaning',
                assignee: t.assigned_to || 'Unassigned',
                priority: 'medium',
                status: 'pending'
            })).concat(progress.map(t => ({
                id: t.cleaning_id || t.room_number,
                room: t.room_number,
                task: 'Cleaning',
                assignee: t.assigned_to || 'Unassigned',
                priority: 'high',
                status: 'in-progress'
            })));
            this.staffTasks = this.housekeepingTasks.map(t => ({
                id: t.id,
                task: `${t.task} · room ${t.room}`,
                assignee: t.assignee,
                priority: t.priority,
                status: t.status,
                deadline: today
            }));
            const maint = maintRes.ok ? await maintRes.json() : {};
            const text = String(maint.result || '');
            this.maintenanceIssues = text.split('\n').map(line => line.trim()).filter(line => line && line.charAt(0) !== '❌').map((line, i) => ({
                id: String(i),
                room: '',
                issue: line,
                assignee: '',
                priority: 'medium',
                status: 'assigned',
                reported: today
            }));
        } catch (error) {
            console.error('Error loading operations data:', error);
        }
    }
    
    updateSummaryCards() {
        const pendingCheckIns = this.checkIns.filter(ci => ci.status === 'pending').length;
        const pendingCheckOuts = this.checkOuts.filter(co => co.status === 'pending').length;
        const housekeepingTasksCount = this.housekeepingTasks.filter(ht => ht.status !== 'completed').length;
        const maintenanceIssuesCount = this.maintenanceIssues.filter(mi => mi.status !== 'completed').length;
        
        document.getElementById('pending-checkins').textContent = pendingCheckIns;
        document.getElementById('pending-checkouts').textContent = pendingCheckOuts;
        document.getElementById('housekeeping-tasks').textContent = housekeepingTasksCount;
        document.getElementById('maintenance-issues').textContent = maintenanceIssuesCount;
    }
    
    setupDateDisplays() {
        const today = new Date().toLocaleDateString('en-US', {
            weekday: 'long',
            year: 'numeric',
            month: 'long',
            day: 'numeric'
        });
        
        document.getElementById('checkin-date').textContent = today;
        document.getElementById('checkout-date').textContent = today;
    }
    
    switchTab(tabName) {
        // Hide all tab contents
        document.querySelectorAll('.tab-content').forEach(content => {
            content.classList.add('hidden');
        });
        
        // Remove active class from all tabs
        document.querySelectorAll('.operations-tab').forEach(tab => {
            tab.classList.remove('tab-active');
        });
        
        // Show selected tab content
        document.getElementById(`${tabName}-content`).classList.remove('hidden');
        document.getElementById(`${tabName}-tab`).classList.add('tab-active');
        
        this.currentTab = tabName;
        this.renderCurrentTab();
    }
    
    renderCurrentTab() {
        switch(this.currentTab) {
            case 'checkin':
                this.renderCheckInOut();
                break;
            case 'housekeeping':
                this.renderHousekeeping();
                break;
            case 'maintenance':
                this.renderMaintenance();
                break;
            case 'tasks':
                this.renderStaffTasks();
                break;
        }
    }
    
    renderCheckInOut() {
        // Render check-ins
        const checkInList = document.getElementById('checkin-list');
        if (this.checkIns.length === 0) {
            checkInList.innerHTML = '<p class="text-gray-500 text-center py-4">No check-ins scheduled for today</p>';
        } else {
            checkInList.innerHTML = this.checkIns.map(ci => `
                <div class="border rounded-lg p-4 ${ci.status === 'completed' ? 'bg-green-50 border-green-200' : 'bg-white border-gray-200'} hover:shadow-md transition-shadow">
                    <div class="flex justify-between items-start">
                        <div>
                            <h4 class="font-semibold text-gray-900">${ci.guest}</h4>
                            <p class="text-sm text-gray-600">Room ${ci.room} • ${ci.time}</p>
                            <p class="text-xs text-gray-500">${ci.phone}</p>
                        </div>
                        <div class="flex items-center space-x-2">
                            <span class="status-badge ${ci.status === 'completed' ? 'bg-green-100 text-green-800' : 'bg-yellow-100 text-yellow-800'}">
                                ${ci.status === 'completed' ? 'Completed' : 'Pending'}
                            </span>
                            ${ci.status === 'pending' ? 
                                `<button onclick="processCheckIn('${ci.id}')" class="text-blue-600 hover:text-blue-800 text-sm">
                                    <i class="fas fa-check"></i>
                                </button>` : ''}
                        </div>
                    </div>
                </div>
            `).join('');
        }
        
        // Render check-outs
        const checkOutList = document.getElementById('checkout-list');
        if (this.checkOuts.length === 0) {
            checkOutList.innerHTML = '<p class="text-gray-500 text-center py-4">No check-outs scheduled for today</p>';
        } else {
            checkOutList.innerHTML = this.checkOuts.map(co => `
                <div class="border rounded-lg p-4 ${co.status === 'completed' ? 'bg-blue-50 border-blue-200' : 'bg-white border-gray-200'} hover:shadow-md transition-shadow">
                    <div class="flex justify-between items-start">
                        <div>
                            <h4 class="font-semibold text-gray-900">${co.guest}</h4>
                            <p class="text-sm text-gray-600">Room ${co.room} • ${co.time}</p>
                            <p class="text-xs text-gray-500">${co.phone}</p>
                        </div>
                        <div class="flex items-center space-x-2">
                            <span class="status-badge ${co.status === 'completed' ? 'bg-blue-100 text-blue-800' : 'bg-yellow-100 text-yellow-800'}">
                                ${co.status === 'completed' ? 'Completed' : 'Pending'}
                            </span>
                            ${co.status === 'pending' ? 
                                `<button onclick="processCheckOut('${co.id}')" class="text-blue-600 hover:text-blue-800 text-sm">
                                    <i class="fas fa-check"></i>
                                </button>` : ''}
                        </div>
                    </div>
                </div>
            `).join('');
        }
    }
    
    renderHousekeeping() {
        const roomGrid = document.getElementById('room-status-grid');
        const rooms = this.rooms || [];
        if (!rooms.length) {
            roomGrid.innerHTML = '<p class="text-gray-500 text-center py-4">No rooms</p>';
        } else {
            roomGrid.innerHTML = rooms.map(room => {
                const status = String(room.readiness_status || 'unknown').toLowerCase();
                const number = String(room.room_number || '');
                return `<div class="room-status-item room-status-${status}" onclick="updateRoomStatus('${number}', '${status}')">${number}</div>`;
            }).join('');
        }
        
        // Render housekeeping tasks
        const taskList = document.getElementById('housekeeping-task-list');
        if (this.housekeepingTasks.length === 0) {
            taskList.innerHTML = '<p class="text-gray-500 text-center py-4">No housekeeping tasks</p>';
        } else {
            taskList.innerHTML = this.housekeepingTasks.map(task => `
                <div class="task-item task-priority-${task.priority}">
                    <div class="flex justify-between items-start mb-2">
                        <h4 class="font-semibold text-gray-900">Room ${task.room}</h4>
                        <span class="status-badge ${this.getTaskStatusClass(task.status)}">
                            ${this.capitalizeFirst(task.status)}
                        </span>
                    </div>
                    <p class="text-sm text-gray-700 mb-2">${task.task}</p>
                    <div class="flex justify-between items-center text-xs text-gray-500">
                        <span>Assigned to: ${task.assignee}</span>
                        <span class="priority-badge priority-${task.priority}">${task.priority.toUpperCase()}</span>
                    </div>
                </div>
            `).join('');
        }
    }
    
    renderMaintenance() {
        // Render maintenance issues
        const issuesList = document.getElementById('maintenance-issues-list');
        if (this.maintenanceIssues.length === 0) {
            issuesList.innerHTML = '<p class="text-gray-500 text-center py-4">No active maintenance issues</p>';
        } else {
            issuesList.innerHTML = this.maintenanceIssues.map(issue => `
                <div class="task-item task-priority-${issue.priority}">
                    <div class="flex justify-between items-start mb-2">
                        <h4 class="font-semibold text-gray-900">Room ${issue.room}</h4>
                        <span class="status-badge ${this.getTaskStatusClass(issue.status)}">
                            ${this.capitalizeFirst(issue.status)}
                        </span>
                    </div>
                    <p class="text-sm text-gray-700 mb-2">${issue.issue}</p>
                    <div class="flex justify-between items-center text-xs text-gray-500">
                        <span>Assigned to: ${issue.assignee}</span>
                        <span>Reported: ${this.formatDate(issue.reported)}</span>
                    </div>
                </div>
            `).join('');
        }
        
        // Render maintenance schedule (placeholder)
        const scheduleList = document.getElementById('maintenance-schedule-list');
        scheduleList.innerHTML = `
            <p class="text-gray-500 text-center py-4">Scheduled maintenance will appear here</p>
        `;
    }
    
    renderStaffTasks() {
        const tasksList = document.getElementById('staff-tasks-list');
        if (this.staffTasks.length === 0) {
            tasksList.innerHTML = '<p class="text-gray-500 text-center py-4">No staff tasks assigned</p>';
        } else {
            tasksList.innerHTML = this.staffTasks.map(task => `
                <div class="task-item task-priority-${task.priority}">
                    <div class="flex justify-between items-start mb-2">
                        <h4 class="font-semibold text-gray-900">${task.task}</h4>
                        <span class="status-badge ${this.getTaskStatusClass(task.status)}">
                            ${this.capitalizeFirst(task.status)}
                        </span>
                    </div>
                    <div class="flex justify-between items-center text-xs text-gray-500">
                        <span>Assigned to: ${task.assignee}</span>
                        <span>Deadline: ${this.formatDate(task.deadline)}</span>
                    </div>
                </div>
            `).join('');
        }
    }
    
    getTaskStatusClass(status) {
        const statusClasses = {
            'pending': 'bg-yellow-100 text-yellow-800',
            'in-progress': 'bg-blue-100 text-blue-800',
            'assigned': 'bg-purple-100 text-purple-800',
            'completed': 'bg-green-100 text-green-800'
        };
        return statusClasses[status] || 'bg-gray-100 text-gray-800';
    }
    
    formatDate(dateStr) {
        return new Date(dateStr).toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric'
        });
    }
    
    capitalizeFirst(str) {
        return str.charAt(0).toUpperCase() + str.slice(1).replace('-', ' ');
    }

    async reload() {
        await this.loadOperationsData();
        this.updateSummaryCards();
        this.renderCurrentTab();
    }

    async processCheckIn(bookingId, assignmentId) {
        const body = { hotel_name: this.hotel, booking_id: bookingId, notes: '' };
        if (assignmentId) body.assignment_id = assignmentId;
        const resp = await fetch(`${this.apiUrl}/assignments/check_in`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(body)
        });
        const data = await resp.json();
        if (data.assignments && data.assignments.length) {
            const lines = data.assignments.map(a => a.room_number + '  ' + a.assignment_id).join('\n');
            const pick = window.prompt('Check in one room:\n' + lines);
            if (!pick) return;
            const chosen = data.assignments.find(a => String(a.room_number).toUpperCase() === String(pick).trim().toUpperCase() || String(a.assignment_id).toUpperCase() === String(pick).trim().toUpperCase());
            if (!chosen) { alert('Choose one listed room'); return; }
            return this.processCheckIn(bookingId, chosen.assignment_id);
        }
        if (!resp.ok || data.error) { alert(data.error || 'Check-in failed'); return; }
        alert(data.result || 'Checked in');
        await this.reload();
    }

    async processCheckOut(bookingId) {
        const resp = await fetch(`${this.apiUrl}/assignments/check_out`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ hotel_name: this.hotel, booking_id: bookingId, notes: '' })
        });
        const data = await resp.json();
        if (!resp.ok || data.error) { alert(data.error || 'Check-out failed'); return; }
        alert(data.result || 'Checked out');
        await this.reload();
    }

    async updateRoomStatus(roomNumber) {
        const next = window.prompt(`Readiness for room ${roomNumber} (clean, dirty, cleaning, occupied)`, 'dirty');
        if (!next) return;
        const resp = await fetch(`${this.apiUrl}/rooms/readiness_status/update`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ hotel_name: this.hotel, room_number: roomNumber, new_status: next, notes: '' })
        });
        const data = await resp.json();
        if (!resp.ok || data.error) { alert(data.error || 'Update failed'); return; }
        await this.reload();
    }

    async createMaintenanceTask() {
        const room = window.prompt('Room number');
        if (!room) return;
        const issue = window.prompt('Issue');
        if (!issue) return;
        const resp = await fetch(`${this.apiUrl}/maintenance/create`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ hotel_name: this.hotel, room_number: room, issue: issue, urgency: 'NORMAL' })
        });
        const data = await resp.json();
        if (!resp.ok || data.error) { alert(data.error || 'Could not create the task'); return; }
        alert(data.result || 'Maintenance task created');
        await this.reload();
    }

    async assignHousekeeping() {
        const room = window.prompt('Room number');
        if (!room) return;
        const staff = window.prompt('Staff phone');
        if (!staff) return;
        const resp = await fetch(`${this.apiUrl}/housekeeping/create_task`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ hotel_name: this.hotel, room_number: room, staff_phone: staff, task_type: 'standard_cleaning', priority: 'normal', notes: '' })
        });
        const data = await resp.json();
        if (!resp.ok || data.error) { alert(data.error || 'Could not assign housekeeping'); return; }
        alert(data.result || 'Housekeeping assigned');
        await this.reload();
    }
} 