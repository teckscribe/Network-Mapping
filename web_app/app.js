// State Management
let currentLat = null;
let currentLon = null;
let currentAccuracy = null;
let mapInstance = null;
let mapMarker = null;
let accuracyCircle = null;
let gpsWatchId = null;
let gpsWatchTimer = null;
let bestAccuracy = Infinity;

const STORAGE_KEY = 'gpon_survey_records_v1';
let records = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');

// Device & Server Configuration
let deviceId = localStorage.getItem('gpon_device_id');
if (!deviceId) {
  deviceId = 'DEV-' + Math.random().toString(36).substring(2, 8).toUpperCase();
  localStorage.setItem('gpon_device_id', deviceId);
}

let serverUrl = localStorage.getItem('gpon_server_url');
if (!serverUrl || (window.location.protocol.startsWith('http') && window.location.port !== '5500')) {
  // If served via web server (e.g. https://network-mapping.online), always use current origin
  serverUrl = window.location.origin;
  localStorage.setItem('gpon_server_url', serverUrl);
}

let isSyncing = false;
let isServerReachable = false;
let serverConnectionChecked = false;
let lastKnownHierarchyVersion = parseInt(localStorage.getItem('gpon_hierarchy_version') || '0', 10);

// User Session & Authentication
let midnightTimer = null;

function getNextMidnightTimestamp() {
  const now = new Date();
  // Next midnight: tomorrow at 00:00:00 local time
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0, 0, 0);
  return midnight.getTime();
}

function clearSessionOnly() {
  currentUser = null;
  networkSurveyedPoints = {};
  localStorage.removeItem('gpon_logged_in_user');
  localStorage.removeItem('gpon_auth_token');
  localStorage.removeItem('gpon_session_expires_at');
  localStorage.removeItem('gpon_network_surveyed_points');
  if (midnightTimer) {
    clearTimeout(midnightTimer);
    midnightTimer = null;
  }
  // ZERO DATA LOSS INVARIANT:
  // STORAGE_KEY ('gpon_survey_records_v1') and 'gpon_pending_deletions' are NEVER cleared or modified.
  // All pending offline survey records are safely preserved on this device.
}

function scheduleMidnightLogout() {
  if (midnightTimer) {
    clearTimeout(midnightTimer);
    midnightTimer = null;
  }
  if (!currentUser) return;

  const nextMidnight = getNextMidnightTimestamp();
  const msUntilMidnight = Math.max(1000, nextMidnight - Date.now());

  midnightTimer = setTimeout(async () => {
    console.log('[Auth] 00:00 Midnight reached. Triggering daily session logout.');
    await handleMidnightSessionReset();
  }, msUntilMidnight);
}

async function handleMidnightSessionReset() {
  if (!currentUser) return;

  // 1. Attempt silent auto-sync of pending records before closing session if connected
  const pendingCount = (records || []).filter(r => r.sync_status !== 'synced').length;
  if (pendingCount > 0 && isServerReachable) {
    try {
      console.log(`[Auth] Attempting auto-sync of ${pendingCount} pending records prior to midnight logout...`);
      await syncWithServer(true);
    } catch (e) {
      console.warn('[Auth] Pre-midnight sync failed (offline):', e);
    }
  }

  // 2. Clear authentication session while keeping all offline records 100% safe
  clearSessionOnly();

  // 3. UI Update
  const foucStyle = document.getElementById('fouc-prevention');
  if (foucStyle) foucStyle.remove();
  updateUserBar();
  const overlay = document.getElementById('login-overlay');
  if (overlay) overlay.style.setProperty('display', 'flex', 'important');

  // Friendly midnight notice on the login card
  const loginErrBox = document.getElementById('login-error-msg');
  if (loginErrBox) {
    loginErrBox.style.display = 'block';
    loginErrBox.style.backgroundColor = '#eff6ff';
    loginErrBox.style.color = '#1e40af';
    loginErrBox.style.borderColor = '#bfdbfe';
    loginErrBox.innerHTML = '🌙 <strong>Daily Midnight Reset:</strong> Session ended at 00:00.<br><span style="font-size: 0.8rem; opacity: 0.95;">Please sign in for today\'s shift. All your pending offline records are safely preserved on this device.</span>';
  }
  showToast('🌙 Daily session reset at 00:00. Offline records preserved.', true);
}

function checkMidnightExpiration() {
  if (!currentUser) return;
  const expiresAt = parseInt(localStorage.getItem('gpon_session_expires_at') || '0', 10);
  if (expiresAt > 0 && Date.now() >= expiresAt) {
    console.log('[Auth] Session crossed midnight (00:00). Triggering session reset.');
    handleMidnightSessionReset();
  }
}

async function hashOfflineCredential(username, password) {
  try {
    if (window.crypto && crypto.subtle) {
      const enc = new TextEncoder();
      const data = enc.encode(`gpon_salt_${username.toLowerCase().trim()}_${password}`);
      const hashBuf = await crypto.subtle.digest('SHA-256', data);
      return Array.from(new Uint8Array(hashBuf)).map(b => b.toString(16).padStart(2, '0')).join('');
    }
  } catch (e) {}
  return btoa(`${username.toLowerCase().trim()}:${password}`);
}

function getStoredUser() {
  try {
    const raw = localStorage.getItem('gpon_logged_in_user');
    if (!raw || raw === 'null' || raw === 'undefined' || raw === '{}') return null;
    const u = JSON.parse(raw);
    if (u && typeof u === 'object' && u.username && typeof u.username === 'string' && u.username.trim().length > 0) {
      // Check if session has crossed 00:00 midnight
      const expiresAt = parseInt(localStorage.getItem('gpon_session_expires_at') || '0', 10);
      if (expiresAt > 0 && Date.now() >= expiresAt) {
        console.log('[Auth] Daily session expired past midnight (00:00). Requiring login for today.');
        clearSessionOnly();
        return null;
      }
      return u;
    }
  } catch (e) {
    console.warn('Invalid user session in localStorage:', e);
  }
  localStorage.removeItem('gpon_logged_in_user');
  return null;
}
let currentUser = getStoredUser();

// Network-Wide Surveyed Points Map (for duplicate checking, field locks, and ACSO supervisor updates)
let networkSurveyedPoints = {};
let currentAcsoEditingUuid = null;

try {
  const cachedPoints = localStorage.getItem('gpon_network_surveyed_points');
  if (cachedPoints) {
    networkSurveyedPoints = JSON.parse(cachedPoints);
  }
} catch (e) {
  networkSurveyedPoints = {};
}

function getColorVariants(color) {
  const c = String(color || '').trim().toUpperCase();
  if (!c) return [];
  const variants = [c];
  const m = c.match(/^OUT\s+(\d+)\s*-\s*(.+)$/);
  if (m) {
    const portNum = parseInt(m[1], 10);
    const colName = m[2].trim();
    // Only ports 1-12 alias to plain color name (prevents Out 13/25 from colliding with Out 1)
    if (portNum <= 12 && !variants.includes(colName)) {
      variants.push(colName);
    }
  } else {
    const out1Variant = `OUT 1 - ${c}`;
    if (!variants.includes(out1Variant)) {
      variants.push(out1Variant);
    }
  }
  return variants;
}

function getLeadKey(eid, spl, color) {
  if (!eid || !spl) return '';
  const c = color ? String(color).trim().toUpperCase() : '';
  return `${eid}|${spl}|${c}`.toUpperCase();
}

function getSplitterKey(eid, spl) {
  if (!eid || !spl) return '';
  return `${eid}|${spl}`.toUpperCase();
}

function getLeadSurveyedInfo(eid, spl, color) {
  if (!eid || !spl) return null;
  const eidUp = eid.trim().toUpperCase();
  const splUp = spl.trim().toUpperCase();
  const colVariants = getColorVariants(color);

  // 1. Check in networkSurveyedPoints
  if (networkSurveyedPoints) {
    for (const cv of colVariants) {
      const k = `${eidUp}|${splUp}|${cv}`;
      if (networkSurveyedPoints[k] && !networkSurveyedPoints[k].is_summary) {
        return networkSurveyedPoints[k];
      }
    }
    if (colVariants.length === 0) {
      const kEmpty = `${eidUp}|${splUp}|`;
      if (networkSurveyedPoints[kEmpty] && !networkSurveyedPoints[kEmpty].is_summary) {
        return networkSurveyedPoints[kEmpty];
      }
    }
  }

  // 2. Check in local records array
  const local = records.find(r => {
    const rEid = (r["Enclosure ID"] || r.enclosure_id || '').trim().toUpperCase();
    const rSpl = (r["Splitter ID"] || r.splitter_id || '').trim().toUpperCase();
    if (rEid !== eidUp || rSpl !== splUp) return false;
    const rCol = (r["Splitter Lead Colour Code"] || r.splitter_lead_color || '').trim().toUpperCase();
    if (colVariants.length === 0 && !rCol) return true;
    const rVariants = getColorVariants(rCol);
    return colVariants.some(v => rVariants.includes(v));
  });

  if (local) {
    return {
      client_uuid: local.client_uuid,
      enclosure_id: local["Enclosure ID"] || local.enclosure_id,
      splitter_id: local["Splitter ID"] || local.splitter_id,
      splitter_ratio: local["Splitter Ratio"] || local.splitter_ratio || '',
      splitter_lead_color: local["Splitter Lead Colour Code"] || local.splitter_lead_color || '',
      surveyor_name: local.surveyor_name || local.surveyor_username || 'Local',
      survey_date_time: local["Date & Time"] || local.survey_date_time || '',
      kseb_post_number: local["KSEB Post Number"] || local.kseb_post_number || '',
      landmark: local["Land Mark"] || local.landmark || '',
      lat_long: local["Lat /Long"] || local.lat_long || '',
      customers_connected: (local["No: Of Customer Connected"] !== undefined ? local["No: Of Customer Connected"] : local.customers_connected) || 0,
      adl_subscriber_id: local["ADL Subscriber ID"] || local.adl_subscriber_id || '',
      acs_subscriber_id: local["ACS Subscriber ID"] || local.acs_subscriber_id || ''
    };
  }
  return null;
}

function getSplitterSummary(eid, spl) {
  if (!eid || !spl) return null;
  const eidUp = eid.trim().toUpperCase();
  const splUp = spl.trim().toUpperCase();
  const splKey = `${eidUp}|${splUp}`;

  const leadsSet = new Set();
  let latestInfo = null;

  if (networkSurveyedPoints) {
    if (networkSurveyedPoints[splKey]) {
      const s = networkSurveyedPoints[splKey];
      latestInfo = Object.assign({}, s);
      if (Array.isArray(s.leads)) {
        s.leads.forEach(l => leadsSet.add(l.toUpperCase()));
      }
    }
    const prefix = splKey + '|';
    Object.keys(networkSurveyedPoints).forEach(k => {
      if (k.startsWith(prefix)) {
        const item = networkSurveyedPoints[k];
        if (item && !item.is_summary) {
          if (!latestInfo) {
            latestInfo = Object.assign({}, item);
          } else {
            // Fill any missing properties from individual lead items
            if (latestInfo.customers_connected === undefined || latestInfo.customers_connected === null || latestInfo.customers_connected === '') {
              if (item.customers_connected !== undefined && item.customers_connected !== null && item.customers_connected !== '') {
                latestInfo.customers_connected = item.customers_connected;
              }
            }
            if (!latestInfo.splitter_ratio && item.splitter_ratio) latestInfo.splitter_ratio = item.splitter_ratio;
            if (!latestInfo.kseb_post_number && item.kseb_post_number) latestInfo.kseb_post_number = item.kseb_post_number;
            if (!latestInfo.landmark && item.landmark) latestInfo.landmark = item.landmark;
            if (!latestInfo.lat_long && item.lat_long) latestInfo.lat_long = item.lat_long;
          }
          const col = (item.splitter_lead_color || '').trim().toUpperCase();
          if (col) leadsSet.add(col);
        }
      }
    });
  }

  records.forEach(r => {
    const rEid = (r["Enclosure ID"] || r.enclosure_id || '').trim().toUpperCase();
    const rSpl = (r["Splitter ID"] || r.splitter_id || '').trim().toUpperCase();
    if (rEid === eidUp && rSpl === splUp) {
      const rCust = (r["No: Of Customer Connected"] !== undefined ? r["No: Of Customer Connected"] : r.customers_connected);
      if (!latestInfo) {
        latestInfo = {
          splitter_ratio: r["Splitter Ratio"] || r.splitter_ratio,
          customers_connected: rCust,
          kseb_post_number: r["KSEB Post Number"] || r.kseb_post_number,
          landmark: r["Land Mark"] || r.landmark,
          lat_long: r["Lat /Long"] || r.lat_long,
          surveyor_name: r.surveyor_name || r.surveyor_username,
          survey_date_time: r["Date & Time"] || r.survey_date_time
        };
      } else {
        if (rCust !== undefined && rCust !== null && rCust !== '') {
          latestInfo.customers_connected = rCust;
        }
        if (!latestInfo.splitter_ratio) latestInfo.splitter_ratio = r["Splitter Ratio"] || r.splitter_ratio;
        if (!latestInfo.kseb_post_number) latestInfo.kseb_post_number = r["KSEB Post Number"] || r.kseb_post_number;
        if (!latestInfo.landmark) latestInfo.landmark = r["Land Mark"] || r.landmark;
        if (!latestInfo.lat_long) latestInfo.lat_long = r["Lat /Long"] || r.lat_long;
      }
      const col = (r["Splitter Lead Colour Code"] || r.splitter_lead_color || '').trim().toUpperCase();
      if (col) leadsSet.add(col);
    }
  });

  const leadsList = Array.from(leadsSet);
  if (leadsList.length === 0 && !latestInfo) return null;

  const resolvedCustomers = (latestInfo && (latestInfo.customers_connected !== undefined ? latestInfo.customers_connected : latestInfo["No: Of Customer Connected"]));

  return {
    count: leadsList.length,
    leads: leadsList,
    splitter_ratio: (latestInfo && latestInfo.splitter_ratio) || '',
    customers_connected: (resolvedCustomers !== undefined && resolvedCustomers !== null && resolvedCustomers !== '') ? resolvedCustomers : '',
    kseb_post_number: (latestInfo && latestInfo.kseb_post_number) || '',
    landmark: (latestInfo && latestInfo.landmark) || '',
    lat_long: (latestInfo && latestInfo.lat_long) || '',
    surveyor_name: (latestInfo && (latestInfo.surveyor_name || latestInfo.surveyor_username)) || 'Surveyor',
    survey_date_time: (latestInfo && latestInfo.survey_date_time) || ''
  };
}

function getSurveyedInfo(eid, spl, color = '') {
  if (!eid || !spl) return null;
  if (color) {
    return getLeadSurveyedInfo(eid, spl, color);
  }
  return getSplitterSummary(eid, spl) || getLeadSurveyedInfo(eid, spl, '');
}

function getSurveyedSplittersForEnclosure(eid) {
  const surveyed = new Set();
  const eidUpper = (eid || '').toUpperCase();
  if (!eidUpper) return [];

  if (networkSurveyedPoints) {
    Object.keys(networkSurveyedPoints).forEach(key => {
      if (key.startsWith(eidUpper + '|')) {
        const parts = key.split('|');
        if (parts[1]) surveyed.add(parts[1]);
      }
    });
  }

  records.forEach(r => {
    const rEid = (r["Enclosure ID"] || r.enclosure_id || '').toUpperCase();
    const rSpl = (r["Splitter ID"] || r.splitter_id || '').toUpperCase();
    if (rEid === eidUpper && rSpl) {
      surveyed.add(rSpl);
    }
  });

  return Array.from(surveyed);
}

async function fetchSurveyedPoints() {
  try {
    const center = (centerSelect && centerSelect.value && centerSelect.value !== 'Center') 
      ? centerSelect.value 
      : (currentUser && currentUser.assigned_center && currentUser.assigned_center !== 'ALL' ? currentUser.assigned_center : '');
    
    const query = center ? `?center=${encodeURIComponent(center)}` : '';
    const token = localStorage.getItem('gpon_auth_token') || '';
    const headers = {};
    if (token) headers['Authorization'] = 'Bearer ' + token;

    const res = await fetch(`${serverUrl}/api/surveyed-points${query}`, { headers });
    if (res.ok) {
      const data = await res.json();
      if (data && data.points) {
        networkSurveyedPoints = data.points;
        localStorage.setItem('gpon_network_surveyed_points', JSON.stringify(networkSurveyedPoints));
        updateAvailableEnclosures();
        updateAvailableSplitters();
      }
    }
  } catch (err) {
    console.warn('Could not fetch surveyed points from server (using offline cache):', err);
  }
}

function normalizeClientRole(role) {
  const r = (role || '').trim().toLowerCase();
  if (r === 'admin' || r === 'super_admin' || r === 'superadmin') return 'super_admin';
  if (r === 'rcsm') return 'rcsm';
  if (r === 'acso') return 'acso';
  return 'field_technician';
}


