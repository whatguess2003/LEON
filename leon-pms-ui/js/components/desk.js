(function () {
    const host = window.location.hostname;
    const local = host === 'localhost' || host === '127.0.0.1' || host === '::1';
    const api = (local ? 'http://localhost:8002' : 'https://api.inap.ai') + '/api';
    const state = { hotel: '', approvals: [], tasks: [] };

    function esc(value) {
        return String(value || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function nights(value) {
        return String(value || '').split(',').map(part => part.trim()).filter(Boolean);
    }

    function post(path, body) {
        return fetch(api + path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(body),
        });
    }

    const runners = {
        cancel_booking: (action) => post('/bookings/cancel', { booking_id: action.booking_id }),
        create_booking: (action) => post('/bookings/create', {
            guest_phone: action.guest_phone,
            room_type: action.room_type,
            stay_dates: nights(action.stay_dates),
            rooms_booked: Number(action.rooms_booked || 1),
        }),
        modify_booking: (action) => {
            const body = { booking_id: action.booking_id };
            if (action.new_stay_dates) body.new_stay_dates = nights(action.new_stay_dates);
            if (action.new_room_type) body.new_room_type = action.new_room_type;
            if (action.new_rooms_booked) body.new_rooms_booked = Number(action.new_rooms_booked);
            return post('/bookings/modify', body);
        },
        check_in: (action) => post('/assignments/check_in', { hotel_name: state.hotel, booking_id: action.booking_id, room_number: action.room_number }),
        check_out: (action) => post('/assignments/check_out', { hotel_name: state.hotel, booking_id: action.booking_id, room_number: action.room_number }),
        reassign_room: (action) => post('/assignments/reassign', {
            hotel_name: state.hotel,
            booking_id: action.booking_id,
            stay_date: action.stay_date,
            old_room_number: action.old_room_number,
            new_room_number: action.new_room_number,
        }),
        record_payment: (action) => post('/payments/update', {
            hotel_name: state.hotel,
            booking_id: action.booking_id,
            payment_amount: Number(action.payment_amount),
            payment_method: action.payment_method,
        }),
        record_refund: (action) => post('/payments/refund', {
            hotel_name: state.hotel,
            booking_id: action.booking_id,
            payment_seq: Number(action.payment_seq),
        }),
        update_guest_profile: (action) => {
            const body = { guest_phone: action.guest_phone };
            ['guest_name', 'email', 'country', 'special_request'].forEach(key => {
                if (action[key]) body[key] = action[key];
            });
            return post('/guest_profile/update', body);
        },
    };

    function render() {
        const approvals = document.getElementById('approval-actions');
        approvals.innerHTML = state.approvals.map((action, index) => `
            <div class="border rounded p-2 bg-white">
                <p class="text-sm">${esc(action.guest_name)} ${esc(action.guest_phone)}</p>
                <p class="text-sm">${esc(action.label || '')}</p>
                <button type="button" data-index="${index}" class="approve-action mt-2 bg-blue-600 text-white px-3 py-1 rounded text-sm">Approve</button>
                <p class="execute-result text-sm mt-1"></p>
            </div>
        `).join('');
        approvals.querySelectorAll('.approve-action').forEach(button => {
            button.addEventListener('click', () => approve(state.approvals[Number(button.dataset.index)], button));
        });
        const tasks = document.getElementById('desk-tasks');
        tasks.innerHTML = state.tasks.map((item, index) => `
            <div class="border rounded p-2 bg-white">
                <p class="text-sm">${esc(item.guest_name)} ${esc(item.guest_phone)}</p>
                <p class="text-sm">${esc(item.task || '')}</p>
                <button type="button" data-index="${index}" class="done-task mt-2 bg-gray-700 text-white px-3 py-1 rounded text-sm">Done</button>
            </div>
        `).join('');
        tasks.querySelectorAll('.done-task').forEach(button => {
            const item = state.tasks[Number(button.dataset.index)];
            button.addEventListener('click', () => doneTask(item.guest_phone, item.task, button));
        });
    }

    async function loadQueue() {
        const response = await fetch(api + '/desk/queue', { credentials: 'include' });
        const data = await response.json();
        if (!response.ok) return;
        state.hotel = data.hotel_name || '';
        state.approvals = data.approvals || [];
        state.tasks = data.tasks || [];
        render();
    }

    async function doneTask(phone, task, button) {
        button.disabled = true;
        const response = await post('/guests/tasks/done', {
            hotel_name: state.hotel,
            guest_phone: phone,
            task: task,
        });
        if (!response.ok) {
            button.disabled = false;
            return;
        }
        await loadQueue();
    }

    async function approve(action, button) {
        const run = runners[action.action];
        const result = button.parentElement.querySelector('.execute-result');
        if (!run) {
            result.textContent = 'This action cannot be executed';
            return;
        }
        button.disabled = true;
        const response = await run(action);
        const data = await response.json();
        if (!response.ok || data.error) {
            result.textContent = data.error || 'Approve failed';
            button.disabled = false;
            return;
        }
        await post('/guests/approval/clear', {
            hotel_name: state.hotel,
            guest_phone: action.guest_phone,
            label: action.label,
        });
        await loadQueue();
    }

    function addLine(who, text) {
        const box = document.getElementById('chat-messages');
        const line = document.createElement('p');
        line.className = 'text-sm mt-2';
        line.textContent = who + ': ' + text;
        box.appendChild(line);
        box.scrollTop = box.scrollHeight;
    }

    async function loadHistory() {
        const response = await fetch(api + '/chat/history', { credentials: 'include' });
        const data = await response.json();
        const box = document.getElementById('chat-messages');
        box.innerHTML = '';
        (data.items || []).forEach(item => addLine(item.sender === 'user' ? 'Desk' : 'Leon', item.text));
    }

    async function send() {
        const input = document.getElementById('chat-input');
        const message = input.value.trim();
        if (!message) return;
        input.value = '';
        addLine('Desk', message);
        const status = document.getElementById('chat-status');
        status.textContent = 'Leon is replying';
        const response = await fetch(api + '/chat/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ message: message }),
        });
        const data = await response.json();
        addLine('Leon', data.final_message || data.error || '');
        status.textContent = '';
        await loadQueue();
    }

    async function clearChat() {
        await fetch(api + '/chat/clear', { method: 'POST', credentials: 'include' });
        document.getElementById('chat-messages').innerHTML = '';
    }

    loadQueue();
    setInterval(loadQueue, 2000);
})();
