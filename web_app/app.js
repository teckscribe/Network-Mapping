// State Management
let currentLat = null;
let currentLon = null;
let currentAccuracy = null;
let mapInstance = null;
let mapMarker = null;

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

// User Session & Authentication
let currentUser = JSON.parse(localStorage.getItem('gpon_logged_in_user') || 'null');

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

    // 3. RCSM: "No option to enter the field inputs"
    // Disable form and hide submit button for RCSM
    const isEntryAllowed = (role !== 'rcsm');
    if (rcsmBanner) rcsmBanner.style.display = isEntryAllowed ? 'none' : 'block';
    if (submitBtn) submitBtn.style.display = isEntryAllowed ? 'flex' : 'none';

    const formInputs = document.querySelectorAll('.gs-card-body input, .gs-card-body select');
    formInputs.forEach(el => {
      if (!isEntryAllowed) {
        el.setAttribute('disabled', 'true');
      } else {
        if (!['tech-select', 'olt-type-select', 'region-select'].includes(el.id)) {
          el.removeAttribute('disabled');
        }
      }
    });

    // 4. Field Technician: "Option to enter the field input only no options to download the data"
    // ACSO & RCSM & Super Admin can download data. Field Technician cannot.
    const isDownloadAllowed = (role !== 'field_technician');
    if (excelBtn) excelBtn.style.display = isDownloadAllowed ? 'inline-flex' : 'none';

  } else {
    userBar.style.display = 'none';
  }
}

