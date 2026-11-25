// Shared by index.html and how.html.
const COLOR = {drone: '#E2A72E', missile: '#C4372B', unknown: '#A49F94'};
const WEAPON = {drone: 'Shahed-type drone', jet_drone: 'Jet drone', ballistic: 'Ballistic missile', cruise_missile: 'Cruise missile',
                missile: 'Missile, type not stated', unknown: 'Weapon not stated'};
const TARGET = {residential_highrise: 'Apartment block', residential_house: 'House', energy: 'Power and heat', industrial: 'Industry',
  warehouse: 'Warehouse', commercial: 'Shop, mall or restaurant', office: 'Office building', education: 'School or university',
  medical: 'Hospital or clinic', transport: 'Transport, bridges, metro', infrastructure: 'Other city infrastructure',
  cultural_religious: 'Culture or church', vehicle: 'Car or vehicle', open_area: 'Open ground', other: 'Other'};
// City hall's three channels are one voice; a newsroom counts as a second voice
// only where its quote adds a figure or a name the officials had not given.
const LEVEL = {confirmed: 'Two separate sources', official: 'Official report only',
               media: 'Media report only', aggregator_only: 'Aggregator channels only',
               disputed: 'Disputed – the auditor thinks this record may join two places'};
const LEVEL_NOTE = {confirmed: 'city hall, the emergency service or a newsroom with its own detail',
                    official: 'city hall or the emergency service, no independent account',
                    media: 'no official report found', aggregator_only: 'treat with care',
                    disputed: 'not counted as confirmed until resolved; see the note'};
const MONTH = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTH_L = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'], DOW_L = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

const sum = a => a.reduce((t, x) => t + (x || 0), 0);
const pl = (n, one, many) => n === 1 ? one : many;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const count = (a, f) => a.reduce((m, x) => (m[f(x)] = (m[f(x)] || 0) + 1, m), {});
const group = (a, f) => a.reduce((m, x) => ((m[f(x)] ||= []).push(x), m), {});
const hm = min => { const h = Math.floor(min / 60), m = Math.round(min % 60); return h ? `${h} h${m ? ' ' + m + ' min' : ''}` : `${m} min`; };
const dateOf = d => new Date(d + 'T12:00:00');
function fmtDay(d, style) {
  const t = dateOf(d), day = t.getDate();
  if (style === 'long') return `${day} ${MONTH_L[t.getMonth()]}`;
  if (style === 'dow') return `${DOW[t.getDay()]} ${day} ${MONTH[t.getMonth()]}`;
  if (style === 'dowlong') return `${DOW_L[t.getDay()]} ${day} ${MONTH_L[t.getMonth()]}`;
  return `${day} ${MONTH[t.getMonth()]}`;
}
const dowOf = d => (dateOf(d).getDay() + 6) % 7;   // Monday = 0
function allNights(period) {
  const out = []; for (let t = dateOf(period[0]); t <= dateOf(period[1]); t.setDate(t.getDate() + 1)) out.push(t.toISOString().slice(0, 10));
  return out;
}
function hourOf(s) {
  const m = String(s.time || '').match(/(\d{1,2})[:.](\d{2})/);
  if (m && +m[1] < 24) return +m[1];
  const f = String(s.first || '').match(/ (\d{2}):/);
  return f ? +f[1] : null;
}
const cas = s => [s.killed ? `${s.killed} killed` : '', s.injured ? `${s.injured} injured` : ''].filter(Boolean).join(', ');
const asOf = R => { const t = R.generated; return `${+t.slice(8, 10)} ${MONTH_L[+t.slice(5, 7) - 1]} ${t.slice(0, 4)}, ${t.slice(11, 16)} Kyiv time`; };
// Places where debris fell on open ground are not places where a building was hit.
const DEBRIS = new Set(['open_area']);
const damaged = S => S.filter(s => !DEBRIS.has(s.target)).length;

