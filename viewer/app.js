/* ===== state ===== */
let DATA = null, VIEW_ROWS = [], VIEW_COLS = [], CELLS = new Map(), SEL = null;
let FILTERS = { type: 'g', op: 'and', items: [], on: true };
let SORTS = [], QUICK = '', VIS = new Set(), PRESET = 'points', CSEARCH = '';
const $ = (id) => document.getElementById(id);

const GROUPS = { 'Must-have': '#2563eb', 'Exclusion': '#dc2626', 'Fit': '#15803d', 'Buying signal': '#7c3aed', 'Weak fit': '#b45309' };
const OVERVIEW = ['ICP', 'ICP status', 'Lead score', 'Lead tier', 'Why', 'Segment', 'Vertical', 'Funding stage', 'Legal entity type', 'Industry', 'Number of Employees', 'Website'];
const CONTEXT = {
  'ICP': ['ICP status', 'Why'], 'ICP status': ['ICP', 'Why'], 'Why': ['ICP', 'ICP status'],
  'Lead score': ['Lead tier', 'Reasoning', 'Score breakdown'], 'Lead tier': ['Lead score', 'Reasoning', 'Score breakdown'],
  'Reasoning': ['Lead score', 'Lead tier'], 'Score breakdown': ['Lead score', 'Lead tier', 'Reasoning'],
};
const OPS = [['eq', 'equal to'], ['neq', 'not equal to'], ['contains', 'contains'], ['ncontains', 'does not contain'],
  ['starts', 'starts with'], ['empty', 'is empty'], ['nempty', 'is not empty'], ['gt', 'greater than'], ['lt', 'less than']];
const NO_VALUE = new Set(['empty', 'nempty']);

function el(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) { if (k === 'class') e.className = v; else e[k] = v; }
  for (const k of kids) e.append(k);
  return e;
}
function btn(text, cls, fn) { return el('button', { class: cls, textContent: text, onclick: fn }); }
function isUrl(s) { return /^https?:\/\//i.test(s); }
function parseCol(c) {
  const m = c.match(/^(Must-have|Exclusion|Fit|Buying signal|Weak fit): (.*)$/);
  return m ? { group: m[1], name: m[2] } : { group: '', name: c };
}
function toast(msg) { const t = el('div', { class: 'toast', textContent: msg }); document.body.append(t); setTimeout(() => t.remove(), 2200); }
function save(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } }
function load(k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } }

/* ===== loading ===== */
/* ===== password-protected static mode (GitHub Pages): data.enc.json is decrypted in the browser ===== */
const STATIC = window.VIEWER_STATIC || null;
let VAULT = null;
const b64 = (s) => Uint8Array.from(atob(s), c => c.charCodeAt(0));

async function unlock(user, pass) {
  let f;
  try { f = await (await fetch(STATIC.data, { cache: 'no-store' })).json(); }
  catch (e) { throw new Error('data'); }  // file missing or unreadable: not a wrong login
  const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(user.normalize('NFC') + '\n' + pass.normalize('NFC')),
    'PBKDF2', false, ['deriveKey']);
  const key = await crypto.subtle.deriveKey({ name: 'PBKDF2', salt: b64(f.salt), iterations: f.iter, hash: 'SHA-256' },
    base, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
  const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: b64(f.iv) }, key, b64(f.data));  // throws on a wrong login
  const text = await new Response(new Blob([plain]).stream().pipeThrough(new DecompressionStream('gzip'))).text();
  return JSON.parse(text);
}

function showLogin() {
  $('lock').hidden = false;
  $('login').hidden = false;
  $('gridwrap').replaceChildren();
  const form = $('loginform'), err = $('loginerr'), go = $('logingo');
  $('loginuser').focus();
  form.onsubmit = async (e) => {
    e.preventDefault();
    err.textContent = ''; go.disabled = true; go.textContent = 'Checking…';
    try {
      VAULT = await unlock($('loginuser').value.trim(), $('loginpass').value);
      $('loginpass').value = '';
      $('login').hidden = true;
      fillLists(VAULT.lists);
    } catch (x) {
      err.textContent = x.message === 'data' ? 'Could not load the data file. Try again later.' : 'Wrong username or password.';
      $('loginpass').select();
    }
    go.disabled = false; go.textContent = 'Open';
  };
}

