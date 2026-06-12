#!/usr/bin/env node

/**
 * OXI E16 Scene Generator
 * Generates valid .oxie16 files from simplified scene definitions.
 */

const fs = require('fs');
const path = require('path');

// Instrument Definition Loader
function loadInstrumentDef(filePath, basePath = '.') {
  if (!filePath) return null;

  const searchPaths = [
    path.resolve(basePath, filePath),
    path.resolve('./instruments', filePath),
    path.resolve('.', filePath)
  ];

  let fullPath = null;
  for (const p of searchPaths) {
    if (fs.existsSync(p)) {
      fullPath = p;
      break;
    }
  }

  if (!fullPath) {
    console.warn(`Warning: Instrument definition not found: ${filePath}`);
    return null;
  }

  const def = JSON.parse(fs.readFileSync(fullPath, 'utf8'));
  const ccMap = new Map();
  const nrpnMap = new Map();

  for (const param of def.parameters || []) {
    if (param.type === 'cc') {
      ccMap.set(param.nr1, param);
    } else if (param.type === 'nrpn') {
      nrpnMap.set(`${param.nr1}:${param.nr2}`, param);
    }
  }

  return { ccMap, nrpnMap, raw: def };
}

// Templates
const DISABLED_PUSH = {
  instrument: 127, parameter: 0, type: 2, display: 0, mode: 2,
  channel: 1, lower: 0, upper: 127, nr1: 119, nr2: 1, output: 1
};

const DISABLED_TURN = {
  instrument: 127, parameter: 0, type: 0, display: 10, mode: 3,
  channel: 0, lower: 0, upper: 127, defaultValue: 0, nr1: 0, nr2: 0, output: 12
};

const DEFAULT_ICON = [56, 28, 84, 34, 146, 69, 146, 73, 130, 65, 68, 34, 56, 28, 0, 0, 0, 0, 56, 28, 68, 34, 162, 65, 146, 73, 130, 69, 68, 34, 56, 28];

// Validate icon
function validateIcon(icon) {
  if (!Array.isArray(icon)) {
    throw new Error('Icon must be an array');
  }
  if (icon.length !== 32) {
    throw new Error(`Icon must be exactly 32 bytes, got ${icon.length}`);
  }
  return icon;
}

// Color assignment
function getColorForParam(abbr, name) {
  const a = (abbr || '').toUpperCase();
  const n = (name || '').toLowerCase();

  // Filters - orange
  if (a.includes('CUT') || a.includes('RESO')) return 46;
  // Envelopes - salmon
  if (a.includes('ATK') || a.includes('DEC') || a.includes('SUS') || a.includes('REL')) return 55;
  // LFO - magenta
  if (a.includes('LFO') || a.includes('FREQ') && a.includes('LFO')) return 74;
  // Oscillators - blue
  if (a.includes('OSC') || a.includes('WAVE') || a.includes('DUTY')) return 5;
  // Effects - purple
  if (a.includes('REV') || a.includes('ECHO') || a.includes('CHO')) return 1;
  // Level - green
  if (a.includes('LEV') || a.includes('AMT')) return 93;
  // Default gray
  return 22;
}

// Build turn action
function buildTurn(def) {
  if (!def || def.type === 'empty') return DISABLED_TURN;

  const base = {
    instrument: 127,
    parameter: 0,
    display: def.display ?? 10,
    mode: def.mode ?? 3,
    channel: def.channel ?? 1,
    lower: def.lower ?? 0,
    upper: def.upper ?? 127,
    defaultValue: def.default ?? def.defaultValue ?? 0,
    output: def.output ?? 12
  };

  switch (def.type) {
    case 'cc':
      return { ...base, type: 0, nr1: def.cc, nr2: 0 };
    case 'nrpn':
      return { ...base, type: 11, nr1: def.lsb, nr2: def.msb };
    default:
      return DISABLED_TURN;
  }
}

// Build push action
function buildPushAction(def) {
  if (!def || def.type === 'empty') return DISABLED_PUSH;

  const base = {
    instrument: 127,
    parameter: 0,
    display: 0,
    mode: 2,
    channel: def.channel ?? 1,
    lower: 0,
    upper: 127,
    nr1: 119,
    nr2: 1,
    output: 1
  };

  switch (def.type) {
    case 'cc':
    case 'nrpn':
      return { ...base, type: 2 };  // Set to default
    default:
      return DISABLED_PUSH;
  }
}

// Build empty encoder
function emptyEncoder() {
  return {
    name: "",
    abbr: "",
    color: 22,
    push_action: DISABLED_PUSH,
    turn_actions: [DISABLED_TURN],
    color2: 0
  };
}

// Build encoder
function buildEncoder(def, pageDefaults = {}, instrumentDef = null) {
  if (!def || typeof def !== 'object' || !def.cc) {
    return emptyEncoder();
  }

  const type = pageDefaults.type ?? 'cc';
  const turnAction = buildTurn({ type, ...def });
  const pushAction = buildPushAction({ type, ...def });
  const color = getColorForParam(def.abbr, def.name);

  return {
    name: def.name ?? "",
    abbr: def.abbr ?? "",
    color,
    push_action: pushAction,
    turn_actions: [turnAction],
    color2: 0
  };
}

// Build page
function buildPage(def, instrumentDef = null) {
  if (!def) return emptyPage();

  const encoders = (def.encoders || []).map(e => buildEncoder(e, def, instrumentDef));
  while (encoders.length < 16) encoders.push(emptyEncoder());

  return {
    title: def.title ?? "",
    output: def.output ?? 0,
    channel: def.channel ?? 1,
    encoders
  };
}

// Empty page
function emptyPage() {
  const encoders = [];
  for (let i = 0; i < 16; i++) encoders.push(emptyEncoder());
  return { title: "", output: 0, channel: 1, encoders };
}

// Build scene
function buildScene(def, basePath = '.') {
  const instrumentDef = def.instrument ? loadInstrumentDef(def.instrument, basePath) : null;
  const pages = (def.pages || []).map(p => buildPage(p, instrumentDef));

  while (pages.length < 12) pages.push(emptyPage());

  const icon = def.icon ? validateIcon(def.icon) : DEFAULT_ICON;

  return {
    title: def.title ?? "New Scene",
    icon,
    selectedPreset: def.selectedPreset ?? 0,
    code: { code: def.code ?? "" },
    pages
  };
}

// CLI
function main() {
  const args = process.argv.slice(2);

  if (args.length === 0) {
    console.log(`OXI E16 Scene Generator
Usage: node generate-scene.js <input.json> [output.oxie16]`);
    process.exit(1);
  }

  const inputFile = args[0];
  const outputFile = args[1] || inputFile.replace('.json', '.oxie16');

  try {
    const input = JSON.parse(fs.readFileSync(inputFile, 'utf8'));
    const scene = buildScene(input, path.dirname(inputFile));
    fs.writeFileSync(outputFile, JSON.stringify(scene, null, 2));
    console.log(`✓ Generated: ${outputFile}`);
  } catch (err) {
    console.error(`Error: ${err.message}`);
    process.exit(1);
  }
}

main();
