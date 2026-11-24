// Shared by index.html and how.html.
const COLOR = {drone: '#E2A72E', missile: '#C4372B', unknown: '#A49F94'};
const WEAPON = {drone: 'Shahed-type drone', jet_drone: 'Jet drone', ballistic: 'Ballistic missile', cruise_missile: 'Cruise missile',
                missile: 'Missile, type not stated', unknown: 'Weapon not stated'};
const TARGET = {residential_highrise: 'Apartment block', residential_house: 'House', energy: 'Power and heat', industrial: 'Industry',
  warehouse: 'Warehouse', commercial: 'Shop, mall or restaurant', office: 'Office building', education: 'School or university',
  medical: 'Hospital or clinic', transport: 'Transport, bridges, metro', infrastructure: 'Other city infrastructure',
  cultural_religious: 'Culture or church', vehicle: 'Car or vehicle', open_area: 'Open ground', other: 'Other'};
const LEVEL = {confirmed: 'Confirmed by two or more independent sources', official: 'Reported by officials only',
               single_source: 'One source so far', aggregator_only: 'Only aggregator channels'};
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