function fillLists(lists) {
  const sel = $('list');
  sel.replaceChildren(...lists.map(l => el('option', { value: l.list, textContent: `${l.list}  ·  ${l.state}` })));
  const saved = new URLSearchParams(location.search).get('list') || load('viewer.list');
  if (saved && lists.some(l => l.list === saved)) sel.value = saved;
  if (lists.length) loadTable(sel.value);
  else $('gridwrap').replaceChildren(el('div', { class: 'empty' }, el('b', { textContent: 'No lists yet' }), 'Enrich a list first; it will show up here.'));
}

async function loadLists() {
  if (STATIC) { showLogin(); return; }
  let lists;
  try { lists = await (await fetch('/api/lists')).json(); }
  catch (e) { $('gridwrap').replaceChildren(el('div', { class: 'empty' }, el('b', { textContent: 'Cannot reach the viewer server' }), 'Start it with: .venv/bin/python viewer/server.py')); return; }
  fillLists(lists);
}

async function loadTable(name) {
  save('viewer.list', name);
  $('gridwrap').replaceChildren(el('div', { class: 'empty', textContent: 'Loading…' }));
  closePanel(); closePops();
  FILTERS = { type: 'g', op: 'and', items: [], on: true }; SORTS = []; QUICK = ''; $('q').value = '';
  let d;
  try { d = STATIC ? (VAULT.tables[name] || { error: 'List not found' }) : await (await fetch('/api/table?list=' + encodeURIComponent(name))).json(); }
  catch (e) { d = { error: String(e) }; }
  if (d.error) { $('gridwrap').replaceChildren(el('div', { class: 'empty' }, el('b', { textContent: 'Could not open this list' }), d.error)); return; }
  DATA = d;
  const savedVis = load('viewer.cols.' + name);
  if (savedVis && savedVis.cols) { VIS = new Set(savedVis.cols.filter(c => DATA.columns.includes(c))); PRESET = savedVis.preset || 'custom'; }
  else applyPreset('points', false);
  drawStats(); drawQuick(); drawFilters(); drawSorts(); drawCols(); render();
}

function drawStats() {
  const c = { YES: 0, NO: 0, PENDING: 0, strong: 0 };
  for (const r of DATA.rows) {
    const v = r.cells['ICP']?.value; if (v in c) c[v]++;
    if (r.cells['Lead tier']?.value === 'Strong fit') c.strong++;
  }
  const s = (n, t) => el('span', { class: 'stat' }, el('b', { textContent: String(n) }), t);
  $('stats').replaceChildren(s(DATA.rows.length, 'companies'), s(c.YES, 'ICP YES'), s(c.NO, 'NO'), s(c.PENDING, 'pending'), s(c.strong, 'strong fit'));
}
function drawQuick() {
  const opts = [['', 'All'], ['YES', 'ICP YES'], ['NO', 'NO'], ['PENDING', 'Pending']];
  $('quick').replaceChildren(...opts.map(([v, t]) => el('button', { class: QUICK === v ? 'on' : '', textContent: t, onclick: () => { QUICK = v; drawQuick(); render(); } })));
}

