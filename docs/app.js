// Octane Comp — démo navigateur. Même modèle de compositing que comp/engine.py,
// mêmes transformations couleur (GLSL ES généré par OpenColorIO, voir ocio.js).
import { META } from "./ocio.js";

const $ = (s) => document.querySelector(s);
const OBJ = META.objects;
const FPS = META.fps;
const NFR = META.frames;
const deep = (o) => JSON.parse(JSON.stringify(o));

// ------------------------------------------------------------------ état
const DEFAULTS = deep(META.defaults);
const S = deep(DEFAULTS);
const V = { mode: 0, wipe: 0.5, channel: 0, exposure: 0, gamma: 1, overlay: -1, bsrc: 0, zoom: 1, panX: 0, panY: 0, fit: true };
let picking = null; // "cg_mask" | "reflection_mask" | "shadow_mask"
let selectedNode = "Recompose1";

// ------------------------------------------------------------------ WebGL
const canvas = $("#gl");
const gl = canvas.getContext("webgl2", { antialias: false, premultipliedAlpha: false, preserveDrawingBuffer: false });
const video = $("#video");

const VERT = `#version 300 es
in vec2 pos; uniform vec4 rect; out vec2 uv;
void main() { vec2 t = pos * 0.5 + 0.5; uv = vec2(t.x, 1.0 - t.y);
  gl_Position = vec4(mix(rect.xy, rect.zw, t), 0.0, 1.0); }`;

const FRAG = `#version 300 es
precision highp float;
precision highp sampler2D;
${META.input.glsl}
${META.output.glsl}
in vec2 uv; out vec4 frag;
uniform sampler2D atlas;
uniform vec2 tileSize, atlasSize;
uniform float logMin, logMax;
uniform float plate_exp, cg_gain, diffuse_gain, refl_gain, shadow_density;
uniform vec3 refl_tint, shadow_color;
uniform bool shadow_invert;
uniform vec3 sel_cg, sel_refl, sel_shadow;   // objets choisis par matte
uniform vec3 m_cg, m_refl, m_shadow;         // actif, inversé, mix
uniform int mode, channel, overlay, bsrc;
uniform float wipe, exposure, gamma, wipePx;

vec2 T(int i) {
  vec2 cell = vec2(float(i % 3), float(i / 3));
  vec2 px = clamp(uv * tileSize, vec2(0.5), tileSize - 0.5);
  return (cell * tileSize + px) / atlasSize;
}
vec3 rgbT(int i) { return texture(atlas, T(i)).rgb; }
float grT(int i) { return texture(atlas, T(i)).g; }
vec3 dec(vec3 e) { return mix(vec3(0.0), exp2(e * (logMax - logMin) + logMin), step(vec3(1.5 / 255.0), e)); }
float maskw(float raw, vec3 p) { if (p.x < 0.5) return 1.0; float v = p.y > 0.5 ? 1.0 - raw : raw; return mix(1.0, v, p.z); }

vec3 plateLin() { return OCIOInput(vec4(rgbT(0), 1.0)).rgb; }
vec4 comp(bool neutral, vec3 objm) {
  vec3 bg = plateLin() * exp2(neutral ? 0.0 : plate_exp);
  vec4 b = vec4(dec(rgbT(1)), grT(4));
  vec3 one = vec3(0.0, 0.0, 1.0);
  float mcg = maskw(clamp(dot(objm, sel_cg), 0.0, 1.0), neutral ? one : m_cg);
  float mrf = maskw(clamp(dot(objm, sel_refl), 0.0, 1.0), neutral ? one : m_refl);
  float msh = maskw(clamp(dot(objm, sel_shadow), 0.0, 1.0), neutral ? one : m_shadow);
  vec3 cg = b.rgb;
  cg += ((neutral ? 1.0 : diffuse_gain) - 1.0) * dec(rgbT(2));
  cg += ((neutral ? vec3(1.0) : refl_gain * refl_tint) - 1.0) * dec(rgbT(3)) * mrf;
  cg *= (neutral ? 1.0 : cg_gain) * mcg;
  float a = clamp(b.a * mcg, 0.0, 1.0);
  float s = grT(5);
  if (shadow_invert) s = 1.0 - s;
  s = clamp(s * (neutral ? 1.0 : shadow_density) * msh, 0.0, 1.0);
  vec3 bgs = mix(bg, bg * (neutral ? vec3(0.0) : shadow_color), s);
  return vec4(cg + bgs * (1.0 - a), a);
}

void main() {
  vec3 objm = vec3(grT(6), grT(7), grT(8));
  vec4 A = comp(false, objm);
  vec4 B = bsrc == 0 ? vec4(plateLin(), 0.0) : comp(true, objm);
  vec4 c = A;
  if (mode == 1) c = B;
  else if (mode == 2) c = uv.x < wipe ? A : B;
  else if (mode == 3) c = vec4(abs(A.rgb - B.rgb), 1.0);
  c.rgb *= exp2(exposure);
  if (channel == 1) c.rgb = c.rrr; else if (channel == 2) c.rgb = c.ggg;
  else if (channel == 3) c.rgb = c.bbb; else if (channel == 4) c.rgb = vec3(c.a);
  else if (channel == 5) c.rgb = vec3(dot(c.rgb, vec3(0.2722287, 0.6740818, 0.0536895)));
  vec3 o = OCIODisplay(vec4(c.rgb, 1.0)).rgb;
  if (gamma != 1.0) o = pow(max(o, 0.0), vec3(1.0 / gamma));
  if (overlay >= 0) {
    vec3 sel = overlay == 0 ? sel_cg : overlay == 1 ? sel_refl : sel_shadow;
    float m = clamp(dot(objm, sel), 0.0, 1.0);
    o = mix(o, vec3(1.0, 0.78, 0.15), 0.45 * m);
  }
  if (mode == 2 && abs(uv.x - wipe) < wipePx) o = vec3(0.94, 0.59, 0.18);
  frag = vec4(clamp(o, 0.0, 1.0), 1.0);
}`;

