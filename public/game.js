'use strict';
/*
 * NEON GRID – browser client.
 *
 * What this file does:
 *   1. Login / registration through the JSON API (api/...).
 *   2. Switches between the screens of index.html (auth, menu, wait, game).
 *   3. Opens a WebSocket (ws) to the game server for matchmaking and the live game.
 *   4. Draws the arena on three stacked <canvas> elements and plays sounds.
 *
 * The client never decides who crashed. It sends the pressed arrow keys to
 * the server ("turn") and draws whatever positions the server reports in its
 * "tick" messages. The server is the referee; see server/server.py.
 *
 * All URLs are relative ('api/login', 'ws'), so the page works both at
 * http://host:8765/ and below a sub-path like https://domain/neongrid/.
 */

// Colors of player 0 (cyan) and player 1 (orange): trail line, bright core, glow.
const COLORS = [
  { line: '#18e7ff', core: '#d9fbff', glow: 'rgba(24,231,255,0.9)' },
  { line: '#ff8a1f', core: '#fff0dc', glow: 'rgba(255,138,31,0.9)' },
];
// Keyboard key -> direction sent to the server. Arrow keys and WASD both work.
const KEYS = {
  ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
  w: 'up', s: 'down', a: 'left', d: 'right', W: 'up', S: 'down', A: 'left', D: 'right',
};

// Short helper: $('#id') finds one element on the page.
const $ = (sel) => document.querySelector(sel);

// --- Global state -------------------------------------------------------------
let token = loadToken();   // session token from login; '' = not logged in
let me = null;             // the logged-in user: {id, username, wins, losses, draws, played}
let ws = null;             // the WebSocket while it is open
let wsReady = null;        // Promise that resolves once the WebSocket is open and authenticated
let game = null;           // state of the current match (see startGame), null outside a match

// ------------------------------------------------------------------ storage
// The token is kept in localStorage so you stay logged in after a reload.
// try/catch: some browsers (private mode, blocked cookies) throw on access.
function loadToken() {
  try { return localStorage.getItem('neongrid-token') || ''; } catch { return ''; }
}
function saveToken(t) {
  token = t;
  try { t ? localStorage.setItem('neongrid-token', t) : localStorage.removeItem('neongrid-token'); } catch { /* ignore */ }
}

// ------------------------------------------------------------------ api
/*
 * Call the server's JSON API. Without `body` it is a GET request, with
 * `body` a POST. Returns the parsed answer, or throws an Error whose message
 * can be shown to the player (and whose .status is the HTTP status code).
 */
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
/*
 * Show exactly one screen: 'auth' (login), 'menu', 'wait' or 'game'.
 * The first three live inside #start next to the highscore table; the game
 * screen replaces all of that. The highscores are refreshed whenever the
 * start page becomes visible.
 */
function show(name) {
  for (const s of ['auth', 'menu', 'wait']) $('#screen-' + s).hidden = s !== name;
  $('#start').hidden = name === 'game';
  $('#screen-game').hidden = name !== 'game';
  document.body.classList.toggle('in-game', name === 'game');
  if (name !== 'game') loadHighscores();
}

// Remember the user and show their name and statistics in the menu.
function renderMe(user) {
  me = user;
  $('#menu-name').textContent = user.username;
  $('#st-wins').textContent = user.wins;
  $('#st-losses').textContent = user.losses;
  $('#st-draws').textContent = user.draws;
  $('#st-played').textContent = user.played;
}

// Fill the highscore table. textContent (not innerHTML) is used for user
// names, so a name can never inject HTML into the page.
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
      if (i === 0) tr.className = 'rank1';                 // gold name for the leader
      if (me && u.id === me.id) tr.classList.add('me');    // highlight your own row
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
// The login form has two tabs; authMode is also the API endpoint name.
let authMode = 'login';   // 'login' or 'register'