/* ===== columns ===== */
function applyPreset(p, redraw = true) {
  PRESET = p;
  const ans = DATA.answer_columns, all = DATA.columns.filter(c => c !== 'Company Name');
  if (p === 'points') VIS = new Set(['ICP', 'Lead score', 'Lead tier', ...DATA.columns.filter(c => ans.includes(c))]);
  else if (p === 'overview') VIS = new Set(OVERVIEW.filter(c => DATA.columns.includes(c)));
  else VIS = new Set(all);
  if (redraw) { persistCols(); drawCols(); render(); }
}
function persistCols() { save('viewer.cols.' + DATA.list, { cols: [...VIS], preset: PRESET }); }
function drawCols() {
  const pop = $('cpop'); pop.replaceChildren();
  const all = DATA.columns.filter(c => c !== 'Company Name');
  const seg = el('div', { class: 'seg' },
    ...[['points', 'Data points'], ['overview', 'Overview'], ['all', 'Everything']].map(([k, t]) =>
      el('button', { class: PRESET === k ? 'on' : '', textContent: t, onclick: () => applyPreset(k) })));
  const search = el('input', { type: 'search', placeholder: 'Find a column', value: CSEARCH });
  search.oninput = () => { CSEARCH = search.value; drawCols(); const s = $('cpop').querySelector('input'); s.focus(); s.setSelectionRange(s.value.length, s.value.length); };
  pop.append(el('div', { class: 'crow' }, seg, search));
  const groups = {};
  for (const c of all) {
    const { group, name } = parseCol(c);
    if (CSEARCH && !c.toLowerCase().includes(CSEARCH.toLowerCase())) continue;
    (groups[group || 'Company info'] ||= []).push([c, name]);
  }
  for (const [g, cols] of Object.entries(groups)) {
    const box = el('div', { class: 'cgroup' });
    box.style.setProperty('--gc', GROUPS[g] || '');
    const gcb = el('input', { type: 'checkbox', checked: cols.every(([c]) => VIS.has(c)) });
    gcb.onchange = () => { cols.forEach(([c]) => gcb.checked ? VIS.add(c) : VIS.delete(c)); PRESET = 'custom'; persistCols(); drawCols(); render(); };
    box.append(el('div', { class: 'ch' }, gcb, el('span', { textContent: `${g} (${cols.filter(([c]) => VIS.has(c)).length}/${cols.length})` })));
    for (const [c, name] of cols) {
      const cb = el('input', { type: 'checkbox', checked: VIS.has(c) });
      cb.onchange = () => { cb.checked ? VIS.add(c) : VIS.delete(c); PRESET = 'custom'; persistCols(); drawCols(); render(); };
      box.append(el('label', { class: 'item' }, cb, name));
    }
    pop.append(box);
  }
  if (!Object.keys(groups).length) pop.append(el('div', { class: 'hintp', textContent: 'No column matches.' }));
  $('ccount').textContent = `${VIS.size}/${all.length}`;
}

/* ===== filtering ===== */
function newFilter() { return { type: 'f', col: DATA.columns.includes('ICP') ? 'ICP' : DATA.columns[0], cmp: 'eq', val: '', on: true }; }
function active(n) { if (!n.on) return false; return n.type === 'g' ? n.items.some(active) : (NO_VALUE.has(n.cmp) || n.val !== ''); }
function matchFilter(f, row) {
  const v = String(row.cells[f.col]?.value ?? '').toLowerCase(), t = f.val.toLowerCase();
  switch (f.cmp) {
    case 'eq': return v === t; case 'neq': return v !== t;
    case 'contains': return v.includes(t); case 'ncontains': return !v.includes(t);
    case 'starts': return v.startsWith(t);
    case 'empty': return v === ''; case 'nempty': return v !== '';
    case 'gt': { const a = parseFloat(v), b = parseFloat(t); return !isNaN(a) && !isNaN(b) && a > b; }
    case 'lt': { const a = parseFloat(v), b = parseFloat(t); return !isNaN(a) && !isNaN(b) && a < b; }
  }
  return true;
}
function evalNode(n, row) {
  if (n.type === 'f') return matchFilter(n, row);
  const items = n.items.filter(active);
  if (!items.length) return true;
  return n.op === 'and' ? items.every(x => evalNode(x, row)) : items.some(x => evalNode(x, row));
}
function countActive(n) { if (!n.on) return 0; return n.type === 'g' ? n.items.reduce((s, x) => s + countActive(x), 0) : (active(n) ? 1 : 0); }
function distinct(col) {
  const s = new Set();
  for (const r of DATA.rows) { const v = r.cells[col]?.value; if (v !== '' && v != null) { s.add(v); if (s.size > 12) return null; } }
  return [...s].sort();
}
function badge(id, n) { $(id).hidden = !n; $(id).textContent = n; }

