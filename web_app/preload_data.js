// Preloaded Network Hierarchy & Metadata for GPON Mapping
const DEFAULT_PRELOAD = {
  regions: ["Thrissur", "Palakkad", "Ernakulam", "Malappuram"],
  technologies: ["GPON", "FTTH", "WDM", "EDFA"],
  hierarchy: {},
  enclosures: Array.from({length: 20}, (_, i) => `E${i + 1}`),
  splitters: ["S1", "S2", "S3", "S4"],
  olt_types: ["8P", "16P", "32P"],
  ports_by_type: {
    "8P": Array.from({length: 8}, (_, i) => `P${i + 1}`),
    "16P": Array.from({length: 16}, (_, i) => `P${i + 1}`),
    "32P": Array.from({length: 32}, (_, i) => `P${i + 1}`)
  },
  ratios: ["1:2", "1:4", "1:8", "1:16", "1:32"],
  color_codes_by_ratio: {
    "1:2": ["Out 1 - Blue", "Out 2 - Orange"],
    "1:4": ["Out 1 - Blue", "Out 2 - Orange", "Out 3 - Green", "Out 4 - Brown"],
    "1:8": [
      "Out 1 - Blue", "Out 2 - Orange", "Out 3 - Green", "Out 4 - Brown",
      "Out 5 - Slate", "Out 6 - White", "Out 7 - Red", "Out 8 - Black"
    ],
    "1:16": [
      "Out 1 - Blue", "Out 2 - Orange", "Out 3 - Green", "Out 4 - Brown",
      "Out 5 - Slate", "Out 6 - White", "Out 7 - Red", "Out 8 - Black",
      "Out 9 - Yellow", "Out 10 - Violet", "Out 11 - Rose", "Out 12 - Aqua",
      "Out 13 - Blue", "Out 14 - Orange", "Out 15 - Green", "Out 16 - Brown"
    ],
    "1:32": [
      "Out 1 - Blue", "Out 2 - Orange", "Out 3 - Green", "Out 4 - Brown",
      "Out 5 - Slate", "Out 6 - White", "Out 7 - Red", "Out 8 - Black",
      "Out 9 - Yellow", "Out 10 - Violet", "Out 11 - Rose", "Out 12 - Aqua"
    ]
  },
  color_codes: [
    "Blue", "Orange", "Green", "Brown", "Slate", "White",
    "Red", "Black", "Yellow", "Violet", "Rose", "Aqua"
  ]
};

// Check for user-uploaded custom hierarchy from Excel / Local Storage
try {
  const savedHierarchy = localStorage.getItem('gpon_custom_hierarchy');
  if (savedHierarchy) {
    const parsed = JSON.parse(savedHierarchy);
    if (parsed && typeof parsed === 'object') {
      DEFAULT_PRELOAD.hierarchy = parsed;
    }
  }
} catch (e) {
  console.warn('Could not load custom hierarchy:', e);
}

// Helper to auto-calculate Enclosure ID from OLT, Port, and Enclosure
function computeEnclosureId(oltName, port, enclosure) {
  if (!oltName) return "";
  const s = oltName.trim();
  let oltCode = "";

  // Standard pattern: e.g. CKY/116/OLT 01/Potta-1, THN156 OLT53, TMM/25/OLT-08, OPM/84/OPM/OLT- 07
  const m = s.match(/([A-Za-z]{2,5})[\/\-_ ]*(\d+)[\/\-_ ]*(?:[A-Za-z]{2,5}[\/\-_ ]*)?(?:8\s*PORT\s+)?OLT[\/\-_ ]*0*(\d+)/i);
  if (m) {
    const pref = m[1].toUpperCase();
    const site = m[2];
    const oltNum = m[3].padStart(2, '0');
    oltCode = `${pref}${site}OLT${oltNum}`;
  } else if (/OLT[- ]*HEADEND/i.test(s)) {
    const m2 = s.match(/([A-Za-z]{2,5})[\/\-_ ]*(\d+)/i);
    const mNum = s.match(/HEADEND\s*(\d+)/i);
    const oltNum = mNum ? mNum[1].padStart(2, '0') : '01';
    const pref = m2 ? m2[1].toUpperCase() : 'OLT';
    const site = m2 ? m2[2] : '01';
    oltCode = `${pref}${site}OLT${oltNum}`;
  } else if (/AMALA[- ]*P1/i.test(s)) {
    const m3 = s.match(/([A-Za-z]{2,5})[\/\-_ ]*(\d+)/i);
    oltCode = m3 ? `${m3[1].toUpperCase()}${m3[2]}OLT01` : 'THN77OLT01';
  } else {
    oltCode = s.replace(/[^A-Za-z0-9]/g, '').toUpperCase().slice(0, 12);
  }

  const portCode = (port || "P1").trim().toUpperCase().replace(/[\s,]+/g, '');
  const encCode = (enclosure || "E1").trim().toUpperCase();

  return `${oltCode}${portCode}${encCode}`;
}