function setAuthMode(mode) {
  authMode = mode;
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.mode === mode));
  $('#confirm-row').hidden = mode !== 'register';   // "Repeat password" only when registering
  $('#auth-pass2').required = mode === 'register';
  // Tells password managers whether to offer a saved password or suggest a new one.
  $('#auth-pass').autocomplete = mode === 'register' ? 'new-password' : 'current-password';
  $('#auth-submit').textContent = mode === 'register' ? 'Create account' : 'Log in';
  $('#auth-error').textContent = '';
}

// Login/register form submitted: call api/login or api/register.
async function submitAuth(ev) {
  ev.preventDefault();   // don't reload the page (default form behaviour)
  const username = $('#auth-user').value.trim();
  const password = $('#auth-pass').value;
  const err = $('#auth-error');
  err.textContent = '';
  if (authMode === 'register' && password !== $('#auth-pass2').value) {
    err.textContent = 'The passwords do not match.';
    return;
  }
  const btn = $('#auth-submit');
  btn.disabled = true;   // prevent double submits while waiting
  try {
    const data = await api(authMode, { username, password });
    saveToken(data.token);
    renderMe(data.user);
    $('#auth-form').reset();
    show('menu');
  } catch (e) {
    err.textContent = e.message;   // the server's error text, e.g. "That username is already taken."
  } finally {
    btn.disabled = false;
  }
}

async function logout() {
  try { await api('logout', {}); } catch { /* ignore */ }
  saveToken('');
  me = null;   // set before closing, so onSocketClosed() stays quiet
  if (ws) ws.close();
  setAuthMode('login');
  show('auth');
}

// ------------------------------------------------------------------ socket
/*
 * Open the WebSocket (once) and log in on it with the token.
 * Returns a Promise that resolves after the server answered "auth_ok" and
 * rejects with 'auth' (token invalid) or 'closed' (connection failed).
 * Calling it again while a connection exists returns the same Promise.
 * All other server messages go to handleMessage().
 */
function connect() {
  if (wsReady) return wsReady;
  wsReady = new Promise((resolve, reject) => {
    // 'ws' relative to the page; use wss:// (encrypted) when the page is https://.
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
      wsReady = null;            // next play() opens a fresh connection
      reject(new Error('closed'));   // no effect if already resolved
      onSocketClosed();
    };
  });
  return wsReady;
}

// Send a message to the server if the connection is open (otherwise drop it).
function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

// Set when the server said "kicked" (same account opened in another window).
let kicked = false;

// The connection closed. Tell the player what that means for them.
function onSocketClosed() {
  if (!me) return;   // we logged out ourselves: nothing to report
  if (kicked) {
    kicked = false;
    stopGame();
    show('menu');
    $('#menu-msg').textContent = 'You opened the game in another window.';
    return;
  }
  if (game && !game.over) {
    // Lost connection mid-match: the server counts that as a forfeit.
    game.over = true;
    overlay('LOST', 'Connection to the grid was lost', 'lose', true);
  } else if (!$('#screen-wait').hidden) {
    show('menu');
    $('#menu-msg').textContent = 'Connection lost. Try again.';
  }
}

// "Enter the grid" pressed: connect if needed, then ask the server for a match.
async function play() {
  $('#menu-msg').textContent = '';
  unlockAudio();   // browsers only allow sound after a user click
  const btn = $('#btn-play');
  btn.disabled = true;
  try {
    await connect();
    send({ type: 'join' });   // server answers "waiting" or directly "match"
  } catch (e) {
    if (e.message === 'auth') { saveToken(''); me = null; show('auth'); $('#auth-error').textContent = 'Please log in again.'; }
    else $('#menu-msg').textContent = 'Cannot connect to the game server.';
  } finally {
    btn.disabled = false;
  }
}

// Dispatch a message from the server. See docs/ARCHITECTURE.md for all message types.
function handleMessage(msg) {
  switch (msg.type) {
    case 'waiting': show('wait'); break;            // we are first, waiting for an opponent
    case 'idle': show('menu'); break;               // waiting was cancelled
    case 'match': startGame(msg); break;            // opponent found
    case 'countdown': onCountdown(msg.n); break;    // 10, 9, … 1, 0 (= go)
    case 'tick': onTick(msg); break;                // new head positions
    case 'end': onEnd(msg); break;                  // match over
    case 'kicked': kicked = true; break;            // connection is about to be closed
  }
}