function drawFilters() {
  const pop = $('fpop'), root = FILTERS; pop.replaceChildren();
  if (!root.items.length) pop.append(el('div', { class: 'hintp', textContent: 'No filters yet. Rows filter as you type a value.' }));
  root.items.forEach((n, i) => pop.append(nodeEl(n, root, i)));
  pop.append(el('div', { class: 'fbar' },
    btn('+ Add filter', 'fbtn', () => { root.items.push(newFilter()); drawFilters(); }),
    btn('+ Add filter group', 'fbtn group', () => { root.items.push({ type: 'g', op: 'and', items: [newFilter()], on: true }); drawFilters(); }),
    btn('✕ Clear filters', 'fbtn clear', () => { root.items = []; drawFilters(); render(); })));
  badge('fcount', countActive(root));
  $('fbtn').classList.toggle('on', countActive(root) > 0 || pop.classList.contains('open'));
}
function lead(parent, i) {
  const l = el('div', { class: 'lead' });
  if (i === 0) l.textContent = 'Where';
  else {
    const s = el('select', {}, el('option', { value: 'and', textContent: 'And' }), el('option', { value: 'or', textContent: 'Or' }));
    s.value = parent.op; s.onchange = () => { parent.op = s.value; drawFilters(); render(); }; l.append(s);
  }
  return l;
}
function eyeBtn(n) { return el('button', { class: 'icon', title: n.on ? 'Disable' : 'Enable', textContent: n.on ? '👁' : '🚫', onclick: () => { n.on = !n.on; drawFilters(); render(); } }); }
function dotsBtn(n, parent, i) {
  const b = el('button', { class: 'icon', title: 'More', textContent: '⋯' });
  b.onclick = (e) => {
    e.stopPropagation(); closeMenu();
    const r = b.getBoundingClientRect();
    const m = el('div', { class: 'menu', id: 'menu' },
      btn('Duplicate', '', () => { parent.items.splice(i + 1, 0, JSON.parse(JSON.stringify(n))); closeMenu(); drawFilters(); render(); }),
      btn('Delete', '', () => { parent.items.splice(i, 1); closeMenu(); drawFilters(); render(); }));
    m.style.top = r.bottom + 4 + 'px'; m.style.left = Math.max(8, r.right - 130) + 'px'; document.body.append(m);
  };
  return b;
}
function closeMenu() { $('menu')?.remove(); }
function colLabel(c) { const p = parseCol(c); return p.group ? `${p.group}: ${p.name}` : c; }
function nodeEl(n, parent, i) {
  if (n.type === 'g') {
    const gb = el('div', { class: 'gbox' }, el('div', { class: 'ghead' }, el('span', { textContent: 'Filter group' }), eyeBtn(n), dotsBtn(n, parent, i)));
    n.items.forEach((c, j) => gb.append(nodeEl(c, n, j)));
    gb.append(btn('+ Add filter', 'fbtn', () => { n.items.push(newFilter()); drawFilters(); }));
    const w = el('div', { class: 'fgroup' }, lead(parent, i), gb); w.style.opacity = n.on ? '' : '.45'; return w;
  }
  const col = el('select', {}, ...DATA.columns.map(c => el('option', { value: c, textContent: colLabel(c) })));
  col.value = n.col; col.onchange = () => { n.col = col.value; n.val = ''; drawFilters(); render(); };
  const cmp = el('select', {}, ...OPS.map(([k, t]) => el('option', { value: k, textContent: t })));
  cmp.value = n.cmp; cmp.onchange = () => { n.cmp = cmp.value; drawFilters(); render(); };
  let val;
  const options = (n.cmp === 'eq' || n.cmp === 'neq') ? distinct(n.col) : null;
  if (NO_VALUE.has(n.cmp)) val = el('input', { type: 'text', disabled: true, placeholder: '—' });
  else if (options) {
    val = el('select', {}, el('option', { value: '', textContent: 'Select…' }), ...options.map(o => el('option', { value: o, textContent: o })));
    val.value = n.val; val.onchange = () => { n.val = val.value; drawFilters(); render(); };
  } else {
    val = el('input', { type: 'text', value: n.val, placeholder: 'Value' });
    val.oninput = () => { n.val = val.value; badge('fcount', countActive(FILTERS)); render(); };
  }
  return el('div', { class: 'frow' + (n.on ? '' : ' off') }, lead(parent, i), col, cmp, val, eyeBtn(n), dotsBtn(n, parent, i));
}