// ---------------------------------------------------------------- shared page parts
const NAV = [['map', './', 'Map'], ['patterns', 'patterns.html', 'Patterns'], ['wiki', 'wikipedia.html', 'Wikipedia<span class="wide"> check</span>'],
             ['records', 'records.html', 'Records']];
function mountHeader(current) {
  const el = document.getElementById('top');
  el.innerHTML = `<div class="wrap"><a class="name" href="./"><img src="logo.svg" alt=""><span>Kyiv Strike Registry</span></a><nav>` +
    NAV.map(([k, h, l]) => `<a class="n-${k}" href="${h}"${k === current ? ' aria-current="page"' : ''}>${l}</a>`).join('') +
    `<a class="btn solid" href="how.html"${current === 'how' ? ' aria-current="page"' : ''}><span class="wide">How it works</span><span class="narrow">How</span></a></nav></div>`;
  const cur = el.querySelector('nav [aria-current]');
  if (cur) cur.scrollIntoView({block: 'nearest', inline: 'center'});
}
function mountFooter(extra = '') {
  const el = document.createElement('footer'), R = window.REGISTRY;
  el.innerHTML = `<div class="wrap"><p>Reports from 13 public Telegram channels: the Kyiv City Military Administration, the mayor, the Kyiv City State Administration, the State Emergency Service, Suspilne, Ukrainska Pravda, Hromadske, the Kyiv Independent, the Kyiv Post, BBC Ukrainian and two local aggregators. Cross-checked against English and Ukrainian Wikipedia (CC BY-SA). Map tiles © Esri; district boundaries © OpenStreetMap contributors.</p>` +
    (R ? `<p>Data as of ${asOf(R)}. Telegram posts can be edited or deleted after they are collected; these are the words as read on that date. Casualty figures are the latest each source gave.</p>` : '') +
    `${extra}<p><a href="how.html">How it works</a> · <a href="https://github.com/tShyshatskyi/kyiv-strikes-agent">Source code</a></p></div>`;
  document.body.append(el);
}

// Filters: what hit (chips), what was hit and where (selects).
const W_CHIPS = [['all', 'All', null], ['drone', 'Drones', 'var(--drone)'], ['missile', 'Missiles', 'var(--missile)'], ['unknown', 'Not stated', 'var(--unk)']];
function passes(state, s, skip) {
  return (skip === 'night' || !state.night || s.day === state.night) && (skip === 'w' || state.w === 'all' || s.wclass === state.w) &&
         (skip === 't' || !state.t || s.target === state.t) && (skip === 'd' || !state.d || s.district === state.d);
}
function mountFilters(el, S, state, onChange) {
  const targets = count(S, s => s.target), dists = count(S.filter(s => s.district), s => s.district);
  el.innerHTML = W_CHIPS.map(([k, l, c]) => `<button class="chip" data-w="${k}">${c ? `<i style="background:${c}"></i>` : ''}${l}</button>`).join('') +
    `<select class="chip" data-f="t" aria-label="Type of place"><option value="">Any place</option>${Object.keys(targets).sort((a, b) => targets[b] - targets[a]).map(k => `<option value="${k}">${TARGET[k] || k} (${targets[k]})</option>`).join('')}</select>` +
    `<select class="chip" data-f="d" aria-label="District"><option value="">Any district</option>${Object.keys(dists).sort().map(k => `<option value="${k}">${k} (${dists[k]})</option>`).join('')}</select>`;
  el.addEventListener('click', e => { const b = e.target.closest('[data-w]'); if (b) onChange({w: b.dataset.w}); });
  el.querySelectorAll('select').forEach(x => x.onchange = () => onChange({[x.dataset.f]: x.value}));
  return () => {
    el.querySelectorAll('[data-w]').forEach(b => b.setAttribute('aria-pressed', b.dataset.w === state.w));
    el.querySelector('[data-f=t]').value = state.t; el.querySelector('[data-f=d]').value = state.d;
  };
}