// ------------------------------------------------------------------ game
/*
 * A match was announced. Set up the local game state and show the arena.
 *   you    – 0 or 1: which player we are
 *   cell   – size of one grid cell in CSS pixels (computed in layout())
 *   trails – for each player the list of grid points visited so far
 *   alive  – false once a cycle crashed (its trail stops growing)
 *   particles – explosion sparks, animated in frame()
 *   raf    – id of the running animation frame, to stop it again
 */
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
  $('#hud-p0').classList.toggle('you', msg.you === 0);   // CSS adds "(you)"
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

/*
 * Size the arena to fit the window. Called at match start and on every
 * window resize. The cell size is a whole number of pixels so grid lines
 * stay sharp. The canvases get devicePixelRatio times more real pixels than
 * CSS pixels, so the picture is crisp on high-resolution (Retina/phone) screens.
 */
function layout() {
  if (!game) return;
  const arena = $('#arena');
  const availW = Math.max(200, document.documentElement.clientWidth - 32);
  const reserved = document.querySelector('.top').offsetHeight + 130;   // header + HUD + hint text
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
    c.getContext('2d').setTransform(dpr, 0, 0, dpr, 0, 0);   // draw in CSS pixels from now on
  }
  redrawTrails();   // resizing a canvas erases it
}

// Pixel coordinates of the middle of grid cell p = [x, y].
function center(p) {
  const c = game.cell;
  return [p[0] * c + c / 2, p[1] * c + c / 2];
}

// Background grid: faint lines every 4 cells, brighter lines every 20 cells.
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

/*
 * Draw a piece of player i's light trail through the given grid points.
 * Each trail is drawn twice: a wide colored line with a big glow on the
 * #glow canvas, and a thin bright "core" on the #trails canvas above it.
 * Trails are only ever added to (never erased), so each tick draws just the
 * newest segment instead of repainting everything.
 */