function compile(type, src) {
  const sh = gl.createShader(type);
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh));
  return sh;
}
let prog, U = {}, atlasTex, lutUnits = [];
function initGL() {
  if (!gl) throw new Error("WebGL2 indisponible sur ce navigateur");
  prog = gl.createProgram();
  gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT));
  gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FRAG));
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
  gl.useProgram(prog);
  const n = gl.getProgramParameter(prog, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < n; i++) { const u = gl.getActiveUniform(prog, i); U[u.name] = gl.getUniformLocation(prog, u.name); }
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, "pos");
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

  atlasTex = gl.createTexture();
  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, atlasTex);
  for (const [p, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR],
    [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, p, v);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array(4));
  gl.uniform1i(U.atlas, 0);

  // LUT OCIO (tables ACES 2.0) en textures flottantes
  const floatLinear = !!gl.getExtension("OES_texture_float_linear");
  let unit = 1;
  for (const t of [...META.input.textures, ...META.output.textures]) {
    const bin = atob(t.data);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const data = new Float32Array(bytes.buffer);
    const tex = gl.createTexture();
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    if (t.ch === 1) gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32F, t.w, t.h, 0, gl.RED, gl.FLOAT, data);
    else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB32F, t.w, t.h, 0, gl.RGB, gl.FLOAT, data);
    const f = t.linear && floatLinear ? gl.LINEAR : gl.NEAREST;
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, f);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, f);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    if (U[t.sampler]) gl.uniform1i(U[t.sampler], unit);
    lutUnits.push(tex);
    unit++;
  }
  gl.activeTexture(gl.TEXTURE0);
  const [tw, th] = META.tile;
  gl.uniform2f(U.tileSize, tw, th);
  gl.uniform2f(U.atlasSize, tw * 3, th * 3);
  gl.uniform1f(U.logMin, META.logMin);
  gl.uniform1f(U.logMax, META.logMax);
}

// ------------------------------------------------------------------ rendu
let haveFrame = false, needUpload = false;
function imageRect() {
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.width, H = canvas.height;
  const [iw, ih] = META.source;
  if (V.fit) { V.zoom = Math.min(W / iw, H / ih) * 0.94; V.panX = V.panY = 0; V.fit = false; }
  const cx = W / 2 + V.panX, cy = H / 2 + V.panY;
  return { l: cx - iw * V.zoom / 2, t: cy - ih * V.zoom / 2, r: cx + iw * V.zoom / 2, b: cy + ih * V.zoom / 2, dpr, W, H };
}
const sel3 = (m) => OBJ.map((o) => (m.objects.includes(o) ? 1 : 0));
const mvec = (m) => [m.objects.length ? 1 : 0, m.invert ? 1 : 0, m.mix];