// One column per attack night: places hit above the line (by weapon), alert hours below it.
function nightStrip(list, R, nights, selected, top = 110, bot = 40) {
  const byDay = group(list, s => s.day);
  const alerts = Object.fromEntries(R.days.map(d => [d.day, (d.alerts.drone + d.alerts.missile) / 60]));
  const n = nights.length, w = 1000, cw = w / n;
  const maxS = Math.max(4, ...Object.values(byDay).map(a => a.length)), maxA = Math.max(6, ...Object.values(alerts));
  let svg = `<svg viewBox="0 -4 ${w} ${top + bot + 12}" preserveAspectRatio="none" role="img" aria-label="Places hit and alert hours per night">`;
  nights.forEach((d, i) => {
    const x = i * cw, a = byDay[d] || [];
    let y = top;
    for (const k of ['drone', 'missile', 'unknown']) {
      const h = a.filter(s => s.wclass === k).length * (top - 6) / maxS;
      if (h) { svg += `<rect x="${x + 1.5}" y="${y - h}" width="${cw - 3}" height="${h}" fill="${COLOR[k]}"/>`; y -= h; }
    }
    const ah = (alerts[d] || 0) * bot / maxA;
    if (ah) svg += `<rect x="${x + 1.5}" y="${top + 3}" width="${cw - 3}" height="${ah}" fill="#B9B4A9"/>`;
    if (selected === d) svg += `<rect class="sel" x="${x + .5}" y="-2" width="${cw - 1}" height="${top + bot + 8}" rx="2" vector-effect="non-scaling-stroke"/>`;
    svg += `<rect class="hit" data-d="${d}" x="${x}" y="-4" width="${cw}" height="${top + bot + 12}" fill="transparent"><title>${fmtDay(d, 'dow')}: ${a.length} ${pl(a.length, 'place', 'places')}, ${hm((alerts[d] || 0) * 60)} under alert</title></rect>`;
  });
  return svg + `<line x1="0" x2="${w}" y1="${top + 1.5}" y2="${top + 1.5}" stroke="#1C1B19" stroke-width="1" vector-effect="non-scaling-stroke"/></svg>`;
}
const nightTicks = nights => [0, 14, 31, 45, nights.length - 1].map(i => `<span>${fmtDay(nights[i])}</span>`).join('');

// A place's confirmation, and the words of its sources.
const LEVEL_SHORT = {confirmed: s => `${s.voices} separate sources`, official: () => 'Official only',
                     media: () => 'Media only', aggregator_only: () => 'Aggregators only',
                     disputed: () => 'Disputed'};
// Service notes from the auditor, folded away: they are written for the next
// agent, not for a reader, so they never greet one.
// A complaint that leaves the strike itself in no doubt (a wrong weapon, two
// car fires that may be one) keeps the evidence level and gets a small marker.
const noteMark = s => s.flags.length && s.level !== 'disputed' ? ' <span class="nmark" title="The auditor left a note on this record">· auditor note</span>' : '';
const auditorNote = s => s.flags.length
  ? `<details class="note"><summary>Auditor's note (${s.flags.length})</summary><p>${esc(s.flags.join(' '))}</p></details>` : '';
const where = s => [s.district, s.address].filter(Boolean).join(', ') || 'District not stated';
const sourcesHTML = s => `<ul class="srcs">${s.sources.map(x =>
  `<li><blockquote lang="uk">“${esc(x.quote)}”</blockquote><span class="who">${esc(x.name)}${x.kind === 'aggregator' ? ' (aggregator)' : ''}, ${esc(x.posted.slice(5).replace('-', '.'))} · <a href="${x.url}" target="_blank" rel="noopener">post</a></span></li>`).join('')}</ul>`;
const photoCaption = s => `${s.title} – ${s.sources.find(x => x.kind === 'official')?.name || ''}`;