function updateUserBar() {
  const userBar = document.getElementById('user-bar');
  const nameEl = document.getElementById('logged-user-name');
  const centerEl = document.getElementById('logged-user-center');
  const roleEl = document.getElementById('logged-user-role');
  const adminLink = document.getElementById('admin-nav-link');
  const rcsmBanner = document.getElementById('rcsm-banner');
  const submitBtn = document.querySelector('.btn-add-row');
  const excelBtn = document.getElementById('btn-export-excel') || document.querySelector('.btn-gs-excel');

  if (currentUser) {
    nameEl.innerText = currentUser.full_name || currentUser.username;
        const assignedStr = currentUser.assigned_center || 'ALL';
    const assignedArr = (currentUser.assigned_centers || assignedStr.split(',')).map(s => s.trim()).filter(Boolean);
    if (assignedArr.length > 1 && !assignedArr.includes('ALL')) {
      centerEl.innerText = `🏢 ${assignedArr.length} Centers Charge`;
      centerEl.title = `Assigned Centers: ${assignedArr.join(', ')}`;
    } else {
      centerEl.innerText = assignedStr;
    }
    userBar.style.display = 'flex';

    const role = normalizeClientRole(currentUser.role);
    currentUser.role = role;

    // 1. Role Badges
    if (roleEl) {
      roleEl.className = `gs-role-badge role-${role}`;
      if (role === 'super_admin') {
        roleEl.innerText = '👑 Super Admin';
      } else if (role === 'rcsm') {
        roleEl.innerText = '📊 RCSM';
      } else if (role === 'acso') {
        roleEl.innerText = '📝 ACSO';
      } else {
        roleEl.innerText = '👷 Field Tech';
      }
    }

    // 2. Dashboard Link for Super Admin & RCSM
    if (adminLink) {
      if (role === 'super_admin') {
        adminLink.innerText = '⚙️ Admin Dashboard';
        adminLink.style.display = 'inline-block';
      } else if (role === 'rcsm') {
        adminLink.innerText = '📊 RCSM Dashboard';
        adminLink.style.display = 'inline-block';
      } else {
        adminLink.style.display = 'none';
      }
    }

    // 3. Field Survey Entry: Enabled for all roles (including RCSM, ACSO, Super Admin, and Field Tech)
    const isEntryAllowed = true;
    if (rcsmBanner) rcsmBanner.style.display = 'none';
    if (submitBtn) submitBtn.style.display = 'flex';

    const formInputs = document.querySelectorAll('.gs-card-body input, .gs-card-body select');
    formInputs.forEach(el => {
      if (!['tech-select', 'olt-type-select', 'region-select'].includes(el.id)) {
        el.removeAttribute('disabled');
      }
    });

    // 4. Field Technician: "Option to enter the field input only no options to download the data"
    // ACSO & RCSM & Super Admin can download data. Field Technician cannot.
    const isDownloadAllowed = (role !== 'field_technician');
    if (excelBtn) excelBtn.style.display = isDownloadAllowed ? 'inline-flex' : 'none';

    updateAvailableEnclosures();
    updateAvailableSplitters();
  } else {
    userBar.style.display = 'none';
  }
}

// ==========================================
// AUTHENTICATION & LOGIN HELPERS
// ==========================================

function togglePasswordVisibility(inputId, btnId) {
  const input = document.getElementById(inputId);
  const btn = document.getElementById(btnId);
  if (!input) return;
  if (input.type === 'password') {
    input.type = 'text';
    if (btn) {
      btn.innerText = '🙈';
      btn.title = 'Hide password';
      btn.setAttribute('aria-label', 'Hide password');
    }
  } else {
    input.type = 'password';
    if (btn) {
      btn.innerText = '👁️';
      btn.title = 'Show password';
      btn.setAttribute('aria-label', 'Show password');
    }
  }
}

function setLoginError(htmlMsg) {
  const errBox = document.getElementById('login-error-msg');
  if (errBox) {
    errBox.innerHTML = htmlMsg;
    errBox.style.display = 'block';
    errBox.style.backgroundColor = '#fef2f2';
    errBox.style.color = '#991b1b';
    errBox.style.borderColor = '#fecaca';
    errBox.classList.remove('shake-anim');
    void errBox.offsetWidth; // Force DOM reflow to re-trigger shake animation
    errBox.classList.add('shake-anim');
  }
  // Strip HTML tags for clean toast notification
  const plainText = htmlMsg.replace(/<[^>]*>?/gm, '');
  showToast(plainText, false);
}

function clearLoginError() {
  const errBox = document.getElementById('login-error-msg');
  if (errBox) {
    errBox.style.display = 'none';
    errBox.innerHTML = '';
    errBox.style.backgroundColor = '';
    errBox.style.color = '';
    errBox.style.borderColor = '';
  }
}

async function handleLogin(e) {
  if (e) e.preventDefault();
  clearLoginError();

  const uInput = document.getElementById('login-username');
  const pInput = document.getElementById('login-password');
  const submitBtn = document.getElementById('btn-login-submit');

  const u = (uInput ? uInput.value : '').trim();
  const p = (pInput ? pInput.value : '').trim();
  const rememberCheckbox = document.getElementById('login-remember-me');
  const remember = rememberCheckbox ? rememberCheckbox.checked : true;
  
  if (!u || !p) {
    setLoginError('⚠️ <strong>Please enter username and password.</strong>');
    if (!u && uInput) uInput.focus();
    else if (!p && pInput) pInput.focus();
    return;
  }

  // Visual loading indicator on submit button
  const origBtnHtml = submitBtn ? submitBtn.innerHTML : 'Sign In';
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerHTML = '⏳ Signing in...';
    submitBtn.style.opacity = '0.75';
    submitBtn.style.cursor = 'not-allowed';
  }

  let serverContacted = false;

  // 1. Attempt login with Ubuntu server (15-second timeout for mobile networks)
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 15000);
    const res = await fetch(`${serverUrl}/api/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: u, password: p }),
      signal: controller.signal
    });
    clearTimeout(timeoutId);
    serverContacted = true;

    if (res.ok) {
      const data = await res.json();
      if (data.token) {
        localStorage.setItem('gpon_auth_token', data.token);
      }
      if (remember) {
        localStorage.setItem('gpon_remember_creds', 'true');
        localStorage.setItem('gpon_remembered_username', u);
      } else {
        localStorage.removeItem('gpon_remember_creds');
        localStorage.removeItem('gpon_remembered_username');
      }
      // Purge any legacy plaintext passwords stored previously
      localStorage.removeItem('gpon_remembered_password');

      // Cache credentials securely for offline field re-authentication
      try {
        const hash = await hashOfflineCredential(u, p);
        localStorage.setItem('gpon_offline_user_' + u.toLowerCase().trim(), JSON.stringify({
          user: data.user,
          hash: hash
        }));
      } catch (e) {}

      setCurrentUser(data.user);
      showToast(`Welcome, ${data.user.full_name}!`);
      return;
    } else {
      // Server returned an HTTP error
      const errData = await res.json().catch(() => ({}));
      if (res.status === 401) {
        setLoginError('⚠️ <strong>Invalid username or password.</strong><br><span style="font-size: 0.78rem; opacity: 0.95;">Tap the 👁️ eye icon to check your password for typos or phone auto-capitalization.</span>');
      } else if (res.status === 429) {
        setLoginError('⏳ <strong>Too many login attempts.</strong><br><span style="font-size: 0.78rem;">Please wait 1 minute before trying again.</span>');
      } else if (res.status >= 500) {
        setLoginError(`⚠️ <strong>Server Error (${res.status}).</strong><br><span style="font-size: 0.78rem;">The server is restarting or busy. Please retry in a few moments.</span>`);
      } else {
        setLoginError(`⚠️ <strong>${errData.detail || 'Login failed. Please check credentials.'}</strong>`);
      }
      return;
    }
  } catch (err) {
    console.log('Server login error / unreachable:', err);
    if (err.name === 'AbortError') {
      setLoginError('⚠️ <strong>Connection Timed Out.</strong><br><span style="font-size: 0.78rem;">Server took too long to respond. Please check your mobile signal and retry.</span>');
      return;
    }
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.innerHTML = origBtnHtml;
      submitBtn.style.opacity = '1';
      submitBtn.style.cursor = 'pointer';
    }
  }

  // 2. Offline fallback credentials for remote emergency field areas
  if (!serverContacted) {
    let offlineMatchedUser = null;

    // Check cached offline credentials for this device
    try {
      const cachedRaw = localStorage.getItem('gpon_offline_user_' + u.toLowerCase().trim());
      if (cachedRaw) {
        const cachedObj = JSON.parse(cachedRaw);
        const inputHash = await hashOfflineCredential(u, p);
        if (cachedObj && cachedObj.hash === inputHash) {
          offlineMatchedUser = cachedObj.user;
        }
      }
    } catch (e) {
      console.warn('Offline credential verification error:', e);
    }

    if (!offlineMatchedUser) {
      const offlineUsers = {
        'admin': { username: 'admin', full_name: 'Central Super Administrator', email: 'admin@gpon.local', assigned_center: 'ALL', assigned_region: 'ALL', role: 'super_admin' }
      };
      if (offlineUsers[u.toLowerCase()] && p === 'admin123') {
        offlineMatchedUser = offlineUsers[u.toLowerCase()];
      }
    }

    if (offlineMatchedUser) {
      if (remember) {
        localStorage.setItem('gpon_remember_creds', 'true');
        localStorage.setItem('gpon_remembered_username', u);
      }
      localStorage.removeItem('gpon_remembered_password');
      setCurrentUser(offlineMatchedUser);
      showToast(`Offline Login: Welcome, ${offlineMatchedUser.full_name}! (Working Offline)`);
    } else {
      setLoginError('⚠️ <strong>Unable to connect to server.</strong><br><span style="font-size: 0.78rem;">Please check your mobile data / Wi-Fi or verify credentials.</span>');
    }
  }
}

function quickLogin(u, p) {
  document.getElementById('login-username').value = u;
  document.getElementById('login-password').value = p;
  handleLogin();
}

function setCurrentUser(user) {
  currentUser = user;
  localStorage.setItem('gpon_logged_in_user', JSON.stringify(user));

  // Schedule midnight daily session expiration (00:00 local time)
  const nextMidnight = getNextMidnightTimestamp();
  localStorage.setItem('gpon_session_expires_at', String(nextMidnight));
  scheduleMidnightLogout();

  const overlay = document.getElementById('login-overlay');
  if (overlay) overlay.style.setProperty('display', 'none', 'important');
  updateUserBar();
  initDropdowns();
  fetchSurveyedPoints();
}

async function logout() {
  const pendingCount = (records || []).filter(r => r.sync_status !== 'synced').length;

  // If connected and pending records exist, attempt auto-sync before signing out
  if (pendingCount > 0 && isServerReachable) {
    showToast('🔄 Syncing pending records before sign out...', true);
    try {
      await syncWithServer(true);
    } catch (e) {
      console.warn('Pre-logout sync error:', e);
    }
  }

  const remainingPending = (records || []).filter(r => r.sync_status !== 'synced').length;
  let confirmMsg = 'Log out from survey account?';
  if (remainingPending > 0) {
    confirmMsg = `⚠️ You have ${remainingPending} unsynced record(s) on this device.\n\nThey are safely saved in local offline storage and will NOT be lost. When you or another surveyor signs in with internet, they will sync to the server.\n\nDo you want to log out now?`;
  }

  if (confirm(confirmMsg)) {
    // If online, notify server of logout
    try {
      const token = localStorage.getItem('gpon_auth_token') || '';
      if (token && isServerReachable) {
        fetch(`${serverUrl}/api/logout`, {
          method: 'POST',
          headers: { 'Authorization': 'Bearer ' + token }
        }).catch(() => {});
      }
    } catch (e) {}

    clearSessionOnly();
    const foucStyle = document.getElementById('fouc-prevention');
    if (foucStyle) foucStyle.remove();
    updateUserBar();
    const overlay = document.getElementById('login-overlay');
    if (overlay) overlay.style.setProperty('display', 'flex', 'important');
    showToast('Signed out successfully. Offline records preserved.', true);
  }
}

let resetResendTimer = null;
let resetResendCountdown = 0;

function goToResetStep(step) {
  const s1 = document.getElementById('reset-step-1');
  const s2 = document.getElementById('reset-step-2');
  const err1 = document.getElementById('reset-step1-error');
  const err2 = document.getElementById('reset-step2-error');
  const succ2 = document.getElementById('reset-step2-success');

  if (err1) err1.style.display = 'none';
  if (err2) err2.style.display = 'none';
  if (succ2) succ2.style.display = 'none';

  if (step === 1) {
    if (s1) s1.style.display = 'block';
    if (s2) s2.style.display = 'none';
    const idField = document.getElementById('reset-identifier');
    if (idField) setTimeout(() => idField.focus(), 100);
  } else {
    if (s1) s1.style.display = 'none';
    if (s2) s2.style.display = 'block';
    const otpField = document.getElementById('reset-otp-code');
    if (otpField) {
      otpField.value = '';
      setTimeout(() => otpField.focus(), 100);
    }
  }
}

function openResetPasswordModal(username, email) {
  const overlay = document.getElementById('reset-password-overlay');
  if (!overlay) return;
  const idField = document.getElementById('reset-identifier');
  const p1 = document.getElementById('reset-new-password');
  const p2 = document.getElementById('reset-confirm-password');

  if (idField) {
    idField.value = username || email || (currentUser ? currentUser.username : (document.getElementById('login-username').value || ''));
  }
  if (p1) p1.value = '';
  if (p2) p2.value = '';

  goToResetStep(1);
  overlay.style.display = 'flex';
}

function closeResetPasswordModal() {
  const overlay = document.getElementById('reset-password-overlay');
  if (overlay) overlay.style.display = 'none';
  if (resetResendTimer) {
    clearInterval(resetResendTimer);
    resetResendTimer = null;
  }
}

function startResendCooldown() {
  const btn = document.getElementById('btn-resend-reset-otp');
  if (!btn) return;
  if (resetResendTimer) clearInterval(resetResendTimer);
  resetResendCountdown = 60;
  btn.disabled = true;
  btn.style.opacity = '0.6';
  btn.innerText = `⏳ Resend in ${resetResendCountdown}s`;

  resetResendTimer = setInterval(() => {
    resetResendCountdown -= 1;
    if (resetResendCountdown <= 0) {
      clearInterval(resetResendTimer);
      resetResendTimer = null;
      btn.disabled = false;
      btn.style.opacity = '1';
      btn.innerText = '🔄 Resend Code';
    } else {
      btn.innerText = `⏳ Resend in ${resetResendCountdown}s`;
    }
  }, 1000);
}

async function handleRequestResetOtp(e) {
  if (e) e.preventDefault();
  const idField = document.getElementById('reset-identifier');
  const btn = document.getElementById('btn-send-reset-otp');
  const errDiv = document.getElementById('reset-step1-error');
  if (errDiv) errDiv.style.display = 'none';

  const val = idField ? idField.value.trim() : '';
  if (!val) {
    if (errDiv) {
      errDiv.innerText = 'Please enter your username or registered email address.';
      errDiv.style.display = 'block';
    }
    return;
  }

  if (btn) {
    btn.disabled = true;
    btn.innerText = '⏳ Sending OTP...';
  }

  try {
    const res = await fetch(`${serverUrl}/api/request-password-reset-otp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username_or_email: val })
    });
    const data = await res.json();
    if (res.ok) {
      document.getElementById('reset-verified-username').value = data.username;
      const maskedEl = document.getElementById('reset-masked-email');
      if (maskedEl) maskedEl.innerText = data.masked_email;
      goToResetStep(2);
      startResendCooldown();
    } else {
      if (errDiv) {
        errDiv.innerText = data.detail || 'Could not send verification OTP.';
        errDiv.style.display = 'block';
      }
    }
  } catch (err) {
    if (errDiv) {
      errDiv.innerText = 'Cannot reach the server. Please check your network connection.';
      errDiv.style.display = 'block';
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerText = '📩 Send Verification OTP';
    }
  }
}

async function resendResetOtp() {
  const uname = document.getElementById('reset-verified-username').value.trim();
  if (!uname) {
    goToResetStep(1);
    return;
  }
  const btn = document.getElementById('btn-resend-reset-otp');
  if (btn && btn.disabled) return;

  try {
    const res = await fetch(`${serverUrl}/api/request-password-reset-otp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username_or_email: uname })
    });
    const data = await res.json();
    if (res.ok) {
      showToast('New 6-digit OTP sent to your registered email!', true);
      startResendCooldown();
    } else {
      showToast(data.detail || 'Failed to resend code.', false);
    }
  } catch (e) {
    showToast('Cannot connect to server to resend code.', false);
  }
}

async function handleVerifyResetOtp(e) {
  if (e) e.preventDefault();
  const uname = document.getElementById('reset-verified-username').value.trim();
  const otp = document.getElementById('reset-otp-code').value.trim();
  const p1 = document.getElementById('reset-new-password').value.trim();
  const p2 = document.getElementById('reset-confirm-password').value.trim();
  const btn = document.getElementById('btn-verify-reset-otp');
  const errDiv = document.getElementById('reset-step2-error');
  const succDiv = document.getElementById('reset-step2-success');

  if (errDiv) errDiv.style.display = 'none';
  if (succDiv) succDiv.style.display = 'none';

  if (!otp || otp.length !== 6) {
    if (errDiv) {
      errDiv.innerText = 'Please enter the complete 6-digit verification OTP.';
      errDiv.style.display = 'block';
    }
    return;
  }

  if (!p1 || p1.length < 4) {
    if (errDiv) {
      errDiv.innerText = 'New password must be at least 4 characters long.';
      errDiv.style.display = 'block';
    }
    return;
  }

  if (p1 !== p2) {
    if (errDiv) {
      errDiv.innerText = 'Passwords do not match. Please re-enter.';
      errDiv.style.display = 'block';
    }
    return;
  }

  if (btn) {
    btn.disabled = true;
    btn.innerText = '⏳ Verifying...';
  }

  try {
    const res = await fetch(`${serverUrl}/api/verify-password-reset-otp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: uname,
        otp: otp,
        new_password: p1
      })
    });
    const data = await res.json();
    if (res.ok) {
      if (succDiv) {
        succDiv.innerText = data.message || 'Password updated successfully!';
        succDiv.style.display = 'block';
      }
      setTimeout(() => {
        closeResetPasswordModal();
        const loginU = document.getElementById('login-username');
        const loginP = document.getElementById('login-password');
        if (loginU) loginU.value = uname;
        if (loginP) loginP.value = p1;
        showToast('Password updated! You can now log in.', true);
      }, 1500);
    } else {
      if (errDiv) {
        errDiv.innerText = data.detail || 'Could not verify OTP.';
        errDiv.style.display = 'block';
      }
    }
  } catch (err) {
    if (errDiv) {
      errDiv.innerText = 'Cannot reach the server. Please check your connection.';
      errDiv.style.display = 'block';
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerText = '✓ Verify OTP & Set Password';
    }
  }
}