function draw() {
  const r = imageRect();
  gl.viewport(0, 0, r.W, r.H);
  gl.clearColor(0.102, 0.102, 0.102, 1);
  gl.clear(gl.COLOR_BUFFER_BIT);
  if (!haveFrame) return;
  gl.useProgram(prog);
  gl.uniform4f(U.rect, 2 * r.l / r.W - 1, 1 - 2 * r.b / r.H, 2 * r.r / r.W - 1, 1 - 2 * r.t / r.H);
  gl.uniform1f(U.plate_exp, S.plate_exposure);
  gl.uniform1f(U.cg_gain, S.cg_gain);
  gl.uniform1f(U.diffuse_gain, S.diffuse_gain);
  gl.uniform1f(U.refl_gain, S.reflection_gain);
  gl.uniform3fv(U.refl_tint, S.reflection_tint);
  gl.uniform1f(U.shadow_density, S.shadow_density);
  gl.uniform3fv(U.shadow_color, S.shadow_color);
  gl.uniform1i(U.shadow_invert, S.shadow_invert ? 1 : 0);
  gl.uniform3fv(U.sel_cg, sel3(S.cg_mask));
  gl.uniform3fv(U.sel_refl, sel3(S.reflection_mask));
  gl.uniform3fv(U.sel_shadow, sel3(S.shadow_mask));
  gl.uniform3fv(U.m_cg, mvec(S.cg_mask));
  gl.uniform3fv(U.m_refl, mvec(S.reflection_mask));
  gl.uniform3fv(U.m_shadow, mvec(S.shadow_mask));
  gl.uniform1i(U.mode, V.mode);
  gl.uniform1i(U.channel, V.channel);
  gl.uniform1i(U.overlay, V.overlay);
  gl.uniform1i(U.bsrc, V.bsrc);
  gl.uniform1f(U.wipe, V.wipe);
  gl.uniform1f(U.wipePx, 1.0 * r.dpr / Math.max(r.r - r.l, 1));
  gl.uniform1f(U.exposure, V.exposure);
  gl.uniform1f(U.gamma, V.gamma);
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
}

function resize() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.round(canvas.clientWidth * dpr), h = Math.round(canvas.clientHeight * dpr);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; V.fit = true; }
}

function upload() {
  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, atlasTex);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, video);
  haveFrame = true;
}

let frames = 0, lastT = performance.now();
function loop() {
  resize();
  if (video.readyState >= 2 && (needUpload || !video.paused)) { upload(); needUpload = false; updateFrameUI(); }
  draw();
  frames++;
  const now = performance.now();
  if (now - lastT > 500) {
    $("#gpu").textContent = `viewer ${Math.round(frames * 1000 / (now - lastT))} i/s`;
    $("#fps").textContent = video.paused ? "" : `${(frames * 1000 / (now - lastT)).toFixed(0)} i/s`;
    frames = 0; lastT = now;
  }
  requestAnimationFrame(loop);
}

// ------------------------------------------------------------------ timeline
const curFrame = () => Math.min(NFR - 1, Math.floor(video.currentTime * FPS + 1e-3));
function seek(i) {
  i = ((i % NFR) + NFR) % NFR;
  video.currentTime = (i + 0.5) / FPS;
}
function updateFrameUI() {
  const f = curFrame();
  if (document.activeElement !== $("#frameNum")) $("#frameNum").value = f + 1;
  if (!scrubbing) $("#scrub").value = f;
  if (!hovering) setIdleInfo();
}
let scrubbing = false;
function initTimeline() {
  $("#scrub").max = NFR - 1;
  $("#frameNum").max = NFR;
  $("#range").textContent = `1 – ${NFR}`;
  $("#play").onclick = togglePlay;
  $("#first").onclick = () => { pause(); seek(0); };
  $("#prev").onclick = () => { pause(); seek(curFrame() - 1); };
  $("#next").onclick = () => { pause(); seek(curFrame() + 1); };
  $("#scrub").addEventListener("input", (e) => { scrubbing = true; pause(); seek(+e.target.value); });
  $("#scrub").addEventListener("change", () => { scrubbing = false; });
  $("#frameNum").addEventListener("change", (e) => { pause(); seek(+e.target.value - 1); });
  video.addEventListener("seeked", () => { needUpload = true; });
  video.addEventListener("loadeddata", () => { needUpload = true; $("#splash").classList.add("hide"); });
  video.addEventListener("error", () => { $("#splash").innerHTML = "<span>Impossible de lire la vidéo des passes.</span>"; });
}
function togglePlay() { video.paused ? play() : pause(); }
function play() { video.play().catch(() => {}); $("#play").textContent = "❚❚"; }
function pause() { video.pause(); $("#play").textContent = "▶"; needUpload = true; }

