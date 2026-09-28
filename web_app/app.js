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
if (!serverUrl) {
  // Default to current host if served via HTTP/S, or default port 8000
  serverUrl = (window.location.protocol.startsWith('http') && window.location.port !== '5500') 
    ? window.location.origin 
    : 'http://localhost:8000';
  localStorage.setItem('gpon_server_url', serverUrl);
}

let isSyncing = false;
let isServerReachable = false;

// User Session & Authentication
let currentUser = JSON.parse(localStorage.getItem('gpon_logged_in_user') || 'null');

function updateUserBar() {
  const userBar = document.getElementById('user-bar');
  const nameEl = document.getElementById('logged-user-name');
  const centerEl = document.getElementById('logged-user-center');
  if (currentUser) {
    nameEl.innerText = currentUser.full_name || currentUser.username;
    centerEl.innerText = currentUser.assigned_center || 'ALL';
    userBar.style.display = 'flex';
  } else {
    userBar.style.display = 'none';
  }
}

async function handleLogin(e) {
  if (e) e.preventDefault();
  const u = document.getElementById('login-username').value.trim();
  const p = document.getElementById('login-password').value.trim();
  
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
      setCurrentUser(data.user);
      showToast(`Welcome, ${data.user.full_name}!`);
      return;
    }
  } catch (err) {
    console.log('Server login unreachable, trying offline fallback...');
  }

  // 2. Offline fallback credentials for remote field areas
  const offlineUsers = {
    'thrissur_agent': { username: 'thrissur_agent', full_name: 'Thrissur Survey Agent', assigned_center: 'Thrissur North', role: 'field_agent' },
    'tmm_agent': { username: 'tmm_agent', full_name: 'Thathamangalam Agent', assigned_center: 'Thathamangalm', role: 'field_agent' },
    'admin': { username: 'admin', full_name: 'Central Administrator', assigned_center: 'ALL', role: 'admin' }
  };

  if (offlineUsers[u] && (p === '1234' || p === 'admin123')) {
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

// DOM Elements
const centerSelect = document.getElementById('center-select');
const rtRoomSelect = document.getElementById('rtroom-select');
const techSelect = document.getElementById('tech-select');
const oltSelect = document.getElementById('olt-select');
const portSelect = document.getElementById('port-select');
const postInput = document.getElementById('post-input');
const landmarkInput = document.getElementById('landmark-input');
const enclosureSelect = document.getElementById('enclosure-select');
const enclosureIdPreview = document.getElementById('enclosure-id-preview');
const splitterIdSelect = document.getElementById('splitter-id-select');
const splitterRatioSelect = document.getElementById('splitter-ratio-select');
const custCountInput = document.getElementById('cust-count-input');

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

// Populate Dropdowns with Center Filtering for Current User
function initDropdowns() {
  // Center
  centerSelect.innerHTML = '';
  let centers = Object.keys(DEFAULT_PRELOAD.hierarchy);
  
  // Filter centers based on logged-in user assignment
  if (currentUser && currentUser.assigned_center && currentUser.assigned_center !== 'ALL') {
    const assigned = currentUser.assigned_center;
    if (DEFAULT_PRELOAD.hierarchy[assigned]) {
      centers = [assigned];
    } else {
      // If custom center, add it to hierarchy
      DEFAULT_PRELOAD.hierarchy[assigned] = {};
      centers = [assigned];
    }
    centerSelect.disabled = true; // Lock dropdown to assigned center
  } else {
    centerSelect.disabled = false;
  }

  centers.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c;
    opt.innerText = c;
    centerSelect.appendChild(opt);
  });

  // Technologies
  techSelect.innerHTML = '';
  DEFAULT_PRELOAD.technologies.forEach(t => {
    const opt = document.createElement('option');
    opt.value = t;
    opt.innerText = t;
    techSelect.appendChild(opt);
  });

  // Enclosures
  enclosureSelect.innerHTML = '';
  DEFAULT_PRELOAD.enclosures.forEach(e => {
    const opt = document.createElement('option');
    opt.value = e;
    opt.innerText = e;
    enclosureSelect.appendChild(opt);
  });

  // Splitter ID
  splitterIdSelect.innerHTML = '';
  DEFAULT_PRELOAD.splitters.forEach(s => {
    const opt = document.createElement('option');
    opt.value = s;
    opt.innerText = s;
    splitterIdSelect.appendChild(opt);
  });

  // Splitter Ratio
  splitterRatioSelect.innerHTML = '';
  DEFAULT_PRELOAD.ratios.forEach(r => {
    const opt = document.createElement('option');
    opt.value = r;
    opt.innerText = r;
    splitterRatioSelect.appendChild(opt);
  });

  onCenterChange();
}