// DOM Elements
const regionSelect = document.getElementById('region-select');
const centerSelect = document.getElementById('center-select');
const rtRoomSelect = document.getElementById('rtroom-select');
const techSelect = document.getElementById('tech-select');
const oltSelect = document.getElementById('olt-select');
const oltTypeSelect = document.getElementById('olt-type-select');
const portSelect = document.getElementById('port-select');
const postInput = document.getElementById('post-input');
const landmarkInput = document.getElementById('landmark-input');
const enclosureSelect = document.getElementById('enclosure-select');
const enclosureIdPreview = document.getElementById('enclosure-id-preview');
const splitterIdSelect = document.getElementById('splitter-id-select');
const splitterRatioSelect = document.getElementById('splitter-ratio-select');
const custCountInput = document.getElementById('cust-count-input');
const splitterColorSelect = document.getElementById('splitter-color-select');
const adlSubInput = document.getElementById('adl-sub-input');
const acsSubInput = document.getElementById('acs-sub-input');

function getFormattedDateTime(d = new Date()) {
  const pad = (n) => String(n).padStart(2, '0');
  const year = d.getFullYear();
  const month = pad(d.getMonth() + 1);
  const day = pad(d.getDate());
  const hours = pad(d.getHours());
  const mins = pad(d.getMinutes());
  const secs = pad(d.getSeconds());
  return `${year}-${month}-${day} ${hours}:${mins}:${secs}`;
}

const manualCoordsInput = document.getElementById('manual-coords-input');
const gpsAccText = document.getElementById('gps-acc');
const mapContainer = document.getElementById('map-container');
const mapToggleBtn = document.getElementById('map-toggle-btn');
const recordsBadge = document.getElementById('records-count-badge');

function parseCoordinates(val) {
  if (!val || typeof val !== 'string') return null;
  const clean = val.trim();
  if (!clean) return null;
  // Match two decimal floats in the string (supports commas, tabs from Excel, spaces, semicolons, etc.)
  const matches = clean.match(/[-+]?[0-9]*\.?[0-9]+/g);
  if (matches && matches.length >= 2) {
    const lat = parseFloat(matches[0]);
    const lon = parseFloat(matches[1]);
    if (!isNaN(lat) && !isNaN(lon) && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180 && (lat !== 0 || lon !== 0)) {
      return { lat, lon, formatted: `${lat.toFixed(6)}, ${lon.toFixed(6)}` };
    }
  }
  return null;
}

function onManualCoordsChange(val) {
  if (!val) return;
  const parsed = parseCoordinates(val);
  if (parsed) {
    currentLat = parsed.lat;
    currentLon = parsed.lon;
    if (manualCoordsInput && manualCoordsInput.value !== parsed.formatted) {
      manualCoordsInput.value = parsed.formatted;
    }
    if (gpsAccText) {
      gpsAccText.innerHTML = '<span style="color:#0284c7; font-weight:600;">📍 Coordinates Entered Manually / Pasted from Excel</span>';
    }
    if (mapInstance) {
      updateMapPosition(currentLat, currentLon, null);
    }
    showToast(`Coordinates updated: ${parsed.formatted}`);
  }
}

// Toast Notification
function showToast(msg, isSuccess = true) {
  const toast = document.getElementById('toast');
  toast.innerText = msg;
  toast.style.background = isSuccess ? '#10b981' : '#ef4444';
  toast.style.display = 'block';
  setTimeout(() => {
    toast.style.display = 'none';
  }, 2500);
}

// HTML Escape utility to prevent XSS in record table rendering
function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

// Update Enclosure ID Preview
function updateEnclosureId() {
  const olt = oltSelect.value;
  const port = portSelect.value;
  const enc = enclosureSelect.value;
  if (!olt || !port || !enc) {
    enclosureIdPreview.innerText = '---';
    return;
  }
  const eid = computeEnclosureId(olt, port, enc);
  enclosureIdPreview.innerText = eid || '---';
}

function getRegionForCenter(centerName) {
  if (!centerName) return "Thrissur";
  const cData = DEFAULT_PRELOAD && DEFAULT_PRELOAD.hierarchy ? DEFAULT_PRELOAD.hierarchy[centerName] : null;
  if (cData && typeof cData === 'object') {
    for (const rt of Object.keys(cData)) {
      const olts = cData[rt];
      if (olts && typeof olts === 'object') {
        for (const oltName of Object.keys(olts)) {
          const entry = olts[oltName];
          if (entry && typeof entry === 'object' && entry.region) {
            return entry.region;
          }
        }
      }
    }
  }
  return "Thrissur";
}

function getRtRoomsForCenter(c) {
  if (!DEFAULT_PRELOAD || !DEFAULT_PRELOAD.hierarchy) return [];
  if (DEFAULT_PRELOAD.hierarchy[c]) return Object.keys(DEFAULT_PRELOAD.hierarchy[c]);
  const cNorm = (c || '').trim().toLowerCase();
  const foundKey = Object.keys(DEFAULT_PRELOAD.hierarchy).find(k => k.trim().toLowerCase() === cNorm);
  return (foundKey && DEFAULT_PRELOAD.hierarchy[foundKey]) ? Object.keys(DEFAULT_PRELOAD.hierarchy[foundKey]) : [];
}

function getOltsForRtRoom(c, rt) {
  if (!DEFAULT_PRELOAD || !DEFAULT_PRELOAD.hierarchy) return [];
  if (DEFAULT_PRELOAD.hierarchy[c] && DEFAULT_PRELOAD.hierarchy[c][rt]) {
    return Object.keys(DEFAULT_PRELOAD.hierarchy[c][rt]);
  }
  const cNorm = (c || '').trim().toLowerCase();
  const cKey = Object.keys(DEFAULT_PRELOAD.hierarchy).find(k => k.trim().toLowerCase() === cNorm);
  if (!cKey || !DEFAULT_PRELOAD.hierarchy[cKey]) return [];
  const rtMap = DEFAULT_PRELOAD.hierarchy[cKey];
  if (rtMap[rt]) return Object.keys(rtMap[rt]);
  const rtNorm = (rt || '').trim().toLowerCase();
  const rtKey = Object.keys(rtMap).find(k => k.trim().toLowerCase() === rtNorm);
  return (rtKey && rtMap[rtKey]) ? Object.keys(rtMap[rtKey]) : [];
}

function initDropdowns(preserveSelection = false) {
  const prevCenter = preserveSelection && centerSelect ? centerSelect.value : '';
  const prevRt = preserveSelection && rtRoomSelect ? rtRoomSelect.value : '';
  const prevOlt = preserveSelection && oltSelect ? oltSelect.value : '';

  // Center
  centerSelect.innerHTML = '';
  let centers = Object.keys(DEFAULT_PRELOAD.hierarchy);
  
  // Filter centers based on logged-in user assignment (supports multiple centers for ACSO)
  if (currentUser && currentUser.assigned_center && currentUser.assigned_center !== 'ALL') {
    let assignedList = [];
    if (Array.isArray(currentUser.assigned_centers) && currentUser.assigned_centers.length > 0) {
      assignedList = currentUser.assigned_centers;
    } else if (currentUser.assigned_center) {
      assignedList = currentUser.assigned_center.split(',').map(s => s.trim()).filter(Boolean);
    }

    if (assignedList.includes('ALL')) {
      // User has access to ALL centers in network
      centerSelect.disabled = false;
    } else if (assignedList.length > 1) {
      // ACSO or Officer in charge of MULTIPLE centers:
      // Allow user to drop down and switch between any of their assigned centers!
      const allHierCenters = Object.keys(DEFAULT_PRELOAD.hierarchy);
      const filtered = [];
      assignedList.forEach(a => {
        const m = allHierCenters.find(c => c.trim().toLowerCase() === a.trim().toLowerCase());
        if (m && !filtered.includes(m)) filtered.push(m);
        else if (!filtered.includes(a)) filtered.push(a);
      });
      centers = filtered.length > 0 ? filtered : centers;
      centerSelect.disabled = false; // ACTIVE & SELECTABLE for multiple centers charge!
    } else if (assignedList.length === 1) {
      // Single assigned center: locked to that center
      const assigned = assignedList[0];
      const match = centers.find(c => c.trim().toLowerCase() === assigned.trim().toLowerCase());
      if (match) {
        centers = [match];
      } else {
        DEFAULT_PRELOAD.hierarchy[assigned] = {};
        centers = [assigned];
      }
      centerSelect.disabled = true; // Locked to single center
    }
  } else {
    centerSelect.disabled = false;
  }

  if (centers.length === 0) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.innerText = '-- No Centers Configured (Upload Node Master) --';
    centerSelect.appendChild(opt);
  } else {
    centers.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c;
      opt.innerText = c;
      centerSelect.appendChild(opt);
    });
  }

  if (prevCenter && centers.includes(prevCenter)) {
    centerSelect.value = prevCenter;
  }

  // Region (Defaulted against Center, locked / not editable)
  if (regionSelect) {
    const reg = getRegionForCenter(centerSelect.value);
    regionSelect.innerHTML = '';
    const opt = document.createElement('option');
    opt.value = reg;
    opt.innerText = reg;
    regionSelect.appendChild(opt);
    regionSelect.value = reg;
    regionSelect.disabled = true;
  }

  // Technologies (Read-only, auto-reflected from Node Master Data)
  techSelect.innerHTML = '';
  (DEFAULT_PRELOAD.technologies || ["GPON", "FTTH", "WDM", "EDFA"]).forEach(t => {
    const opt = document.createElement('option');
    opt.value = t;
    opt.innerText = t;
    techSelect.appendChild(opt);
  });
  techSelect.disabled = true;

  if (oltTypeSelect) {
    oltTypeSelect.disabled = true;
  }

  // Enclosures - placeholder only; cascade via updateAvailableEnclosures() will rebuild
  enclosureSelect.innerHTML = '';
  const encDefOpt = document.createElement('option');
  encDefOpt.value = '';
  encDefOpt.innerText = '-- Select Enclosure --';
  enclosureSelect.appendChild(encDefOpt);

  // Splitter Ratio - always rebuild with empty default (no auto-selection)
  splitterRatioSelect.innerHTML = '';
  const ratioDefOpt = document.createElement('option');
  ratioDefOpt.value = '';
  ratioDefOpt.innerText = '-- Select Splitter Ratio --';
  splitterRatioSelect.appendChild(ratioDefOpt);
  DEFAULT_PRELOAD.ratios.forEach(r => {
    const opt = document.createElement('option');
    opt.value = r;
    opt.innerText = r;
    splitterRatioSelect.appendChild(opt);
  });

  // Splitter ID - placeholder only; cascade via updateAvailableSplitters() will rebuild
  splitterIdSelect.innerHTML = '';
  const sidDefOpt = document.createElement('option');
  sidDefOpt.value = '';
  sidDefOpt.innerText = '-- Select Splitter ID --';
  splitterIdSelect.appendChild(sidDefOpt);

  updateSplitterColorOptions();
  onCenterChange(prevRt, prevOlt);
}

// Splitter Lead Colour Code dynamically based on Splitter Ratio and Survey Status
function updateSplitterColorOptions() {
  if (!splitterColorSelect) return;
  const olt = oltSelect ? oltSelect.value : '';
  const port = portSelect ? portSelect.value : '';
  const enc = enclosureSelect ? enclosureSelect.value : '';
  const spl = splitterIdSelect ? splitterIdSelect.value : '';
  const ratio = splitterRatioSelect ? splitterRatioSelect.value : '';
  const eid = (olt && port && enc) ? computeEnclosureId(olt, port, enc) : '';

  const prev = splitterColorSelect.value;
  splitterColorSelect.innerHTML = '';

  const defOpt = document.createElement('option');
  defOpt.value = '';
  defOpt.innerText = ratio ? `-- Select Out Color (Optional) --` : '-- Select Ratio First --';
  splitterColorSelect.appendChild(defOpt);

  if (!ratio) {
    handleColorSelectionChange();
    return;
  }

  const colorList = (DEFAULT_PRELOAD.color_codes_by_ratio && DEFAULT_PRELOAD.color_codes_by_ratio[ratio])
    ? DEFAULT_PRELOAD.color_codes_by_ratio[ratio]
    : (DEFAULT_PRELOAD.color_codes || []);

  const role = currentUser ? normalizeClientRole(currentUser.role) : 'field_technician';
  const isSupervisor = (role === 'acso' || role === 'rcsm' || role === 'super_admin');

  let firstAvailableVal = '';

  colorList.forEach(col => {
    const opt = document.createElement('option');
    opt.value = col;

    const leadSurvey = (eid && spl) ? getLeadSurveyedInfo(eid, spl, col) : null;
    if (leadSurvey) {
      const byWho = leadSurvey.surveyor_name || leadSurvey.surveyor_username || 'Surveyor';
      if (isSupervisor) {
        opt.innerText = `${col} — ✏️ Surveyed (${byWho})`;
        opt.style.color = '#0284c7';
        opt.style.fontWeight = '600';
      } else {
        opt.innerText = `${col} — 🔒 Surveyed (${byWho})`;
        opt.disabled = true;
        opt.style.color = '#94a3b8';
        opt.style.backgroundColor = '#f1f5f9';
      }
    } else {
      opt.innerText = `${col} — ✅ Available`;
      opt.style.color = '#15803d';
      opt.style.fontWeight = '600';
      if (!firstAvailableVal) firstAvailableVal = col;
    }

    splitterColorSelect.appendChild(opt);
  });

  // Preserve previous selection only if still valid and not disabled.
  // NEVER auto-select a lead color: user must explicitly choose a color, or leave empty if 0 connections.
  if (prev) {
    const optMatch = Array.from(splitterColorSelect.options).find(o => o.value === prev && !o.disabled);
    if (optMatch) {
      splitterColorSelect.value = prev;
    } else {
      splitterColorSelect.value = '';
    }
  } else {
    splitterColorSelect.value = '';
  }

  handleColorSelectionChange();
}

// Filter Enclosures to reflect survey status and role rights
function updateAvailableEnclosures() {
  const olt = oltSelect ? oltSelect.value : '';
  const port = portSelect ? portSelect.value : '';

  const prevVal = enclosureSelect.value;
  enclosureSelect.innerHTML = '';

  // Empty default placeholder
  const defOpt = document.createElement('option');
  defOpt.value = '';
  defOpt.innerText = (olt && port) ? '-- Select Enclosure --' : '-- Select Port First --';
  enclosureSelect.appendChild(defOpt);

  const role = currentUser ? normalizeClientRole(currentUser.role) : 'field_technician';
  const isSupervisor = (role === 'acso' || role === 'rcsm' || role === 'super_admin');

  if (olt && port) {
    DEFAULT_PRELOAD.enclosures.forEach(e => {
      const eid = computeEnclosureId(olt, port, e);
      const surveyedSplitters = getSurveyedSplittersForEnclosure(eid);
      const hasSurvey = surveyedSplitters.length > 0;

      const opt = document.createElement('option');
      opt.value = e;

      if (hasSurvey) {
        if (isSupervisor) {
          opt.innerText = `${e} (✏️ ${surveyedSplitters.length} Splitter${surveyedSplitters.length > 1 ? 's' : ''} Mapped)`;
          opt.style.color = '#0284c7';
        } else {
          opt.innerText = `${e} (${surveyedSplitters.length} Splitter${surveyedSplitters.length > 1 ? 's' : ''} Mapped)`;
          opt.style.color = '#0f172a';
        }
      } else {
        opt.innerText = e;
      }

      enclosureSelect.appendChild(opt);
    });

    if (prevVal) {
      const optMatch = Array.from(enclosureSelect.options).find(o => o.value === prevVal && !o.disabled);
      if (optMatch) {
        enclosureSelect.value = prevVal;
      }
    }
  }

  updateEnclosureId();
  updateAvailableSplitters();
}

// Filter Splitter IDs to enforce Field Tech capacity lock and ACSO update rights
function updateAvailableSplitters() {
  const olt = oltSelect ? oltSelect.value : '';
  const port = portSelect ? portSelect.value : '';
  const enc = enclosureSelect ? enclosureSelect.value : '';

  const prevVal = splitterIdSelect.value;
  splitterIdSelect.innerHTML = '';

  // Empty default placeholder
  const defOpt = document.createElement('option');
  defOpt.value = '';
  defOpt.innerText = enc ? '-- Select Splitter ID --' : '-- Select Enclosure First --';
  splitterIdSelect.appendChild(defOpt);

  const role = currentUser ? normalizeClientRole(currentUser.role) : 'field_technician';
  const isSupervisor = (role === 'acso' || role === 'rcsm' || role === 'super_admin');

  if (enc) {
    const eid = computeEnclosureId(olt, port, enc);

    DEFAULT_PRELOAD.splitters.forEach((s, idx) => {
      const splSummary = getSplitterSummary(eid, s);
      const opt = document.createElement('option');
      opt.value = s;

      const count = splSummary ? splSummary.count : 0;
      const ratio = (splSummary && splSummary.splitter_ratio) || (splitterRatioSelect ? splitterRatioSelect.value : '');
      const totalLeads = (ratio && DEFAULT_PRELOAD.color_codes_by_ratio && DEFAULT_PRELOAD.color_codes_by_ratio[ratio])
        ? DEFAULT_PRELOAD.color_codes_by_ratio[ratio].length
        : 0;

      if (count > 0) {
        const capText = totalLeads ? `${count}/${totalLeads} mapped` : `${count} lead${count > 1 ? 's' : ''} mapped`;
        const isFull = totalLeads > 0 && count >= totalLeads;

        if (isFull) {
          if (isSupervisor) {
            opt.innerText = `${s} — ✏️ Full (${capText})`;
            opt.style.color = '#0284c7';
            opt.style.fontWeight = '600';
          } else {
            opt.innerText = `${s} — 🔒 Full (${capText})`;
            opt.disabled = true;
            opt.style.color = '#94a3b8';
            opt.style.backgroundColor = '#f1f5f9';
          }
        } else {
          // Open leads available! Both tech and supervisor can select!
          opt.innerText = `${s} (${capText})`;
          opt.style.color = '#0f172a';
          opt.style.fontWeight = '500';
        }
      } else if (splSummary) {
        // Splitter surveyed with 0 leads mapped (zero connections recorded)
        opt.innerText = `${s} (0 mapped)`;
        opt.style.color = '#0f172a';
        opt.style.fontWeight = '500';
      } else {
        // Brand new splitter with 0 mapped leads
        // Enforce sequential splitter addition (S2 requires S1, S3 requires S2, etc.)
        if (idx > 0) {
          const prevSplitter = DEFAULT_PRELOAD.splitters[idx - 1];
          const prevSummary = getSplitterSummary(eid, prevSplitter);
          if (!prevSummary) {
            opt.innerText = `${s} — 🔒 Add ${prevSplitter} first`;
            opt.disabled = true;
            opt.style.color = '#94a3b8';
            opt.style.backgroundColor = '#f8fafc';
          } else {
            opt.innerText = s;
          }
        } else {
          opt.innerText = s;
        }
      }

      splitterIdSelect.appendChild(opt);
    });

    if (prevVal) {
      const optMatch = Array.from(splitterIdSelect.options).find(o => o.value === prevVal && !o.disabled);
      if (optMatch) {
        splitterIdSelect.value = prevVal;
      } else {
        const firstAvail = Array.from(splitterIdSelect.options).find(o => o.value && !o.disabled);
        if (firstAvail) splitterIdSelect.value = firstAvail.value;
      }
    } else {
      const firstAvail = Array.from(splitterIdSelect.options).find(o => o.value && !o.disabled);
      if (firstAvail) splitterIdSelect.value = firstAvail.value;
    }
  }

  handleSplitterSelectionChange();
}