// ------------------------------------------------------------------ lecture des mattes (pipette, survol)
const probe = document.createElement("canvas");
probe.width = probe.height = 1;
const pctx = probe.getContext("2d", { willReadFrequently: true });
function objectAt(u, v) {
  if (video.readyState < 2) return null;
  const [tw, th] = META.tile;
  let best = null, bestV = 0.5;
  OBJ.forEach((name, k) => {
    const i = 6 + k;
    const sx = (i % 3) * tw + Math.min(Math.max(u * tw, 0), tw - 1);
    const sy = Math.floor(i / 3) * th + Math.min(Math.max(v * th, 0), th - 1);
    pctx.drawImage(video, sx, sy, 1, 1, 0, 0, 1, 1);
    const g = pctx.getImageData(0, 0, 1, 1).data[1] / 255;
    if (g > bestV) { bestV = g; best = name; }
  });
  return best;
}

// ------------------------------------------------------------------ viewer : souris / tactile
let hovering = false, drag = null;
function toImage(e) {
  const r = imageRect();
  const rect = canvas.getBoundingClientRect();
  const x = (e.clientX - rect.left) * (canvas.width / rect.width);
  const y = (e.clientY - rect.top) * (canvas.height / rect.height);
  return { u: (x - r.l) / (r.r - r.l), v: (y - r.t) / (r.b - r.t), x, y, r };
}
function setIdleInfo() {
  const info = $("#info");
  info.classList.toggle("pick", !!picking);
  if (picking) { info.textContent = "PIPETTE CRYPTOMATTE · clic : ajouter l'objet · Maj+clic : retirer · Échap : quitter"; return; }
  const [iw, ih] = META.source;
  const m = ["A", "B", "wipe A | B", "différence"][V.mode];
  info.textContent = `${iw}×${ih} · frame ${curFrame() + 1}/${NFR} · vue ${m}`;
}
function initViewer() {
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const p = toImage(e);
    const f = Math.pow(1.0015, -e.deltaY);
    const nz = Math.min(Math.max(V.zoom * f, 0.05), 32);
    const k = nz / V.zoom;
    const mx = p.x - p.r.W / 2, my = p.y - p.r.H / 2;
    V.panX = mx - (mx - V.panX) * k;
    V.panY = my - (my - V.panY) * k;
    V.zoom = nz;
  }, { passive: false });
  canvas.addEventListener("pointerdown", (e) => {
    const p = toImage(e);
    canvas.setPointerCapture(e.pointerId);
    if (e.button === 1 || e.altKey) { drag = { pan: true, x: e.clientX, y: e.clientY, px: V.panX, py: V.panY }; return; }
    if (picking) {
      if (p.u >= 0 && p.u <= 1 && p.v >= 0 && p.v <= 1) {
        const name = objectAt(p.u, p.v);
        if (name) toggleObject(picking, name, !e.shiftKey);
      }
      return;
    }
    if (V.mode === 2) { drag = { wipe: true }; V.wipe = Math.min(Math.max(p.u, 0), 1); }
  });
  canvas.addEventListener("pointermove", (e) => {
    const p = toImage(e);
    if (drag && drag.pan) {
      const s = canvas.width / canvas.getBoundingClientRect().width;
      V.panX = drag.px + (e.clientX - drag.x) * s;
      V.panY = drag.py + (e.clientY - drag.y) * s;
    } else if (drag && drag.wipe) {
      V.wipe = Math.min(Math.max(p.u, 0), 1);
    }
    if (e.pointerType === "mouse" && p.u >= 0 && p.u <= 1 && p.v >= 0 && p.v <= 1) {
      hovering = true;
      const [iw, ih] = META.source;
      const name = objectAt(p.u, p.v);
      $("#info").textContent = `${picking ? "PIPETTE · " : ""}x ${Math.floor(p.u * iw)}  y ${Math.floor(p.v * ih)}` + (name ? `  ·  ${name}` : "");
      const h = document.getElementById("hoverObj");
      if (h) h.textContent = name || "—";
    } else if (hovering) { hovering = false; setIdleInfo(); }
  });
  const end = () => { drag = null; };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("pointerleave", () => { hovering = false; setIdleInfo(); });
  canvas.addEventListener("dblclick", () => { V.fit = true; });
}