function onCenterChange() {
  const c = centerSelect.value;
  rtRoomSelect.innerHTML = '';
  const rtRooms = DEFAULT_PRELOAD.hierarchy[c] ? Object.keys(DEFAULT_PRELOAD.hierarchy[c]) : [];
  rtRooms.forEach(rt => {
    const opt = document.createElement('option');
    opt.value = rt;
    opt.innerText = rt;
    rtRoomSelect.appendChild(opt);
  });
  onRTRoomChange();
}

function onRTRoomChange() {
  const c = centerSelect.value;
  const rt = rtRoomSelect.value;
  oltSelect.innerHTML = '';
  const olts = (DEFAULT_PRELOAD.hierarchy[c] && DEFAULT_PRELOAD.hierarchy[c][rt]) 
    ? Object.keys(DEFAULT_PRELOAD.hierarchy[c][rt]) 
    : [];
  olts.forEach(o => {
    const opt = document.createElement('option');
    opt.value = o;
    opt.innerText = o;
    oltSelect.appendChild(opt);
  });
  onOLTChange();
}

function onOLTChange() {
  const c = centerSelect.value;
  const rt = rtRoomSelect.value;
  const o = oltSelect.value;
  portSelect.innerHTML = '';
  const ports = (DEFAULT_PRELOAD.hierarchy[c] && DEFAULT_PRELOAD.hierarchy[c][rt] && DEFAULT_PRELOAD.hierarchy[c][rt][o])
    ? DEFAULT_PRELOAD.hierarchy[c][rt][o]
    : ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"];
  ports.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p;
    opt.innerText = p;
    portSelect.appendChild(opt);
  });
  updateEnclosureId();
}

// Event Listeners for Cascading
centerSelect.addEventListener('change', onCenterChange);
rtRoomSelect.addEventListener('change', onRTRoomChange);
oltSelect.addEventListener('change', onOLTChange);
portSelect.addEventListener('change', updateEnclosureId);
enclosureSelect.addEventListener('change', updateEnclosureId);

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

  const entry = {
    client_uuid: clientUuid,
    sync_status: 'pending',
    id: Date.now(),
    Region: "Thrissur",
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
    timestamp: new Date().toISOString()
  };

  records.push(entry);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(records));
  updateRecordsBadge();
  updateSyncUI();
  showToast('Saved locally: ' + eid);

  // Auto-increment Enclosure number for convenience (e.g. E1 -> E2)
  const curIdx = DEFAULT_PRELOAD.enclosures.indexOf(enc);
  if (curIdx >= 0 && curIdx < DEFAULT_PRELOAD.enclosures.length - 1) {
    enclosureSelect.value = DEFAULT_PRELOAD.enclosures[curIdx + 1];
  }
  
  // Clear landmark and post number if moving to new post
  postInput.value = '';
  landmarkInput.value = '';
  custCountInput.value = '0';
  updateEnclosureId();

  // Trigger silent background sync if server is reachable
  syncWithServer(true);
}

function updateRecordsBadge() {
  recordsBadge.innerText = records.length;
}