// Reset customer-specific subscriber inputs and editing state (preserves splitter-level connected customers)
function clearCustomerInputs() {
  if (adlSubInput) adlSubInput.value = '';
  if (acsSubInput) acsSubInput.value = '';
  currentAcsoEditingUuid = null;
  const banner = document.getElementById('acso-update-banner');
  if (banner) banner.style.display = 'none';
  const submitBtn = document.getElementById('btn-save-record') || document.querySelector('.btn-add-row');
  if (submitBtn) {
    submitBtn.innerHTML = '<span>➕</span> Submit';
    submitBtn.style.background = '';
  }
  updateSubscriberInputsState();
}

// Toggle customer subscriber inputs editable state: only editable when a Splitter Out Colour Code is selected
function updateSubscriberInputsState() {
  const hasColor = !!(splitterColorSelect && splitterColorSelect.value);
  if (adlSubInput) {
    adlSubInput.disabled = !hasColor;
    if (!hasColor) {
      adlSubInput.value = '';
      adlSubInput.placeholder = 'Select Splitter Out first';
      adlSubInput.style.backgroundColor = '#f1f5f9';
      adlSubInput.style.cursor = 'not-allowed';
    } else {
      adlSubInput.placeholder = 'e.g. ADL10234';
      adlSubInput.style.backgroundColor = '';
      adlSubInput.style.cursor = '';
    }
  }
  if (acsSubInput) {
    acsSubInput.disabled = !hasColor;
    if (!hasColor) {
      acsSubInput.value = '';
      acsSubInput.placeholder = 'Select Splitter Out first';
      acsSubInput.style.backgroundColor = '#f1f5f9';
      acsSubInput.style.cursor = 'not-allowed';
    } else {
      acsSubInput.placeholder = 'e.g. ACS56789';
      acsSubInput.style.backgroundColor = '';
      acsSubInput.style.cursor = '';
    }
  }
}

// Reset pole-specific fields when changing equipment locations
function resetPoleFields() {
  if (postInput) postInput.value = '';
  if (landmarkInput) landmarkInput.value = '';
  if (custCountInput) custCountInput.value = '0';
  currentLat = null;
  currentLon = null;
  currentAccuracy = null;
  bestAccuracy = Infinity;
  if (manualCoordsInput) {
    manualCoordsInput.value = '';
    manualCoordsInput.placeholder = 'e.g. 10.606650, 76.214490 (Mandatory)';
  }
  if (gpsAccText) {
    gpsAccText.innerHTML = '<span style="color:#d97706; font-weight:600;">⚠️ Tap 🎯 GPS at pole</span>';
  }
  if (mapMarker && mapInstance) {
    mapInstance.removeLayer(mapMarker);
    mapMarker = null;
  }
  if (accuracyCircle && mapInstance) {
    mapInstance.removeLayer(accuracyCircle);
    accuracyCircle = null;
  }
}

// Retrieve pole info (coordinates, post number, landmark) for an enclosure if already surveyed
function getEnclosurePoleInfo(eid) {
  if (!eid) return null;
  const eidUp = eid.trim().toUpperCase();

  // 1. Search in local records (latest survey first)
  for (let i = records.length - 1; i >= 0; i--) {
    const r = records[i];
    const rEid = (r["Enclosure ID"] || r.enclosure_id || '').trim().toUpperCase();
    if (rEid === eidUp) {
      const latLong = r["Lat /Long"] || r.lat_long || '';
      const kseb = r["KSEB Post Number"] || r.kseb_post_number || '';
      const lmark = r["Land Mark"] || r.landmark || '';
      if (latLong || kseb || lmark) {
        return { lat_long: latLong, kseb_post_number: kseb, landmark: lmark };
      }
    }
  }

  // 2. Search in networkSurveyedPoints
  if (networkSurveyedPoints) {
    const matchingKey = Object.keys(networkSurveyedPoints).find(k => k.toUpperCase().startsWith(eidUp + '|'));
    if (matchingKey && networkSurveyedPoints[matchingKey]) {
      const pt = networkSurveyedPoints[matchingKey];
      return {
        lat_long: pt.lat_long || '',
        kseb_post_number: pt.kseb_post_number || '',
        landmark: pt.landmark || ''
      };
    }
  }

  return null;
}

// Handle Enclosure Selection Change: Loads surveyed pole data if enclosure was surveyed, or resets pole fields for new site
function handleEnclosureChange() {
  const olt = oltSelect ? oltSelect.value : '';
  const port = portSelect ? portSelect.value : '';
  const enc = enclosureSelect ? enclosureSelect.value : '';

  clearCustomerInputs();

  if (enc && olt && port) {
    const eid = computeEnclosureId(olt, port, enc);
    const poleInfo = getEnclosurePoleInfo(eid);
    if (poleInfo && (poleInfo.lat_long || poleInfo.kseb_post_number || poleInfo.landmark)) {
      if (postInput) postInput.value = poleInfo.kseb_post_number || '';
      if (landmarkInput) landmarkInput.value = poleInfo.landmark || '';
      if (poleInfo.lat_long && poleInfo.lat_long.includes(',')) {
        if (manualCoordsInput) manualCoordsInput.value = poleInfo.lat_long;
        const parts = poleInfo.lat_long.split(',').map(s => parseFloat(s.trim()));
        if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
          currentLat = parts[0];
          currentLon = parts[1];
          if (gpsAccText) {
            gpsAccText.innerHTML = `<span style="color:#0284c7; font-weight:600;">📍 Existing Survey Coords (${poleInfo.lat_long})</span>`;
          }
          if (mapInstance && typeof L !== 'undefined') {
            renderMapMarker(currentLat, currentLon, 10);
          }
        }
      }
    } else {
      // New / unmapped enclosure: reset pole fields for the fresh site
      resetPoleFields();
    }
  } else {
    resetPoleFields();
  }

  updateEnclosureId();
  updateAvailableSplitters();
  handleSplitterSelectionChange();
}

// Handle Splitter Selection: Auto-fills splitter metadata & triggers color updates
function handleSplitterSelectionChange() {
  const olt = oltSelect ? oltSelect.value : '';
  const port = portSelect ? portSelect.value : '';
  const enc = enclosureSelect ? enclosureSelect.value : '';
  const spl = splitterIdSelect ? splitterIdSelect.value : '';

  if (!olt || !port || !enc || !spl) {
    updateSplitterColorOptions();
    return;
  }

  const eid = computeEnclosureId(olt, port, enc);
  const splSummary = getSplitterSummary(eid, spl);

  if (splSummary) {
    // If splitter already has surveyed leads, pre-fill common splitter ratio and pole details
    if (splSummary.splitter_ratio && splitterRatioSelect) {
      splitterRatioSelect.value = splSummary.splitter_ratio;
    }
    // Note: Do not auto-fetch customers_connected (user controls manually or leaves 0)
    if (postInput && (!postInput.value.trim() || splSummary.kseb_post_number)) {
      if (splSummary.kseb_post_number) postInput.value = splSummary.kseb_post_number;
    }
    if (landmarkInput && (!landmarkInput.value.trim() || splSummary.landmark)) {
      if (splSummary.landmark) landmarkInput.value = splSummary.landmark;
    }
    if (splSummary.lat_long && splSummary.lat_long.includes(',')) {
      if (manualCoordsInput && (!manualCoordsInput.value.trim() || splSummary.lat_long)) manualCoordsInput.value = splSummary.lat_long;
      const parts = splSummary.lat_long.split(',').map(s => parseFloat(s.trim()));
      if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
        currentLat = parts[0];
        currentLon = parts[1];
        if (gpsAccText) {
          gpsAccText.innerHTML = `<span style="color:#0284c7; font-weight:600;">📍 Existing Survey Coords (${splSummary.lat_long})</span>`;
        }
        if (mapInstance && typeof L !== 'undefined') {
          renderMapMarker(currentLat, currentLon, 10);
        }
      }
    }
  }

  updateSplitterColorOptions();
}

// Handle Lead Color Selection: Toggles ACSO Update Banner & Pre-fills Existing Lead Survey Data
function handleColorSelectionChange() {
  const banner = document.getElementById('acso-update-banner');
  const descEl = document.getElementById('acso-update-desc');
  const submitBtn = document.getElementById('btn-save-record') || document.querySelector('.btn-add-row');

  const olt = oltSelect ? oltSelect.value : '';
  const port = portSelect ? portSelect.value : '';
  const enc = enclosureSelect ? enclosureSelect.value : '';
  const spl = splitterIdSelect ? splitterIdSelect.value : '';
  const col = splitterColorSelect ? splitterColorSelect.value : '';

  const role = currentUser ? normalizeClientRole(currentUser.role) : 'field_technician';
  const isSupervisor = (role === 'acso' || role === 'rcsm' || role === 'super_admin');

  if (!olt || !port || !enc || !spl || !col) {
    if (banner) banner.style.display = 'none';
    if (submitBtn) {
      submitBtn.innerHTML = '<span>➕</span> Submit';
      submitBtn.style.background = '';
    }
    currentAcsoEditingUuid = null;
    updateSubscriberInputsState();
    return;
  }

  const eid = computeEnclosureId(olt, port, enc);
  const leadSurvey = getLeadSurveyedInfo(eid, spl, col);

  if (leadSurvey && isSupervisor) {
    // Supervisor Update Mode for THIS specific lead!
    currentAcsoEditingUuid = leadSurvey.client_uuid;
    const by = leadSurvey.surveyor_name || leadSurvey.surveyor_username || 'another surveyor';
    const dt = leadSurvey.survey_date_time || leadSurvey["Date & Time"] || 'previous survey';

    if (banner && descEl) {
      descEl.innerHTML = `Editing customer on <strong>${eid} (${spl} - ${col})</strong>, originally surveyed by <strong>${by}</strong> (${dt}). Submitting will overwrite this record.`;
      banner.style.display = 'block';
    }
    if (submitBtn) {
      submitBtn.innerHTML = '<span>✏️</span> Update Master Record';
      submitBtn.style.background = '#0284c7';
    }

    // Pre-populate subscriber inputs from existing record
    if (adlSubInput) adlSubInput.value = leadSurvey.adl_subscriber_id || leadSurvey["ADL Subscriber ID"] || '';
    if (acsSubInput) acsSubInput.value = leadSurvey.acs_subscriber_id || leadSurvey["ACS Subscriber ID"] || '';
  } else {
    // Normal / Brand-New Lead Entry Mode!
    currentAcsoEditingUuid = null;
    if (banner) banner.style.display = 'none';
    if (submitBtn) {
      submitBtn.innerHTML = '<span>➕</span> Submit';
      submitBtn.style.background = '';
    }
    if (adlSubInput) adlSubInput.value = '';
    if (acsSubInput) acsSubInput.value = '';
    // Total connected customers in the splitter continues its last status across leads!
  }

  // Splitter Out is selected: enable subscriber inputs and auto-focus ADL input
  updateSubscriberInputsState();
  if (adlSubInput && !adlSubInput.disabled) {
    try { adlSubInput.focus(); } catch (e) {}
  }
}

function populateFieldsFromExistingRecord(info) {
  if (!info) return;
  if (postInput && !postInput.value.trim()) {
    postInput.value = info.kseb_post_number || info["KSEB Post Number"] || '';
  }
  if (landmarkInput && !landmarkInput.value.trim()) {
    landmarkInput.value = info.landmark || info["Land Mark"] || '';
  }
  if (custCountInput) {
    custCountInput.value = (info.customers_connected !== undefined ? info.customers_connected : info["No: Of Customer Connected"]) || 0;
  }
  const ratio = info.splitter_ratio || info["Splitter Ratio"];
  if (ratio && splitterRatioSelect && !splitterRatioSelect.value) {
    splitterRatioSelect.value = ratio;
    updateSplitterColorOptions();
  }

  const color = info.splitter_lead_color || info["Splitter Lead Colour Code"];
  if (color && splitterColorSelect) {
    splitterColorSelect.value = color;
  }

  updateSubscriberInputsState();

  if (adlSubInput) {
    adlSubInput.value = info.adl_subscriber_id || info["ADL Subscriber ID"] || '';
  }
  if (acsSubInput) {
    acsSubInput.value = info.acs_subscriber_id || info["ACS Subscriber ID"] || '';
  }

  const coords = info.lat_long || info["Lat /Long"];
  if (coords && coords.includes(',') && (!currentLat || !currentLon)) {
    if (manualCoordsInput) manualCoordsInput.value = coords;
    const parts = coords.split(',').map(s => parseFloat(s.trim()));
    if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
      currentLat = parts[0];
      currentLon = parts[1];
      if (gpsAccText) {
        gpsAccText.innerHTML = `<span style="color:#0284c7; font-weight:600;">📍 Existing Survey Coords (${coords})</span>`;
      }
      if (mapInstance && typeof L !== 'undefined') {
        renderMapMarker(currentLat, currentLon, 10);
      }
    }
  }
}


function onCenterChange(targetRt = null, targetOlt = null) {
  const c = centerSelect.value;
  if (regionSelect) {
    const reg = getRegionForCenter(c);
    regionSelect.innerHTML = '';
    const opt = document.createElement('option');
    opt.value = reg;
    opt.innerText = reg;
    regionSelect.appendChild(opt);
    regionSelect.value = reg;
    regionSelect.disabled = true;
  }
  rtRoomSelect.innerHTML = '';
  const rtRooms = getRtRoomsForCenter(c);

  // Empty default placeholder
  const rtDefOpt = document.createElement('option');
  rtDefOpt.value = '';
  rtDefOpt.innerText = '-- Select RT Room --';
  rtRoomSelect.appendChild(rtDefOpt);

  rtRooms.forEach(rt => {
    const opt = document.createElement('option');
    opt.value = rt;
    opt.innerText = rt;
    rtRoomSelect.appendChild(opt);
  });
  if (targetRt && rtRooms.includes(targetRt)) {
    rtRoomSelect.value = targetRt;
  }
  onRTRoomChange(targetOlt);
}

function onRTRoomChange(targetOlt = null) {
  const c = centerSelect.value;
  const rt = rtRoomSelect.value;
  oltSelect.innerHTML = '';
  const olts = getOltsForRtRoom(c, rt);

  // Empty default placeholder
  const oltDefOpt = document.createElement('option');
  oltDefOpt.value = '';
  oltDefOpt.innerText = rt ? '-- Select Node Name --' : '-- Select RT Room First --';
  oltSelect.appendChild(oltDefOpt);

  olts.forEach(o => {
    const opt = document.createElement('option');
    opt.value = o;
    opt.innerText = o;
    oltSelect.appendChild(opt);
  });
  if (targetOlt && olts.includes(targetOlt)) {
    oltSelect.value = targetOlt;
  }
  onOLTChange();
}

function findNodeEntry(c, rt, olt) {
  if (!DEFAULT_PRELOAD || !DEFAULT_PRELOAD.hierarchy) return null;
  // 1. Direct key match
  if (DEFAULT_PRELOAD.hierarchy[c] && DEFAULT_PRELOAD.hierarchy[c][rt] && DEFAULT_PRELOAD.hierarchy[c][rt][olt]) {
    return DEFAULT_PRELOAD.hierarchy[c][rt][olt];
  }
  // 2. Case-insensitive & trimmed fallback
  const cNorm = (c || '').trim().toLowerCase();
  const cKey = Object.keys(DEFAULT_PRELOAD.hierarchy).find(k => k.trim().toLowerCase() === cNorm);
  if (!cKey) return null;

  const rtMap = DEFAULT_PRELOAD.hierarchy[cKey];
  if (!rtMap) return null;
  const rtNorm = (rt || '').trim().toLowerCase();
  const rtKey = Object.keys(rtMap).find(k => k.trim().toLowerCase() === rtNorm);
  if (!rtKey) return null;

  const oltMap = rtMap[rtKey];
  if (!oltMap) return null;
  const oltNorm = (olt || '').trim().toLowerCase();
  const oltKey = Object.keys(oltMap).find(k => k.trim().toLowerCase() === oltNorm);
  if (!oltKey) return null;

  return oltMap[oltKey];
}