function strokeTrail(i, points) {
  // Glow and core live on separate canvases, so a new segment's glow never
  // covers the bright core of the previous one.
  if (points.length < 2) return;
  const col = COLORS[i];
  const c = game.cell;
  //            canvas                         line width  color     glow size
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

// Repaint grid and complete trails (after a resize).
function redrawTrails() {
  drawGrid($('#glow').getContext('2d'));
  const core = $('#trails').getContext('2d');
  core.clearRect(0, 0, game.w * game.cell, game.h * game.cell);
  game.trails.forEach((pts, i) => strokeTrail(i, pts));
}

// Countdown number from the server: show it big and beep; 0 means "Go!".
function onCountdown(n) {
  if (!game) return;
  beep(n === 0 ? 880 : 440, n === 0 ? 0.35 : 0.12);   // higher, longer beep for "Go"
  if (n === 0) {
    game.running = true;
    overlay('0', 'Go!');
    setTimeout(() => { if (game && game.running && !game.over) overlay('', ''); }, 700);
  } else {
    overlay(String(n), n === 10 ? 'Get ready' : '');
  }
}

/*
 * One game step from the server: msg.heads[i] is the new head position of
 * player i, msg.crashed[i] whether that move was a crash. Extend each living
 * trail by one segment, or let it explode.
 */
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

// Match over: show the result and the "Play again" / "Menu" buttons.
function onEnd(msg) {
  if (!game) return;
  game.over = true;
  game.running = false;
  renderMe(msg.me);   // updated win/loss numbers for the menu
  const forfeit = msg.reason === 'forfeit';
  if (msg.result === 'win') {
    overlay('YOU WIN', forfeit ? 'Your opponent left the grid' : 'Your opponent crashed', 'win', true);
  } else if (msg.result === 'lose') {
    overlay('CRASHED', forfeit ? 'You left the match' : 'You lose this round', 'lose', true);
  } else {
    overlay('DRAW', msg.reason === 'error' ? 'Match aborted' : 'Both cycles crashed', 'draw', true);
  }
}

/*
 * Text over the arena: a big line (`big`), a small line (`sub`), an optional
 * CSS class for the color (win/lose/draw) and optionally the end-of-match buttons.
 */
function overlay(big, sub, cls = '', buttons = false) {
  const el = $('#ov-big');
  el.className = 'ov-big ' + cls;
  el.textContent = big;
  // Reading offsetWidth forces the browser to apply the class reset first,
  // so the "pop" animation restarts for every new countdown number.
  if (big) { void el.offsetWidth; el.classList.add('pop'); }
  $('#ov-sub').textContent = sub;
  $('#ov-buttons').hidden = !buttons;
  $('#overlay').classList.toggle('dim', buttons);
  if (buttons) $('#btn-again').focus();   // Enter = play again
}

// ------------------------------------------------------------------ effects
// A cycle crashed: crash sound and 60 sparks flying out in random directions.
function explode(i, head) {
  beepNoise();
  // The crash position may be outside the arena (hit the wall): clamp it to the edge.
  const [x, y] = center(head.map((v, k) => Math.max(0, Math.min(v, (k ? game.h : game.w) - 1))));
  for (let k = 0; k < 60; k++) {
    const a = Math.random() * Math.PI * 2;
    const s = (0.5 + Math.random() * 3.5) * game.cell * 0.4;
    game.particles.push({ x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s, life: 1, color: COLORS[i].line });
  }
}

/*
 * Animation loop, runs once per screen refresh (~60x per second) while a
 * match is shown. Draws only the moving things on the #fx canvas: the
 * pulsing glow of each cycle's head and the explosion sparks. The trails
 * themselves are drawn in onTick().
 */
function frame(t) {
  if (!game) return;
  const ctx = $('#fx').getContext('2d');
  const { w, h, cell } = game;
  ctx.clearRect(0, 0, w * cell, h * cell);
  const pulse = 0.75 + 0.25 * Math.sin(t / 90);   // between 0.5 and 1.0
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
  // Sparks: move, slow down (friction 0.94) and fade out over ~55 frames.
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

// --- Sound (generated with the Web Audio API, no sound files needed) ---------
let audio = null;
// Create/resume the audio context. Must happen inside a click handler,
// otherwise browsers keep it muted.
function unlockAudio() {
  try {
    if (!audio) audio = new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === 'suspended') audio.resume();
  } catch { audio = null; }
}
// Short retro "beep": a square wave at `freq` Hz that fades out over `dur` seconds.
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
// Crash sound: half a second of white noise that fades out.
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
// Steering: send arrow keys / WASD to the server while a match is on.
// preventDefault stops the arrow keys from scrolling the page.
document.addEventListener('keydown', (ev) => {
  const dir = KEYS[ev.key];
  if (!dir || !game || game.over || $('#screen-game').hidden) return;
  ev.preventDefault();
  send({ type: 'turn', dir });
});

// Buttons and form.
document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => setAuthMode(t.dataset.mode)));
$('#auth-form').addEventListener('submit', submitAuth);
$('#btn-play').addEventListener('click', play);
$('#btn-logout').addEventListener('click', logout);
$('#btn-cancel').addEventListener('click', () => { send({ type: 'leave' }); show('menu'); });
$('#btn-again').addEventListener('click', () => { stopGame(); show('menu'); play(); });
$('#btn-menu').addEventListener('click', () => { stopGame(); show('menu'); });
window.addEventListener('resize', layout);

// Page start: if a token is saved, check it with api/me and go straight to
// the menu; otherwise (or if it expired) show the login form.
(async function init() {
  setAuthMode('login');
  if (token) {
    try {
      const data = await api('me');
      renderMe(data.user);
      show('menu');
      return;
    } catch (e) {
      if (e.status === 401) saveToken('');   // expired: forget it. Other errors: keep it for later.
    }
  }
  show('auth');
})();
