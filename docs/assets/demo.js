/* Hindsight sculpt demo. Plain WebGL 1, no libraries.
 *
 * A clay blob you can push and pull. Every stroke is stored the way the add-on stores it: only the
 * vertices it moved, with their values before and after. The slider jumps by applying those
 * deltas, so going back and forward is exact (bit for bit). Morph History paints the shape of an
 * earlier step back onto the area under the brush. Movie Mode plays the steps in order.
 */
(function () {
  "use strict";

  var root = document.getElementById("demo");
  if (!root) return;
  var stage = root.querySelector(".demo-stage");
  var canvas = stage.querySelector("canvas");
  var ring = stage.querySelector(".ring");
  var hint = stage.querySelector(".hint");
  var track = root.querySelector(".track");
  var fill = track.querySelector(".fill");
  var knob = track.querySelector(".knob");
  var tick = track.querySelector(".tick");
  var count = root.querySelector(".count");
  var readout = root.querySelector(".readout");
  var live = root.querySelector("[aria-live]");
  var menu = root.querySelector(".menu");
  var btnBack = root.querySelector("[data-act=back]");
  var btnFwd = root.querySelector("[data-act=fwd]");
  var btnPlay = root.querySelector("[data-act=play]");
  var btnMenu = root.querySelector("[data-act=menu]");
  var btnReset = root.querySelector("[data-act=reset]");
  var toolBtns = root.querySelectorAll("[data-tool]");
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ------------------------------------------------------------------ mesh: a subdivided icosphere
  function icosphere(level) {
    var t = (1 + Math.sqrt(5)) / 2;
    var v = [-1, t, 0, 1, t, 0, -1, -t, 0, 1, -t, 0, 0, -1, t, 0, 1, t, 0, -1, -t, 0, 1, -t,
      t, 0, -1, t, 0, 1, -t, 0, -1, -t, 0, 1];
    var f = [0, 11, 5, 0, 5, 1, 0, 1, 7, 0, 7, 10, 0, 10, 11, 1, 5, 9, 5, 11, 4, 11, 10, 2, 10, 7, 6, 7, 1, 8,
      3, 9, 4, 3, 4, 2, 3, 2, 6, 3, 6, 8, 3, 8, 9, 4, 9, 5, 2, 4, 11, 6, 2, 10, 8, 6, 7, 9, 8, 1];
    for (var i = 0; i < v.length; i += 3) {
      var l = Math.hypot(v[i], v[i + 1], v[i + 2]);
      v[i] /= l; v[i + 1] /= l; v[i + 2] /= l;
    }
    for (var s = 0; s < level; s++) {
      var cache = {};
      var nf = [];
      var mid = function (a, b) {
        var key = a < b ? a + "_" + b : b + "_" + a;
        if (cache[key] !== undefined) return cache[key];
        var x = (v[a * 3] + v[b * 3]) / 2, y = (v[a * 3 + 1] + v[b * 3 + 1]) / 2, z = (v[a * 3 + 2] + v[b * 3 + 2]) / 2;
        var len = Math.hypot(x, y, z);
        v.push(x / len, y / len, z / len);
        return (cache[key] = v.length / 3 - 1);
      };
      for (var k = 0; k < f.length; k += 3) {
        var a = f[k], b = f[k + 1], c = f[k + 2];
        var ab = mid(a, b), bc = mid(b, c), ca = mid(c, a);
        nf.push(a, ab, ca, b, bc, ab, c, ca, bc, ab, bc, ca);
      }
      f = nf;
    }
    return { pos: new Float32Array(v), idx: new Uint16Array(f) };
  }

  var T0 = performance.now(), TIMES = {};
  var mesh = icosphere(5);                 // 10,242 vertices, 20,480 triangles
  TIMES.icosphere = performance.now() - T0;
  var N = mesh.pos.length / 3;
  var P = mesh.pos;                        // live positions
  // A soft, slightly asymmetric clay lump, so there is something to shape.
  for (var i = 0; i < N; i++) {
    var x = P[i * 3], y = P[i * 3 + 1], z = P[i * 3 + 2];
    var r = 1 + 0.09 * Math.sin(3 * x + 1.3) * Math.cos(2 * y + 0.5) + 0.07 * Math.sin(4 * z + 0.4) * Math.cos(x)
      + 0.04 * Math.cos(5 * x * y + 1.1) + 0.05 * Math.sin(2.5 * y + 2.0 * z);
    var flat = y < -0.55 ? 1 - (y + 0.55) * -0.35 : 1;      // a slightly flattened base, like a lump on a table
    P[i * 3] = x * r * 1.02;
    P[i * 3 + 1] = y * r * 0.92 * flat;
    P[i * 3 + 2] = z * r * 0.96;
  }
  var START = new Float32Array(P);
  var NRM = new Float32Array(N * 3);
  var faceN = new Float32Array(mesh.idx.length);
  var NF = mesh.idx.length / 3;
  // vertex -> faces (CSR), so a dab only recomputes the normals around the vertices it moved
  var vfStart = new Uint32Array(N + 1), vfList = new Uint32Array(mesh.idx.length);
  (function () {
    var F = mesh.idx, cnt = new Uint32Array(N);
    for (var k = 0; k < F.length; k++) cnt[F[k]]++;
    for (var v = 0; v < N; v++) vfStart[v + 1] = vfStart[v] + cnt[v];
    var fillAt = vfStart.slice(0, N);
    for (var f = 0; f < NF; f++) for (var c = 0; c < 3; c++) { var vv = F[f * 3 + c]; vfList[fillAt[vv]++] = f; }
  })();
  var pend = new Uint8Array(N), pendList = [];
  function touch(v) { if (!pend[v]) { pend[v] = 1; pendList.push(v); } }
  var faceMark = new Uint8Array(NF), vertMark = new Uint8Array(N);
  function faceNormal(f) {
    var F = mesh.idx, a = F[f * 3] * 3, b = F[f * 3 + 1] * 3, c = F[f * 3 + 2] * 3;
    var ux = P[b] - P[a], uy = P[b + 1] - P[a + 1], uz = P[b + 2] - P[a + 2];
    var vx = P[c] - P[a], vy = P[c + 1] - P[a + 1], vz = P[c + 2] - P[a + 2];
    faceN[f * 3] = uy * vz - uz * vy; faceN[f * 3 + 1] = uz * vx - ux * vz; faceN[f * 3 + 2] = ux * vy - uy * vx;
  }
  function vertNormal(v) {
    var x = 0, y = 0, z = 0;
    for (var k = vfStart[v]; k < vfStart[v + 1]; k++) { var f = vfList[k] * 3; x += faceN[f]; y += faceN[f + 1]; z += faceN[f + 2]; }
    var l = Math.hypot(x, y, z) || 1;
    NRM[v * 3] = x / l; NRM[v * 3 + 1] = y / l; NRM[v * 3 + 2] = z / l;
  }
  // Recompute normals and screen positions only where vertices moved.
  function flushPending() {
    if (!pendList.length) return;
    var faces = [], verts = [];
    for (var i = 0; i < pendList.length; i++) {
      var v = pendList[i];
      pend[v] = 0;
      for (var k = vfStart[v]; k < vfStart[v + 1]; k++) { var f = vfList[k]; if (!faceMark[f]) { faceMark[f] = 1; faces.push(f); } }
    }
    pendList.length = 0;
    var F = mesh.idx;
    for (var j = 0; j < faces.length; j++) {
      var ff = faces[j];
      faceMark[ff] = 0;
      faceNormal(ff);
      for (var c = 0; c < 3; c++) { var vv = F[ff * 3 + c]; if (!vertMark[vv]) { vertMark[vv] = 1; verts.push(vv); } }
    }
    for (var m = 0; m < verts.length; m++) { vertMark[verts[m]] = 0; vertNormal(verts[m]); }
    if (projected) for (var q = 0; q < verts.length; q++) projectOne(verts[q]);
  }

  function computeNormals() {
    pendList.length = 0;
    pend.fill(0);
    var F = mesh.idx, n = NRM;
    n.fill(0);
    for (var k = 0; k < F.length; k += 3) {
      var a = F[k] * 3, b = F[k + 1] * 3, c = F[k + 2] * 3;
      var ux = P[b] - P[a], uy = P[b + 1] - P[a + 1], uz = P[b + 2] - P[a + 2];
      var vx = P[c] - P[a], vy = P[c + 1] - P[a + 1], vz = P[c + 2] - P[a + 2];
      var nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
      n[a] += nx; n[a + 1] += ny; n[a + 2] += nz;
      n[b] += nx; n[b + 1] += ny; n[b + 2] += nz;
      n[c] += nx; n[c + 1] += ny; n[c + 2] += nz;
    }
    for (var i = 0; i < n.length; i += 3) {
      var l = Math.hypot(n[i], n[i + 1], n[i + 2]) || 1;
      n[i] /= l; n[i + 1] /= l; n[i + 2] /= l;
    }
  }
  computeNormals();
  TIMES.normals = performance.now() - T0;

  // ------------------------------------------------------------------ WebGL
  TIMES.beforeGL = performance.now() - T0;
  var gl = canvas.getContext("webgl", { antialias: true, alpha: true, premultipliedAlpha: true })
    || canvas.getContext("experimental-webgl");
  if (!gl) {
    stage.innerHTML = '<div class="nogl"><p>This demo needs WebGL, which this browser has switched off. ' +
      'The screenshots further down show the real thing.</p></div>';
    root.querySelector(".dock").hidden = true;
    return;
  }
  var VS = "attribute vec3 p;attribute vec3 n;uniform mat4 mv;uniform mat4 pr;varying vec3 vn;varying vec3 vp;" +
    "void main(){vec4 q=mv*vec4(p,1.0);vp=q.xyz;vn=(mv*vec4(n,0.0)).xyz;gl_Position=pr*q;}";
  var FS = "precision mediump float;varying vec3 vn;varying vec3 vp;" +
    "void main(){vec3 n=normalize(vn);vec3 v=normalize(-vp);" +
    "vec3 key=normalize(vec3(-0.55,0.65,0.55));vec3 fill=normalize(vec3(0.7,-0.15,0.45));" +
    "float d=max(dot(n,key),0.0);float f=max(dot(n,fill),0.0);" +
    "float hemi=0.5+0.5*n.y;float rim=pow(1.0-max(dot(n,v),0.0),3.0);" +
    "vec3 clay=vec3(0.80,0.765,0.72);" +
    "vec3 c=clay*(0.20+0.78*d+0.22*f+0.18*hemi);" +
    "c+=vec3(1.0,0.72,0.45)*rim*0.22;" +
    "float s=pow(max(dot(reflect(-key,n),v),0.0),24.0);c+=vec3(0.18)*s;" +
    "gl_FragColor=vec4(pow(c,vec3(1.0/1.12)),1.0);}";
  function shader(type, src) {
    var s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    return s;
  }
  var prog = gl.createProgram();
  gl.attachShader(prog, shader(gl.VERTEX_SHADER, VS));
  gl.attachShader(prog, shader(gl.FRAGMENT_SHADER, FS));
  gl.linkProgram(prog);
  gl.useProgram(prog);
  var aP = gl.getAttribLocation(prog, "p"), aN = gl.getAttribLocation(prog, "n");
  var uMV = gl.getUniformLocation(prog, "mv"), uPR = gl.getUniformLocation(prog, "pr");
  var bP = gl.createBuffer(), bN = gl.createBuffer(), bI = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, bP);
  gl.bufferData(gl.ARRAY_BUFFER, P, gl.DYNAMIC_DRAW);
  gl.enableVertexAttribArray(aP);
  gl.vertexAttribPointer(aP, 3, gl.FLOAT, false, 0, 0);
  gl.bindBuffer(gl.ARRAY_BUFFER, bN);
  gl.bufferData(gl.ARRAY_BUFFER, NRM, gl.DYNAMIC_DRAW);
  gl.enableVertexAttribArray(aN);
  gl.vertexAttribPointer(aN, 3, gl.FLOAT, false, 0, 0);
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, bI);
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.idx, gl.STATIC_DRAW);
  gl.enable(gl.DEPTH_TEST);
  gl.enable(gl.CULL_FACE);
  gl.cullFace(gl.BACK);
  gl.frontFace(gl.CCW);

  // ------------------------------------------------------------------ camera
  var yaw = -0.5, pitch = 0.22, dist = 4.6;
  var MV = new Float32Array(16), PR = new Float32Array(16);
  var W = 1, H = 1, dpr = 1;

  function perspective(out, fovy, aspect, near, far) {
    var f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
    out.fill(0);
    out[0] = f / aspect; out[5] = f; out[10] = (far + near) * nf; out[11] = -1; out[14] = 2 * far * near * nf;
  }
  function view(out) {
    var cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
    // rotate about Y (yaw), then X (pitch), then pull back along -Z
    out[0] = cy; out[1] = sp * sy; out[2] = -cp * sy; out[3] = 0;
    out[4] = 0; out[5] = cp; out[6] = sp; out[7] = 0;
    out[8] = sy; out[9] = -sp * cy; out[10] = cp * cy; out[11] = 0;
    out[12] = 0; out[13] = 0; out[14] = -dist; out[15] = 1;
  }
  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    var r = stage.getBoundingClientRect();
    W = Math.max(1, r.width); H = Math.max(1, r.height);
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    gl.viewport(0, 0, canvas.width, canvas.height);
    var fov = W < H ? 0.95 : 0.72;
    perspective(PR, fov, W / H, 0.1, 50);
    dirty = true;
  }

  // ------------------------------------------------------------------ screen projection (for the brush)
  var SX = new Float32Array(N), SY = new Float32Array(N), SZ = new Float32Array(N), FACING = new Uint8Array(N);
  function project() {
    view(MV);
    var m = MV, p = PR;
    for (var i = 0; i < N; i++) {
      var x = P[i * 3], y = P[i * 3 + 1], z = P[i * 3 + 2];
      var vx = m[0] * x + m[4] * y + m[8] * z + m[12];
      var vy = m[1] * x + m[5] * y + m[9] * z + m[13];
      var vz = m[2] * x + m[6] * y + m[10] * z + m[14];
      var cx = p[0] * vx, cy = p[5] * vy, cw = -vz;
      SX[i] = (cx / cw * 0.5 + 0.5) * W;
      SY[i] = (1 - (cy / cw * 0.5 + 0.5)) * H;
      SZ[i] = -vz;
      var nx = NRM[i * 3], ny = NRM[i * 3 + 1], nz = NRM[i * 3 + 2];
      var vnz = m[2] * nx + m[6] * ny + m[10] * nz;
      FACING[i] = vnz > 0.05 ? 1 : 0;      // normal points toward the camera
    }
    projected = true;
  }
  var projected = false;
  function projectOne(i) {
    var m = MV, p = PR;
    var x = P[i * 3], y = P[i * 3 + 1], z = P[i * 3 + 2];
    var vx = m[0] * x + m[4] * y + m[8] * z + m[12];
    var vy = m[1] * x + m[5] * y + m[9] * z + m[13];
    var vz = m[2] * x + m[6] * y + m[10] * z + m[14];
    var cw = -vz;
    SX[i] = (p[0] * vx / cw * 0.5 + 0.5) * W;
    SY[i] = (1 - (p[5] * vy / cw * 0.5 + 0.5)) * H;
    SZ[i] = cw;
    FACING[i] = (m[2] * NRM[i * 3] + m[6] * NRM[i * 3 + 1] + m[10] * NRM[i * 3 + 2]) > 0.05 ? 1 : 0;
  }

  // Vertices under the brush, front surface only. Returns {list, w} or null.
  function underBrush(mx, my, radius) {
    if (!projected) project();
    var r2 = radius * radius, best = Infinity, cand = [];
    for (var i = 0; i < N; i++) {
      if (!FACING[i]) continue;
      var dx = SX[i] - mx, dy = SY[i] - my, d2 = dx * dx + dy * dy;
      if (d2 > r2) continue;
      cand.push(i, d2);
      if (SZ[i] < best) best = SZ[i];
    }
    if (!cand.length) return null;
    var list = [], w = [];
    var depthTol = 0.6;
    for (var k = 0; k < cand.length; k += 2) {
      var j = cand[k];
      if (SZ[j] > best + depthTol) continue;
      var t = 1 - Math.sqrt(cand[k + 1]) / radius;
      list.push(j);
      w.push(t * t * (3 - 2 * t));            // smoothstep falloff
    }
    return { list: list, w: w };
  }

  // ------------------------------------------------------------------ history (deltas only)
  var steps = [];        // {idx: Uint32Array, before: Float32Array, after: Float32Array, label}
  var cursor = 0;
  var morphTarget = null;  // history position, or null
  var targetPos = null;    // positions at morphTarget

  function apply(step, forward) {
    var src = forward ? step.after : step.before, idx = step.idx;
    for (var k = 0; k < idx.length; k++) {
      var i = idx[k] * 3;
      P[i] = src[k * 3]; P[i + 1] = src[k * 3 + 1]; P[i + 2] = src[k * 3 + 2];
    }
  }
  function goto(pos) {
    pos = Math.max(0, Math.min(steps.length, pos | 0));
    if (pos === cursor) return;
    while (cursor > pos) apply(steps[--cursor], false);
    while (cursor < pos) apply(steps[cursor++], true);
    computeNormals();
    projected = false;
    dirty = true;
    updateUI();
  }
  function positionsAt(pos) {
    var out = new Float32Array(P), c = cursor;
    function ap(step, fwd) {
      var src = fwd ? step.after : step.before;
      for (var k = 0; k < step.idx.length; k++) {
        var i = step.idx[k] * 3;
        out[i] = src[k * 3]; out[i + 1] = src[k * 3 + 1]; out[i + 2] = src[k * 3 + 2];
      }
    }
    while (c > pos) ap(steps[--c], false);
    while (c < pos) ap(steps[c++], true);
    return out;
  }

  // ------------------------------------------------------------------ strokes
  var tool = "pull";
  var stroke = null;     // {touched: Map(idx -> before xyz), last: [x, y]}
  var RADIUS = 46;

  function brushRadius() { return RADIUS * Math.max(0.75, Math.min(1.25, W / 760)); }

  function dab(mx, my) {
    var hit = underBrush(mx, my, brushRadius());
    if (!hit) return false;
    var strength = 0.012;
    var sign = tool === "push" ? -1 : 1;
    // average normal under the brush: a dab moves the area, not each vertex its own way
    var ax = 0, ay = 0, az = 0;
    for (var k = 0; k < hit.list.length; k++) {
      var i = hit.list[k] * 3;
      ax += NRM[i] * hit.w[k]; ay += NRM[i + 1] * hit.w[k]; az += NRM[i + 2] * hit.w[k];
    }
    var al = Math.hypot(ax, ay, az) || 1;
    ax /= al; ay /= al; az /= al;
    for (var m = 0; m < hit.list.length; m++) {
      var v = hit.list[m], j = v * 3, w = hit.w[m];
      if (!stroke.touched.has(v)) stroke.touched.set(v, [P[j], P[j + 1], P[j + 2]]);
      touch(v);
      if (tool === "morph") {
        if (!targetPos) return false;
        var t = Math.min(1, w * 0.35);
        P[j] += (targetPos[j] - P[j]) * t;
        P[j + 1] += (targetPos[j + 1] - P[j + 1]) * t;
        P[j + 2] += (targetPos[j + 2] - P[j + 2]) * t;
      } else {
        var s = sign * strength * w;
        P[j] += ax * s; P[j + 1] += ay * s; P[j + 2] += az * s;
      }
    }
    return true;
  }

  function strokeTo(mx, my) {
    var last = stroke.last, spacing = Math.max(3, brushRadius() * 0.18);
    var dx = mx - last[0], dy = my - last[1], d = Math.hypot(dx, dy);
    if (d < spacing) return;
    var n = Math.floor(d / spacing);
    for (var k = 1; k <= n; k++) { dab(last[0] + dx * k / n, last[1] + dy * k / n); flushPending(); }
    stroke.last = [mx, my];
    dirty = true;
  }

  function endStroke() {
    var touched = stroke.touched;
    stroke = null;
    if (!touched.size) return;
    var idx = Array.from(touched.keys()).sort(function (a, b) { return a - b; });
    var before = new Float32Array(idx.length * 3), after = new Float32Array(idx.length * 3), moved = 0;
    for (var k = 0; k < idx.length; k++) {
      var b = touched.get(idx[k]), i = idx[k] * 3;
      before[k * 3] = b[0]; before[k * 3 + 1] = b[1]; before[k * 3 + 2] = b[2];
      after[k * 3] = P[i]; after[k * 3 + 1] = P[i + 1]; after[k * 3 + 2] = P[i + 2];
      if (b[0] !== P[i] || b[1] !== P[i + 1] || b[2] !== P[i + 2]) moved++;
    }
    if (!moved) return;
    if (cursor < steps.length) {                // sculpting from an earlier step starts a new branch
      steps.length = cursor;
      if (morphTarget !== null && morphTarget > cursor) { morphTarget = null; targetPos = null; setTool(tool === "morph" ? "pull" : tool); }
    }
    steps.push({ idx: new Uint32Array(idx), before: before, after: after, label: tool });
    cursor = steps.length;
    strokesMade++;
    updateUI(true);
    hints();
  }

  // ------------------------------------------------------------------ pointer input on the stage
  var drag = null;       // {mode: "sculpt" | "orbit", id, x, y}
  function local(e) {
    var r = stage.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }
  function showRing(x, y, on) {
    var d = brushRadius() * 2;
    ring.style.width = ring.style.height = d + "px";
    ring.style.transform = "translate(" + (x - d / 2) + "px," + (y - d / 2) + "px)";
    ring.style.opacity = on ? "1" : "0";
    ring.style.borderColor = tool === "morph" ? "rgba(121,214,255,0.9)" : tool === "push" ? "rgba(255,190,140,0.9)" : "rgba(255,255,255,0.75)";
  }
  stage.addEventListener("pointerdown", function (e) {
    if (e.target.closest("button")) return;        // the tool buttons sit on top of the stage
    if (e.button !== undefined && e.button > 0 && e.pointerType === "mouse") {
      drag = { mode: "orbit", id: e.pointerId, x: e.clientX, y: e.clientY };
    } else {
      var p = local(e);
      if (movie) stopMovie();
      var onSurface = !!underBrush(p[0], p[1], 4) || !!underBrush(p[0], p[1], brushRadius() * 0.5);
      if (onSurface && !(tool === "morph" && !targetPos)) {
        drag = { mode: "sculpt", id: e.pointerId };
        stroke = { touched: new Map(), last: p };
        dab(p[0], p[1]);
        flushPending();
        dirty = true;
      } else {
        drag = { mode: "orbit", id: e.pointerId, x: e.clientX, y: e.clientY };
        stage.classList.add("orbit");
      }
    }
    stage.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  stage.addEventListener("pointermove", function (e) {
    var p = local(e);
    if (!drag) {
      var over = !!underBrush(p[0], p[1], brushRadius() * 0.5);
      showRing(p[0], p[1], over && e.pointerType !== "touch");
      stage.classList.toggle("orbit", !over);
      return;
    }
    if (e.pointerId !== drag.id) return;
    if (drag.mode === "sculpt") {
      var t0 = performance.now();
      var evs = e.getCoalescedEvents ? e.getCoalescedEvents() : [e];
      for (var k = 0; k < evs.length; k++) strokeTo(local(evs[k])[0], local(evs[k])[1]);
      workMs.push(performance.now() - t0);
      if (workMs.length > 400) workMs.shift();
      showRing(p[0], p[1], e.pointerType !== "touch");
    } else {
      yaw += (e.clientX - drag.x) * 0.008;
      pitch = Math.max(-1.2, Math.min(1.2, pitch + (e.clientY - drag.y) * 0.006));
      drag.x = e.clientX; drag.y = e.clientY;
      projected = false;
      dirty = true;
    }
  });
  function release(e) {
    if (!drag || e.pointerId !== drag.id) return;
    if (drag.mode === "sculpt" && stroke) endStroke();
    drag = null;
    stage.classList.remove("orbit");
  }
  stage.addEventListener("pointerup", release);
  stage.addEventListener("pointercancel", release);
  stage.addEventListener("pointerleave", function () { if (!drag) showRing(0, 0, false); });
  stage.addEventListener("contextmenu", function (e) { e.preventDefault(); });

  // ------------------------------------------------------------------ slider (role=slider)
  var scrubbing = false;
  function posFromX(clientX) {
    var r = track.getBoundingClientRect();
    var t = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
    return Math.round(t * steps.length);
  }
  track.addEventListener("pointerdown", function (e) {
    if (!steps.length) return;
    stopMovie();
    scrubbing = true;
    track.classList.remove("smooth");
    track.setPointerCapture(e.pointerId);
    goto(posFromX(e.clientX));
    usedSlider = true;
    hints();
    e.preventDefault();
  });
  track.addEventListener("pointermove", function (e) { if (scrubbing) goto(posFromX(e.clientX)); });
  track.addEventListener("pointerup", function () { scrubbing = false; });
  track.addEventListener("pointercancel", function () { scrubbing = false; });
  track.addEventListener("keydown", function (e) {
    var k = e.key, n = steps.length, to = null;
    if (k === "ArrowLeft" || k === "ArrowDown") to = cursor - 1;
    else if (k === "ArrowRight" || k === "ArrowUp") to = cursor + 1;
    else if (k === "PageDown") to = cursor - Math.max(1, Math.round(n / 10));
    else if (k === "PageUp") to = cursor + Math.max(1, Math.round(n / 10));
    else if (k === "Home") to = 0;
    else if (k === "End") to = n;
    if (to === null) return;
    e.preventDefault();
    stopMovie();
    smooth();
    goto(to);
    usedSlider = true;
    hints();
  });
  function smooth() { if (!reduced) track.classList.add("smooth"); }

  // ------------------------------------------------------------------ buttons, menu, keys
  function back() { stopMovie(); smooth(); goto(cursor - 1); usedSlider = true; hints(); }
  function fwd() { stopMovie(); smooth(); goto(cursor + 1); }
  btnBack.addEventListener("click", back);
  btnFwd.addEventListener("click", fwd);
  btnReset.addEventListener("click", function () {
    stopMovie();
    P.set(START);
    steps.length = 0;
    cursor = 0;
    morphTarget = null;
    targetPos = null;
    setTool("pull");
    computeNormals();
    projected = false;
    dirty = true;
    lastInfo = "";
    updateUI(true);
  });
  for (var t = 0; t < toolBtns.length; t++) {
    toolBtns[t].addEventListener("click", function () { setTool(this.getAttribute("data-tool")); });
  }
  function setTool(name) {
    tool = name;
    for (var k = 0; k < toolBtns.length; k++) {
      var b = toolBtns[k];
      b.setAttribute("aria-pressed", b.getAttribute("data-tool") === name ? "true" : "false");
      if (b.getAttribute("data-tool") === "morph") b.disabled = !targetPos;
    }
  }

  function openMenu(open) {
    menu.classList.toggle("open", open);
    btnMenu.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      menu.querySelector("[data-m=target]").disabled = !steps.length;
      menu.querySelector("[data-m=morph]").disabled = !targetPos;
      menu.querySelector("[data-m=movie]").disabled = steps.length < 2;
      var r = btnMenu.getBoundingClientRect(), rr = root.getBoundingClientRect();
      menu.style.left = Math.max(8, r.left - rr.left) + "px";
      menu.style.top = (r.top - rr.top - menu.offsetHeight - 10) + "px";
      var first = menu.querySelector("button:not(:disabled)");
      if (first) first.focus();
    }
  }
  btnMenu.addEventListener("click", function (e) { e.stopPropagation(); openMenu(!menu.classList.contains("open")); });
  document.addEventListener("click", function (e) { if (!menu.contains(e.target)) openMenu(false); });
  menu.addEventListener("keydown", function (e) {
    var items = Array.prototype.filter.call(menu.querySelectorAll("button"), function (b) { return !b.disabled; });
    var i = items.indexOf(document.activeElement);
    if (e.key === "Escape") { openMenu(false); btnMenu.focus(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
  });
  menu.addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (!b) return;
    var m = b.getAttribute("data-m");
    if (m === "target") setTarget();
    else if (m === "morph") setTool("morph");
    else if (m === "movie") toggleMovie();
    else if (m === "reset") btnReset.click();
    openMenu(false);
  });
  function setTarget() {
    morphTarget = cursor;
    targetPos = new Float32Array(P);
    setTool(tool);
    say("Morph target set to step " + cursor);
    lastInfo = "Morph target: step <b>" + cursor + "</b>. Pick Morph and paint to bring that shape back.";
    updateUI();
  }

  root.addEventListener("keydown", function (e) {
    var mod = e.ctrlKey || e.metaKey;
    if (!mod) return;
    var k = e.key.toLowerCase();
    if (k === "z" && !e.shiftKey) { e.preventDefault(); back(); }
    else if ((k === "z" && e.shiftKey) || k === "y") { e.preventDefault(); fwd(); }
  });

  // ------------------------------------------------------------------ Movie Mode
  var movie = null;
  function toggleMovie() { if (movie) stopMovie(); else startMovie(); }
  function startMovie() {
    if (steps.length < 2) return;
    var start = cursor >= steps.length ? 0 : cursor;
    goto(start);
    var per = Math.max(40, Math.min(220, 3600 / steps.length));
    var next = performance.now() + per;
    movie = { per: per, next: next };
    btnPlay.classList.add("playing");
    btnPlay.setAttribute("aria-pressed", "true");
    btnPlay.setAttribute("aria-label", "Stop Movie Mode");
  }
  function stopMovie() {
    if (!movie) return;
    movie = null;
    btnPlay.classList.remove("playing");
    btnPlay.setAttribute("aria-pressed", "false");
    btnPlay.setAttribute("aria-label", "Play the history (Movie Mode)");
  }
  btnPlay.addEventListener("click", toggleMovie);

  // ------------------------------------------------------------------ UI state
  var strokesMade = 0, usedSlider = false, lastInfo = "", lastSaid = 0;
  function fmtKB(b) { return b < 1024 ? b + " bytes" : (b / 1024).toFixed(1) + " KB"; }
  function updateUI(fromStroke) {
    var n = steps.length;
    var t = n ? cursor / n : 0;
    fill.style.width = (t * 100) + "%";
    knob.style.left = (t * 100) + "%";
    track.setAttribute("aria-valuemax", String(n));
    track.setAttribute("aria-valuenow", String(cursor));
    track.setAttribute("aria-valuetext", "Step " + cursor + " of " + n);
    track.setAttribute("aria-disabled", n ? "false" : "true");
    count.textContent = cursor + " / " + n;
    btnBack.disabled = cursor === 0;
    btnFwd.disabled = cursor === n;
    btnPlay.disabled = n < 2;
    if (morphTarget !== null && n) {
      tick.classList.add("on");
      tick.style.left = (morphTarget / n * 100) + "%";
    } else tick.classList.remove("on");
    if (fromStroke && n) {
      var s = steps[n - 1], v = s.idx.length;
      lastInfo = "Step " + n + " stored <b>" + v.toLocaleString("en-GB") + " vertices</b> (" + fmtKB(v * 16) +
        "). A full copy of this mesh would be " + fmtKB(N * 12) + ".";
    }
    if (!n) lastInfo = "";
    readout.innerHTML = lastInfo || "Each stroke stores only the vertices it moved.";
    var now = performance.now();
    if (now - lastSaid > 600) { say("Step " + cursor + " of " + n); lastSaid = now; }
  }
  function say(text) { if (live) live.textContent = text; }

  function hints() {
    if (!hint) return;
    if (strokesMade === 0) { hint.textContent = "Drag on the shape"; hint.classList.add("on"); hint.classList.remove("low"); }
    else if (strokesMade >= 3 && !usedSlider) {
      hint.textContent = "Now drag the slider below";
      hint.classList.add("on", "low");
    } else hint.classList.remove("on");
  }

  // ------------------------------------------------------------------ render loop
  var dirty = true, visible = false, raf = 0, frameTimes = [], workMs = [], renderMs = [];
  function frame(now) {
    raf = 0;
    if (movie && now >= movie.next) {
      if (cursor < steps.length) { goto(cursor + 1); movie.next = now + movie.per; }
      else stopMovie();
    }
    if (dirty) {
      var r0 = performance.now();
      view(MV);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.bindBuffer(gl.ARRAY_BUFFER, bP);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, P);
      gl.bindBuffer(gl.ARRAY_BUFFER, bN);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, NRM);
      gl.uniformMatrix4fv(uMV, false, MV);
      gl.uniformMatrix4fv(uPR, false, PR);
      gl.drawElements(gl.TRIANGLES, mesh.idx.length, gl.UNSIGNED_SHORT, 0);
      dirty = false;
      renderMs.push(performance.now() - r0);
      if (renderMs.length > 400) renderMs.shift();
      frameTimes.push(now);
      if (frameTimes.length > 240) frameTimes.shift();
    }
    if (visible) raf = requestAnimationFrame(frame);
  }
  function kick() { if (!raf && visible) raf = requestAnimationFrame(frame); }
  var io = new IntersectionObserver(function (es) {
    visible = es[0].isIntersecting;
    kick();
  }, { rootMargin: "100px" });
  io.observe(stage);
  window.addEventListener("resize", function () { resize(); kick(); });

  TIMES.setup = performance.now() - T0;
  resize();
  updateUI();
  hints();

  // Test hooks (used by the site's Playwright suite).
  TIMES.total = performance.now() - T0;
  window.__hindsightDemo = {
    times: TIMES,
    state: function () { return { steps: steps.length, cursor: cursor, target: morphTarget, tool: tool, movie: !!movie }; },
    hash: function () {
      var h = 2166136261 >>> 0, u = new Uint8Array(P.buffer);
      for (var i = 0; i < u.length; i++) { h ^= u[i]; h = Math.imul(h, 16777619) >>> 0; }
      return h.toString(16);
    },
    goto: function (p) { goto(p); },
    stageRect: function () { var r = stage.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; },
    frameTimes: function () { return frameTimes.slice(); },
    work: function () { return { input: workMs.slice(), render: renderMs.slice() }; },
    vertsUnder: function (x, y) { var h = underBrush(x, y, brushRadius()); return h ? h.list.length : 0; }
  };
})();