// ------------------------------------------------------------------ barre du viewer
function knob(el, get, set) {
  const min = +el.dataset.min, max = +el.dataset.max, def = +el.dataset.def, dec = +(el.dataset.dec || 3);
  const num = document.createElement("input");
  num.type = "number"; num.step = dec >= 3 ? "0.01" : "0.1";
  const rng = document.createElement("input");
  rng.type = "range"; rng.min = min; rng.max = max; rng.step = (max - min) / 1000;
  rng.title = "Double-clic : valeur par défaut";
  const show = (v) => { num.value = (+v).toFixed(dec); rng.value = v; };
  show(get());
  num.addEventListener("change", () => { const v = +num.value; if (!isNaN(v)) { set(v); show(v); } });
  rng.addEventListener("input", () => { set(+rng.value); num.value = (+rng.value).toFixed(dec); });
  rng.addEventListener("dblclick", () => { set(def); show(def); });
  el.append(num, rng);
  el._show = () => show(get());
  return el;
}
function initViewerBar() {
  document.querySelectorAll(".viewerbar .knob").forEach((el) => {
    const k = el.dataset.view;
    knob(el, () => V[k], (v) => { V[k] = v; });
  });
  $("#channel").onchange = (e) => { V.channel = +e.target.value; };
  $("#bsrc").onchange = (e) => { V.bsrc = +e.target.value; };
  $("#overlay").onchange = (e) => { V.overlay = +e.target.value; };
  document.querySelectorAll("#mode button").forEach((b) => (b.onclick = () => setMode(+b.dataset.v)));
  $("#viewName").textContent = `${META.display} · ${META.view}`;
}
function setMode(m) {
  V.mode = m;
  document.querySelectorAll("#mode button").forEach((b) => b.classList.toggle("on", +b.dataset.v === m));
  setIdleInfo();
}

// ------------------------------------------------------------------ Properties
const NODES = [
  ["Read1", "Read", "#9a9a9a"], ["Read2", "Read", "#9a9a9a"], ["Cryptomatte1", "Cryptomatte", "#7f9a45"],
  ["Recompose1", "Recompose", "#6c9a6a"], ["ShadowCatcher1", "ShadowCatcher", "#8a7d9e"],
  ["Merge1", "Merge", "#4f78a8"], ["OCIODisplay1", "OCIODisplay", "#6c9a6a"],
];
const panels = {};
const refreshers = [];
function getPath(p) { return p.split(".").reduce((o, k) => o[k], S); }
function setPath(p, v) { const ks = p.split("."); const last = ks.pop(); ks.reduce((o, k) => o[k], S)[last] = v; }

