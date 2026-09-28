// Preloaded Network Hierarchy & Metadata for GPON Mapping
const DEFAULT_PRELOAD = {
  regions: ["Thrissur", "Palakkad", "Ernakulam", "Malappuram"],
  technologies: ["GPON", "FTTH", "WDM"],
  hierarchy: {
    "Thrissur North": {
      "Mulamkunnathukavu": {
        "THN156 OLT53 Mulamkunnathukavu": ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"]
      }
    },
    "Thathamangalm": {
      "Kollengode": {
        "TMM/25/OLT-08-KOLLEMGODE": ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"]
      }
    }
  },
  enclosures: ["E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9", "E10", "E11", "E12", "E13", "E14", "E15", "E16"],
  splitters: ["S1", "S2", "S3", "S4"],
  ratios: ["1:8", "1:4", "1:16", "1:32", "1:2"]
};

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