function onOLTChange() {
  const c = centerSelect ? centerSelect.value : '';
  const rt = rtRoomSelect ? rtRoomSelect.value : '';
  const olt = oltSelect ? oltSelect.value : '';

  const entry = findNodeEntry(c, rt, olt);
  if (entry) {
    if (entry.region && regionSelect) {
      regionSelect.value = entry.region;
    }

    // 1. Reflect Technology from Node Master Data & lock as read-only
    if (techSelect) {
      const tVal = (entry.tech || 'GPON').trim();
      let matched = false;
      for (let i = 0; i < techSelect.options.length; i++) {
        if (techSelect.options[i].value.toLowerCase() === tVal.toLowerCase()) {
          techSelect.selectedIndex = i;
          matched = true;
          break;
        }
      }
      if (!matched && tVal) {
        const opt = document.createElement('option');
        opt.value = tVal;
        opt.innerText = tVal;
        techSelect.appendChild(opt);
        techSelect.value = tVal;
      }
      techSelect.disabled = true; // Read-only mode
    }

    // 2. Reflect Number of Ports from Node Master Data & lock as read-only
    if (oltTypeSelect) {
      let portCount = 8;
      if (entry.olt_type) {
        const clean = String(entry.olt_type).replace(/\s+/g, '');
        if (clean.includes('32')) portCount = 32;
        else if (clean.includes('16')) portCount = 16;
        else portCount = 8;
      } else if (Array.isArray(entry.ports) && entry.ports.length > 0) {
        portCount = entry.ports.length;
      } else if (Array.isArray(entry) && entry.length > 0) {
        portCount = entry.length;
      }
      oltTypeSelect.value = (portCount >= 32 ? '32P' : (portCount >= 16 ? '16P' : '8P'));
      oltTypeSelect.disabled = true; // Read-only mode
    }
  } else {
    if (techSelect) techSelect.disabled = true;
    if (oltTypeSelect) oltTypeSelect.disabled = true;
  }

  onOLTTypeChange();
}

// --- Multi-Port Checkbox Selection Management ---
let currentAvailablePorts = [];

function togglePortDropdown(e) {
  if (e) {
    e.stopPropagation();
    e.preventDefault();
  }
  const panel = document.getElementById('port-dropdown-panel');
  const trigger = document.getElementById('btn-port-picker');
  if (!panel) return;
  
  const olt = oltSelect ? oltSelect.value : '';
  if (!olt) {
    showToast('⚠️ Please select a Node Name first!', false);
    if (oltSelect) oltSelect.focus();
    return;
  }

  const isHidden = (panel.style.display === 'none' || panel.style.display === '');
  if (isHidden) {
    panel.style.display = 'block';
    if (trigger) trigger.classList.add('active');
  } else {
    panel.style.display = 'none';
    if (trigger) trigger.classList.remove('active');
  }
}

function closePortDropdown() {
  const panel = document.getElementById('port-dropdown-panel');
  const trigger = document.getElementById('btn-port-picker');
  if (panel) panel.style.display = 'none';
  if (trigger) trigger.classList.remove('active');
}

function renderPortCheckboxes(ports, selectedPorts = []) {
  currentAvailablePorts = Array.isArray(ports) ? ports : [];
  const grid = document.getElementById('port-checkbox-grid');
  if (!grid) return;

  grid.innerHTML = '';

  if (!currentAvailablePorts.length) {
    grid.innerHTML = '<div style="grid-column: 1/-1; text-align:center; color:#94a3b8; font-size:0.8rem; padding:10px;">Select Node to view ports</div>';
    syncPortSelection([]);
    return;
  }

  currentAvailablePorts.forEach(p => {
    const isChecked = selectedPorts.includes(p);
    const tile = document.createElement('label');
    tile.className = 'port-checkbox-tile' + (isChecked ? ' checked' : '');
    tile.dataset.port = p;
    tile.innerHTML = `
      <input type="checkbox" value="${escapeHtml(p)}" ${isChecked ? 'checked' : ''} onchange="onPortCheckboxChange(this, event)">
      <span>${escapeHtml(p)}</span>
    `;
    grid.appendChild(tile);
  });

  syncPortSelection(selectedPorts);
}

function onPortCheckboxChange(checkbox, event) {
  if (event) event.stopPropagation();
  const tile = checkbox.closest('.port-checkbox-tile');
  if (tile) {
    if (checkbox.checked) {
      tile.classList.add('checked');
    } else {
      tile.classList.remove('checked');
    }
  }
  updateSelectedPortsFromCheckboxes();
}

function selectAllPorts(selectAll) {
  const grid = document.getElementById('port-checkbox-grid');
  if (!grid) return;
  const checkboxes = grid.querySelectorAll('input[type="checkbox"]');
  checkboxes.forEach(cb => {
    cb.checked = !!selectAll;
    const tile = cb.closest('.port-checkbox-tile');
    if (tile) {
      if (selectAll) tile.classList.add('checked');
      else tile.classList.remove('checked');
    }
  });
  updateSelectedPortsFromCheckboxes();
}

function getSelectedPortsArray() {
  const grid = document.getElementById('port-checkbox-grid');
  if (!grid) return [];
  const checked = grid.querySelectorAll('input[type="checkbox"]:checked');
  const vals = Array.from(checked).map(cb => cb.value);
  vals.sort((a, b) => {
    const numA = parseInt(a.replace(/\D/g, ''), 10) || 0;
    const numB = parseInt(b.replace(/\D/g, ''), 10) || 0;
    return numA - numB;
  });
  return vals;
}

function updateSelectedPortsFromCheckboxes() {
  const selected = getSelectedPortsArray();
  if (enclosureSelect) enclosureSelect.value = '';
  resetPoleFields();
  clearCustomerInputs();
  syncPortSelection(selected);
}

function syncPortSelection(selected) {
  const labelEl = document.getElementById('port-picker-label');
  const badgeEl = document.getElementById('selected-ports-count-badge');
  const hiddenInput = document.getElementById('port-select');

  const commaStr = selected.join(', ');

  if (hiddenInput) {
    hiddenInput.value = commaStr;
  }

  if (selected.length === 0) {
    if (labelEl) {
      const olt = oltSelect ? oltSelect.value : '';
      labelEl.innerText = olt ? '-- Select Port(s) --' : '-- Select Node First --';
      labelEl.className = 'port-picker-placeholder';
    }
    if (badgeEl) badgeEl.style.display = 'none';
  } else if (selected.length === 1) {
    if (labelEl) {
      labelEl.innerText = selected[0];
      labelEl.className = 'port-picker-selected';
    }
    if (badgeEl) {
      badgeEl.innerText = '1 port';
      badgeEl.style.display = 'inline-block';
    }
  } else {
    if (labelEl) {
      labelEl.innerText = commaStr;
      labelEl.className = 'port-picker-selected';
    }
    if (badgeEl) {
      badgeEl.innerText = `${selected.length} ports`;
      badgeEl.style.display = 'inline-block';
    }
  }

  // Trigger cascade updates
  updateAvailableEnclosures();
  updateAvailableSplitters();
  updateEnclosureId();
}

function setSelectedPorts(portsArray) {
  const grid = document.getElementById('port-checkbox-grid');
  if (!grid) return;
  const checkboxes = grid.querySelectorAll('input[type="checkbox"]');
  checkboxes.forEach(cb => {
    const shouldCheck = portsArray.includes(cb.value);
    cb.checked = shouldCheck;
    const tile = cb.closest('.port-checkbox-tile');
    if (tile) {
      if (shouldCheck) tile.classList.add('checked');
      else tile.classList.remove('checked');
    }
  });
  syncPortSelection(portsArray);
}

function onOLTTypeChange() {
  const c = centerSelect ? centerSelect.value : '';
  const rt = rtRoomSelect ? rtRoomSelect.value : '';
  const olt = oltSelect ? oltSelect.value : '';

  // Get current selected ports if any, to keep if valid for the new node
  const prevVal = portSelect ? portSelect.value : '';
  const prevSelected = prevVal ? prevVal.split(',').map(s => s.trim()).filter(Boolean) : [];

  if (olt) {
    const entry = findNodeEntry(c, rt, olt);
    let ports = null;
    if (entry && Array.isArray(entry.ports) && entry.ports.length > 0) {
      ports = entry.ports;
    } else if (Array.isArray(entry) && entry.length > 0) {
      ports = entry;
    } else {
      const oType = oltTypeSelect ? oltTypeSelect.value : '8P';
      ports = (DEFAULT_PRELOAD.ports_by_type && DEFAULT_PRELOAD.ports_by_type[oType])
        ? DEFAULT_PRELOAD.ports_by_type[oType]
        : (oType === '16P' ? Array.from({length: 16}, (_, i) => `P${i + 1}`) : (oType === '32P' ? Array.from({length: 32}, (_, i) => `P${i + 1}`) : Array.from({length: 8}, (_, i) => `P${i + 1}`)));
    }

    const validPrev = prevSelected.filter(p => ports.includes(p));
    renderPortCheckboxes(ports, validPrev);
  } else {
    renderPortCheckboxes([]);
  }
}

// Excel Hierarchy Upload (Center, RT Room, Technology, OLT/Node Name)
function handleExcelHierarchyUpload(e) {
  const file = e.target.files[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = function(evt) {
    try {
      const data = evt.target.result;
      const workbook = XLSX.read(data, { type: 'binary' });

      let importedOlts = 0;
      const importedCenters = new Set();
      const newHierarchy = {};

      workbook.SheetNames.forEach(sheetName => {
        const sheet = workbook.Sheets[sheetName];
        const jsonRows = XLSX.utils.sheet_to_json(sheet, { defval: '' });

        jsonRows.forEach(row => {
          const normRow = {};
          Object.keys(row).forEach(k => {
            const cleanKey = k.toLowerCase().replace(/[^a-z0-9]/g, '');
            normRow[cleanKey] = String(row[k]).trim();
          });

          const region = normRow['region'] || normRow['district'] || 'Thrissur';
          const center = normRow['center'] || normRow['regioncenter'] || normRow['centre'] || '';
          const rtRoom = normRow['rtroom'] || normRow['room'] || 'Main RT';
          const tech = normRow['gponftthwdm'] || normRow['tech'] || normRow['technology'] || 'GPON';
          let olt = normRow['oltnodename'] || normRow['oltname'] || normRow['olt'] || '';
          if (!olt) {
            const ipVal = normRow['deviceip'] || normRow['ip'] || normRow['ipaddress'] || '';
            if (ipVal) olt = ipVal.toLowerCase().startsWith('olt') ? ipVal : `OLT (${ipVal})`;
          }

          if (center && olt && center.toLowerCase() !== 'center' && !olt.toLowerCase().includes('olt/node')) {
            importedCenters.add(center);

            if (!newHierarchy[center]) newHierarchy[center] = {};
            if (!newHierarchy[center][rtRoom]) newHierarchy[center][rtRoom] = {};

            const oType = normRow['olttype'] || normRow['type'] || '8 P';
            let portCount = 8;
            if (oType.includes('16')) portCount = 16;
            else if (oType.includes('32')) portCount = 32;

            // Deduplicate across each RT room case-insensitively
            let targetKey = olt;
            for (const existKey of Object.keys(newHierarchy[center][rtRoom])) {
              if (existKey.toLowerCase().trim() === olt.toLowerCase().trim()) {
                targetKey = existKey;
                break;
              }
            }

            if (targetKey === olt && !newHierarchy[center][rtRoom][targetKey]) {
              importedOlts++;
            }

            newHierarchy[center][rtRoom][targetKey] = {
              region,
              tech,
              olt_type: oType,
              ports: Array.from({ length: portCount }, (_, i) => `P${i + 1}`)
            };
          }
        });
      });

      if (importedOlts > 0) {
        Object.keys(newHierarchy).forEach(c => {
          if (!DEFAULT_PRELOAD.hierarchy[c]) DEFAULT_PRELOAD.hierarchy[c] = {};
          Object.assign(DEFAULT_PRELOAD.hierarchy[c], newHierarchy[c]);
        });

        localStorage.setItem('gpon_custom_hierarchy', JSON.stringify(DEFAULT_PRELOAD.hierarchy));
        initDropdowns();
        showToast(`🎉 Imported ${importedOlts} OLTs across ${importedCenters.size} Centers from Excel!`);
        syncHierarchyToServer(DEFAULT_PRELOAD.hierarchy);
      } else {
        showToast('No matching Center and OLT columns found in uploaded Excel.', false);
      }
    } catch (err) {
      console.error(err);
      showToast('Error reading Excel file: ' + err.message, false);
    }
  };
  reader.readAsBinaryString(file);
  e.target.value = '';
}

// Event Listeners for Cascading
centerSelect.addEventListener('change', () => {
  resetPoleFields();
  clearCustomerInputs();
  onCenterChange();
  fetchSurveyedPoints();
});
rtRoomSelect.addEventListener('change', () => {
  resetPoleFields();
  clearCustomerInputs();
  onRTRoomChange();
});
oltSelect.addEventListener('change', () => {
  resetPoleFields();
  clearCustomerInputs();
  onOLTChange();
});
if (oltTypeSelect) oltTypeSelect.addEventListener('change', onOLTTypeChange);
if (portSelect) portSelect.addEventListener('change', () => {
  if (enclosureSelect) enclosureSelect.value = '';
  resetPoleFields();
  clearCustomerInputs();
  updateAvailableEnclosures();
  updateAvailableSplitters();
});
enclosureSelect.addEventListener('change', handleEnclosureChange);
splitterRatioSelect.addEventListener('change', () => {
  clearCustomerInputs();
  updateSplitterColorOptions();
});
splitterIdSelect.addEventListener('change', () => {
  clearCustomerInputs();
  handleSplitterSelectionChange();
});
if (splitterColorSelect) splitterColorSelect.addEventListener('change', handleColorSelectionChange);

// Close port dropdown panel on tap/click outside
document.addEventListener('click', (e) => {
  const panel = document.getElementById('port-dropdown-panel');
  const trigger = document.getElementById('btn-port-picker');
  if (!panel || panel.style.display === 'none') return;
  if (!panel.contains(e.target) && (!trigger || !trigger.contains(e.target))) {
    closePortDropdown();
  }
});

// High-Precision GNSS / GPS Geolocation Engine (Multi-Sample Satellite Convergence)
function captureGPS() {
  if (!navigator.geolocation) {
    showToast('GPS is not supported on this browser/device', false);
    return;
  }
  
  // Clear any existing active GPS watcher or timeout
  if (gpsWatchId !== null) {
    navigator.geolocation.clearWatch(gpsWatchId);
    gpsWatchId = null;
  }
  if (gpsWatchTimer !== null) {
    clearTimeout(gpsWatchTimer);
    gpsWatchTimer = null;
  }

  const gpsBtn = document.querySelector('.btn-gps-sheet');
  if (gpsBtn) {
    gpsBtn.innerHTML = '⏳ Locating...';
    gpsBtn.disabled = true;
  }
  manualCoordsInput.placeholder = 'Acquiring GNSS Satellites...';
  gpsAccText.innerHTML = '<span style="color:#0284c7; font-weight:600;">📡 Connecting to satellites...</span>';

  bestAccuracy = Infinity;
  let sampleCount = 0;

  const onLocationSuccess = (pos) => {
    sampleCount++;
    const lat = pos.coords.latitude;
    const lon = pos.coords.longitude;
    const acc = pos.coords.accuracy;

    // Retain the fix with the tightest accuracy radius
    if (acc < bestAccuracy || currentLat === null) {
      bestAccuracy = acc;
      currentLat = lat;
      currentLon = lon;
      currentAccuracy = acc;

      // 6 decimal places = ~10 cm ground resolution (Google Maps standard)
      manualCoordsInput.value = `${currentLat.toFixed(6)}, ${currentLon.toFixed(6)}`;

      // Live Color-coded accuracy indicator
      let accBadge = '';
      if (acc <= 5) {
        accBadge = `<span style="color:#10b981; font-weight:700;">🟢 ±${Math.round(acc)}m (High Satellite Precision)</span>`;
      } else if (acc <= 12) {
        accBadge = `<span style="color:#059669; font-weight:600;">🟢 ±${Math.round(acc)}m (Good GNSS Fix)</span>`;
      } else if (acc <= 25) {
        accBadge = `<span style="color:#d97706; font-weight:600;">🟡 ±${Math.round(acc)}m (Moderate - Adjust on Map)</span>`;
      } else {
        accBadge = `<span style="color:#dc2626; font-weight:600;">🔴 ±${Math.round(acc)}m (Cell Tower - Fine-tune on Map)</span>`;
      }
      gpsAccText.innerHTML = accBadge;

      // Update map marker and accuracy circle
      updateMapPosition(currentLat, currentLon, currentAccuracy);
    }

    // Stop early if excellent satellite accuracy (< 3m) is achieved or 10+ samples converged
    if (acc <= 3.0 || sampleCount >= 10) {
      finalizeGPS(gpsBtn, 'High precision satellite fix locked!');
    }
  };

  const onLocationError = (err) => {
    if (currentLat === null) {
      manualCoordsInput.placeholder = 'GPS unavailable - enter manually or tap map';
      gpsAccText.innerHTML = `<span style="color:#dc2626; font-weight:600;">⚠️ ${err.message || 'Signal lost'}</span>`;
      showToast('GPS signal weak or unavailable. Tap map to select pole.', false);
    }
    finalizeGPS(gpsBtn);
  };

  try {
    gpsWatchId = navigator.geolocation.watchPosition(
      onLocationSuccess,
      onLocationError,
      {
        enableHighAccuracy: true,
        timeout: 15000,
        maximumAge: 0
      }
    );
  } catch (e) {
    navigator.geolocation.getCurrentPosition(onLocationSuccess, onLocationError, {
      enableHighAccuracy: true,
      timeout: 15000,
      maximumAge: 0
    });
  }

  // Settle time limit: After 12 seconds, lock the best fix obtained so far
  gpsWatchTimer = setTimeout(() => {
    finalizeGPS(gpsBtn, bestAccuracy <= 15 ? 'GPS locked at best available satellite accuracy' : null);
  }, 12000);
}

function finalizeGPS(btn, successMsg = null) {
  if (gpsWatchId !== null) {
    navigator.geolocation.clearWatch(gpsWatchId);
    gpsWatchId = null;
  }
  if (gpsWatchTimer !== null) {
    clearTimeout(gpsWatchTimer);
    gpsWatchTimer = null;
  }
  if (btn) {
    btn.innerHTML = '🎯 GPS';
    btn.disabled = false;
  }
  if (successMsg) {
    showToast(successMsg, true);
  }
}

// Update Map Position, Marker, and Accuracy Halo
function updateMapPosition(lat, lon, acc = null) {
  if (!mapInstance) return;

  if (mapMarker) {
    mapMarker.setLatLng([lat, lon]);
  } else {
    mapMarker = L.marker([lat, lon], { draggable: true, autoPan: true }).addTo(mapInstance);
    mapMarker.bindPopup("<b>📍 Pole Location</b><br>Drag to fine-tune exact post spot.").openPopup();
    mapMarker.on('dragend', function(e) {
      const pt = e.target.getLatLng();
      currentLat = pt.lat;
      currentLon = pt.lng;
      manualCoordsInput.value = `${currentLat.toFixed(6)}, ${currentLon.toFixed(6)}`;
      gpsAccText.innerHTML = '<span style="color:#0284c7; font-weight:600;">📍 Fine-tuned via Satellite Map</span>';
      showToast('Pin position updated!');
    });
  }

  if (acc) {
    if (accuracyCircle) {
      accuracyCircle.setLatLng([lat, lon]).setRadius(acc);
    } else {
      accuracyCircle = L.circle([lat, lon], {
        radius: acc,
        color: '#1a73e8',
        fillColor: '#1a73e8',
        fillOpacity: 0.16,
        weight: 1.5
      }).addTo(mapInstance);
    }
  }

  mapInstance.setView([lat, lon], Math.max(mapInstance.getZoom(), 19));
}

// Initialize Leaflet Map with Google Satellite, Google Streets, Esri & OSM
function initMap(lat, lon) {
  if (mapInstance) return;

  mapInstance = L.map('map-container', {
    maxZoom: 21,
    zoomControl: false
  }).setView([lat, lon], 19);

  // Zoom control in top-left
  L.control.zoom({ position: 'topleft' }).addTo(mapInstance);

  // 1. Google Satellite (Hybrid - Aerial Imagery + Road Overlay + Building Labels)
  const googleHybrid = L.tileLayer('https://mt{s}.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', {
    subdomains: ['0', '1', '2', '3'],
    maxZoom: 21,
    maxNativeZoom: 20,
    attribution: '© Google Maps'
  });

  // 2. Google Streets (Standard Google Road Map)
  const googleStreets = L.tileLayer('https://mt{s}.google.com/vt/lyrs=m&x={x}&y={y}&z={z}', {
    subdomains: ['0', '1', '2', '3'],
    maxZoom: 21,
    maxNativeZoom: 20,
    attribution: '© Google Maps'
  });

  // 3. Esri World Imagery (Satellite)
  const esriSatellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 19,
    attribution: '© Esri World Imagery'
  });

  // 4. OpenStreetMap
  const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '© OpenStreetMap'
  });

  // Default Layer: Google Hybrid Satellite
  googleHybrid.addTo(mapInstance);

  // Top-Right Layer Switcher
  const baseLayers = {
    "🛰️ Google Satellite": googleHybrid,
    "🗺️ Google Streets": googleStreets,
    "🌍 Esri Satellite": esriSatellite,
    "📍 OpenStreetMap": osm
  };
  L.control.layers(baseLayers, null, { position: 'topright', collapsed: true }).addTo(mapInstance);

  // Initial Marker and Accuracy Halo
  if (currentAccuracy) {
    accuracyCircle = L.circle([lat, lon], {
      radius: currentAccuracy,
      color: '#1a73e8',
      fillColor: '#1a73e8',
      fillOpacity: 0.16,
      weight: 1.5
    }).addTo(mapInstance);
  }

  mapMarker = L.marker([lat, lon], { draggable: true, autoPan: true }).addTo(mapInstance);
  mapMarker.bindPopup("<b>📍 Pole Location</b><br>Drag to fine-tune exact post spot.").openPopup();

  // Dragend event
  mapMarker.on('dragend', function(e) {
    const pt = e.target.getLatLng();
    currentLat = pt.lat;
    currentLon = pt.lng;
    manualCoordsInput.value = `${currentLat.toFixed(6)}, ${currentLon.toFixed(6)}`;
    gpsAccText.innerHTML = '<span style="color:#0284c7; font-weight:600;">📍 Fine-tuned via Satellite Map</span>';
    showToast('Pin position updated!');
  });

  // Map tap / click event
  mapInstance.on('click', function(e) {
    currentLat = e.latlng.lat;
    currentLon = e.latlng.lng;
    mapMarker.setLatLng(e.latlng);
    mapMarker.openPopup();
    if (accuracyCircle) accuracyCircle.setLatLng(e.latlng);
    manualCoordsInput.value = `${currentLat.toFixed(6)}, ${currentLon.toFixed(6)}`;
    gpsAccText.innerHTML = '<span style="color:#0284c7; font-weight:600;">📍 Selected on Satellite Map</span>';
  });
}