function panel(name, klass, color) {
  const wrap = document.createElement("div");
  wrap.className = "node-panel";
  wrap.id = "panel-" + name;
  const head = document.createElement("div");
  head.className = "node-head";
  head.innerHTML = `<span class="dot" style="background:${color}"></span><span class="name">${name}</span><span class="klass">${klass}</span><span class="chev">▾</span>`;
  head.onclick = () => { wrap.classList.toggle("collapsed"); head.querySelector(".chev").textContent = wrap.classList.contains("collapsed") ? "▸" : "▾"; };
  const body = document.createElement("div");
  body.className = "node-body";
  wrap.append(head, body);
  $("#props").append(wrap);
  panels[name] = wrap;
  const api = {
    row(label, el) {
      const k = document.createElement("div"); k.className = "k"; k.textContent = label;
      const v = document.createElement("div"); v.className = "v"; v.append(el);
      body.append(k, v); return el;
    },
    info(label, text, id) {
      const el = document.createElement("div"); el.className = "info"; el.textContent = text; if (id) el.id = id;
      return api.row(label, el);
    },
    section(t) { const s = document.createElement("div"); s.className = "section"; s.textContent = t.toUpperCase(); body.append(s); },
    float(label, path, min, max, def, dec = 3) {
      const el = document.createElement("div");
      el.className = "knob"; Object.assign(el.dataset, { min, max, def, dec });
      knob(el, () => getPath(path), (v) => setPath(path, v));
      refreshers.push(() => el._show());
      return api.row(label, el);
    },
    color(label, path) {
      const el = document.createElement("div"); el.className = "colorknob";
      const nums = [0, 1, 2].map((i) => {
        const n = document.createElement("input"); n.type = "number"; n.step = "0.01"; n.min = 0;
        n.addEventListener("change", () => { const c = getPath(path).slice(); c[i] = Math.max(0, +n.value || 0); setPath(path, c); show(); });
        return n;
      });
      const sw = document.createElement("input"); sw.type = "color"; sw.title = "Couleur (encodée sRGB, stockée linéaire)";
      sw.addEventListener("input", () => {
        const h = sw.value; const c = [1, 3, 5].map((o) => Math.pow(parseInt(h.substr(o, 2), 16) / 255, 2.2));
        setPath(path, c); show();
      });
      const show = () => {
        const c = getPath(path);
        nums.forEach((n, i) => (n.value = c[i].toFixed(3)));
        sw.value = "#" + c.map((x) => Math.round(Math.pow(Math.min(Math.max(x, 0), 1), 1 / 2.2) * 255).toString(16).padStart(2, "0")).join("");
      };
      show(); refreshers.push(show);
      el.append(...nums, sw);
      return api.row(label, el);
    },
    check(label, text, path) {
      const l = document.createElement("label"); l.className = "check";
      const c = document.createElement("input"); c.type = "checkbox";
      c.onchange = () => setPath(path, c.checked);
      refreshers.push(() => (c.checked = !!getPath(path)));
      l.append(c, document.createTextNode(text));
      return api.row(label, l);
    },
    matte(label, key) {
      const el = document.createElement("div"); el.className = "matte";
      const btns = document.createElement("div"); btns.className = "btns";
      const pick = document.createElement("button"); pick.textContent = "Pipette"; pick.dataset.matte = key;
      pick.title = "Cliquer un objet dans le viewer";
      pick.onclick = () => setPicking(picking === key ? null : key);
      const clear = document.createElement("button"); clear.textContent = "Vider";
      clear.onclick = () => { S[key].objects = []; renderChips(); };
      const inv = document.createElement("label"); inv.className = "check";
      const ic = document.createElement("input"); ic.type = "checkbox"; ic.onchange = () => (S[key].invert = ic.checked);
      inv.append(ic, document.createTextNode("inv."));
      btns.append(pick, clear, inv);
      const chips = document.createElement("div"); chips.className = "chips";
      const renderChips = () => {
        chips.innerHTML = "";
        OBJ.forEach((o) => {
          const c = document.createElement("span");
          c.className = "chip" + (S[key].objects.includes(o) ? " on" : "");
          c.textContent = o; c.title = "Cliquer pour ajouter / retirer";
          c.onclick = () => toggleObject(key, o, !S[key].objects.includes(o));
          chips.append(c);
        });
        ic.checked = S[key].invert;
      };
      renderChips();
      refreshers.push(renderChips);
      el._render = renderChips;
      mattes[key] = el;
      el.append(btns, chips);
      return api.row(label, el);
    },
  };
  return api;
}
const mattes = {};
function toggleObject(key, name, on) {
  const objs = S[key].objects.filter((o) => o !== name);
  if (on) objs.push(name);
  S[key].objects = objs;
  S[key].layer = META.cryptoLayer;
  mattes[key]._render();
}
function setPicking(key) {
  picking = key;
  document.querySelectorAll(".matte button[data-matte]").forEach((b) => b.classList.toggle("on", b.dataset.matte === key));
  $("#viewerBox").classList.toggle("picking", !!key);
  if (key) {
    const idx = { cg_mask: 0, reflection_mask: 1, shadow_mask: 2 }[key];
    V.overlay = idx; $("#overlay").value = String(idx);
  }
  setIdleInfo();
}