/* ===== sorting ===== */
function cmpValues(a, b) {
  const ea = a === '' || a == null, eb = b === '' || b == null;
  if (ea || eb) return ea === eb ? 0 : (ea ? 1 : -1);
  const na = Number(a), nb = Number(b);
  if (!isNaN(na) && !isNaN(nb)) return na - nb;
  return String(a).localeCompare(String(b), undefined, { sensitivity: 'base', numeric: true });
}
function sortRows(rows) {
  const s = SORTS.filter(x => x.on);
  if (!s.length) return rows;
  return rows.map((r, i) => [r, i]).sort(([a, ia], [b, ib]) => {
    for (const { col, dir } of s) {
      const av = a.cells[col]?.value, bv = b.cells[col]?.value;
      const e = (v) => v === '' || v == null;
      let c = cmpValues(av, bv);
      if (dir === 'desc' && !e(av) && !e(bv)) c = -c;
      if (c) return c;
    }
    return ia - ib;
  }).map(([r]) => r);
}
function drawSorts() {
  const pop = $('spop'); pop.replaceChildren();
  if (!SORTS.length) pop.append(el('div', { class: 'hintp', textContent: 'No sorting yet. You can also click a column header.' }));
  SORTS.forEach((s, i) => {
    const col = el('select', {}, ...DATA.columns.map(c => el('option', { value: c, textContent: colLabel(c) })));
    col.value = s.col; col.onchange = () => { s.col = col.value; drawSorts(); render(); };
    const dir = el('select', {}, el('option', { value: 'asc', textContent: 'A → Z / low → high' }), el('option', { value: 'desc', textContent: 'Z → A / high → low' }));
    dir.value = s.dir; dir.onchange = () => { s.dir = dir.value; drawSorts(); render(); };
    pop.append(el('div', { class: 'srow' + (s.on ? '' : ' off') }, el('div', { class: 'lead', textContent: i === 0 ? 'Sort by' : 'Then by' }), col, dir,
      el('button', { class: 'icon', title: s.on ? 'Disable' : 'Enable', textContent: s.on ? '👁' : '🚫', onclick: () => { s.on = !s.on; drawSorts(); render(); } }),
      el('button', { class: 'icon', title: 'Remove', textContent: '✕', onclick: () => { SORTS.splice(i, 1); drawSorts(); render(); } })));
  });
  pop.append(el('div', { class: 'fbar' },
    btn('+ Add sort', 'fbtn', () => { const used = new Set(SORTS.map(s => s.col)); SORTS.push({ col: DATA.columns.find(c => !used.has(c)) || DATA.columns[0], dir: 'asc', on: true }); drawSorts(); render(); }),
    btn('✕ Clear sorting', 'fbtn clear', () => { SORTS = []; drawSorts(); render(); })));
  const n = SORTS.filter(s => s.on).length; badge('scount', n);
  $('sbtn').classList.toggle('on', n > 0 || pop.classList.contains('open'));
}
function headerSort(col, shift) {
  const i = SORTS.findIndex(s => s.col === col);
  if (shift) { if (i < 0) SORTS.push({ col, dir: 'asc', on: true }); else if (SORTS[i].dir === 'asc') SORTS[i].dir = 'desc'; else SORTS.splice(i, 1); }
  else if (i >= 0 && SORTS.length === 1) { if (SORTS[0].dir === 'asc') SORTS[0].dir = 'desc'; else SORTS = []; }
  else SORTS = [{ col, dir: 'asc', on: true }];
  drawSorts(); render();
}