// Map Toggle (Open / Hide Google Satellite Map)
function toggleMap() {
  const mapWrapper = document.getElementById('map-wrapper');
  const targetEl = mapWrapper || mapContainer;
  const isHidden = (targetEl.style.display === 'none' || !targetEl.style.display);

  if (isHidden) {
    targetEl.style.display = 'block';
    mapToggleBtn.innerText = '✕ Close Map';
    mapToggleBtn.style.color = '#ef4444';
    
    const lat = currentLat || 10.60665;
    const lon = currentLon || 76.21449;

    if (!mapInstance) {
      initMap(lat, lon);
    }

    setTimeout(() => {
      if (mapInstance) {
        mapInstance.invalidateSize();
        if (currentLat && currentLon) {
          updateMapPosition(currentLat, currentLon, currentAccuracy);
        }
      }
    }, 150);
  } else {
    targetEl.style.display = 'none';
    mapToggleBtn.innerText = '🗺️ Google Satellite Map';
    mapToggleBtn.style.color = '#0284c7';
  }
}

// Counter helpers
function adjustCustomer(delta) {
  let val = parseInt(custCountInput.value) || 0;
  val = Math.max(0, val + delta);
  custCountInput.value = val;
}

// Save Entry
function saveRecord() {
  if (!postInput.value.trim()) {
    showToast('Please enter KSEB Post Number', false);
    postInput.focus();
    return;
  }

  let latLongStr = '';
  if (currentLat && currentLon) {
    latLongStr = `${currentLat.toFixed(6)}, ${currentLon.toFixed(6)}`;
  } else if (manualCoordsInput && manualCoordsInput.value) {
    const parsed = parseCoordinates(manualCoordsInput.value);
    if (parsed) {
      currentLat = parsed.lat;
      currentLon = parsed.lon;
      latLongStr = parsed.formatted;
      manualCoordsInput.value = parsed.formatted;
    }
  }

  // Mandatory GPS Enforcement: Network mapping strictly requires coordinates for every surveyed pole
  if (!latLongStr) {
    showToast('⚠️ GPS location is mandatory! Paste coordinates from Excel or tap [🎯 GPS].', false);
    const gpsBtn = document.querySelector('.btn-gps-sheet');
    if (gpsBtn) {
      gpsBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
      gpsBtn.focus();
      gpsBtn.style.boxShadow = '0 0 0 3px #ef4444';
      setTimeout(() => { if (gpsBtn) gpsBtn.style.boxShadow = ''; }, 2500);
    }
    if (manualCoordsInput) {
      manualCoordsInput.style.borderColor = '#ef4444';
      setTimeout(() => { if (manualCoordsInput) manualCoordsInput.style.borderColor = ''; }, 2500);
    }
    return;
  }

  // Mandatory Dropdown Validation: All cascading fields must be explicitly selected
  const mandatoryDropdowns = [
    { el: rtRoomSelect, label: 'RT Room' },
    { el: oltSelect, label: 'Node Name' },
    { el: portSelect, label: 'Port Number', triggerEl: document.getElementById('btn-port-picker') },
    { el: enclosureSelect, label: 'Enclosure #' },
    { el: splitterRatioSelect, label: 'Splitter Ratio' },
    { el: splitterIdSelect, label: 'Splitter ID' }
  ];
  for (const dd of mandatoryDropdowns) {
    if (!dd.el || !dd.el.value) {
      showToast(`⚠️ ${dd.label} is mandatory! Please select a value.`, false);
      const targetEl = dd.triggerEl || dd.el;
      if (targetEl) {
        targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
        targetEl.focus();
        targetEl.style.borderColor = '#ef4444';
        targetEl.style.boxShadow = '0 0 0 3px rgba(239,68,68,0.2)';
        setTimeout(() => { targetEl.style.borderColor = ''; targetEl.style.boxShadow = ''; }, 2500);
      }
      return;
    }
  }

  const olt = oltSelect.value;
  const port = portSelect.value;
  const enc = enclosureSelect.value;
  const eid = computeEnclosureId(olt, port, enc);
  const spl = splitterIdSelect.value;
  const colorCode = splitterColorSelect ? splitterColorSelect.value : '';
  const adlId = (colorCode && adlSubInput) ? adlSubInput.value.trim() : '';
  const acsId = (colorCode && acsSubInput) ? acsSubInput.value.trim() : '';

  // Mandatory check: Splitter Out Colour Code is strictly required if entering a Subscriber ID
  const rawAdl = adlSubInput ? adlSubInput.value.trim() : '';
  const rawAcs = acsSubInput ? acsSubInput.value.trim() : '';
  if ((rawAdl || rawAcs) && !colorCode) {
    showToast('⚠️ Splitter Out Colour Code is mandatory to enter a Subscriber ID!', false);
    if (splitterColorSelect) {
      splitterColorSelect.scrollIntoView({ behavior: 'smooth', block: 'center' });
      splitterColorSelect.focus();
      splitterColorSelect.style.borderColor = '#ef4444';
      splitterColorSelect.style.boxShadow = '0 0 0 3px rgba(239,68,68,0.2)';
      setTimeout(() => {
        if (splitterColorSelect) {
          splitterColorSelect.style.borderColor = '';
          splitterColorSelect.style.boxShadow = '';
        }
      }, 2500);
    }
    return;
  }

  const role = currentUser ? normalizeClientRole(currentUser.role) : 'field_technician';
  const isSupervisor = (role === 'acso' || role === 'rcsm' || role === 'super_admin');

  // Check if this SPECIFIC lead (or zero-connection splitter) is already surveyed
  const existingLeadSurvey = getLeadSurveyedInfo(eid, spl, colorCode);

  // OPTION D: Field Technician Hard Block on already surveyed lead or splitter
  if (existingLeadSurvey && !isSupervisor) {
    const surveyor = existingLeadSurvey.surveyor_name || existingLeadSurvey.surveyor_username || 'another surveyor';
    const blockedMsg = colorCode
      ? `❌ Lead "${colorCode}" of Splitter ${spl} under Enclosure ${eid} was already surveyed by ${surveyor}! Field technicians cannot overwrite existing records.`
      : `❌ Splitter ${spl} under Enclosure ${eid} was already surveyed by ${surveyor}! Field technicians cannot overwrite existing records.`;
    showToast(blockedMsg, false);
    if (splitterColorSelect) splitterColorSelect.focus();
    return;
  }

  // OPTION C: ACSO Supervisor Update Mode for this specific lead
  const isAcsoUpdate = !!(existingLeadSurvey && isSupervisor);

  if (!isAcsoUpdate) {
    // Condition for brand-new entries: prevent duplicate lead in same enclosure locally
    const isDuplicateLead = records.some(r => {
      const rEid = (r["Enclosure ID"] || r.enclosure_id || '').trim().toUpperCase();
      const rSpl = (r["Splitter ID"] || r.splitter_id || '').trim().toUpperCase();
      if (rEid !== eid.toUpperCase() || rSpl !== spl.toUpperCase()) return false;
      const rCol = (r["Splitter Lead Colour Code"] || r.splitter_lead_color || '').trim().toUpperCase();
      if (!colorCode && !rCol) return true;
      if (!colorCode || !rCol) return false;
      const colVars = getColorVariants(colorCode);
      const rVars = getColorVariants(rCol);
      return colVars.some(v => rVars.includes(v));
    });

    if (isDuplicateLead) {
      const msg = colorCode
        ? `❌ Lead "${colorCode}" of Splitter ${spl} is already in your pending records! Duplicate entry not allowed.`
        : `❌ Splitter ${spl} is already in your pending records with zero connections!`;
      showToast(msg, false);
      if (splitterColorSelect) splitterColorSelect.focus();
      return;
    }

    // Condition: S2, S3, S4 can only be added if prior splitter was already surveyed
    if (spl !== 'S1') {
      const sIndex = DEFAULT_PRELOAD.splitters.indexOf(spl);
      if (sIndex > 0) {
        const prevSplitter = DEFAULT_PRELOAD.splitters[sIndex - 1];
        const prevSummary = getSplitterSummary(eid, prevSplitter);
        if (!prevSummary) {
          showToast(`❌ Splitter ${prevSplitter} must be added first before adding ${spl} in Enclosure ${enc}!`, false);
          splitterIdSelect.focus();
          return;
        }
      }
    }
  }

  const clientUuid = isAcsoUpdate
    ? (existingLeadSurvey.client_uuid || currentAcsoEditingUuid || crypto.randomUUID())
    : ((typeof crypto !== 'undefined' && crypto.randomUUID) 
        ? crypto.randomUUID() 
        : ('rec_' + Date.now() + '_' + Math.random().toString(36).substring(2, 9)));

  const now = new Date();
  const formattedDateTime = getFormattedDateTime(now);
  const isoTimestamp = now.toISOString();

  const entry = {
    client_uuid: clientUuid,
    sync_status: 'pending',
    id: Date.now(),
    "Date & Time": formattedDateTime,
    Region: (regionSelect && regionSelect.value) ? regionSelect.value : getRegionForCenter(centerSelect.value),
    Center: centerSelect.value,
    "RT Room": rtRoomSelect.value,
    "GPON/FTTH/WDM": techSelect.value,
    "OLT/Node  Name": olt,
    "Port Number": port,
    "KSEB Post Number": postInput.value.trim(),
    "Land Mark": landmarkInput.value.trim(),
    "Enclosure Number": enc,
    "Enclosure ID": eid,
    "Lat /Long": latLongStr,
    "Splitter ID": spl,
    "Splitter Ratio": splitterRatioSelect.value,
    "No: Of Customer Connected": parseInt(custCountInput.value) || 0,
    "Splitter Lead Colour Code": colorCode,
    "ADL Subscriber ID": adlId,
    "ACS Subscriber ID": acsId,
    splitter_lead_color: colorCode,
    adl_subscriber_id: adlId,
    acs_subscriber_id: acsId,
    survey_date_time: formattedDateTime,
    timestamp: isoTimestamp,
    created_at: isoTimestamp,
    surveyor_username: currentUser ? currentUser.username : '',
    surveyor_name: currentUser ? (currentUser.full_name || currentUser.username) : ''
  };

  try {
    if (isAcsoUpdate) {
      const existingIdx = records.findIndex(r => r.client_uuid === clientUuid);
      if (existingIdx >= 0) {
        records[existingIdx] = entry;
      } else {
        records.push(entry);
      }
    } else {
      records.push(entry);
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(records));

    // Update in-memory and local cache map immediately for this lead
    const leadKey = getLeadKey(eid, spl, colorCode);
    networkSurveyedPoints[leadKey] = entry;

    const variants = getColorVariants(colorCode);
    variants.forEach(v => {
      networkSurveyedPoints[`${eid}|${spl}|${v}`.toUpperCase()] = entry;
    });

    // Update splitter summary in cache
    const splKey = getSplitterKey(eid, spl);
    if (!networkSurveyedPoints[splKey]) {
      networkSurveyedPoints[splKey] = {
        is_summary: true,
        enclosure_id: eid,
        splitter_id: spl,
        splitter_ratio: entry["Splitter Ratio"],
        leads: [],
        count: 0,
        kseb_post_number: entry["KSEB Post Number"],
        landmark: entry["Land Mark"],
        lat_long: entry["Lat /Long"],
        surveyor_name: entry.surveyor_name,
        survey_date_time: formattedDateTime
      };
    }
    const normC = colorCode.trim().toUpperCase();
    if (normC && !networkSurveyedPoints[splKey].leads.includes(normC)) {
      networkSurveyedPoints[splKey].leads.push(normC);
    }
    networkSurveyedPoints[splKey].count = networkSurveyedPoints[splKey].leads.length;
    networkSurveyedPoints[splKey].customers_connected = entry["No: Of Customer Connected"];
    localStorage.setItem('gpon_network_surveyed_points', JSON.stringify(networkSurveyedPoints));
  } catch (err) {
    console.error('Local storage write failed:', err);
    showToast('Failed to save on device storage: ' + (err.message || 'Storage full'), false);
    return;
  }

  updateRecordsBadge();
  updateSyncUI();
  if (isAcsoUpdate) {
    showToast('✏️ Master Record Updated Successfully');
  } else {
    showToast('Record Submitted Successfully');
  }
  showSubmitConfirmModal(entry, isAcsoUpdate);

  // Clear customer-specific subscriber inputs only (keep equipment & pole strictly pinned!)
  clearCustomerInputs();

  const savedSpl = splitterIdSelect ? splitterIdSelect.value : '';
  const savedRatio = splitterRatioSelect ? splitterRatioSelect.value : '';

  // Refresh available splitters to reflect new capacity count
  updateAvailableSplitters();

  // Retain Splitter Ratio
  if (savedRatio && splitterRatioSelect) {
    splitterRatioSelect.value = savedRatio;
  }

  // Clear color selection so surveyor must explicitly select the next splitter lead
  if (splitterColorSelect) splitterColorSelect.value = '';
  updateSplitterColorOptions();

  // Prompt surveyor to select the next Splitter Out lead
  if (splitterColorSelect) {
    setTimeout(() => {
      try { splitterColorSelect.focus(); } catch (e) {}
    }, 300);
  }

  // Trigger silent background sync if server is reachable
  syncWithServer(true);
}

