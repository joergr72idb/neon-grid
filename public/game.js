'use strict';

const COLORS = [
  { line: '#18e7ff', core: '#d9fbff', glow: 'rgba(24,231,255,0.9)' },
  { line: '#ff8a1f', core: '#fff0dc', glow: 'rgba(255,138,31,0.9)' },
];
const KEYS = {
  ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
  w: 'up', s: 'down', a: 'left', d: 'right', W: 'up', S: 'down', A: 'left', D: 'right',
};

const $ = (sel) => document.querySelector(sel);
let token = loadToken();
let me = null;
let ws = null;
let wsReady = null;
let game = null;

// ------------------------------------------------------------------ storage
function loadToken() {
  try { return localStorage.getItem('neongrid-token') || ''; } catch { return ''; }
}
function saveToken(t) {
  token = t;
  try { t ? localStorage.setItem('neongrid-token', t) : localStorage.removeItem('neongrid-token'); } catch { /* ignore */ }
}

// ------------------------------------------------------------------ api
async function api(path, body) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = 'Bearer ' + token;
  let res;
  try {
    res = await fetch('api/' + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error('Cannot reach the game server.');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `Server error (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

// ------------------------------------------------------------------ screens
function show(name) {
  for (const s of ['auth', 'menu', 'wait']) $('#screen-' + s).hidden = s !== name;
  $('#start').hidden = name === 'game';
  $('#screen-game').hidden = name !== 'game';
  document.body.classList.toggle('in-game', name === 'game');
  if (name !== 'game') loadHighscores();
}

function renderMe(user) {
  me = user;
  $('#menu-name').textContent = user.username;
  $('#st-wins').textContent = user.wins;
  $('#st-losses').textContent = user.losses;
  $('#st-draws').textContent = user.draws;
  $('#st-played').textContent = user.played;
}

async function loadHighscores() {
  const body = $('#score-body');
  try {
    const data = await api('highscores');
    $('#total-matches').textContent = data.total_matches;
    body.textContent = '';
    if (!data.highscores.length) {
      body.innerHTML = '<tr><td colspan="6" class="muted">No matches yet. Be the first!</td></tr>';
      return;
    }
    data.highscores.forEach((u, i) => {
      const tr = document.createElement('tr');
      if (i === 0) tr.className = 'rank1';
      if (me && u.id === me.id) tr.classList.add('me');
      for (const v of [i + 1, u.username, u.wins, u.losses, u.draws, u.played]) {
        const td = document.createElement('td');
        td.textContent = v;
        tr.appendChild(td);
      }
      body.appendChild(tr);
    });
  } catch (e) {
    body.innerHTML = '<tr><td colspan="6" class="muted"></td></tr>';
    body.querySelector('td').textContent = e.message;
  }
}

// ------------------------------------------------------------------ auth
let authMode = 'login';

function setAuthMode(mode) {
  authMode = mode;
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.mode === mode));
  $('#confirm-row').hidden = mode !== 'register';
  $('#auth-pass2').required = mode === 'register';
  $('#auth-pass').autocomplete = mode === 'register' ? 'new-password' : 'current-password';
  $('#auth-submit').textContent = mode === 'register' ? 'Create account' : 'Log in';
  $('#auth-error').textContent = '';
}

async function submitAuth(ev) {
  ev.preventDefault();
  const username = $('#auth-user').value.trim();
  const password = $('#auth-pass').value;
  const err = $('#auth-error');
  err.textContent = '';
  if (authMode === 'register' && password !== $('#auth-pass2').value) {
    err.textContent = 'The passwords do not match.';
    return;
  }
  const btn = $('#auth-submit');
  btn.disabled = true;
  try {
    const data = await api(authMode, { username, password });
    saveToken(data.token);
    renderMe(data.user);
    $('#auth-form').reset();
    show('menu');
  } catch (e) {
    err.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

async function logout() {
  try { await api('logout', {}); } catch { /* ignore */ }
  saveToken('');
  me = null;
  if (ws) ws.close();
  setAuthMode('login');
  show('auth');
}

// ------------------------------------------------------------------ socket
function connect() {
  if (wsReady) return wsReady;
  wsReady = new Promise((resolve, reject) => {
    const url = new URL('ws', location.href);
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(url);
    ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token }));
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'auth_ok') { renderMe(msg.user); resolve(); }
      else if (msg.type === 'auth_failed') { reject(new Error('auth')); }
      else handleMessage(msg);
    };
    ws.onclose = () => {
      ws = null;
      wsReady = null;
      reject(new Error('closed'));
      onSocketClosed();
    };
  });
  return wsReady;
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

let kicked = false;
function onSocketClosed() {
  if (!me) return;
  if (kicked) {
    kicked = false;
    stopGame();
    show('menu');
    $('#menu-msg').textContent = 'You opened the game in another window.';
    return;
  }
  if (game && !game.over) {
    game.over = true;
    overlay('LOST', 'Connection to the grid was lost', 'lose', true);
  } else if (!$('#screen-wait').hidden) {
    show('menu');
    $('#menu-msg').textContent = 'Connection lost. Try again.';
  }
}

async function play() {
  $('#menu-msg').textContent = '';
  unlockAudio();
  const btn = $('#btn-play');
  btn.disabled = true;
  try {
    await connect();
    send({ type: 'join' });
  } catch (e) {
    if (e.message === 'auth') { saveToken(''); me = null; show('auth'); $('#auth-error').textContent = 'Please log in again.'; }
    else $('#menu-msg').textContent = 'Cannot connect to the game server.';
  } finally {
    btn.disabled = false;
  }
}

function handleMessage(msg) {
  switch (msg.type) {
    case 'waiting': show('wait'); break;
    case 'idle': show('menu'); break;
    case 'match': startGame(msg); break;
    case 'countdown': onCountdown(msg.n); break;
    case 'tick': onTick(msg); break;
    case 'end': onEnd(msg); break;
    case 'kicked': kicked = true; break;
  }
}

// ------------------------------------------------------------------ game
function startGame(msg) {
  stopGame();
  const [w, h] = msg.grid;
  game = {
    you: msg.you,
    w, h,
    cell: 6,
    trails: msg.starts.map((p) => [p.slice()]),
    alive: [true, true],
    particles: [],
    over: false,
    running: false,
    raf: 0,
  };
  const names = msg.players.map((p) => p.username);
  $('#hud-p0').textContent = names[0];
  $('#hud-p1').textContent = names[1];
  $('#hud-p0').classList.toggle('you', msg.you === 0);
  $('#hud-p1').classList.toggle('you', msg.you === 1);
  show('game');
  layout();
  const side = msg.you === 0 ? 'cyan · left side' : 'orange · right side';
  overlay('', `You are ${side}`);
  game.raf = requestAnimationFrame(frame);
}

function stopGame() {
  if (game) cancelAnimationFrame(game.raf);
  game = null;
}

function layout() {
  if (!game) return;
  const arena = $('#arena');
  const availW = Math.max(200, document.documentElement.clientWidth - 32);
  const reserved = document.querySelector('.top').offsetHeight + 130;
  const availH = Math.max(160, window.innerHeight - reserved);
  game.cell = Math.max(3, Math.floor(Math.min(availW / game.w, availH / game.h)));
  const cssW = game.w * game.cell;
  const cssH = game.h * game.cell;
  arena.style.width = cssW + 'px';
  arena.style.height = cssH + 'px';
  const dpr = window.devicePixelRatio || 1;
  for (const c of [$('#glow'), $('#trails'), $('#fx')]) {
    c.width = Math.round(cssW * dpr);
    c.height = Math.round(cssH * dpr);
    c.getContext('2d').setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  redrawTrails();
}

function center(p) {
  const c = game.cell;
  return [p[0] * c + c / 2, p[1] * c + c / 2];
}

function drawGrid(ctx) {
  const { w, h, cell } = game;
  ctx.clearRect(0, 0, w * cell, h * cell);
  ctx.lineWidth = 1;
  ctx.strokeStyle = 'rgba(24,231,255,0.07)';
  ctx.beginPath();
  for (let x = 0; x <= w; x += 4) { ctx.moveTo(x * cell + 0.5, 0); ctx.lineTo(x * cell + 0.5, h * cell); }
  for (let y = 0; y <= h; y += 4) { ctx.moveTo(0, y * cell + 0.5); ctx.lineTo(w * cell, y * cell + 0.5); }
  ctx.stroke();
  ctx.strokeStyle = 'rgba(24,231,255,0.16)';
  ctx.beginPath();
  for (let x = 0; x <= w; x += 20) { ctx.moveTo(x * cell + 0.5, 0); ctx.lineTo(x * cell + 0.5, h * cell); }
  for (let y = 0; y <= h; y += 20) { ctx.moveTo(0, y * cell + 0.5); ctx.lineTo(w * cell, y * cell + 0.5); }
  ctx.stroke();
}

function strokeTrail(i, points) {
  // Glow and core live on separate canvases, so a new segment's glow never
  // covers the bright core of the previous one.
  if (points.length < 2) return;
  const col = COLORS[i];
  const c = game.cell;
  const layers = [
    [$('#glow').getContext('2d'), c * 0.7, col.line, c * 2.2],
    [$('#trails').getContext('2d'), c * 0.28, col.core, c * 0.8],
  ];
  for (const [ctx, width, color, blur] of layers) {
    ctx.lineCap = 'square';
    ctx.lineJoin = 'miter';
    ctx.lineWidth = width;
    ctx.strokeStyle = color;
    ctx.shadowColor = col.glow;
    ctx.shadowBlur = blur;
    ctx.beginPath();
    ctx.moveTo(...center(points[0]));
    for (let k = 1; k < points.length; k++) ctx.lineTo(...center(points[k]));
    ctx.stroke();
    ctx.shadowBlur = 0;
  }
}

function redrawTrails() {
  drawGrid($('#glow').getContext('2d'));
  const core = $('#trails').getContext('2d');
  core.clearRect(0, 0, game.w * game.cell, game.h * game.cell);
  game.trails.forEach((pts, i) => strokeTrail(i, pts));
}

function onCountdown(n) {
  if (!game) return;
  beep(n === 0 ? 880 : 440, n === 0 ? 0.35 : 0.12);
  if (n === 0) {
    game.running = true;
    overlay('0', 'Go!');
    setTimeout(() => { if (game && game.running && !game.over) overlay('', ''); }, 700);
  } else {
    overlay(String(n), n === 10 ? 'Get ready' : '');
  }
}

function onTick(msg) {
  if (!game || game.over) return;
  msg.heads.forEach((head, i) => {
    if (!game.alive[i]) return;
    const pts = game.trails[i];
    const prev = pts[pts.length - 1];
    if (msg.crashed[i]) {
      game.alive[i] = false;
      explode(i, head);
      return;
    }
    pts.push(head);
    strokeTrail(i, [prev, head]);
  });
}

function onEnd(msg) {
  if (!game) return;
  game.over = true;
  game.running = false;
  renderMe(msg.me);
  const forfeit = msg.reason === 'forfeit';
  if (msg.result === 'win') {
    overlay('YOU WIN', forfeit ? 'Your opponent left the grid' : 'Your opponent crashed', 'win', true);
  } else if (msg.result === 'lose') {
    overlay('CRASHED', forfeit ? 'You left the match' : 'You lose this round', 'lose', true);
  } else {
    overlay('DRAW', msg.reason === 'error' ? 'Match aborted' : 'Both cycles crashed', 'draw', true);
  }
}

function overlay(big, sub, cls = '', buttons = false) {
  const el = $('#ov-big');
  el.className = 'ov-big ' + cls;
  el.textContent = big;
  if (big) { void el.offsetWidth; el.classList.add('pop'); }
  $('#ov-sub').textContent = sub;
  $('#ov-buttons').hidden = !buttons;
  $('#overlay').classList.toggle('dim', buttons);
  if (buttons) $('#btn-again').focus();
}

// ------------------------------------------------------------------ effects
function explode(i, head) {
  beepNoise();
  const [x, y] = center(head.map((v, k) => Math.max(0, Math.min(v, (k ? game.h : game.w) - 1))));
  for (let k = 0; k < 60; k++) {
    const a = Math.random() * Math.PI * 2;
    const s = (0.5 + Math.random() * 3.5) * game.cell * 0.4;
    game.particles.push({ x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s, life: 1, color: COLORS[i].line });
  }
}

function frame(t) {
  if (!game) return;
  const ctx = $('#fx').getContext('2d');
  const { w, h, cell } = game;
  ctx.clearRect(0, 0, w * cell, h * cell);
  const pulse = 0.75 + 0.25 * Math.sin(t / 90);
  game.trails.forEach((pts, i) => {
    if (!game.alive[i]) return;
    const [x, y] = center(pts[pts.length - 1]);
    const r = cell * 0.75;
    ctx.fillStyle = COLORS[i].core;
    ctx.shadowColor = COLORS[i].glow;
    ctx.shadowBlur = cell * 4 * pulse;
    ctx.fillRect(x - r, y - r, r * 2, r * 2);
  });
  ctx.shadowBlur = 0;
  for (const p of game.particles) {
    p.x += p.vx; p.y += p.vy; p.vx *= 0.94; p.vy *= 0.94; p.life -= 0.018;
    if (p.life <= 0) continue;
    ctx.globalAlpha = p.life;
    ctx.fillStyle = p.color;
    ctx.fillRect(p.x - 1.5, p.y - 1.5, 3, 3);
  }
  ctx.globalAlpha = 1;
  game.particles = game.particles.filter((p) => p.life > 0);
  game.raf = requestAnimationFrame(frame);
}

let audio = null;
function unlockAudio() {
  try {
    if (!audio) audio = new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === 'suspended') audio.resume();
  } catch { audio = null; }
}
function beep(freq, dur) {
  if (!audio) return;
  const o = audio.createOscillator();
  const g = audio.createGain();
  o.type = 'square';
  o.frequency.value = freq;
  g.gain.setValueAtTime(0.06, audio.currentTime);
  g.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + dur);
  o.connect(g).connect(audio.destination);
  o.start();
  o.stop(audio.currentTime + dur);
}
function beepNoise() {
  if (!audio) return;
  const len = audio.sampleRate * 0.5;
  const buf = audio.createBuffer(1, len, audio.sampleRate);
  const d = buf.getChannelData(0);
  for (let k = 0; k < len; k++) d[k] = (Math.random() * 2 - 1) * (1 - k / len) ** 2;
  const src = audio.createBufferSource();
  const g = audio.createGain();
  g.gain.value = 0.25;
  src.buffer = buf;
  src.connect(g).connect(audio.destination);
  src.start();
}

// ------------------------------------------------------------------ wiring
document.addEventListener('keydown', (ev) => {
  const dir = KEYS[ev.key];
  if (!dir || !game || game.over || $('#screen-game').hidden) return;
  ev.preventDefault();
  send({ type: 'turn', dir });
});

document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => setAuthMode(t.dataset.mode)));
$('#auth-form').addEventListener('submit', submitAuth);
$('#btn-play').addEventListener('click', play);
$('#btn-logout').addEventListener('click', logout);
$('#btn-cancel').addEventListener('click', () => { send({ type: 'leave' }); show('menu'); });
$('#btn-again').addEventListener('click', () => { stopGame(); show('menu'); play(); });
$('#btn-menu').addEventListener('click', () => { stopGame(); show('menu'); });
window.addEventListener('resize', layout);

(async function init() {
  setAuthMode('login');
  if (token) {
    try {
      const data = await api('me');
      renderMe(data.user);
      show('menu');
      return;
    } catch (e) {
      if (e.status === 401) saveToken('');
    }
  }
  show('auth');
})();