/* ===== export ===== */
function csvCell(v) { v = String(v ?? ''); return /[",\n\r]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }
function exportCsv(withProof) {
  const cols = VIEW_COLS.filter(c => c !== 'Domain');
  const head = ['Company Name', 'Domain'];
  for (const c of cols) { head.push(c); if (withProof && DATA.answer_columns.includes(c)) head.push(c + ' (reasoning)', c + ' (proof)', c + ' (source)', c + ' (confidence)'); }
  const lines = [head.map(csvCell).join(',')];
  for (const r of VIEW_ROWS) {
    const row = [r.cells['Company Name']?.value ?? '', r.domain];
    for (const c of cols) {
      const cell = r.cells[c]; row.push(cell?.value ?? '');
      if (withProof && DATA.answer_columns.includes(c)) { const d = cell?.details; row.push(d?.Reasoning ?? '', d?.Proof ?? '', d?.Source ?? '', d?.Confidence ?? ''); }
    }
    lines.push(row.map(csvCell).join(','));
  }
  const a = el('a', { href: URL.createObjectURL(new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' })), download: `${DATA.list}_view${withProof ? '_with_proof' : ''}.csv` });
  document.body.append(a); a.click(); a.remove();
  toast(`Exported ${VIEW_ROWS.length} rows`); closePops();
}
function drawExport() {
  const mk = (t, s, p) => { const b = el('button', { onclick: () => exportCsv(p) }, t); b.append(el('small', { textContent: s })); return b; };
  $('epop').replaceChildren(mk('Current view', 'Visible columns and rows, values only', false), mk('Current view with proof', 'Adds reasoning, proof quote, source link and confidence for each data point', true));
}

/* ===== table ===== */
function pillFor(col, cell) {
  const v = cell.value;
  if (cell.details) { const k = v.toLowerCase(); return el('span', { class: 'pill ' + (k === 'yes' ? 'yes' : k === 'no' ? 'no' : 'unknown'), textContent: v }); }
  if (col === 'ICP') return el('span', { class: 'pill ' + (v === 'YES' ? 'yes' : v === 'NO' ? 'no' : 'pending'), textContent: v });
  if (col === 'Lead tier' && v) return el('span', { class: 'pill ' + (v === 'Strong fit' ? 'strong' : 'weak'), textContent: v });
  if (col === 'Lead score' && v !== '') return el('span', { class: 'score', textContent: v });
  return null;
}
function render() {
  const q = $('q').value.toLowerCase();
  VIEW_ROWS = sortRows(DATA.rows.filter(r => {
    if (QUICK && r.cells['ICP']?.value !== QUICK) return false;
    if (!evalNode(FILTERS, r)) return false;
    return !q || r.domain.toLowerCase().includes(q) || Object.values(r.cells).some(c => c.value.toLowerCase().includes(q));
  }));
  VIEW_COLS = DATA.columns.filter(c => c !== 'Company Name' && VIS.has(c));
  const total = DATA.columns.length - 1;
  $('count').textContent = `${VIEW_ROWS.length}/${DATA.rows.length} rows · ${VIEW_COLS.length}/${total} columns`;
  $('ccount').textContent = `${VIEW_COLS.length}/${total}`;
  const act = SORTS.filter(s => s.on);
  const thead = el('tr', {}, el('th', { class: 'n', textContent: '#' }), (() => {
    const th = el('th', { class: 'pin', title: 'Click to sort' }, el('span', { class: 'nm', textContent: 'Company' }));
    const k = act.findIndex(s => s.col === 'Company Name');
    if (k >= 0) th.append(el('span', { class: 'arrow', textContent: act[k].dir === 'asc' ? '▲' : '▼' }));
    th.onclick = (e) => headerSort('Company Name', e.shiftKey); return th;
  })(), ...VIEW_COLS.map(c => {
    const { group, name } = parseCol(c), th = el('th', { title: `${c}\nClick to sort, shift-click to add a sort level` });
    if (group) { th.style.setProperty('--gc', GROUPS[group]); th.append(el('span', { class: 'g', textContent: group })); }
    th.append(el('span', { class: 'nm', textContent: name }));
    const k = act.findIndex(s => s.col === c);
    if (k >= 0) th.lastChild.append(el('span', { class: 'arrow', textContent: (act.length > 1 ? k + 1 : '') + (act[k].dir === 'asc' ? '▲' : '▼') }));
    th.onclick = (e) => headerSort(c, e.shiftKey); return th;
  }));
  CELLS = new Map();
  const tbody = VIEW_ROWS.map((r, ri) => {
    const tr = el('tr', {}, el('td', { class: 'n', textContent: String(ri + 1) }));
    const pin = el('td', { class: 'pin cell' }, el('span', { class: 'nm', textContent: r.cells['Company Name']?.value || r.domain }), el('span', { class: 'dm', textContent: r.domain }));
    pin.onclick = () => selectCell(ri, -1); CELLS.set(ri + '|-1', pin); tr.append(pin);
    VIEW_COLS.forEach((c, ci) => {
      const cell = r.cells[c], td = el('td', { class: 'cell', title: cell.value });
      const p = pillFor(c, cell); if (p) td.append(p); else td.textContent = cell.value;
      td.onclick = () => selectCell(ri, ci); CELLS.set(ri + '|' + ci, td); tr.append(td);
    });
    return tr;
  });
  if (!VIEW_ROWS.length) {
    const clear = btn('Clear filters and search', 'tbtn', () => { FILTERS.items = []; QUICK = ''; $('q').value = ''; drawQuick(); drawFilters(); render(); });
    $('gridwrap').replaceChildren(el('div', { class: 'empty' }, el('b', { textContent: 'No rows match' }), 'Try removing a filter or changing your search.', el('br'), clear));
  } else if (!VIEW_COLS.length) {
    $('gridwrap').replaceChildren(el('div', { class: 'empty' }, el('b', { textContent: 'No columns selected' }), 'Open Columns and pick what to show.'));
  } else $('gridwrap').replaceChildren(el('table', {}, el('thead', {}, thead), el('tbody', {}, ...tbody)));
  // keep the selected cell if it is still in view
  if (SEL) {
    const ri = VIEW_ROWS.findIndex(r => r.domain === SEL.domain), ci = SEL.col === 'Company Name' ? -1 : VIEW_COLS.indexOf(SEL.col);
    if (ri >= 0 && (ci >= 0 || SEL.col === 'Company Name')) markSelected(ri, ci); else closePanel();
  }
}

/* ===== details panel ===== */
function selectCell(ri, ci) {
  const row = VIEW_ROWS[ri]; if (!row) return;
  SEL = { domain: row.domain, col: ci < 0 ? 'Company Name' : VIEW_COLS[ci] };
  markSelected(ri, ci); $('psearch').value = ''; drawPanel();
  $('panel').classList.add('open');
}
function markSelected(ri, ci) {
  document.querySelectorAll('td.sel').forEach(t => t.classList.remove('sel'));
  const td = CELLS.get(ri + '|' + ci);
  if (td) { td.classList.add('sel'); td.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
}
function selPos() {
  if (!SEL) return null;
  return { ri: VIEW_ROWS.findIndex(r => r.domain === SEL.domain), ci: SEL.col === 'Company Name' ? -1 : VIEW_COLS.indexOf(SEL.col) };
}
function move(dr, dc) {
  const p = selPos(); if (!p || p.ri < 0) return;
  const ri = Math.min(VIEW_ROWS.length - 1, Math.max(0, p.ri + dr)), ci = Math.min(VIEW_COLS.length - 1, Math.max(-1, p.ci + dc));
  selectCell(ri, ci);
}
function section(label, node) { return el('div', { class: 'sec' }, el('div', { class: 'lbl', textContent: label }), node); }
function drawPanel() {
  const p = selPos(); if (!SEL || !p || p.ri < 0) return;
  const row = VIEW_ROWS[p.ri], col = SEL.col, cell = row.cells[col], f = $('psearch').value.toLowerCase();
  const pc = parseCol(col);
  $('ptitle').replaceChildren(el('div', { class: 't', textContent: row.cells['Company Name']?.value || row.domain }),
    el('div', { class: 's', textContent: `${row.domain} · ${col === 'Company Name' ? 'Company' : pc.group ? pc.group + ': ' + pc.name : col}` }));
  const parts = [];
  if (cell.details) {
    const d = cell.details, k = d.Response.toLowerCase();
    parts.push(['Answer', section('Answer', el('span', { class: 'pill bigans ' + (k === 'yes' ? 'yes' : k === 'no' ? 'no' : 'unknown'), textContent: d.Response })), d.Response]);
    if (d.Reasoning) parts.push(['Reasoning', section('Reasoning', el('div', { class: 'val', textContent: d.Reasoning })), d.Reasoning]);
    if (d.Proof) parts.push(['Proof', section('Proof', el('div', { class: 'quote', textContent: d.Proof })), d.Proof]);
    if (d.Source) {
      const w = el('div', {});
      if (isUrl(d.Source)) { w.append(el('a', { class: 'linkbtn', href: d.Source, target: '_blank', rel: 'noopener noreferrer', textContent: 'Open source ↗' }), el('span', { class: 'url', textContent: d.Source })); }
      else w.append(el('div', { class: 'val', textContent: d.Source }));
      parts.push(['Source', section('Source', w), d.Source]);
    }
    const meta = el('div', { class: 'meta' });
    if (d.Confidence) { const c = d.Confidence, cls = /very high|^high/.test(c) ? 'yes' : c === 'medium' ? 'pending' : c === 'low' ? 'no' : 'blue'; meta.append(el('span', { class: 'pill ' + cls, textContent: 'Confidence: ' + c })); }
    meta.append(el('span', { class: 'pill unknown', textContent: 'Decided by: ' + d.Method }), el('span', { class: 'pill unknown', textContent: d.Phase === 'icp' ? 'ICP phase' : 'Signals phase' }));
    parts.push(['Details', section('Details', meta), d.Confidence + ' ' + d.Method + ' ' + d.Phase]);
  } else {
    parts.push(['Value', section(col === 'Company Name' ? 'Company' : col, el('div', { class: 'val', textContent: cell.value || '—' })), cell.value]);
    const extra = col === 'Company Name' ? ['ICP', 'ICP status', 'Why', 'Lead score', 'Lead tier'] : (CONTEXT[col] || []);
    for (const c of extra) { const v = row.cells[c]?.value; if (v) parts.push([c, section(c, el('div', { class: 'val', textContent: v })), v]); }
  }
  const body = $('pbody'); body.replaceChildren();
  for (const [label, node, text] of parts) { if (f && !(label + ' ' + text).toLowerCase().includes(f)) continue; body.append(node); }
  if (!body.children.length) body.append(el('div', { class: 'hintp', textContent: 'Nothing matches your search.' }));
  $('pfoot').textContent = `Row ${p.ri + 1} of ${VIEW_ROWS.length}`;
}
function closePanel() { $('panel').classList.remove('open'); document.querySelectorAll('td.sel').forEach(t => t.classList.remove('sel')); SEL = null; }

/* ===== popovers ===== */
const POPS = { f: ['fpop', 'fbtn', drawFilters], s: ['spop', 'sbtn', drawSorts], c: ['cpop', 'cbtn', drawCols], e: ['epop', 'ebtn', drawExport] };
function closePops(except) {
  for (const [k, [p, b, d]] of Object.entries(POPS)) if (k !== except) { $(p).classList.remove('open'); if (DATA) d(); }
}
function togglePop(k, e) {
  e.stopPropagation(); closeMenu(); closePops(k);
  const [p, b, d] = POPS[k], pop = $(p), btn_ = $(b);
  pop.classList.toggle('open');
  if (pop.classList.contains('open')) {
    const max = $('tools').clientWidth - pop.offsetWidth - 16;
    pop.style.left = Math.max(8, Math.min(btn_.offsetLeft, max)) + 'px';
  }
  d();
}
for (const k of Object.keys(POPS)) {
  $(POPS[k][1]).onclick = (e) => togglePop(k, e);
  $(POPS[k][0]).addEventListener('click', (e) => { if (!e.target.closest('.menu')) closeMenu(); e.stopPropagation(); });
}
document.addEventListener('click', () => { closeMenu(); closePops(); });

/* ===== wiring ===== */
$('list').onchange = (e) => loadTable(e.target.value);
$('q').oninput = render;
$('close').onclick = closePanel;
$('pprev').onclick = () => move(-1, 0);
$('pnext').onclick = () => move(1, 0);
$('psearch').oninput = drawPanel;
document.addEventListener('keydown', (e) => {
  const tag = (e.target.tagName || '').toLowerCase();
  if (e.key === 'Escape') {
    closeMenu();
    if (Object.values(POPS).some(([p]) => $(p).classList.contains('open'))) closePops(); else closePanel();
    return;
  }
  if (tag === 'input' || tag === 'select' || tag === 'textarea' || !SEL) return;
  const k = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] }[e.key];
  if (k) { e.preventDefault(); move(...k); }
});
loadLists();