// Confirmation Message Modal
function showSubmitConfirmModal(entry, isAcsoUpdate = false) {
  const modal = document.getElementById('submit-confirm-modal');
  const detailsEl = document.getElementById('submit-confirm-details');
  const iconEl = document.getElementById('submit-confirm-icon');
  const titleEl = document.getElementById('submit-confirm-title');
  const descEl = document.getElementById('submit-confirm-desc');
  if (!modal || !detailsEl) return;

  if (isAcsoUpdate) {
    if (iconEl) iconEl.innerText = '✏️';
    if (titleEl) {
      titleEl.innerText = 'Master Record Updated';
      titleEl.style.color = '#0284c7';
    }
    if (descEl) {
      descEl.innerText = 'Supervisor update saved. Central database will be overwritten with corrections.';
    }
  } else {
    if (iconEl) iconEl.innerText = '✅';
    if (titleEl) {
      titleEl.innerText = 'Record Submitted Successfully';
      titleEl.style.color = '#0f9d58';
    }
    if (descEl) {
      descEl.innerText = 'The survey record has been recorded and safely saved.';
    }
  }

  const encId = entry["Enclosure ID"] || '-';
  const splitInfo = `${entry["Splitter ID"] || '-'} (${entry["Splitter Ratio"] || '-'})`;
  const portStr = entry["Port Number"] || '-';
  const portLabel = (portStr && portStr.includes(',')) ? 'Ports' : 'Port';
  const nodeInfo = `${entry["OLT/Node  Name"] || '-'} [${portLabel} ${portStr}]`;
  const centerInfo = entry["Center"] || '-';
  const custCount = entry["No: Of Customer Connected"] || 0;
  const timeStr = entry["Date & Time"] || '';

  const syncText = (entry.sync_status === 'synced')
    ? '<span style="color:#10b981; font-weight:700;">☁️ Synced to Server ✓</span>'
    : (isServerReachable 
        ? '<span style="color:#0284c7; font-weight:600;">☁️ Syncing to Server...</span>' 
        : '<span style="color:#f59e0b; font-weight:600;">💾 Saved on Phone (Pending Sync)</span>');

  detailsEl.innerHTML = `
    ${isAcsoUpdate ? `
    <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:6px; padding:6px 10px; margin-bottom:10px; font-size:0.8rem; color:#1e40af; display:flex; align-items:center; gap:6px;">
      <span>✏️</span> <strong>ACSO Correction:</strong> Master database record updated.
    </div>` : ''}
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
      <span style="color:#64748b;">Enclosure ID:</span>
      <strong style="color:#0284c7; font-family:monospace; font-size:1.05rem;">${encId}</strong>
    </div>
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
      <span style="color:#64748b;">Splitter & Lead:</span>
      <strong style="color:#334155;">${splitInfo} • ${entry["Splitter Lead Colour Code"] || 'No Lead (0 Connected)'}</strong>
    </div>
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
      <span style="color:#64748b;">Subscriber ID:</span>
      <strong style="color:#0284c7; font-family:monospace;">${entry["ADL Subscriber ID"] || entry["ACS Subscriber ID"] || '-'}</strong>
    </div>
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
      <span style="color:#64748b;">Center / Node:</span>
      <span style="color:#334155; font-size:0.83rem;">${centerInfo} • ${nodeInfo}</span>
    </div>
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
      <span style="color:#64748b;">Connected Customers:</span>
      <strong style="color:#10b981; font-size:0.95rem;">${custCount}</strong>
    </div>
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
      <span style="color:#64748b;">Storage & Sync:</span>
      <span id="confirm-sync-status-badge">${syncText}</span>
    </div>
    <div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px; padding-top:6px; border-top:1px dashed #cbd5e1; font-size:0.75rem;">
      <span style="color:#64748b;">Survey Time:</span>
      <span style="color:#64748b; font-family:monospace;">${timeStr}</span>
    </div>
  `;

  modal.style.display = 'flex';
  const okBtn = document.getElementById('btn-submit-confirm-ok');
  if (okBtn) okBtn.focus();
}


function closeSubmitConfirmModal() {
  const modal = document.getElementById('submit-confirm-modal');
  if (modal) modal.style.display = 'none';
  if (adlSubInput && adlSubInput.offsetParent !== null) {
    adlSubInput.focus();
  } else if (postInput) {
    postInput.focus();
  }
}

// Allow Enter key or Escape to dismiss confirmation modal
document.addEventListener('keydown', (e) => {
  const modal = document.getElementById('submit-confirm-modal');
  if (modal && modal.style.display === 'flex') {
    if (e.key === 'Enter' || e.key === 'Escape') {
      e.preventDefault();
      closeSubmitConfirmModal();
    }
  }
});

function updateRecordsBadge() {
  if (recordsBadge) recordsBadge.innerText = records.length;
  renderSheetTable();
}

function renderSheetTable() {
  const tbody = document.getElementById('sheet-table-body');
  if (!tbody) return;
  tbody.innerHTML = '';

  if (records.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="16" style="text-align: center; padding: 24px; color: #80868b;">
          No survey points entered yet. Fill the row above and tap "Submit".
        </td>
      </tr>
    `;
    return;
  }

  records.forEach((r, idx) => {
    const isSynced = r.sync_status === 'synced';
    const syncTag = isSynced 
      ? '<span class="gs-tag-synced">✓ Synced</span>' 
      : '<span class="gs-tag-pending">⏳ Pending</span>';

    const timeDisplay = r["Date & Time"] || r.survey_date_time || (r.timestamp ? r.timestamp.slice(0, 19).replace('T', ' ') : '-');
    const colorDisplay = r["Splitter Lead Colour Code"] || r.splitter_lead_color || '-';
    const adlDisplay = r["ADL Subscriber ID"] || r.adl_subscriber_id || '-';
    const acsDisplay = r["ACS Subscriber ID"] || r.acs_subscriber_id || '-';

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="gs-row-num">${idx + 1}</td>
      <td style="font-family: monospace; font-size: 0.75rem; color:#5f6368;">${escapeHtml(timeDisplay)}</td>
      <td class="gs-id-cell">${escapeHtml(r["Enclosure ID"]) || '-'}</td>
      <td><strong>${escapeHtml(r["KSEB Post Number"]) || '-'}</strong></td>
      <td>${escapeHtml(r["Land Mark"]) || '-'}</td>
      <td>${escapeHtml(r["Center"]) || '-'}</td>
      <td>${escapeHtml(r["RT Room"]) || '-'}</td>
      <td>${escapeHtml(r["OLT/Node  Name"]) || '-'} [${escapeHtml(r["Port Number"]) || '-'}]</td>
      <td style="font-family: monospace; font-size: 0.75rem;">${escapeHtml(r["Lat /Long"]) || '-'}</td>
      <td>${escapeHtml(r["Splitter Ratio"]) || '-'}</td>
      <td style="text-align:center;">${r["No: Of Customer Connected"] || 0}</td>
      <td><span style="background:#e8f0fe; color:#1a73e8; padding:2px 6px; border-radius:4px; font-size:0.75rem; font-weight:600;">${escapeHtml(colorDisplay)}</span></td>
      <td style="font-family: monospace; font-size: 0.75rem;">${escapeHtml(adlDisplay)}</td>
      <td style="font-family: monospace; font-size: 0.75rem;">${escapeHtml(acsDisplay)}</td>
      <td>${syncTag}</td>
      <td style="text-align:center;"><button class="btn-del-cell" onclick="deleteRecord(${idx})" title="Delete Row">🗑️</button></td>
    `;
    tbody.appendChild(tr);
  });
}

// Export to Excel (.xlsx) - Downloads central server survey records for the user's center
async function exportToExcel() {
  if (currentUser && normalizeClientRole(currentUser.role) === 'field_technician') {
    showToast('Permission Denied: Field Technicians cannot download survey data.', false);
    return;
  }

  // 1. Determine target center to export
  let targetCenter = '';
  if (centerSelect && centerSelect.value) {
    targetCenter = centerSelect.value.trim();
  } else if (currentUser && currentUser.assigned_center) {
    targetCenter = currentUser.assigned_center.trim();
  } else {
    targetCenter = 'ALL';
  }

  // If user is assigned to multiple centers, provide option to download active center or all assigned centers
  if (currentUser && currentUser.assigned_center && currentUser.assigned_center.includes(',')) {
    const centersList = currentUser.assigned_center.split(',').map(s => s.trim()).filter(Boolean);
    if (centersList.length > 1) {
      const choice = confirm(`You have charge of multiple centers: ${centersList.join(', ')}.\n\n• Click OK to download all data for currently selected center "${targetCenter}".\n• Click Cancel to download combined data for ALL your assigned centers.`);
      if (!choice) {
        targetCenter = currentUser.assigned_center;
      }
    }
  }

  showToast(`Preparing Excel export for ${targetCenter}...`);

  // 2. Online Mode: Stream live central survey data from Ubuntu Server SQLite database
  if (isServerReachable) {
    try {
      const authToken = localStorage.getItem('gpon_auth_token') || (currentUser && currentUser.token) || '';
      const headers = authToken ? { 'Authorization': `Bearer ${authToken}` } : {};
      const exportUrl = `${serverUrl}/api/export-center-excel?center=${encodeURIComponent(targetCenter)}${authToken ? `&token=${encodeURIComponent(authToken)}` : ''}`;
      const res = await fetch(exportUrl, { headers });
      if (res.ok) {
        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const today = new Date().toISOString().slice(0, 10);
        const safeCenterName = targetCenter.replace(/[\s,]+/g, '_');
        a.download = `${safeCenterName}_Survey_Data_${today}.xlsx`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
        showToast(`Excel downloaded for Center: ${targetCenter}!`);
        return;
      } else {
        const errJson = await res.json().catch(() => ({}));
        showToast(errJson.detail || 'Server export error, trying local records...', false);
      }
    } catch (err) {
      console.warn('Server export fetch failed, falling back to local storage:', err);
    }
  }

  // 3. Offline Mode Fallback: Export local records saved on this device
  let localExportRows = records;
  if (targetCenter && targetCenter.toUpperCase() !== 'ALL') {
    const targetCentersList = targetCenter.split(',').map(s => s.trim().toLowerCase());
    const filtered = records.filter(r => r.Center && targetCentersList.includes(r.Center.trim().toLowerCase()));
    if (filtered.length > 0) {
      localExportRows = filtered;
    }
  }

  if (!localExportRows || localExportRows.length === 0) {
    showToast(`No records found for center: ${targetCenter}`, false);
    return;
  }

  // Column headers matching standard target template
  const headers = [
    'Region', 'Center', 'RT Room', 'GPON/FTTH/WDM', 'OLT/Node  Name', 
    'Port Number', 'KSEB Post Number', 'Land Mark', 'Enclosure Number', 
    'Enclosure ID', 'Lat /Long', 'Splitter ID', 'Splitter Ratio', 
    'No: Of Customer Connected', 'Splitter Lead Colour Code', 
    'ADL Subscriber ID', 'ACS Subscriber ID', 'Date & Time'
  ];

  const rows = [
    [], // Row 1 blank like original
    headers
  ];

  localExportRows.forEach(r => {
    rows.push([
      r["Region"] || "Thrissur",
      r["Center"] || "",
      r["RT Room"] || "",
      r["GPON/FTTH/WDM"] || "",
      r["OLT/Node  Name"] || "",
      r["Port Number"] || "",
      r["KSEB Post Number"] || "",
      r["Land Mark"] || "",
      r["Enclosure Number"] || "",
      r["Enclosure ID"] || "",
      r["Lat /Long"] || "",
      r["Splitter ID"] || "",
      r["Splitter Ratio"] || "",
      r["No: Of Customer Connected"] || 0,
      r["Splitter Lead Colour Code"] || r["splitter_lead_color"] || "",
      r["ADL Subscriber ID"] || r["adl_subscriber_id"] || "",
      r["ACS Subscriber ID"] || r["acs_subscriber_id"] || "",
      r["Date & Time"] || r["survey_date_time"] || (r["timestamp"] ? r["timestamp"].slice(0, 19).replace('T', ' ') : "")
    ]);
  });

  const ws = XLSX.utils.aoa_to_sheet(rows);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Sheet1");

  const today = new Date().toISOString().slice(0, 10);
  const safeName = targetCenter.replace(/[\s,]+/g, '_');
  XLSX.writeFile(wb, `${safeName}_Survey_Export_${today}.xlsx`);
  showToast(`Offline Excel exported for ${targetCenter}!`);
}

// Records Modal Management
function openRecordsModal() {
  const modal = document.getElementById('records-modal');
  const container = document.getElementById('records-list-container');
  if (!modal || !container) {
    console.warn('Records modal element not found in DOM');
    return;
  }
  container.innerHTML = '';

  if (records.length === 0) {
    container.innerHTML = '<p style="text-align:center; color:#94a3b8; padding:20px;">No survey points saved yet.</p>';
  } else {
    records.slice().reverse().forEach((r, idx) => {
      const realIndex = records.length - 1 - idx;
      const isSynced = r.sync_status === 'synced';
      const syncBadge = isSynced 
        ? '<span style="color:#10b981; font-weight:bold; font-size:0.75rem;">[Synced ✓]</span>' 
        : '<span style="color:#f59e0b; font-weight:bold; font-size:0.75rem;">[Pending Sync ⏳]</span>';
      const oltDisplay = r["OLT/Node  Name"] || r["OLT/Node Name"] || r.olt_name || '';

      const item = document.createElement('div');
      item.className = 'record-item';
      item.innerHTML = `
        <div class="record-item-main">
          <div class="record-title">${escapeHtml(r["Enclosure ID"])} — ${escapeHtml(r["KSEB Post Number"])} ${syncBadge}</div>
          <div class="record-sub">${escapeHtml(oltDisplay)} | Port: ${escapeHtml(r["Port Number"])} | Ratio: ${escapeHtml(r["Splitter Ratio"])}</div>
          <div class="record-sub">GPS: ${escapeHtml(r["Lat /Long"]) || 'No GPS'} | Cust: ${r["No: Of Customer Connected"]}</div>
        </div>
        <button class="record-del" onclick="deleteRecord(${realIndex})">✕</button>
      `;
      container.appendChild(item);
    });
  }

  modal.style.display = 'block';
}

function closeRecordsModal() {
  const modal = document.getElementById('records-modal');
  if (modal) modal.style.display = 'none';
}

let pendingDeleteIndex = null;

function deleteRecord(index) {
  if (index < 0 || index >= records.length) return;
  pendingDeleteIndex = index;
  const r = records[index];

  const modal = document.getElementById('delete-confirm-modal');
  const detailsEl = document.getElementById('delete-confirm-details');

  if (!modal || !detailsEl) {
    if (confirm('Are you sure you want to permanently delete this survey point from this device and the server?')) {
      executeDeleteRecord(index);
    }
    return;
  }

  const eid = r["Enclosure ID"] || r.enclosure_id || '-';
  const spl = r["Splitter ID"] || r.splitter_id || '-';
  const post = r["KSEB Post Number"] || r.kseb_post_number || '-';
  const lmark = r["Land Mark"] || r.landmark || '-';
  const olt = r["OLT/Node  Name"] || r["OLT/Node Name"] || r.olt_name || '-';
  const port = r["Port Number"] || r.port_number || '-';
  const isSynced = r.sync_status === 'synced';

  detailsEl.innerHTML = `
    <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
      <span style="color:#7f1d1d;">Enclosure ID:</span>
      <strong style="font-family:monospace; color:#991b1b;">${escapeHtml(eid)}</strong>
    </div>
    <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
      <span style="color:#7f1d1d;">Splitter:</span>
      <strong style="color:#991b1b;">${escapeHtml(spl)} (${escapeHtml(r["Splitter Ratio"] || r.splitter_ratio || '-')})</strong>
    </div>
    <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
      <span style="color:#7f1d1d;">KSEB Post / Landmark:</span>
      <strong style="color:#991b1b;">${escapeHtml(post)} / ${escapeHtml(lmark)}</strong>
    </div>
    <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
      <span style="color:#7f1d1d;">OLT / Port:</span>
      <strong style="color:#991b1b;">${escapeHtml(olt)} [${escapeHtml(port)}]</strong>
    </div>
    <div style="display:flex; justify-content:space-between;">
      <span style="color:#7f1d1d;">Server Status:</span>
      <strong style="color:${isSynced ? '#059669' : '#d97706'};">${isSynced ? 'Synced on Server' : 'Pending Local Sync'}</strong>
    </div>
  `;

  const confirmBtn = document.getElementById('btn-confirm-delete-action');
  if (confirmBtn) {
    confirmBtn.onclick = () => {
      confirmDeleteRecord();
    };
  }

  modal.style.display = 'flex';
}

function confirmDeleteRecord() {
  if (pendingDeleteIndex !== null && pendingDeleteIndex !== undefined) {
    const idx = pendingDeleteIndex;
    closeDeleteConfirmModal();
    executeDeleteRecord(idx);
  }
}

function closeDeleteConfirmModal() {
  const modal = document.getElementById('delete-confirm-modal');
  if (modal) modal.style.display = 'none';
  pendingDeleteIndex = null;
}

