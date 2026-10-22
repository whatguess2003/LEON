(function(){
  var API = (location.hostname==='localhost'||location.hostname==='127.0.0.1') ? 'http://localhost:8002' : 'https://api.inap.ai';
  var HOTEL = '';

  function qs(id){ return document.getElementById(id); }

  async function initHotel(){
    try { var m=document.cookie.match(/(?:^|; )leon_hotel=([^;]+)/); if(m&&m[1]) HOTEL = decodeURIComponent(m[1]); } catch(_) {}
    if (!HOTEL) {
      try { var res = await fetch(API + '/api/auth/me', { credentials:'include' }); if(res.ok){ var d=await res.json(); HOTEL = (d && d.hotel_name) ? d.hotel_name : ''; } } catch(_) {}
    }
    if (!HOTEL) {
      var q = new URLSearchParams(location.search);
      HOTEL = q.get('hotel_name') || q.get('hotel') || '';
    }
    var label = qs('prop-hotel-name'); if (label) label.textContent = 'Hotel: ' + (HOTEL || '-');
  }

  async function loadRatePlans(){
    var list = qs('rp-list');
    if (!list) return;
    if (!HOTEL){ list.textContent = 'Hotel required'; return; }
    list.textContent = 'Loading...';
    try {
      var res = await fetch(API + '/api/rate_plans?hotel_name=' + encodeURIComponent(HOTEL), { credentials:'include' });
      var data = await res.json();
      var plans = (data && Array.isArray(data.rate_plans)) ? data.rate_plans : [];
      list.textContent = '';
      if (!plans.length){ list.textContent = 'No rate plans'; return; }
      plans.forEach(function(plan){
        var row = document.createElement('div');
        row.className = 'border-b py-1 flex items-center justify-between gap-2';
        var text = document.createElement('div');
        var name = document.createElement('span');
        name.className = 'font-medium';
        name.textContent = String(plan.rate_plan || '');
        var desc = document.createElement('span');
        desc.className = 'text-gray-500 ml-2';
        desc.textContent = String(plan.description || '');
        text.appendChild(name);
        text.appendChild(desc);
        var manage = document.createElement('button');
        manage.type = 'button';
        manage.className = 'px-2 py-1 text-xs border rounded bg-white hover:bg-gray-50';
        manage.textContent = 'Manage';
        manage.addEventListener('click', function(){ openRatePlan(plan); });
        row.appendChild(text);
        row.appendChild(manage);
        list.appendChild(row);
      });
    } catch(e) { list.textContent = 'Failed to load'; }
  }

  async function addRatePlan(){
    if (!HOTEL){ alert('Hotel required'); return; }
    var name = String(qs('rp-name') && qs('rp-name').value || '').trim().toUpperCase();
    var description = String(qs('rp-desc') && qs('rp-desc').value || '').trim();
    if (!name){ alert('Package name required'); return; }
    try {
      var res = await fetch(API + '/api/rate_plans', { method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ rate_plan: name, description: description }) });
      var data = await res.json();
      if (!res.ok || (data && data.error)){ alert((data && data.error) || 'Failed to add package'); return; }
      if (qs('rp-name')) qs('rp-name').value = '';
      if (qs('rp-desc')) qs('rp-desc').value = '';
      await loadRatePlans();
    } catch(e) { alert('Failed to add package'); }
  }

  function openRatePlan(plan){
    if (qs('rp-edit-original')) qs('rp-edit-original').value = String(plan.rate_plan || '');
    if (qs('rp-edit-name')) qs('rp-edit-name').value = String(plan.rate_plan || '');
    if (qs('rp-edit-desc')) qs('rp-edit-desc').value = String(plan.description || '');
    var modal = qs('rp-modal');
    if (modal) modal.classList.remove('hidden');
  }

  function closeRatePlan(){
    var modal = qs('rp-modal');
    if (modal) modal.classList.add('hidden');
  }

  async function saveRatePlan(){
    var ratePlan = String(qs('rp-edit-original') && qs('rp-edit-original').value || '').trim().toUpperCase();
    var newRatePlan = String(qs('rp-edit-name') && qs('rp-edit-name').value || '').trim().toUpperCase();
    var description = String(qs('rp-edit-desc') && qs('rp-edit-desc').value || '').trim();
    if (!ratePlan || !newRatePlan){ alert('Package name required'); return; }
    try {
      var res = await fetch(API + '/api/rate_plans/update', { method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ rate_plan: ratePlan, new_rate_plan: newRatePlan, description: description }) });
      var data = await res.json();
      if (!res.ok || (data && data.error)){ alert((data && data.error) || 'Failed to save package'); return; }
      closeRatePlan();
      await loadRatePlans();
    } catch(e) { alert('Failed to save package'); }
  }

  async function removeRatePlan(){
    var ratePlan = String(qs('rp-edit-original') && qs('rp-edit-original').value || '').trim().toUpperCase();
    if (!ratePlan) return;
    if (!window.confirm('Remove ' + ratePlan + '?')) return;
    try {
      var res = await fetch(API + '/api/rate_plans/delete', { method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ rate_plan: ratePlan }) });
      var data = await res.json();
      if (!res.ok || (data && data.error)){ alert((data && data.error) || 'Failed to remove package'); return; }
      closeRatePlan();
      await loadRatePlans();
    } catch(e) { alert('Failed to remove package'); }
  }

  async function loadRegistrationCard(){
    var rules = qs('rc-rules');
    var terms = qs('rc-terms');
    if (!rules || !terms || !HOTEL) return;
    try {
      var res = await fetch(API + '/api/property/registration_card?hotel_name=' + encodeURIComponent(HOTEL), { credentials:'include' });
      var data = await res.json();
      if (!res.ok || (data && data.error)) return;
      rules.value = String(data.house_rules || '');
      terms.value = String(data.terms || '');
    } catch(e) {}
  }

  async function saveRegistrationCard(){
    if (!HOTEL){ alert('Hotel required'); return; }
    var houseRules = String(qs('rc-rules') && qs('rc-rules').value || '');
    var terms = String(qs('rc-terms') && qs('rc-terms').value || '');
    try {
      var res = await fetch(API + '/api/property/registration_card', { method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ house_rules: houseRules, terms: terms }) });
      var data = await res.json();
      if (!res.ok || (data && data.error)){ alert((data && data.error) || 'Failed to save'); return; }
      alert('Saved');
    } catch(e) { alert('Failed to save'); }
  }

  async function loadTemplates(){
    const elOut = qs('kw-output'); if (elOut) elOut.textContent = 'Loading...';
    try {
      const url = API + '/api/property/templates' + (HOTEL?('?hotel_name='+encodeURIComponent(HOTEL)):'');
      const res = await fetch(url, { credentials:'include' });
      const data = await res.json();
      if (!res.ok || data.error){ if (elOut) elOut.textContent = 'Failed to load'; return; }
      const kt = data.keywords_template || {}; const ft = data.facilities_template || {}; const at = data.amenities_template || {};
      // Fill keywords form (policy-managed fields excluded)
      if (qs('kw-contact')) qs('kw-contact').value = kt.contact_phone || '';
      if (qs('kw-email')) qs('kw-email').value = kt.contact_email || '';
      if (qs('kw-tz')) qs('kw-tz').value = kt.timezone || '';
      if (qs('kw-address')) qs('kw-address').value = kt.address || '';
      if (qs('kw-city')) qs('kw-city').value = kt.city || '';
      if (qs('kw-state')) qs('kw-state').value = kt.state_region || '';
      if (qs('kw-country')) qs('kw-country').value = kt.country || '';
      if (qs('kw-desc')) qs('kw-desc').value = kt.description || '';
      // Facilities checkboxes
      Array.from(document.querySelectorAll('.kw-fac')).forEach(function(cb){
        const key = String(cb.value || '').toLowerCase();
        cb.checked = !!ft[key];
      });
      // Amenities inputs (allowed categories only)
      Array.from(document.querySelectorAll('.kw-am')).forEach(function(inp){
        const cat = String(inp.getAttribute('data-cat') || '').toLowerCase();
        inp.value = at[cat] || '';
      });
      // Output summary
      elOut.textContent = 'Templates loaded';
    } catch(e) { if (elOut) elOut.textContent = 'Failed to load'; }
  }

  async function saveKeywords(){
    // Facilities (booleans) from template checkboxes
    var fac = {};
    try {
      Array.from(document.querySelectorAll('.kw-fac')).forEach(function(cb){
        if (!cb || !cb.value) return;
        fac[String(cb.value).toLowerCase()] = !!cb.checked;
      });
    } catch(_){ }

    // Build templates from form (policy-managed fields excluded)
    var keywords_template = {
        contact_phone: String(qs('kw-contact')?.value || ''),
        contact_email: String(qs('kw-email')?.value || ''),
        timezone: String(qs('kw-tz')?.value || ''),
        address: String(qs('kw-address')?.value || ''),
        city: String(qs('kw-city')?.value || ''),
        state_region: String(qs('kw-state')?.value || ''),
        country: String(qs('kw-country')?.value || ''),
        description: String(qs('kw-desc')?.value || '')
    };
    // Amenities (one value per allowed category)
    var amenities_template = {};
    Array.from(document.querySelectorAll('.kw-am')).forEach(function(inp){
      const cat = String(inp.getAttribute('data-cat') || '').toLowerCase();
      const val = String(inp.value || '').trim();
      if (cat) amenities_template[cat] = val ? val.toUpperCase() : '';
    });
    if (!HOTEL){ alert('Hotel required'); return; }
    try {
      // Apply templates (template-first flow)
      var res = await fetch(API + '/api/property/templates/apply', { method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ hotel_name: HOTEL, facilities_template: fac, amenities_template, keywords_template }) });
      var data = await res.json();
      if (!res.ok || (data && data.error)){ alert((data && data.error) || 'Save failed'); return; }
      await loadTemplates();
      alert('Saved');
    } catch(e) { alert('Save failed'); }
  }

  async function loadPolicies(){
    var out = qs('pol-output'); if (out) out.textContent = 'Loading...';
    try {
      var t = (qs('pol-type')?.value || '').trim();
      var url = API + '/api/property/policies' + (HOTEL?('?hotel_name='+encodeURIComponent(HOTEL)):'') + (t?(HOTEL?('&'):'?') + 'policy_type='+encodeURIComponent(t):'');
      var res = await fetch(url, { credentials:'include' });
      var data = await res.json();
      // Prefer structured items if available
      if (data && Array.isArray(data.items)){
        var lines = [];
        data.items.forEach(function(it){
          var pt = String(it.policy_type || '').toUpperCase();
          var exists = it.exists ? 'EXISTS' : 'NEW';
          lines.push('[' + pt + '] ' + exists);
          try {
            var det = it.details || {};
            if (pt === 'PAYMENT'){
              lines.push('  - minimum payment(%): ' + (det.payment_threshold_percent ?? ''));
              lines.push('  - payment window(days): ' + (det.payment_window_days ?? ''));
              lines.push('  - methods: ' + ((det.methods_allowed || []).join(', ')));
              lines.push('  - deadline: ' + (det.deadline_policy || ''));
            } else if (pt === 'BOOKING'){
              lines.push('  - checkin: ' + (det.checkin_time || ''));
              lines.push('  - checkout: ' + (det.checkout_time || ''));
              lines.push('  - max nights: ' + (det.max_stay_nights ?? ''));
              var cp = det.cancellation_policy || {}; lines.push('  - cancellation: ' + (cp.window_hours ?? '') + 'h window, penalty ' + (cp.penalty_nights ?? '') + ' night(s), refund ' + (cp.refund_percent ?? '') + '%');
              var ns = det.no_show_policy || {}; lines.push('  - no-show penalty nights: ' + (ns.penalty_nights ?? ''));
              lines.push('  - overbooking: ' + (det.overbooking_policy || ''));
            } else if (pt === 'TAX'){
              lines.push('  - VAT%: ' + (det.vat_percent ?? ''));
              lines.push('  - Service%: ' + (det.service_charge_percent ?? ''));
              lines.push('  - Tourism tax/night: ' + (det.tourism_tax_per_night ?? ''));
            }
          } catch(_) {}
          if (it.updated_at){ lines.push('  - updated_at: ' + it.updated_at); }
          lines.push('');
        });
        out.textContent = lines.join('\n') || '-';
      } else {
        out.textContent = (data && data.result) ? data.result : '-';
      }
    } catch(e) { if (out) out.textContent = 'Failed to load'; }
  }

  function templateSchema(type){
    if (type === 'payment') return {
      payment_threshold_percent: 30,
      payment_window_days: 7,
      methods_allowed: ["CASH","CARD","BANK TRANSFER","OTA"],
      deadline_policy: "before_check_in",
      notes: "optional"
    };
    if (type === 'booking') return {
      checkin_time: "15:00",
      checkout_time: "12:00",
      max_stay_nights: 14,
      cancellation_policy: { window_hours: 48, penalty_nights: 1, refund_percent: 50 },
      no_show_policy: { penalty_nights: 1 },
      overbooking_policy: "do_not_overbook",
      notes: "optional"
    };
    if (type === 'tax') return {
      vat_percent: 6.0,
      service_charge_percent: 10.0,
      tourism_tax_per_night: 10.0,
      notes: "optional"
    };
    return {};
  }

  // Build payloads from modal inputs (fixed templates)
  function collectPaymentPayload(){
    return {
      payment_threshold_percent: parseFloat(qs('pm-threshold')?.value || '30') || 0,
      payment_window_days: parseInt(qs('pm-window-days')?.value || '7', 10) || 0,
      methods_allowed: Array.from(document.querySelectorAll('.pm-method'))
        .filter(function(cb){ return cb && cb.checked; })
        .map(function(cb){ return String(cb.value || '').toUpperCase(); })
        .filter(Boolean),
      deadline_policy: String(qs('pm-deadline')?.value || 'before_check_in'),
      notes: String(qs('pm-notes')?.value || '')
    };
  }
  function collectBookingPayload(){
    return {
      checkin_time: String(qs('bk-checkin')?.value || '15:00'),
      checkout_time: String(qs('bk-checkout')?.value || '12:00'),
      max_stay_nights: parseInt(qs('bk-max-nights')?.value || '14', 10) || 0,
      cancellation_policy: {
        window_hours: parseInt(qs('bk-cancel-window')?.value || '48', 10) || 0,
        penalty_nights: parseInt(qs('bk-cancel-penalty')?.value || '1', 10) || 0,
        refund_percent: parseFloat(qs('bk-cancel-refund')?.value || '50') || 0
      },
      no_show_policy: { penalty_nights: parseInt(qs('bk-noshow-penalty')?.value || '1', 10) || 0 },
      overbooking_policy: String(qs('bk-overbook')?.value || 'do_not_overbook'),
      notes: String(qs('bk-notes')?.value || '')
    };
  }
  function collectTaxPayload(){
    return {
      vat_percent: parseFloat(qs('tx-vat')?.value || '6.0') || 0,
      service_charge_percent: parseFloat(qs('tx-service')?.value || '10.0') || 0,
      tourism_tax_per_night: parseFloat(qs('tx-tourism')?.value || '10.0') || 0,
      notes: String(qs('tx-notes')?.value || '')
    };
  }

  async function createPolicyFixed(type, details){
    if (!HOTEL){ alert('Hotel required'); return; }
    try {
      var res = await fetch(API + '/api/property/policies/create', { method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ hotel_name: HOTEL, policy_type: type, policy_details: details }) });
      var data = await res.json();
      if (data && (data.error || (typeof data.result === 'string' && data.result.indexOf('❌') !== -1))){ return false; }
      await loadPolicies();
      return true;
    } catch(e) { return false; }
  }

  async function updatePolicyFixed(type, updates){
    if (!HOTEL){ alert('Hotel required'); return; }
    try {
      var res = await fetch(API + '/api/property/policies/update', { method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ hotel_name: HOTEL, policy_type: type, updates: updates }) });
      var data = await res.json();
      if (data && (data.error || (typeof data.result === 'string' && data.result.indexOf('❌') !== -1))){ return false; }
      await loadPolicies();
      return true;
    } catch(e) { return false; }
  }

  async function init(){
    await initHotel();
    try {
      var r1 = qs('kw-refresh'); if (r1) r1.addEventListener('click', function(){ loadTemplates(); });
      var s1 = qs('kw-save'); if (s1) s1.addEventListener('click', function(e){ try{ e.preventDefault(); e.stopPropagation(); }catch(_){} saveKeywords(); });
      var r2 = qs('pol-refresh'); if (r2) r2.addEventListener('click', function(){ loadPolicies(); });
      var rpR = qs('rp-refresh'); if (rpR) rpR.addEventListener('click', function(){ loadRatePlans(); });
      var rpA = qs('rp-add'); if (rpA) rpA.addEventListener('click', function(e){ try{ e.preventDefault(); }catch(_){} addRatePlan(); });
      var rpC = qs('rp-close'); if (rpC) rpC.addEventListener('click', function(){ closeRatePlan(); });
      var rpS = qs('rp-save'); if (rpS) rpS.addEventListener('click', function(e){ try{ e.preventDefault(); }catch(_){} saveRatePlan(); });
      var rpD = qs('rp-remove'); if (rpD) rpD.addEventListener('click', function(e){ try{ e.preventDefault(); }catch(_){} removeRatePlan(); });
      var rcS = qs('rc-save'); if (rcS) rcS.addEventListener('click', function(e){ try{ e.preventDefault(); }catch(_){} saveRegistrationCard(); });
      var pt = qs('pol-type'); if (pt) pt.addEventListener('change', function(){ loadPolicies(); });
      // Modal open/close
      var om = qs('open-pm'); if (om) om.addEventListener('click', function(){ qs('pm-modal').classList.remove('hidden'); });
      var cm = qs('pm-close'); if (cm) cm.addEventListener('click', function(){ qs('pm-modal').classList.add('hidden'); });
      var ob = qs('open-bk'); if (ob) ob.addEventListener('click', function(){ qs('bk-modal').classList.remove('hidden'); });
      var cb = qs('bk-close'); if (cb) cb.addEventListener('click', function(){ qs('bk-modal').classList.add('hidden'); });
      var ot = qs('open-tx'); if (ot) ot.addEventListener('click', function(){ qs('tx-modal').classList.remove('hidden'); });
      var ct = qs('tx-close'); if (ct) ct.addEventListener('click', function(){ qs('tx-modal').classList.add('hidden'); });
      // Modal submit bindings
      var pmU = qs('pm-update'); if (pmU) pmU.addEventListener('click', async function(e){ try{ e.preventDefault(); e.stopPropagation(); }catch(_){ }
        var ok = await updatePolicyFixed('payment', collectPaymentPayload());
        if (!ok) { await createPolicyFixed('payment', collectPaymentPayload()); }
        qs('pm-modal').classList.add('hidden');
      });
      var bkU = qs('bk-update'); if (bkU) bkU.addEventListener('click', async function(e){ try{ e.preventDefault(); e.stopPropagation(); }catch(_){ }
        var ok = await updatePolicyFixed('booking', collectBookingPayload());
        if (!ok) { await createPolicyFixed('booking', collectBookingPayload()); }
        qs('bk-modal').classList.add('hidden');
      });
      var txU = qs('tx-update'); if (txU) txU.addEventListener('click', async function(e){ try{ e.preventDefault(); e.stopPropagation(); }catch(_){ }
        var ok = await updatePolicyFixed('tax', collectTaxPayload());
        if (!ok) { await createPolicyFixed('tax', collectTaxPayload()); }
        qs('tx-modal').classList.add('hidden');
      });
    } catch(_) {}
    loadTemplates();
    loadPolicies();
    loadRatePlans();
    loadRegistrationCard();
  }

  document.addEventListener('DOMContentLoaded', init);
})();