// Export to Excel (.xlsx)
function exportToExcel() {
  if (records.length === 0) {
    showToast('No records to export yet!', false);
    return;
  }

  // Column headers matching target template
  const headers = [
    'Region', 'Center', 'RT Room', 'GPON/FTTH/WDM', 'OLT/Node  Name', 
    'Port Number', 'KSEB Post Number', 'Land Mark', 'Enclosure Number', 
    'Enclosure ID', 'Lat /Long', 'Splitter ID', 'Splitter Ratio', 'No: Of Customer Connected'
  ];

  const rows = [
    [], // Row 1 blank like original
    headers
  ];

  records.forEach(r => {
    rows.push([
      r["Region"] || "Thrissur",
      r["Center"],
      r["RT Room"],
      r["GPON/FTTH/WDM"],
      r["OLT/Node  Name"],
      r["Port Number"],
      r["KSEB Post Number"],
      r["Land Mark"],
      r["Enclosure Number"],
      r["Enclosure ID"],
      r["Lat /Long"],
      r["Splitter ID"],
      r["Splitter Ratio"],
      r["No: Of Customer Connected"]
    ]);
  });

  const ws = XLSX.utils.aoa_to_sheet(rows);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Sheet1");

  const today = new Date().toISOString().slice(0, 10);
  XLSX.writeFile(wb, `GPON_Survey_Export_${today}.xlsx`);
  showToast('Excel downloaded!');
}

// Export to CSV
function exportToCSV() {
  if (records.length === 0) {
    showToast('No records to export yet!', false);
    return;
  }

  const headers = [
    'Region', 'Center', 'RT Room', 'GPON/FTTH/WDM', 'OLT/Node  Name', 
    'Port Number', 'KSEB Post Number', 'Land Mark', 'Enclosure Number', 
    'Enclosure ID', 'Lat /Long', 'Splitter ID', 'Splitter Ratio', 'No: Of Customer Connected'
  ];

  let csvContent = headers.join(',') + '\n';

  records.forEach(r => {
    const row = [
      `"${r.Region || 'Thrissur'}"`,
      `"${r.Center || ''}"`,
      `"${r['RT Room'] || ''}"`,
      `"${r['GPON/FTTH/WDM'] || ''}"`,
      `"${r['OLT/Node  Name'] || ''}"`,
      `"${r['Port Number'] || ''}"`,
      `"${r['KSEB Post Number'] || ''}"`,
      `"${r['Land Mark'] || ''}"`,
      `"${r['Enclosure Number'] || ''}"`,
      `"${r['Enclosure ID'] || ''}"`,
      `"${r['Lat /Long'] || ''}"`,
      `"${r['Splitter ID'] || ''}"`,
      `"${r['Splitter Ratio'] || ''}"`,
      r['No: Of Customer Connected'] || 0
    ];
    csvContent += row.join(',') + '\n';
  });

  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  const today = new Date().toISOString().slice(0, 10);
  a.download = `GPON_Survey_Export_${today}.csv`;
  a.click();
  URL.revokeObjectURL(url);
  showToast('CSV downloaded!');
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
    openRecordsModal();
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
    openRecordsModal();
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
    } else {
      isServerReachable = false;
    }
  } catch (err) {
    isServerReachable = false;
  }
  updateSyncUI();
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
      device_id: deviceId,
      surveyor_username: r.surveyor_username || (currentUser ? currentUser.username : ''),
      surveyor_name: r.surveyor_name || (currentUser ? currentUser.full_name : ''),
      created_at: r.timestamp
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
      .then(reg => console.log('SW registered successfully:', reg.scope))
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
  // Check Login State
  if (!currentUser) {
    document.getElementById('login-overlay').style.display = 'flex';
  } else {
    document.getElementById('login-overlay').style.display = 'none';
    updateUserBar();
  }

  initDropdowns();
  updateRecordsBadge();
  updateSyncUI();
  checkServerConnection();
  captureGPS(); // Automatically attempt GPS lock on app launch
  
  // Periodic background sync attempt every 25 seconds
  setInterval(() => {
    checkServerConnection();
    if (isServerReachable && records.some(r => r.sync_status !== 'synced')) {
      syncWithServer(true);
    }
  }, 25000);
});