async function handleLogin(e) {
  if (e) e.preventDefault();
  const u = document.getElementById('login-username').value.trim();
  const p = document.getElementById('login-password').value.trim();
  const rememberCheckbox = document.getElementById('login-remember-me');
  const remember = rememberCheckbox ? rememberCheckbox.checked : true;
  
  if (!u || !p) {
    showToast('Please enter username and password', false);
    return;
  }

  // 1. Attempt login with Ubuntu server
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 4000);
    const res = await fetch(`${serverUrl}/api/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: u, password: p }),
      signal: controller.signal
    });
    clearTimeout(timeoutId);

    if (res.ok) {
      const data = await res.json();
      if (remember) {
        localStorage.setItem('gpon_remember_creds', 'true');
        localStorage.setItem('gpon_remembered_username', u);
        localStorage.setItem('gpon_remembered_password', p);
      } else {
        localStorage.removeItem('gpon_remember_creds');
        localStorage.removeItem('gpon_remembered_username');
        localStorage.removeItem('gpon_remembered_password');
      }
      setCurrentUser(data.user);
      showToast(`Welcome, ${data.user.full_name}!`);
      return;
    }
  } catch (err) {
    console.log('Server login unreachable, trying offline fallback...');
  }

  // 2. Offline fallback credentials for remote field areas
  const offlineUsers = {
    'admin': { username: 'admin', full_name: 'Central Super Administrator', email: 'admin@gpon.local', assigned_center: 'ALL', assigned_region: 'ALL', role: 'super_admin' }
  };

  if (offlineUsers[u] && p === 'admin123') {
    if (remember) {
      localStorage.setItem('gpon_remember_creds', 'true');
      localStorage.setItem('gpon_remembered_username', u);
      localStorage.setItem('gpon_remembered_password', p);
    }
    setCurrentUser(offlineUsers[u]);
    showToast(`Offline Login: Welcome, ${offlineUsers[u].full_name}!`);
  } else {
    showToast('Invalid username or password', false);
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
  document.getElementById('login-overlay').style.display = 'none';
  updateUserBar();
  initDropdowns();
}

function logout() {
  if (confirm('Log out from survey account?')) {
    currentUser = null;
    localStorage.removeItem('gpon_logged_in_user');
    updateUserBar();
    document.getElementById('login-overlay').style.display = 'flex';
  }
}

function openResetPasswordModal(username, email) {
  const overlay = document.getElementById('reset-password-overlay');
  if (!overlay) return;
  const uField = document.getElementById('reset-username');
  const eField = document.getElementById('reset-email');
  const p1 = document.getElementById('reset-new-password');
  const p2 = document.getElementById('reset-confirm-password');
  const errDiv = document.getElementById('reset-pwd-error');
  const succDiv = document.getElementById('reset-pwd-success');

  if (uField) uField.value = username || (currentUser ? currentUser.username : (document.getElementById('login-username').value || ''));
  if (eField) eField.value = email || (currentUser ? (currentUser.email || '') : '');
  if (p1) p1.value = '';
  if (p2) p2.value = '';
  if (errDiv) errDiv.style.display = 'none';
  if (succDiv) succDiv.style.display = 'none';

  overlay.style.display = 'flex';
}

function closeResetPasswordModal() {
  const overlay = document.getElementById('reset-password-overlay');
  if (overlay) overlay.style.display = 'none';
}

async function handlePasswordReset(e) {
  if (e) e.preventDefault();
  const u = document.getElementById('reset-username').value.trim();
  const mail = document.getElementById('reset-email').value.trim();
  const p1 = document.getElementById('reset-new-password').value.trim();
  const p2 = document.getElementById('reset-confirm-password').value.trim();
  const errDiv = document.getElementById('reset-pwd-error');
  const succDiv = document.getElementById('reset-pwd-success');

  if (errDiv) errDiv.style.display = 'none';
  if (succDiv) succDiv.style.display = 'none';

  if (!u || !mail || !p1) {
    if (errDiv) {
      errDiv.innerText = 'Username, registered email, and new password are required.';
      errDiv.style.display = 'block';
    }
    return;
  }

  if (p1 !== p2) {
    if (errDiv) {
      errDiv.innerText = 'New passwords do not match. Please re-enter.';
      errDiv.style.display = 'block';
    }
    return;
  }

  try {
    const res = await fetch('/api/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: u,
        email: mail,
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
        if (loginU) loginU.value = u;
        if (loginP) loginP.value = p1;
        showToast('Password updated! Please log in.', true);
      }, 1500);
    } else {
      if (errDiv) {
        errDiv.innerText = data.detail || 'Could not update password.';
        errDiv.style.display = 'block';
      }
    }
  } catch(err) {
    if (errDiv) {
      errDiv.innerText = 'Network error: ' + err.message;
      errDiv.style.display = 'block';
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
const liveTimestampPreview = document.getElementById('live-timestamp-preview');

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

function updateLiveClock() {
  if (liveTimestampPreview) {
    liveTimestampPreview.innerText = '⏱️ ' + getFormattedDateTime();
  }
}
setInterval(updateLiveClock, 1000);

const manualCoordsInput = document.getElementById('manual-coords-input');
const gpsAccText = document.getElementById('gps-acc');
const mapContainer = document.getElementById('map-container');
const mapToggleBtn = document.getElementById('map-toggle-btn');
const recordsBadge = document.getElementById('records-count-badge');

function onManualCoordsChange(val) {
  if (!val) return;
  const parts = val.split(',');
  if (parts.length === 2) {
    const lat = parseFloat(parts[0].trim());
    const lon = parseFloat(parts[1].trim());
    if (!isNaN(lat) && !isNaN(lon)) {
      currentLat = lat;
      currentLon = lon;
      gpsAccText.innerText = 'Manual Entry';
      if (mapInstance) {
        mapInstance.setView([currentLat, currentLon], 18);
        if (mapMarker) mapMarker.setLatLng([currentLat, currentLon]);
      }
      showToast('Coordinates updated');
    }
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

// Update Enclosure ID Preview
function updateEnclosureId() {
  const olt = oltSelect.value;
  const port = portSelect.value;
  const enc = enclosureSelect.value;
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

  centers.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c;
    opt.innerText = c;
    centerSelect.appendChild(opt);
  });

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

  // Enclosures
  if (enclosureSelect.options.length === 0) {
    DEFAULT_PRELOAD.enclosures.forEach(e => {
      const opt = document.createElement('option');
      opt.value = e;
      opt.innerText = e;
      enclosureSelect.appendChild(opt);
    });
  }

  // Splitter Ratio
  if (splitterRatioSelect.options.length === 0) {
    DEFAULT_PRELOAD.ratios.forEach(r => {
      const opt = document.createElement('option');
      opt.value = r;
      opt.innerText = r;
      splitterRatioSelect.appendChild(opt);
    });
    if (DEFAULT_PRELOAD.ratios.includes("1:8")) {
      splitterRatioSelect.value = "1:8";
    }
  }

  updateSplitterColorOptions();
  onCenterChange(prevRt, prevOlt);
}

// Splitter Lead Colour Code dynamically based on Splitter Ratio
function updateSplitterColorOptions() {
  if (!splitterColorSelect) return;
  const ratio = splitterRatioSelect ? splitterRatioSelect.value : '1:8';
  const colorList = (DEFAULT_PRELOAD.color_codes_by_ratio && DEFAULT_PRELOAD.color_codes_by_ratio[ratio])
    ? DEFAULT_PRELOAD.color_codes_by_ratio[ratio]
    : (DEFAULT_PRELOAD.color_codes || []);

  const prev = splitterColorSelect.value;
  splitterColorSelect.innerHTML = '';

  const defOpt = document.createElement('option');
  defOpt.value = '';
  defOpt.innerText = `-- Select Out Color (${ratio}) --`;
  splitterColorSelect.appendChild(defOpt);

  colorList.forEach(col => {
    const opt = document.createElement('option');
    opt.value = col;
    opt.innerText = col;
    splitterColorSelect.appendChild(opt);
  });

  if (prev && colorList.includes(prev)) {
    splitterColorSelect.value = prev;
  }
}

// Filter Enclosures to prevent selecting duplicate enclosure number multiple times in same OLT port
function updateAvailableEnclosures() {
  const olt = oltSelect ? oltSelect.value : '';
  const port = portSelect ? portSelect.value : '';

  // Enclosures already used for this exact OLT & Port
  const usedEnc = new Set(
    records
      .filter(r => (r["OLT/Node  Name"] || r.olt_name) === olt && (r["Port Number"] || r.port_number) === port)
      .map(r => r["Enclosure Number"] || r.enclosure_number)
  );

  const prevVal = enclosureSelect.value;
  enclosureSelect.innerHTML = '';

  let firstAvailable = null;
  DEFAULT_PRELOAD.enclosures.forEach(e => {
    const opt = document.createElement('option');
    opt.value = e;
    if (usedEnc.has(e)) {
      opt.innerText = `${e} (Already Used in ${port})`;
      opt.disabled = true;
      opt.style.color = '#94a3b8';
    } else {
      opt.innerText = e;
      if (!firstAvailable) firstAvailable = e;
    }
    enclosureSelect.appendChild(opt);
  });

  if (prevVal && !usedEnc.has(prevVal)) {
    enclosureSelect.value = prevVal;
  } else if (firstAvailable) {
    enclosureSelect.value = firstAvailable;
  }

  updateEnclosureId();
  updateAvailableSplitters();
}

// Filter Splitter IDs to prevent selecting duplicate splitter ID under same Enclosure ID
function updateAvailableSplitters() {
  const olt = oltSelect ? oltSelect.value : '';
  const port = portSelect ? portSelect.value : '';
  const enc = enclosureSelect ? enclosureSelect.value : '';
  const eid = computeEnclosureId(olt, port, enc);

  // Splitters already used under this exact Enclosure ID
  const usedSplitters = new Set(
    records
      .filter(r => (r["Enclosure ID"] || r.enclosure_id) === eid)
      .map(r => r["Splitter ID"] || r.splitter_id)
  );

  const prevVal = splitterIdSelect.value;
  splitterIdSelect.innerHTML = '';

  let firstAvailable = null;
  DEFAULT_PRELOAD.splitters.forEach(s => {
    const opt = document.createElement('option');
    opt.value = s;
    if (usedSplitters.has(s)) {
      opt.innerText = `${s} (Already Used in ${eid})`;
      opt.disabled = true;
      opt.style.color = '#94a3b8';
    } else {
      opt.innerText = s;
      if (!firstAvailable) firstAvailable = s;
    }
    splitterIdSelect.appendChild(opt);
  });

  if (prevVal && !usedSplitters.has(prevVal)) {
    splitterIdSelect.value = prevVal;
  } else if (firstAvailable) {
    splitterIdSelect.value = firstAvailable;
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

function onOLTTypeChange() {
  const c = centerSelect ? centerSelect.value : '';
  const rt = rtRoomSelect ? rtRoomSelect.value : '';
  const olt = oltSelect ? oltSelect.value : '';
  const entry = findNodeEntry(c, rt, olt);

  portSelect.innerHTML = '';
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

  ports.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p;
    opt.innerText = p;
    portSelect.appendChild(opt);
  });

  updateAvailableEnclosures();
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
centerSelect.addEventListener('change', onCenterChange);
rtRoomSelect.addEventListener('change', onRTRoomChange);
oltSelect.addEventListener('change', onOLTChange);
if (oltTypeSelect) oltTypeSelect.addEventListener('change', onOLTTypeChange);
portSelect.addEventListener('change', updateAvailableEnclosures);
enclosureSelect.addEventListener('change', () => { updateEnclosureId(); updateAvailableSplitters(); });
splitterRatioSelect.addEventListener('change', updateSplitterColorOptions);

// GPS Geolocation
function captureGPS() {
  if (!navigator.geolocation) {
    showToast('GPS not supported on this browser', false);
    return;
  }
  
  manualCoordsInput.value = 'Locating...';
  gpsAccText.innerText = 'Acquiring GPS fix';

  navigator.geolocation.getCurrentPosition(
    (pos) => {
      currentLat = pos.coords.latitude;
      currentLon = pos.coords.longitude;
      currentAccuracy = pos.coords.accuracy;

      manualCoordsInput.value = `${currentLat.toFixed(5)}, ${currentLon.toFixed(5)}`;
      gpsAccText.innerText = `± ${Math.round(currentAccuracy)}m accuracy`;

      // Update Map if open
      if (mapInstance) {
        mapInstance.setView([currentLat, currentLon], 18);
        if (mapMarker) {
          mapMarker.setLatLng([currentLat, currentLon]);
        } else {
          mapMarker = L.marker([currentLat, currentLon], { draggable: true }).addTo(mapInstance);
          mapMarker.on('dragend', function(e) {
            const pt = e.target.getLatLng();
            currentLat = pt.lat;
            currentLon = pt.lng;
            manualCoordsInput.value = `${currentLat.toFixed(5)}, ${currentLon.toFixed(5)}`;
            gpsAccText.innerText = 'Adjusted via map';
          });
        }
      }
      showToast('GPS Location Captured!');
    },
    (err) => {
      manualCoordsInput.value = '';
      manualCoordsInput.placeholder = 'GPS unavailable - enter manually or tap map';
      gpsAccText.innerText = err.message;
      showToast('GPS signal not found. You can tap the map or enter manually.', false);
    },
    {
      enableHighAccuracy: true,
      timeout: 12000,
      maximumAge: 0
    }
  );
}

// Map Toggle
function toggleMap() {
  if (mapContainer.style.display === 'none' || !mapContainer.style.display) {
    mapContainer.style.display = 'block';
    mapToggleBtn.innerText = 'Hide Map';
    
    const lat = currentLat || 10.60665;
    const lon = currentLon || 76.21449;

    if (!mapInstance) {
      mapInstance = L.map('map-container').setView([lat, lon], 17);
      
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '© OpenStreetMap'
      }).addTo(mapInstance);

      mapMarker = L.marker([lat, lon], { draggable: true }).addTo(mapInstance);
      
      mapMarker.on('dragend', function(e) {
        const pt = e.target.getLatLng();
        currentLat = pt.lat;
        currentLon = pt.lng;
        manualCoordsInput.value = `${currentLat.toFixed(5)}, ${currentLon.toFixed(5)}`;
        gpsAccText.innerText = 'Adjusted via map';
      });

      mapInstance.on('click', function(e) {
        currentLat = e.latlng.lat;
        currentLon = e.latlng.lng;
        mapMarker.setLatLng(e.latlng);
        manualCoordsInput.value = `${currentLat.toFixed(5)}, ${currentLon.toFixed(5)}`;
        gpsAccText.innerText = 'Selected on map';
      });
    } else {
      setTimeout(() => {
        mapInstance.invalidateSize();
        if (currentLat && currentLon) {
          mapInstance.setView([currentLat, currentLon], 18);
          mapMarker.setLatLng([currentLat, currentLon]);
        }
      }, 100);
    }
  } else {
    mapContainer.style.display = 'none';
    mapToggleBtn.innerText = 'Adjust Pin on Map';
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
  if (currentUser && normalizeClientRole(currentUser.role) === 'rcsm') {
    showToast('RCSM accounts cannot enter or submit field survey records.', false);
    return;
  }

  if (!postInput.value.trim()) {
    showToast('Please enter KSEB Post Number', false);
    postInput.focus();
    return;
  }

  let latLongStr = '';
  if (currentLat && currentLon) {
    latLongStr = `${currentLat.toFixed(5)},${currentLon.toFixed(5)}`;
  } else if (manualCoordsInput.value && manualCoordsInput.value.includes(',')) {
    latLongStr = manualCoordsInput.value.trim();
  }

  const clientUuid = (typeof crypto !== 'undefined' && crypto.randomUUID) 
    ? crypto.randomUUID() 
    : ('rec_' + Date.now() + '_' + Math.random().toString(36).substring(2, 9));

  const olt = oltSelect.value;
  const port = portSelect.value;
  const enc = enclosureSelect.value;
  const eid = computeEnclosureId(olt, port, enc);

  // Condition 1: Can not able to select the same enclosure number multiple time in a same OLT port
  const isDuplicateEnclosure = records.some(r => 
    (r["OLT/Node  Name"] || r.olt_name) === olt && 
    (r["Port Number"] || r.port_number) === port && 
    (r["Enclosure Number"] || r.enclosure_number) === enc
  );
  if (isDuplicateEnclosure) {
    showToast(`❌ Enclosure ${enc} is already used in ${olt} (${port})! Duplicate enclosures on same Node port not allowed.`, false);
    enclosureSelect.focus();
    return;
  }

  // Condition 2: Can not able to select the same splitter id number under same Enclosure ID
  const isDuplicateSplitter = records.some(r => 
    (r["Enclosure ID"] || r.enclosure_id) === eid && 
    (r["Splitter ID"] || r.splitter_id) === splitterIdSelect.value
  );
  if (isDuplicateSplitter) {
    showToast(`❌ Splitter ${splitterIdSelect.value} is already used under Enclosure ${eid}! Duplicate Splitter ID under same Enclosure not allowed.`, false);
    splitterIdSelect.focus();
    return;
  }

  const now = new Date();
  const formattedDateTime = getFormattedDateTime(now);
  const isoTimestamp = now.toISOString();

  const colorCode = splitterColorSelect ? splitterColorSelect.value : '';
  const adlId = adlSubInput ? adlSubInput.value.trim() : '';
  const acsId = acsSubInput ? acsSubInput.value.trim() : '';

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
    "Splitter ID": splitterIdSelect.value,
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

  records.push(entry);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(records));
  updateRecordsBadge();
  updateSyncUI();
  showToast('Saved locally: ' + eid);

  // Clear pole-specific inputs
  postInput.value = '';
  landmarkInput.value = '';
  custCountInput.value = '0';
  if (adlSubInput) adlSubInput.value = '';
  if (acsSubInput) acsSubInput.value = '';

  // Refresh available enclosures & splitters for next entry
  updateAvailableEnclosures();
  updateAvailableSplitters();
  updateEnclosureId();

  // Trigger silent background sync if server is reachable
  syncWithServer(true);
}

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
      <td style="font-family: monospace; font-size: 0.75rem; color:#5f6368;">${timeDisplay}</td>
      <td class="gs-id-cell">${r["Enclosure ID"] || '-'}</td>
      <td><strong>${r["KSEB Post Number"] || '-'}</strong></td>
      <td>${r["Land Mark"] || '-'}</td>
      <td>${r["Center"] || '-'}</td>
      <td>${r["RT Room"] || '-'}</td>
      <td>${r["OLT/Node  Name"] || '-'} [${r["Port Number"] || '-'}]</td>
      <td style="font-family: monospace; font-size: 0.75rem;">${r["Lat /Long"] || '-'}</td>
      <td>${r["Splitter Ratio"] || '-'}</td>
      <td style="text-align:center;">${r["No: Of Customer Connected"] || 0}</td>
      <td><span style="background:#e8f0fe; color:#1a73e8; padding:2px 6px; border-radius:4px; font-size:0.75rem; font-weight:600;">${colorDisplay}</span></td>
      <td style="font-family: monospace; font-size: 0.75rem;">${adlDisplay}</td>
      <td style="font-family: monospace; font-size: 0.75rem;">${acsDisplay}</td>
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
      const exportUrl = `${serverUrl}/api/export-center-excel?center=${encodeURIComponent(targetCenter)}`;
      const res = await fetch(exportUrl);
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

      const item = document.createElement('div');
      item.className = 'record-item';
      item.innerHTML = `
        <div class="record-item-main">
          <div class="record-title">${r["Enclosure ID"]} — ${r["KSEB Post Number"]} ${syncBadge}</div>
          <div class="record-sub">${r["OLT/Node  Name"]} | Port: ${r["Port Number"]} | Ratio: ${r["Splitter Ratio"]}</div>
          <div class="record-sub">GPS: ${r["Lat /Long"] || 'No GPS'} | Cust: ${r["No: Of Customer Connected"]}</div>
        </div>
        <button class="record-del" onclick="deleteRecord(${realIndex})">✕</button>
      `;
      container.appendChild(item);
    });
  }

  modal.style.display = 'block';
}

function closeRecordsModal() {
  document.getElementById('records-modal').style.display = 'none';
}

function deleteRecord(index) {
  if (confirm('Delete this survey point?')) {
    records.splice(index, 1);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(records));
    updateRecordsBadge();
    updateSyncUI();
    updateAvailableEnclosures();
    updateAvailableSplitters();
    showToast('Record deleted');
  }
}

function clearAllRecords() {
  if (records.length === 0) return;
  if (confirm(`Are you sure you want to delete all ${records.length} records? Make sure you have exported to Excel first!`)) {
    records = [];
    localStorage.setItem(STORAGE_KEY, JSON.stringify(records));
    updateRecordsBadge();
    updateSyncUI();
    updateAvailableEnclosures();
    updateAvailableSplitters();
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
      fetchHierarchyFromServer();
      refreshCurrentUserProfile();
    } else {
      isServerReachable = false;
    }
  } catch (err) {
    isServerReachable = false;
  }
  updateSyncUI();
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
    const res = await fetch(`${serverUrl}/api/upload-hierarchy`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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
  const badge = document.getElementById('sync-status-badge');
  const dot = document.getElementById('sync-dot');
  const text = document.getElementById('sync-text');
  if (!badge) return;

  const unsyncedCount = records.filter(r => r.sync_status !== 'synced').length;

  if (isSyncing) {
    dot.style.background = '#38bdf8';
    text.innerText = 'Syncing...';
    return;
  }

  if (!isServerReachable) {
    dot.style.background = '#94a3b8';
    text.innerText = unsyncedCount > 0 ? `Offline (${unsyncedCount} unsynced)` : 'Offline (Local Safe)';
    badge.title = 'Offline mode. Data is stored safely on phone. Tap to retry server sync.';
  } else {
    if (unsyncedCount === 0) {
      dot.style.background = '#10b981';
      text.innerText = 'Synced ✓';
      badge.title = 'All records are synced with Ubuntu server!';
    } else {
      dot.style.background = '#f59e0b';
      text.innerText = `Sync (${unsyncedCount})`;
      badge.title = `${unsyncedCount} records ready to sync. Tap to sync now.`;
    }
  }
}

async function syncWithServer(silent = false) {
  if (isSyncing) return;
  
  const pendingRecords = records.filter(r => r.sync_status !== 'synced');
  if (pendingRecords.length === 0) {
    if (!silent) showToast('All records are already synced with Ubuntu server!');
    checkServerConnection();
    return;
  }

  isSyncing = true;
  updateSyncUI();

  try {
    const formattedRecords = pendingRecords.map(r => ({
      client_uuid: r.client_uuid,
      region: r.Region || 'Thrissur',
      center: r.Center,
      rt_room: r['RT Room'],
      technology: r['GPON/FTTH/WDM'],
      olt_name: r['OLT/Node  Name'],
      port_number: r['Port Number'],
      kseb_post_number: r['KSEB Post Number'],
      landmark: r['Land Mark'],
      enclosure_number: r['Enclosure Number'],
      enclosure_id: r['Enclosure ID'],
      lat_long: r['Lat /Long'],
      splitter_id: r['Splitter ID'],
      splitter_ratio: r['Splitter Ratio'],
      customers_connected: r['No: Of Customer Connected'] || 0,
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
    
    const res = await fetch(`${serverUrl}/api/sync`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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
      
      // Update local storage status
      records.forEach(r => {
        if (syncedIds.has(r.client_uuid)) {
          r.sync_status = 'synced';
        }
      });
      localStorage.setItem(STORAGE_KEY, JSON.stringify(records));
      isServerReachable = true;
      if (!silent) showToast(`Synced ${syncedIds.size} records with Ubuntu server!`);
    } else {
      isServerReachable = false;
      if (!silent) showToast('Server connection failed. Data is safe locally.', false);
    }
  } catch (err) {
    isServerReachable = false;
    if (!silent) showToast('Could not reach server. Data is stored safely on phone.', false);
  } finally {
    isSyncing = false;
    updateSyncUI();
  }
}

function triggerManualSync() {
  syncWithServer(false);
}

function configureServerUrl() {
  const current = localStorage.getItem('gpon_server_url') || serverUrl;
  const input = prompt('Enter Ubuntu Server Address (e.g. http://192.168.1.100:8000 or Cloudflare Tunnel URL):', current);
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
  showToast('Internet connected. Syncing with server...');
  syncWithServer(true);
});

window.addEventListener('offline', () => {
  isServerReachable = false;
  updateSyncUI();
});

// Init on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  // Ensure Submit button label is strictly set to 'Submit'
  const submitBtnEl = document.querySelector('.btn-add-row');
  if (submitBtnEl) {
    submitBtnEl.innerHTML = '<span>➕</span> Submit';
  }

  // Pre-fill / Restore credentials if remembered
  const isRemembered = localStorage.getItem('gpon_remember_creds') === 'true';
  const savedU = localStorage.getItem('gpon_remembered_username') || '';
  const savedP = localStorage.getItem('gpon_remembered_password') || '';
  const uInput = document.getElementById('login-username');
  const pInput = document.getElementById('login-password');
  const rCheckbox = document.getElementById('login-remember-me');
  if (uInput && savedU) uInput.value = savedU;
  if (pInput && savedP) pInput.value = savedP;
  if (rCheckbox && localStorage.getItem('gpon_remember_creds') !== null) {
    rCheckbox.checked = isRemembered;
  }

  // Check Login State
  if (!currentUser) {
    if (isRemembered && savedU && savedP) {
      // Auto-restore saved session seamlessly
      handleLogin();
    } else {
      document.getElementById('login-overlay').style.display = 'flex';
    }
  } else {
    document.getElementById('login-overlay').style.display = 'none';
    updateUserBar();
    refreshCurrentUserProfile();
  }

  initDropdowns();
  updateRecordsBadge();
  updateSyncUI();
  checkServerConnection();
  captureGPS(); // Automatically attempt GPS lock on app launch
  
  // Periodic background check for Node Master Data updates & record sync
  setInterval(() => {
    checkServerConnection();
    if (isServerReachable && records.some(r => r.sync_status !== 'synced')) {
      syncWithServer(true);
    }
  }, 10000); // Check every 10 seconds

  // Live auto-refresh when surveyor returns to the app
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      checkServerConnection();
      refreshCurrentUserProfile();
    }
  });
  window.addEventListener('focus', () => {
    checkServerConnection();
    refreshCurrentUserProfile();
  });
});
