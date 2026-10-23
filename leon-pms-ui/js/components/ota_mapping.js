(function(){
  var API = (location.hostname==='localhost'||location.hostname==='127.0.0.1') ? 'http://localhost:8002' : 'https://api.inap.ai';
  var CM = (location.hostname==='localhost'||location.hostname==='127.0.0.1') ? 'http://127.0.0.1:8006' : 'https://cm.inap.ai';
  var TABS = [
    {id:'rate_out', operation:'rates', label:'Rate Out', line:'Aiosell will send Rates to'},
    {id:'inventory_out', operation:'inventory', label:'Inventory Out', line:'Aiosell will send Inventory to'},
    {id:'reservation_in', operation:'reservation', label:'Reservation In', line:'Aiosell will receive Reservations from'},
    {id:'inventory_in', operation:'inventory_in', label:'Inventory In', line:'Aiosell will receive Inventory from'},
    {id:'rate_in', operation:'rates_in', label:'Rate In', line:'Aiosell will receive Rates from'},
    {id:'reservation_out', operation:'reservation_out', label:'Reservation Out', line:'Aiosell will send Reservations to'},
    {id:'pms_status', operation:'pms_status', label:'PMS Status', line:'PMS status for'},
    {id:'room_mapping', operation:'room_mapping', label:'Room Mapping', line:''}
  ];
  var selected = TABS[0].id;
  var channels = [];
  var rooms = [];
  var roomTypes = [];
  var roomMaps = {};
  var logs = [];
  var unread = 0;
  var sessionHotel = '';

  async function pageHotel(){
    if (sessionHotel) return sessionHotel;
    sessionHotel = String(window.leonHotel ? window.leonHotel() : '').toUpperCase();
    return sessionHotel;
  }

  function qs(id){ return document.getElementById(id); }

  function channelLabel(partnerId){
    var text = String(partnerId || '');
    if (!text) return '';
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function tabById(id){
    var found = null;
    TABS.forEach(function(tab){ if (tab.id === id) found = tab; });
    return found || TABS[0];
  }

  function channelsFor(tab){
    return channels.filter(function(channel){
      return String(channel.operation || '') === tab.operation;
    });
  }

  function addLog(message){
    var stamp = new Date().toLocaleString();
    logs.unshift(stamp + ' — ' + message);
    unread += 1;
  }

  function showLogs(onlyLatest){
    var panel = qs('ota-log-panel');
    if (!panel) return;
    panel.classList.remove('hidden');
    panel.textContent = '';
    var rows = onlyLatest ? logs.slice(0, 1) : logs;
    if (!rows.length){
      panel.textContent = 'No logs';
      return;
    }
    rows.forEach(function(line){
      var div = document.createElement('div');
      div.className = 'py-1';
      div.textContent = line;
      panel.appendChild(div);
    });
    unread = 0;
  }

  function renderTabs(){
    var host = qs('ota-tabs');
    if (!host) return;
    host.textContent = '';
    TABS.forEach(function(tab){
      var count = tab.id === 'room_mapping' ? Object.keys(roomMaps).filter(function(key){ return roomMaps[key]; }).length : channelsFor(tab).length;
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'flex flex-col items-center flex-shrink-0 px-4 py-3 text-xs border-b-2 ' + (tab.id === selected ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500');
      var icon = document.createElement('i');
      icon.className = count ? 'fas fa-check-circle text-green-500 mb-1' : 'far fa-circle text-gray-400 mb-1';
      var label = document.createElement('span');
      label.className = 'whitespace-nowrap';
      label.textContent = tab.label;
      button.appendChild(icon);
      button.appendChild(label);
      button.addEventListener('click', function(){
        selected = tab.id;
        renderTabs();
        renderRows();
        renderRooms();
        renderReservationOut();
      });
      host.appendChild(button);
    });
  }

  function renderRows(){
    var host = qs('ota-rows');
    if (!host) return;
    host.textContent = '';
    var tab = tabById(selected);
    var rows = channelsFor(tab);
    if (tab.id === 'reservation_out' || tab.id === 'room_mapping'){
      return;
    }
    if (!rows.length){
      var empty = document.createElement('div');
      empty.className = 'px-4 py-8 text-center text-sm text-gray-500';
      empty.textContent = 'No mapping';
      host.appendChild(empty);
      return;
    }
    rows.forEach(function(channel){
      var row = document.createElement('div');
      row.className = 'border-b';
      var line = document.createElement('div');
      line.className = 'flex items-center px-4 py-3';
      var name = document.createElement('div');
      name.className = 'w-32 flex-shrink-0 text-sm text-gray-700';
      name.textContent = channelLabel(channel.partner_id);
      var note = document.createElement('div');
      note.className = 'flex-1 text-sm text-green-600';
      var code = String(channel.hotel_code || '');
      note.textContent = tab.line + ' ' + channelLabel(channel.partner_id) + (code ? ' ( ' + code + ' )' : '');
      var toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'ota-switch w-10 h-5 bg-blue-500 rounded-full flex items-center justify-end px-1 flex-shrink-0';
      toggle.setAttribute('aria-pressed', 'true');
      var knob = document.createElement('span');
      knob.className = 'w-3 h-3 bg-white rounded-full';
      toggle.appendChild(knob);
      toggle.addEventListener('click', function(){
        var status = qs('ota-status');
        if (status) status.textContent = 'Connected channels are read from Aiosell. Submit reloads them.';
      });
      var chevron = document.createElement('button');
      chevron.type = 'button';
      chevron.className = 'ml-3 text-gray-400';
      chevron.innerHTML = '<i class="fas fa-chevron-down"></i>';
      var detail = document.createElement('div');
      detail.className = 'hidden px-4 pb-3 text-xs text-gray-600';
      var bits = [];
      if (code) bits.push('Hotel code ' + code);
      if (channel.rate_multiplier != null && channel.rate_multiplier !== '') bits.push('Rate multiplier ' + channel.rate_multiplier);
      detail.textContent = bits.join(' · ');
      chevron.addEventListener('click', function(){ detail.classList.toggle('hidden'); });
      line.appendChild(name);
      line.appendChild(note);
      line.appendChild(toggle);
      line.appendChild(chevron);
      row.appendChild(line);
      row.appendChild(detail);
      host.appendChild(row);
    });
  }

  function renderReservationOut(){
    var panel = qs('ota-res-out');
    if (!panel) return;
    var tab = tabById(selected).id;
    var inbound = tab === 'reservation_in';
    var show = inbound || tab === 'reservation_out';
    if (!show){
      panel.classList.add('hidden');
      return;
    }
    panel.classList.remove('hidden');
    panel.textContent = '';
    var intro = document.createElement('div');
    intro.className = 'px-4 py-3 text-sm text-gray-700';
    intro.textContent = inbound
      ? 'Bookings created in the PMS are reservation in to the channel manager.'
      : 'Aiosell posts book, modify, and cancel to https://api.inap.ai/update_reservation. This list is what was stored.';
    panel.appendChild(intro);
    var status = document.createElement('div');
    status.className = 'px-4 pb-3 text-sm text-gray-600';
    status.textContent = inbound ? 'Loading reservation in...' : 'Loading stored reservations...';
    panel.appendChild(status);
    var list = document.createElement('div');
    panel.appendChild(list);
    var path = inbound ? '/api/ota/reservation-in' : '/api/ota/reservation-out';
    fetch(API + path, {
      method: 'GET',
      credentials: 'include'
    }).then(function(res){ return res.json().then(function(data){ return {ok: res.ok, data: data}; }); }).then(function(result){
      var data = result.data || {};
      if (!result.ok){
        status.textContent = data.error || (inbound ? 'Reservation in failed' : 'Reservation out failed');
        return;
      }
      var rows = Array.isArray(data.reservations) ? data.reservations : [];
      if (inbound){
        status.textContent = rows.length ? (rows.length + ' reservation in') : 'No reservation in yet';
      } else if (!data.synced_at){
        status.textContent = 'No stored reservations yet';
        return;
      } else {
        var saved = rows.filter(function(row){ return row.success; }).length;
        status.textContent = saved + ' of ' + rows.length + ' stored for ' + data.start_date + ' to ' + data.end_date + '. Synced ' + data.synced_at;
        if (data.error) status.textContent = data.error + '. ' + status.textContent;
      }
      rows.forEach(function(row){
        var line = document.createElement('div');
        line.className = 'px-4 py-2 border-t text-sm ' + (row.success ? 'text-green-600' : 'text-gray-700');
        var guest = String(row.guest_name || '').trim();
        line.textContent = String(row.source || '') + ' ' + String(row.action || '') + ' ' + String(row.bookingId || '') + (guest ? ' — ' + guest : '') + ' — ' + String(row.message || '');
        list.appendChild(line);
      });
    }).catch(function(){
      status.textContent = inbound ? 'Reservation in failed' : 'Reservation out failed';
    });
  }

  function pushNote(data){
    var sent = data && data.aiosell;
    if (!sent) return '';
    var parts = [];
    if (sent.error) parts.push(sent.error);
    if (sent.skipped) parts.push(sent.skipped);
    ['inventory', 'rates'].forEach(function(key){
      var rows = Array.isArray(sent[key]) ? sent[key] : [];
      rows.forEach(function(row){
        if (row && row.error) parts.push(row.error);
        else if (row && row.result && row.result.message) parts.push(row.result.message);
      });
    });
    return parts.join(' ');
  }

  function renderRooms(){
    var wrap = qs('ota-rooms-wrap');
    var host = qs('ota-rooms');
    if (!wrap || !host) return;
    host.textContent = '';
    var show = tabById(selected).id === 'room_mapping';
    if (!show){
      wrap.classList.add('hidden');
      return;
    }
    wrap.classList.remove('hidden');
    rooms.forEach(function(room){
      var code = String(room.room_id || '');
      var block = document.createElement('div');
      block.className = 'border-b py-3';
      var title = document.createElement('div');
      title.className = 'text-sm font-medium text-gray-800';
      title.textContent = String(room.room_name || '') + ' · ' + code;
      var mapRow = document.createElement('div');
      mapRow.className = 'flex flex-wrap items-center mt-2';
      if (!roomTypes.length){
        var missing = document.createElement('div');
        missing.className = 'text-sm text-gray-500 mr-2';
        missing.textContent = 'No LEON room types';
        mapRow.appendChild(missing);
      }
      roomTypes.forEach(function(roomType){
        var pick = document.createElement('button');
        pick.type = 'button';
        var on = roomMaps[code] === roomType;
        pick.className = 'mr-2 mb-2 px-3 py-1 text-sm border rounded ' + (on ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-700 border-gray-300');
        pick.textContent = roomType;
        pick.addEventListener('click', function(){
          roomMaps[code] = roomType;
          renderRooms();
        });
        mapRow.appendChild(pick);
      });
      var save = document.createElement('button');
      save.type = 'button';
      save.className = 'mb-2 px-3 py-1 text-sm bg-blue-600 text-white rounded';
      save.textContent = 'Save';
      save.addEventListener('click', function(){ saveRoomMap(code, roomMaps[code] || '', save); });
      mapRow.appendChild(save);
      var meta = document.createElement('div');
      meta.className = 'text-xs text-gray-500 mt-1';
      meta.textContent = 'Count ' + String(room.count != null ? room.count : '') + ' · Occupancy ' + String(room.min_occ != null ? room.min_occ : '') + '-' + String(room.max_occ != null ? room.max_occ : '');
      block.appendChild(title);
      block.appendChild(mapRow);
      block.appendChild(meta);
      var plans = Array.isArray(room.rateplans) ? room.rateplans : [];
      plans.forEach(function(plan){
        var planRow = document.createElement('div');
        planRow.className = 'text-sm text-gray-700 mt-1';
        planRow.textContent = String(plan.rateplan_name || '') + ' · ' + String(plan.rateplan_id || '') + ' · occupancy ' + String(plan.occupancy != null ? plan.occupancy : '') + ' · meals ' + String(plan.no_of_meals != null ? plan.no_of_meals : '');
        block.appendChild(planRow);
      });
      host.appendChild(block);
    });
  }

  function applyPayload(data){
    if (qs('ota-hotel-code')) qs('ota-hotel-code').value = String((data && data.hotel_code) || '');
    channels = (data && Array.isArray(data.channels)) ? data.channels : [];
    rooms = (data && Array.isArray(data.rooms)) ? data.rooms : [];
    roomTypes = (data && Array.isArray(data.room_types)) ? data.room_types : [];
    roomMaps = {};
    ((data && Array.isArray(data.room_maps)) ? data.room_maps : []).forEach(function(item){
      roomMaps[String(item.room_code || '')] = String(item.room_type || '');
    });
    var property = qs('ota-property');
    if (property){
      var parts = [data && data.property_name, data && data.currency, data && data.timezone].filter(function(part){ return String(part || '').trim(); });
      property.textContent = parts.join(' · ');
    }
    var label = qs('ota-hotel');
    if (label && data && data.hotel_name) label.textContent = 'Hotel: ' + data.hotel_name;
    renderTabs();
    renderRows();
    renderRooms();
    renderReservationOut();
  }

  async function saveRoomMap(code, roomType, button){
    var status = qs('ota-status');
    var hotel = await pageHotel();
    if (!hotel){
      if (status) status.textContent = 'Sign in required';
      return;
    }
    if (!roomType){
      if (status) status.textContent = 'Select a room type';
      return;
    }
    if (button) button.disabled = true;
    try {
      var res = await fetch(CM + '/room-mapping', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({hotel_name: hotel, room_code: code, room_type: roomType})
      });
      var data = await res.json();
      if (!res.ok || (data && data.error)){
        if (status) status.textContent = (data && data.error) || 'Save failed';
      } else {
        applyPayload(data);
        var note = pushNote(data);
        if (status) status.textContent = note ? ('Saved ' + code + '. ' + note) : ('Saved ' + code);
      }
    } catch(e) {
      if (status) status.textContent = 'Save failed';
    }
    if (button) button.disabled = false;
  }

  async function loadMapping(){
    var status = qs('ota-status');
    var hotel = await pageHotel();
    if (!hotel){
      if (status) status.textContent = 'Sign in required';
      return;
    }
    if (status) status.textContent = 'Loading...';
    try {
      var res = await fetch(CM + '/mapping?hotel_name=' + encodeURIComponent(hotel));
      var data = await res.json();
      applyPayload(data);
      if (!res.ok || (data && data.error)){
        if (status) status.textContent = (data && data.error) || 'Failed to load';
        addLog((data && data.error) || 'Failed to load');
        return;
      }
      if (!data.hotel_code){
        if (status) status.textContent = 'Enter the Aiosell hotel code, then Submit.';
        return;
      }
      if (status) status.textContent = 'Loaded ' + channels.length + ' channel connections.';
      addLog('Loaded property details for ' + data.hotel_code);
    } catch(e) {
      if (status) status.textContent = 'Failed to load';
      addLog('Failed to load');
    }
  }

  async function submitMapping(){
    var status = qs('ota-status');
    var hotelCode = String(qs('ota-hotel-code') && qs('ota-hotel-code').value || '').trim();
    if (!hotelCode){
      if (status) status.textContent = 'Hotel code is required';
      return;
    }
    var hotel = await pageHotel();
    if (!hotel){
      if (status) status.textContent = 'Sign in required';
      return;
    }
    if (status) status.textContent = 'Saving...';
    try {
      var res = await fetch(CM + '/mapping', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ hotel_name: hotel, hotel_code: hotelCode })
      });
      var data = await res.json();
      applyPayload(data);
      if (!res.ok || (data && data.error)){
        if (status) status.textContent = (data && data.error) || 'Save failed';
        addLog((data && data.error) || 'Save failed');
        return;
      }
      if (status) status.textContent = 'Saved. Loaded ' + channels.length + ' channel connections.';
      addLog('Saved ' + hotelCode);
    } catch(e) {
      if (status) status.textContent = 'Save failed';
      addLog('Save failed');
    }
  }

  document.addEventListener('DOMContentLoaded', async function(){
    var hotel = await pageHotel();
    var label = qs('ota-hotel');
    if (label) label.textContent = 'Hotel: ' + (hotel || '-');
    var logsNew = qs('ota-logs-new');
    if (logsNew) logsNew.addEventListener('click', function(){ showLogs(true); });
    var logsAll = qs('ota-logs');
    if (logsAll) logsAll.addEventListener('click', function(){ showLogs(false); });
    var submit = qs('ota-submit');
    if (submit) submit.addEventListener('click', function(e){ e.preventDefault(); submitMapping(); });
    renderTabs();
    renderRows();
    renderRooms();
    renderReservationOut();
    var add = qs('ota-add');
    if (add) add.addEventListener('click', function(){
      selected = 'room_mapping';
      renderTabs();
      renderRows();
      renderRooms();
      var wrap = qs('ota-rooms-wrap');
      if (wrap && !wrap.classList.contains('hidden')){
        wrap.scrollIntoView();
        return;
      }
      var status = qs('ota-status');
      if (status) status.textContent = 'Room and rate plan codes appear after Aiosell property details load.';
    });
    loadMapping();
  });
})();