async function executeDeleteRecord(index) {
  if (index === null || index < 0 || index >= records.length) {
    closeDeleteConfirmModal();
    return;
  }
  const r = records[index];
  const clientUuid = r.client_uuid || '';
  const eid = (r["Enclosure ID"] || r.enclosure_id || '').trim();
  const spl = (r["Splitter ID"] || r.splitter_id || '').trim();
  const col = (r["Splitter Lead Colour Code"] || r.splitter_lead_color || '').trim();

  // Guarantee modal is closed immediately
  closeDeleteConfirmModal();

  try {
    // 1. Remove from local array & localStorage
    records.splice(index, 1);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(records));

    // 2. Clear from local surveyed points cache so UI unlocks immediately
    if (eid && spl) {
      if (typeof networkSurveyedPoints !== 'undefined') {
        const variants = getColorVariants(col);
        variants.forEach(v => {
          const k = `${eid}|${spl}|${v}`.toUpperCase();
          delete networkSurveyedPoints[k];
        });
        const kEmpty = `${eid}|${spl}|`.toUpperCase();
        if (!col) delete networkSurveyedPoints[kEmpty];

        const splKey = `${eid}|${spl}`.toUpperCase();
        if (networkSurveyedPoints[splKey]) {
          const s = networkSurveyedPoints[splKey];
          if (Array.isArray(s.leads)) {
            const normC = col.toUpperCase();
            s.leads = s.leads.filter(l => l !== normC);
            s.count = s.leads.length;
            if (s.count === 0 && !col) {
              delete networkSurveyedPoints[splKey];
            }
          }
        }
        localStorage.setItem('gpon_network_surveyed_points', JSON.stringify(networkSurveyedPoints));
      }
    }

    // 3. Update UI
    updateRecordsBadge();
    updateSyncUI();
    updateAvailableEnclosures();
    updateAvailableSplitters();

    // If records modal was open, refresh it
    const recModal = document.getElementById('records-modal');
    if (recModal && recModal.style.display === 'block') {
      openRecordsModal();
    }
  } catch (err) {
    console.error('Error during local record deletion:', err);
  }

  // 4. Send DELETE to server with both UUID and natural key fallback
  let serverDeleted = false;
  try {
    const authToken = localStorage.getItem('gpon_auth_token') || (currentUser && currentUser.token) || '';
    const headers = authToken ? { 'Authorization': `Bearer ${authToken}` } : {};
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 8000);

    const uuidParam = encodeURIComponent(clientUuid || 'by-point');
    const qParams = new URLSearchParams();
    if (eid) qParams.append('enclosure_id', eid);
    if (spl) qParams.append('splitter_id', spl);
    if (col) qParams.append('splitter_lead_color', col);

    const deleteUrl = `${serverUrl}/api/records/${uuidParam}?${qParams.toString()}`;
    const res = await fetch(deleteUrl, {
      method: 'DELETE',
      headers: headers,
      signal: controller.signal
    });
    clearTimeout(timeoutId);
    if (res.ok) {
      serverDeleted = true;
    }
  } catch (err) {
    console.warn('[Delete] Could not delete from server right now (network offline):', err);
  }

  if (serverDeleted) {
    showToast('✓ Record permanently deleted from device and server');
    // Refresh surveyed points in background
    if (typeof fetchSurveyedPoints === 'function') {
      fetchSurveyedPoints().catch(() => {});
    }
  } else {
    // Offline fallback: queue clientUuid for deletion on next server sync
    if (clientUuid) {
      let pendingDeletions = [];
      try {
        pendingDeletions = JSON.parse(localStorage.getItem('gpon_pending_deletions') || '[]');
      } catch(e) { pendingDeletions = []; }
      if (!pendingDeletions.includes(clientUuid)) {
        pendingDeletions.push(clientUuid);
        localStorage.setItem('gpon_pending_deletions', JSON.stringify(pendingDeletions));
      }
    }
    showToast('✓ Record deleted locally (will delete from server when connected)');
  }
}

async function clearAllRecords() {
  if (records.length === 0) return;
  if (!confirm(`Are you sure you want to permanently delete all ${records.length} records from both this device AND the server? This cannot be undone.`)) {
    return;
  }
  
  const allUuids = records.map(r => r.client_uuid).filter(Boolean);
  records = [];
  localStorage.setItem(STORAGE_KEY, JSON.stringify(records));
  if (typeof networkSurveyedPoints !== 'undefined') {
    networkSurveyedPoints = {};
  }
  updateRecordsBadge();
  updateSyncUI();
  updateAvailableEnclosures();
  updateAvailableSplitters();
  closeRecordsModal();

  if (allUuids.length > 0) {
    try {
      const authToken = localStorage.getItem('gpon_auth_token') || '';
      const delHeaders = { 'Content-Type': 'application/json' };
      if (authToken) delHeaders['Authorization'] = 'Bearer ' + authToken;
      await fetch(`${serverUrl}/api/records/bulk-delete`, {
        method: 'POST',
        headers: delHeaders,
        body: JSON.stringify({ uuids: allUuids })
      });
      showToast('All records permanently deleted from device and server');
    } catch (e) {
      let pendingDeletions = [];
      try {
        pendingDeletions = JSON.parse(localStorage.getItem('gpon_pending_deletions') || '[]');
      } catch(err) { pendingDeletions = []; }
      allUuids.forEach(u => {
        if (!pendingDeletions.includes(u)) pendingDeletions.push(u);
      });
      localStorage.setItem('gpon_pending_deletions', JSON.stringify(pendingDeletions));
      showToast('All records cleared locally (server will sync deletions)');
    }
  } else {
    showToast('All records cleared');
  }
}

// ==========================================
// OFFLINE-FIRST SYNC ENGINE (UBUNTU SERVER)
// ==========================================

async function checkServerConnection() {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3500);
    const res = await fetch(`${serverUrl}/api/health`, { signal: controller.signal });
    clearTimeout(timeoutId);
    if (res.ok) {
      isServerReachable = true;
      fetchSurveyedPoints();
      const data = await res.json().catch(() => null);
      if (data && data.hierarchy_version) {
        if (data.hierarchy_version !== lastKnownHierarchyVersion) {
          lastKnownHierarchyVersion = data.hierarchy_version;
          localStorage.setItem('gpon_hierarchy_version', String(data.hierarchy_version));
          fetchHierarchyFromServer();
        }
      }
    } else {
      isServerReachable = false;
    }
  } catch (err) {
    isServerReachable = false;
  } finally {
    serverConnectionChecked = true;
    updateSyncUI();
  }
}

async function fetchHierarchyFromServer() {
  try {
    const res = await fetch(`${serverUrl}/api/hierarchy?t=${Date.now()}`);
    if (res.ok) {
      const data = await res.json();
      if (data && data.hierarchy && typeof data.hierarchy === 'object' && Object.keys(data.hierarchy).length > 0) {
        const newStr = JSON.stringify(data.hierarchy);
        const oldStr = localStorage.getItem('gpon_custom_hierarchy');
        if (newStr !== oldStr) {
          DEFAULT_PRELOAD.hierarchy = data.hierarchy;
          localStorage.setItem('gpon_custom_hierarchy', newStr);
          initDropdowns(true);
        }
      }
    }
  } catch (e) {}
}

async function refreshCurrentUserProfile() {
  if (!currentUser || !currentUser.username) return;
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 4000);
    const res = await fetch(`${serverUrl}/api/user-profile?username=${encodeURIComponent(currentUser.username)}`, {
      signal: controller.signal
    });
    clearTimeout(timeoutId);
    if (res.ok) {
      const data = await res.json();
      if (data && data.user) {
        const oldRole = currentUser.role;
        const oldCenters = currentUser.assigned_center;
        currentUser = data.user;
        localStorage.setItem('gpon_logged_in_user', JSON.stringify(currentUser));
        updateUserBar();
        if (oldRole !== currentUser.role || oldCenters !== currentUser.assigned_center) {
          initDropdowns();
          console.log(`[Auth] User profile auto-refreshed from server. Role: ${currentUser.role}`);
        }
      }
    }
  } catch (e) {
    // Offline or server unreachable
  }
}

async function syncHierarchyToServer(hierarchy) {
  try {
    const token = localStorage.getItem('gpon_auth_token') || '';
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = 'Bearer ' + token;

    const res = await fetch(`${serverUrl}/api/upload-hierarchy`, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({ hierarchy: hierarchy })
    });
    if (res.ok) {
      console.log('Hierarchy synced to server successfully');
    } else {
      console.warn('Hierarchy sync returned error:', res.status);
    }
  } catch (e) {
    console.log('Server not reachable for hierarchy upload sync');
  }
}

function updateSyncUI() {
  const badge = document.getElementById('sync-status-badge') || document.querySelector('.gs-doc-subtitle');
  const dot = document.getElementById('sync-dot');
  const text = document.getElementById('sync-text');
  if (!text) return;

  const unsyncedCount = records.filter(r => r.sync_status !== 'synced').length;

  if (isSyncing) {
    if (dot) dot.style.background = '#38bdf8';
    text.innerText = 'Syncing with Server...';
    if (badge) badge.title = 'Uploading survey records to Ubuntu server...';
    return;
  }

  if (!serverConnectionChecked) {
    if (dot) dot.style.background = '#f59e0b';
    text.innerText = 'Checking server...';
    if (badge) badge.title = 'Testing connection to Ubuntu server...';
    return;
  }

  if (!isServerReachable) {
    if (dot) dot.style.background = '#ef4444';
    text.innerText = unsyncedCount > 0 
      ? `Server Disconnected (${unsyncedCount} unsynced)` 
      : 'Server Disconnected';
    if (badge) badge.title = 'Ubuntu server unreachable / offline. Data is saved safely on your device. Tap to test connection.';
  } else {
    if (unsyncedCount === 0) {
      if (dot) dot.style.background = '#10b981';
      text.innerText = 'Server Connected (Synced ✓)';
      if (badge) badge.title = 'Connected to Ubuntu server. All records synced! Tap to re-check.';
    } else {
      if (dot) dot.style.background = '#0284c7';
      text.innerText = `Server Connected (${unsyncedCount} unsynced)`;
      if (badge) badge.title = `Connected to Ubuntu server. ${unsyncedCount} records ready to sync. Tap to sync now.`;
    }
  }
}

async function syncWithServer(silent = false) {
  if (isSyncing) return;

  // 1. Process any queued offline deletions first!
  let pendingDeletions = [];
  try {
    pendingDeletions = JSON.parse(localStorage.getItem('gpon_pending_deletions') || '[]');
  } catch(e) { pendingDeletions = []; }

  if (pendingDeletions.length > 0) {
    try {
      const syncToken = localStorage.getItem('gpon_auth_token') || '';
      const delHeaders = { 'Content-Type': 'application/json' };
      if (syncToken) delHeaders['Authorization'] = 'Bearer ' + syncToken;
      const delRes = await fetch(`${serverUrl}/api/records/bulk-delete`, {
        method: 'POST',
        headers: delHeaders,
        body: JSON.stringify({ uuids: pendingDeletions })
      });
      if (delRes.ok) {
        localStorage.removeItem('gpon_pending_deletions');
      }
    } catch(err) {
      console.warn('[Sync] Could not process pending deletions:', err);
    }
  }
  
  const pendingRecords = records.filter(r => r.sync_status !== 'synced');
  if (pendingRecords.length === 0) {
    if (!silent) showToast('All records are already synced with Ubuntu server!');
    checkServerConnection();
    updateRecordsBadge();
    return;
  }

  isSyncing = true;
  updateSyncUI();

  try {
    const formattedRecords = pendingRecords.map(r => ({
      client_uuid: r.client_uuid,
      region: r.Region || r.region || 'Thrissur',
      center: r.Center || r.center || '',
      rt_room: r['RT Room'] || r.rt_room || '',
      technology: r['GPON/FTTH/WDM'] || r.technology || 'GPON',
      olt_name: r['OLT/Node  Name'] || r['OLT/Node Name'] || r.olt_name || '',
      port_number: r['Port Number'] || r.port_number || '',
      kseb_post_number: r['KSEB Post Number'] || r.kseb_post_number || '',
      landmark: r['Land Mark'] || r.landmark || '',
      enclosure_number: r['Enclosure Number'] || r.enclosure_number || '',
      enclosure_id: r['Enclosure ID'] || r.enclosure_id || '',
      lat_long: r['Lat /Long'] || r['Lat/Long'] || r.lat_long || '',
      splitter_id: r['Splitter ID'] || r.splitter_id || '',
      splitter_ratio: r['Splitter Ratio'] || r.splitter_ratio || '',
      customers_connected: r['No: Of Customer Connected'] || r['No: of Customer Connected'] || r.customers_connected || 0,
      splitter_lead_color: r['Splitter Lead Colour Code'] || r.splitter_lead_color || '',
      adl_subscriber_id: r['ADL Subscriber ID'] || r.adl_subscriber_id || '',
      acs_subscriber_id: r['ACS Subscriber ID'] || r.acs_subscriber_id || '',
      survey_date_time: r['Date & Time'] || r.survey_date_time || r.timestamp || '',
      device_id: deviceId,
      surveyor_username: r.surveyor_username || (currentUser ? currentUser.username : ''),
      surveyor_name: r.surveyor_name || (currentUser ? currentUser.full_name : ''),
      created_at: r.timestamp || r.created_at || r['Date & Time'] || ''
    }));

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 8000);
    
    const syncToken = localStorage.getItem('gpon_auth_token') || '';
    const syncHeaders = { 'Content-Type': 'application/json' };
    if (syncToken) syncHeaders['Authorization'] = 'Bearer ' + syncToken;

    const res = await fetch(`${serverUrl}/api/sync`, {
      method: 'POST',
      headers: syncHeaders,
      body: JSON.stringify({
        device_id: deviceId,
        records: formattedRecords
      }),
      signal: controller.signal
    });
    clearTimeout(timeoutId);

    if (res.ok) {
      const data = await res.json();
      const syncedIds = new Set(data.synced_uuids || []);
      const skippedDupes = data.skipped_duplicates || [];
      const skippedIds = new Set(skippedDupes.map(d => d.client_uuid));

      // Update local storage status
      records.forEach(r => {
        if (syncedIds.has(r.client_uuid)) {
          r.sync_status = 'synced';
        } else if (skippedIds.has(r.client_uuid)) {
          r.sync_status = 'duplicate_skipped';
        }
      });
      localStorage.setItem(STORAGE_KEY, JSON.stringify(records));
      isServerReachable = true;
      const modalSyncBadge = document.getElementById('confirm-sync-status-badge');
      if (modalSyncBadge) {
        modalSyncBadge.innerHTML = '<span style="color:#10b981; font-weight:700;">☁️ Synced to Server ✓</span>';
      }

      if (skippedDupes.length > 0) {
        showToast(`⚠️ ${skippedDupes.length} duplicate record(s) rejected by server (already surveyed).`, false);
      } else if (!silent) {
        showToast(`Synced ${syncedIds.size} records with Ubuntu server!`);
      }

      // Re-fetch network surveyed points so dropdown locks immediately reflect all newly synced data
      fetchSurveyedPoints();
      updateRecordsBadge();
    } else {
      isServerReachable = false;
      serverConnectionChecked = true;
      if (!silent) showToast('Server connection failed. Data is safe locally.', false);
    }
  } catch (err) {
    isServerReachable = false;
    serverConnectionChecked = true;
    if (!silent) showToast('Could not reach server. Data is stored safely on phone.', false);
  } finally {
    isSyncing = false;
    serverConnectionChecked = true;
    updateSyncUI();
    updateRecordsBadge();
  }
}

async function triggerManualSync() {
  await checkServerConnection();
  refreshCurrentUserProfile();
  syncWithServer(false);
}

function configureServerUrl() {
  const current = localStorage.getItem('gpon_server_url') || serverUrl;
  const input = prompt('Enter Ubuntu Server Address (e.g. http://192.168.1.100:9001 or Cloudflare Tunnel URL):', current);
  if (input !== null && input.trim() !== '') {
    serverUrl = input.trim().replace(/\/+$/, '');
    localStorage.setItem('gpon_server_url', serverUrl);
    showToast('Server address updated!');
    checkServerConnection();
    syncWithServer(false);
  }
}

// Register Service Worker for Offline PWA
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('./sw.js')
      .then(reg => {
        console.log('SW registered successfully:', reg.scope);
        reg.update();
      })
      .catch(err => console.log('SW registration failed:', err));
  });
}

// Auto-Sync Listeners
window.addEventListener('online', () => {
  showToast('Internet connected. Checking server...');
  checkServerConnection();
  syncWithServer(true);
});

window.addEventListener('offline', () => {
  isServerReachable = false;
  serverConnectionChecked = true;
  updateSyncUI();
});

// Init App Lifecycle
function bootApp() {
  // Ensure Submit button label is strictly set to 'Submit'
  const submitBtnEl = document.getElementById('btn-save-record') || document.querySelector('.btn-add-row');
  if (submitBtnEl) {
    submitBtnEl.innerHTML = '<span>➕</span> Submit';
  }

  // Pre-fill username if remembered
  const isRemembered = localStorage.getItem('gpon_remember_creds') === 'true';
  const savedU = localStorage.getItem('gpon_remembered_username') || '';
  const uInput = document.getElementById('login-username');
  const rCheckbox = document.getElementById('login-remember-me');
  if (uInput && savedU) uInput.value = savedU;
  if (rCheckbox && localStorage.getItem('gpon_remember_creds') !== null) {
    rCheckbox.checked = isRemembered;
  }
  // Purge any legacy plaintext passwords stored previously
  localStorage.removeItem('gpon_remembered_password');

  // Check Login State
  const loginOverlay = document.getElementById('login-overlay');
  const userBar = document.getElementById('user-bar');
  if (!currentUser) {
    const foucStyle = document.getElementById('fouc-prevention');
    if (foucStyle) foucStyle.remove();
    if (loginOverlay) loginOverlay.style.setProperty('display', 'flex', 'important');
    if (userBar) userBar.style.display = 'none';
  } else {
    if (loginOverlay) loginOverlay.style.setProperty('display', 'none', 'important');
    updateUserBar();
    refreshCurrentUserProfile();
    initDropdowns();
    fetchSurveyedPoints();
    scheduleMidnightLogout();
  }

  updateSubscriberInputsState();
  updateRecordsBadge();
  updateSyncUI();
  checkServerConnection();
}

// Ensure bootApp executes regardless of whether DOMContentLoaded already fired
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootApp);
} else {
  bootApp();
}

// Smart Lightweight Heartbeat: Periodic background check (every 20 seconds, active tab only)
setInterval(() => {
  // If phone is locked or surveyor switched apps, pause heartbeat to save phone battery & data
  if (document.visibilityState !== 'visible') return;

  checkMidnightExpiration();
  checkServerConnection();
  if (isServerReachable && records.some(r => r.sync_status !== 'synced')) {
    syncWithServer(true);
  }
}, 20000);

// Live auto-refresh when surveyor returns to the app
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    checkMidnightExpiration();
    checkServerConnection();
    refreshCurrentUserProfile();
  }
});
window.addEventListener('focus', () => {
  checkMidnightExpiration();
  checkServerConnection();
  refreshCurrentUserProfile();
});
