// Preloaded Network Hierarchy & Metadata for GPON Mapping
const DEFAULT_PRELOAD = {
  regions: ["Thrissur", "Palakkad", "Ernakulam", "Malappuram"],
  technologies: ["GPON", "FTTH", "WDM"],
  hierarchy: {
    "Thrissur North": {
      "Mulamkunnathukavu": {
        "THN156 OLT53 Mulamkunnathukavu": ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"],
        "THN-156-OLT-53- Mulamkunnathukavu": ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"]
      }
    },
    "Thathamangalm": {
      "Kollengode": {
        "TMM/25/OLT-08-KOLLEMGODE": ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"]
      }
    }
  },
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
    "1:2": ["Port 1 - Blue", "Port 2 - Orange"],
    "1:4": ["Port 1 - Blue", "Port 2 - Orange", "Port 3 - Green", "Port 4 - Brown"],
    "1:8": [
      "Port 1 - Blue", "Port 2 - Orange", "Port 3 - Green", "Port 4 - Brown",
      "Port 5 - Slate", "Port 6 - White", "Port 7 - Red", "Port 8 - Black"
    ],
    "1:16": [
      "Port 1 - Blue", "Port 2 - Orange", "Port 3 - Green", "Port 4 - Brown",
      "Port 5 - Slate", "Port 6 - White", "Port 7 - Red", "Port 8 - Black",
      "Port 9 - Yellow", "Port 10 - Violet", "Port 11 - Rose", "Port 12 - Aqua",
      "Port 13 - Blue", "Port 14 - Orange", "Port 15 - Green", "Port 16 - Brown"
    ],
    "1:32": [
      "Port 1 - Blue", "Port 2 - Orange", "Port 3 - Green", "Port 4 - Brown",
      "Port 5 - Slate", "Port 6 - White", "Port 7 - Red", "Port 8 - Black",
      "Port 9 - Yellow", "Port 10 - Violet", "Port 11 - Rose", "Port 12 - Aqua"
    ]
  },
  color_codes: [
    "Blue", "Orange", "Green", "Brown", "Slate", "White",
    "Red", "Black", "Yellow", "Violet", "Rose", "Aqua"
  ]
};

// Check for user-uploaded custom hierarchy from Excel
try {
  const savedHierarchy = localStorage.getItem('gpon_custom_hierarchy');
  if (savedHierarchy) {
    const parsed = JSON.parse(savedHierarchy);
    if (parsed && typeof parsed === 'object') {
      DEFAULT_PRELOAD.hierarchy = Object.assign(DEFAULT_PRELOAD.hierarchy, parsed);
    }
  }
} catch (e) {
  console.warn('Could not load custom hierarchy:', e);
}

// Helper to auto-calculate Enclosure ID from OLT, Port, and Enclosure
function computeEnclosureId(oltName, port, enclosure) {
  if (!oltName) return "";
  
  // Extract alphanumeric up to OLT number (e.g. THN156 OLT53 -> THN156OLT53, TMM/25/OLT-08 -> TMM25OLT08)
  const regex = /([A-Za-z]+[\s\/\-_]*\d+[\s\/\-_]*OLT[\s\/\-_]*\d+)/i;
  const match = oltName.match(regex);
  let oltCode = "";
  if (match) {
    oltCode = match[1].replace(/[\s\/\-_]/g, '').toUpperCase();
  } else {
    oltCode = oltName.replace(/[\s\/\-_]/g, '').toUpperCase().slice(0, 12);
  }
  
  const portCode = (port || "").trim().toUpperCase();
  const encCode = (enclosure || "").trim().toUpperCase();
  
  return `${oltCode}${portCode}${encCode}`;
}