function buildProps() {
  let p = panel("Read1", "Read", "#9a9a9a");
  p.info("file", "plate_flog2.mov  (démo de synthèse)");
  p.info("input transform", META.plateSpace + " → ACEScg");
  p.float("exposure", "plate_exposure", -4, 4, 0, 2);
  p = panel("Read2", "Read", "#9a9a9a");
  p.info("file", "octane.####.exr");
  p.info("passes", "Beauty (rgba), Diffuse, Reflection, Shadow, CryptoObject");
  p.info("input transform", "ACEScg");
  p = panel("Cryptomatte1", "Cryptomatte", "#7f9a45");
  p.info("layer", META.cryptoLayer);
  p.info("manifest", `${OBJ.length} objets : ${OBJ.join(", ")}`);
  p.info("sous le curseur", "—", "hoverObj");
  p = panel("Recompose1", "Recompose", "#6c9a6a");
  p.float("gain", "cg_gain", 0, 4, 1);
  p.float("diffuse", "diffuse_gain", 0, 4, 1);
  p.section("reflets");
  p.float("balance reflets", "reflection_gain", 0, 4, 1);
  p.color("teinte", "reflection_tint");
  p.matte("matte", "reflection_mask");
  p.float("mix masque", "reflection_mask.mix", 0, 1, 1);
  p = panel("ShadowCatcher1", "ShadowCatcher", "#8a7d9e");
  p.float("densité", "shadow_density", 0, 3, 1);
  p.color("couleur", "shadow_color");
  p.check("", "inverser la passe Shadow", "shadow_invert");
  p.matte("matte", "shadow_mask");
  p.float("mix masque", "shadow_mask.mix", 0, 1, 1);
  p = panel("Merge1", "Merge", "#4f78a8");
  p.info("operation", "over");
  p.matte("matte CG", "cg_mask");
  p.float("mix masque", "cg_mask.mix", 0, 1, 1);
  p = panel("OCIODisplay1", "OCIODisplay", "#6c9a6a");
  p.info("config", `${META.config} + F-Log2 F-Gamut`);
  p.info("OCIO", META.ocioVersion + " (shaders GLSL générés)");
  p.info("display / view", `${META.display} · ${META.view}`);

  const tools = document.createElement("div");
  tools.className = "tools";
  const mk = (t, title, fn) => { const b = document.createElement("button"); b.className = "tool"; b.textContent = t; b.title = title; b.onclick = fn; tools.append(b); };
  mk("Exporter JSON", "Réglages au format de l'application (rendu batch)", exportJSON);
  mk("Importer JSON", "Recharger des réglages", () => fileIn.click());
  mk("Réinitialiser", "Revenir aux réglages de la démo", () => { Object.assign(S, deep(DEFAULTS)); refreshers.forEach((f) => f()); });
  const fileIn = document.createElement("input"); fileIn.type = "file"; fileIn.accept = ".json,application/json"; fileIn.hidden = true;
  fileIn.onchange = async () => {
    const f = fileIn.files[0]; if (!f) return;
    try {
      const d = JSON.parse(await f.text());
      for (const k of Object.keys(DEFAULTS)) if (k in d) S[k] = d[k];
      refreshers.forEach((fn) => fn());
    } catch (e) { $("#status").textContent = "JSON illisible : " + e.message; }
    fileIn.value = "";
  };
  tools.append(fileIn);
  $("#props").append(tools);
  refreshers.forEach((f) => f());
  if (window.matchMedia("(max-width: 980px)").matches) {  // téléphone : on va droit aux réglages
    for (const n of ["Read1", "Read2", "Cryptomatte1", "OCIODisplay1"]) {
      panels[n].classList.add("collapsed");
      panels[n].querySelector(".chev").textContent = "▸";
    }
  }
}
function exportJSON() {
  const d = { version: 1, plate_path: "", cg_path: "", ...deep(S) };
  const blob = new Blob([JSON.stringify(d, null, 2) + "\n"], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "reglages.json"; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ------------------------------------------------------------------ Node Graph
function buildGraph() {
  const N = { Read1: [0, 0, "F-Log2 rush"], Read2: [300, 0, "Octane EXR"], Cryptomatte1: [470, 55, ""],
    Recompose1: [300, 100, "diffuse · reflets"], ShadowCatcher1: [0, 100, "densité ombres"],
    Merge1: [150, 175, "over"], OCIODisplay1: [150, 235, ""], Viewer1: [150, 290, ""] };
  const C = { Read1: "#9a9a9a", Read2: "#9a9a9a", Cryptomatte1: "#7f9a45", Recompose1: "#6c9a6a",
    ShadowCatcher1: "#8a7d9e", Merge1: "#4f78a8", OCIODisplay1: "#6c9a6a", Viewer1: "#b8945a" };
  const E = [["Read1", "ShadowCatcher1", ""], ["Read2", "Recompose1", ""], ["Read2", "Cryptomatte1", ""],
    ["Cryptomatte1", "Recompose1", "mask"], ["Cryptomatte1", "ShadowCatcher1", "mask"], ["Cryptomatte1", "Merge1", "mask"],
    ["Recompose1", "Merge1", "A"], ["ShadowCatcher1", "Merge1", "B"], ["Merge1", "OCIODisplay1", ""], ["OCIODisplay1", "Viewer1", ""]];
  const W = 116, H = 26;
  let s = `<svg viewBox="-40 -20 666 360" preserveAspectRatio="xMidYMid meet"><defs><marker id="ar" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#8a8a8a"/></marker></defs>`;
  for (const [a, b, l] of E) {
    const [ax, ay] = N[a], [bx, by] = N[b];
    const x1 = ax + W / 2, y1 = ay + H;
    let x2 = bx + W / 2, y2 = by;
    if (l === "mask") { x2 = bx + W; y2 = by + H / 2; }
    if (l === "A") x2 += 18;
    if (l === "B") x2 -= 18;
    s += `<path class="edge ${l === "mask" ? "mask" : ""}" d="M${x1},${y1} L${x2},${y2}" marker-end="url(#ar)"/>`;
    if (l) s += `<text class="elabel" x="${x1 + (x2 - x1) * 0.8 + 4}" y="${y1 + (y2 - y1) * 0.8}">${l}</text>`;
  }
  for (const [n, [x, y, sub]] of Object.entries(N)) {
    s += `<g class="node${n === selectedNode ? " sel" : ""}" data-n="${n}" transform="translate(${x},${y})">
      <rect width="${W}" height="${H}" rx="3" fill="${C[n]}"/>
      <text x="${W / 2}" y="${H / 2 + 3.5}" text-anchor="middle">${n}</text>
      ${sub ? `<text class="sub" x="${W / 2}" y="${H + 10}" text-anchor="middle">${sub}</text>` : ""}</g>`;
  }
  s += "</svg>";
  $("#graph").innerHTML = s;
  $("#graph").querySelectorAll(".node").forEach((g) => (g.onclick = () => selectNode(g.dataset.n)));
}
function selectNode(n, scroll = true) {
  selectedNode = n;
  $("#graph").querySelectorAll(".node").forEach((g) => g.classList.toggle("sel", g.dataset.n === n));
  Object.entries(panels).forEach(([k, el]) => el.classList.toggle("sel", k === n));
  const el = panels[n];
  if (el && scroll) {
    el.classList.remove("collapsed");
    const box = $("#props");
    if (box.scrollHeight > box.clientHeight) box.scrollTo({ top: el.offsetTop - box.offsetTop, behavior: "smooth" });
    else el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

// ------------------------------------------------------------------ clavier
function initKeys() {
  window.addEventListener("keydown", (e) => {
    if (e.target.matches("input, select, textarea")) return;
    const k = e.key;
    if (k === " ") { e.preventDefault(); togglePlay(); }
    else if (k === "ArrowRight") { pause(); seek(curFrame() + 1); }
    else if (k === "ArrowLeft") { pause(); seek(curFrame() - 1); }
    else if (k === "1") setMode(0);
    else if (k === "2") setMode(1);
    else if (k === "w" || k === "W") setMode(2);
    else if (k === "d" || k === "D") setMode(3);
    else if (k === "f" || k === "F") V.fit = true;
    else if (k === "Escape" && picking) setPicking(null);
  });
}

// ------------------------------------------------------------------ démarrage
function start() {
  $("#status").textContent = `working ACEScg · rush ${META.plateSpace} → ACEScg · CG ACEScg · sortie ${META.display} · ${META.view} · OCIO ${META.ocioVersion} · scène de synthèse`;
  $("#aboutBtn").onclick = () => $("#about").showModal();
  try { initGL(); } catch (e) {
    $("#splash").innerHTML = `<span>WebGL2 indisponible : ${e.message.slice(0, 200)}</span>`;
    console.error(e);
    return;
  }
  initViewerBar(); buildProps(); buildGraph(); selectNode(selectedNode, false); initTimeline(); initViewer(); initKeys();
  setIdleInfo();
  window.__octane = { S, V, seek, play, pause, setMode, setPicking, toggleObject };
  requestAnimationFrame(loop);
  video.load();
  play();
}
start();
