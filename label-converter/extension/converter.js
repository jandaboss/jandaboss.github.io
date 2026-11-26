// Label Converter: schneidet das Etikett aus einer A4-Seite aus und legt es
// auf eine Seite im Format des Etikettendruckers. Läuft komplett lokal.
import * as pdfjsLib from "./lib/pdf.min.js";

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL("./lib/pdf.worker.min.js", import.meta.url).href;
const STD_FONTS = new URL("./lib/standard_fonts/", import.meta.url).href;
const { PDFDocument, degrees } = window.PDFLib;

const MM = 72 / 25.4; // PDF-Punkte pro Millimeter
const RASTER_DPI = 300;
const PAD_MM = 1.5; // Luft um einen erkannten Block
const MIN_CROP_MM = 5;

// ---------------------------------------------------------------------------
// Einstellungen (chrome.storage in der Extension, sonst localStorage)
// ---------------------------------------------------------------------------
const DEFAULTS = {
  format: "100x150", customW: 100, customH: 150, rotation: "auto", margin: 2,
  scaleMode: "shrink", remember: false, raster: false, savedCrop: null,
};
let settings = { ...DEFAULTS };
const hasChromeStorage = () => { try { return !!(globalThis.chrome && chrome.storage && chrome.storage.local); } catch { return false; } };
async function loadSettings() {
  let s = {};
  try {
    if (hasChromeStorage()) s = (await chrome.storage.local.get("settings")).settings || {};
    else s = JSON.parse(localStorage.getItem("labelConverter.settings") || "{}");
  } catch { s = {}; }
  settings = { ...DEFAULTS, ...s };
}
let saveTimer;
function saveSettings() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      if (hasChromeStorage()) await chrome.storage.local.set({ settings });
      else localStorage.setItem("labelConverter.settings", JSON.stringify(settings));
    } catch { /* Speichern ist nur Komfort */ }
  }, 200);
}

// ---------------------------------------------------------------------------
// Zustand
// ---------------------------------------------------------------------------
const files = []; // { name, kind: "pdf"|"image", bytes, pdf, libDoc, bitmap }
let items = [];   // ein Eintrag = ein Etikett im Ergebnis
let current = 0;
let uid = 0;
let work = null;  // { id, canvas, scale } – Darstellung der aktuellen Seite

const $ = (s) => document.querySelector(s);
const nf = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const dpr = () => Math.min(3, window.devicePixelRatio || 1);
const itemMm = (it) => ({ w: it.wPt / MM, h: it.hPt / MM });

// ---------------------------------------------------------------------------
// Dateien laden
// ---------------------------------------------------------------------------
function isPdf(f) { return f.type === "application/pdf" || /\.pdf$/i.test(f.name); }
function isImage(f) { return /^image\/(png|jpeg)$/.test(f.type) || /\.(png|jpe?g)$/i.test(f.name); }

async function addFiles(list) {
  const accepted = [...list].filter((f) => isPdf(f) || isImage(f));
  if (!accepted.length) { toast("Bitte eine PDF-, PNG- oder JPG-Datei verwenden.", true); return; }
  showWork();
  setLoading(true, accepted.length > 1 ? `${accepted.length} Dateien werden gelesen …` : "Etikett wird gesucht …");
  const before = items.length;
  for (const f of accepted) {
    try {
      if (isPdf(f)) await loadPdf(f); else await loadImage(f);
    } catch (e) {
      console.error(e);
      const pw = e && (e.name === "PasswordException" || /password/i.test(e.message || ""));
      toast(pw ? `„${f.name}“ ist passwortgeschützt und kann nicht gelesen werden.` : `„${f.name}“ konnte nicht gelesen werden.`, true);
    }
  }
  setLoading(false);
  if (!items.length) { showEmpty(); return; }
  if (items.length > before) current = before;
  renderAll();
}

async function loadPdf(f) {
  const bytes = new Uint8Array(await f.arrayBuffer());
  const pdf = await pdfjsLib.getDocument({
    data: bytes.slice(), isEvalSupported: false, standardFontDataUrl: STD_FONTS,
  }).promise;
  const file = { name: f.name, kind: "pdf", bytes, pdf, libDoc: null, pages: pdf.numPages };
  files.push(file);
  let added = 0;
  for (let n = 1; n <= pdf.numPages; n++) {
    const page = await pdf.getPage(n);
    const vp = page.getViewport({ scale: 1 });
    const scale = Math.min(0.75, 1600 / Math.max(vp.width, vp.height));
    const det = await renderPdfPage(page, scale);
    const item = makeItem(file, n, vp.width, vp.height, det);
    if (item.empty && pdf.numPages > 1) continue; // leere Seiten überspringen
    items.push(item); added++;
  }
  if (!added) throw new Error("Keine Seite mit Inhalt");
}

async function loadImage(f) {
  const bitmap = await createImageBitmap(f);
  const ar = bitmap.width / bitmap.height, a4 = 210 / 297;
  let wMm, hMm;
  if (Math.abs(ar - a4) / a4 < 0.04) { wMm = 210; hMm = 297; }
  else if (Math.abs(ar - 1 / a4) * a4 < 0.04) { wMm = 297; hMm = 210; }
  else { wMm = bitmap.width / 300 * 25.4; hMm = bitmap.height / 300 * 25.4; }
  const file = { name: f.name, kind: "image", bitmap, pages: 1 };
  files.push(file);
  const wPt = wMm * MM, hPt = hMm * MM;
  const scale = Math.min(0.75, 1600 / Math.max(wPt, hPt));
  const det = drawBitmap(bitmap, Math.round(wPt * scale), Math.round(hPt * scale));
  items.push(makeItem(file, 1, wPt, hPt, det));
}

async function renderPdfPage(page, scale, cropPt) {
  const vp = page.getViewport({
    scale,
    offsetX: cropPt ? -cropPt.x * scale : 0,
    offsetY: cropPt ? -cropPt.y * scale : 0,
  });
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(cropPt ? cropPt.w * scale : vp.width));
  canvas.height = Math.max(1, Math.round(cropPt ? cropPt.h * scale : vp.height));
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  await page.render({ canvasContext: ctx, viewport: vp }).promise;
  return canvas;
}

function drawBitmap(bitmap, w, h, sx = 0, sy = 0, sw = bitmap.width, sh = bitmap.height) {
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, w); canvas.height = Math.max(1, h);
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(bitmap, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
  return canvas;
}

function makeItem(file, pageNo, wPt, hPt, det) {
  const item = { id: ++uid, file, pageNo, wPt, hPt, det, blocks: [], crop: null, source: "auto", include: true, empty: false };
  const { w, h } = itemMm(item);
  const found = detectBlocks(det, w, h);
  item.blocks = found.blocks;
  item.empty = found.total === 0;
  const saved = settings.remember && settings.savedCrop;
  if (saved && Math.abs(saved.wMm - w) < 3 && Math.abs(saved.hMm - h) < 3) {
    item.crop = { x: saved.x, y: saved.y, w: saved.w, h: saved.h };
    item.source = "saved";
  } else if (item.blocks.length) {
    item.crop = { ...item.blocks[0] };
    item.source = "auto";
  } else {
    item.crop = { x: 0, y: 0, w: 1, h: 1 };
    item.source = item.empty ? "none" : "full";
  }
  return item;
}

// ---------------------------------------------------------------------------
// Etikett auf der Seite finden
// Raster aus ~1-mm-Zellen, Zellen mit dunklen Pixeln werden um 5 mm
// erweitert, zusammenhängende Flächen ergeben Blöcke. Der Block mit der
// meisten Druckfarbe (Adresse, Barcode, Rahmen) ist meist das Etikett.
// ---------------------------------------------------------------------------
function detectBlocks(canvas, wMm, hMm) {
  const W = canvas.width, H = canvas.height;
  const data = canvas.getContext("2d", { willReadFrequently: true }).getImageData(0, 0, W, H).data;
  const cellMm = Math.max(1, Math.max(wMm, hMm) / 600);
  const gw = Math.max(1, Math.ceil(wMm / cellMm)), gh = Math.max(1, Math.ceil(hMm / cellMm));
  const fx = gw / W, fy = gh / H;
  const ink = new Uint32Array(gw * gh);
  let total = 0;
  for (let y = 0; y < H; y++) {
    const row = Math.min(gh - 1, Math.floor(y * fy)) * gw;
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      if (data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114 < 200) {
        ink[row + Math.min(gw - 1, Math.floor(x * fx))]++;
        total++;
      }
    }
  }
  if (!total) return { blocks: [], total: 0 };

  const n = gw * gh, R = Math.max(1, Math.round(5 / cellMm));
  const tmp = new Uint8Array(n), dil = new Uint8Array(n);
  for (let y = 0; y < gh; y++) {
    const row = y * gw; let cnt = 0;
    for (let x = 0; x < Math.min(R, gw); x++) cnt += ink[row + x] > 0;
    for (let x = 0; x < gw; x++) {
      if (x + R < gw) cnt += ink[row + x + R] > 0;
      if (x - R - 1 >= 0) cnt -= ink[row + x - R - 1] > 0;
      tmp[row + x] = cnt > 0;
    }
  }
  for (let x = 0; x < gw; x++) {
    let cnt = 0;
    for (let y = 0; y < Math.min(R, gh); y++) cnt += tmp[y * gw + x];
    for (let y = 0; y < gh; y++) {
      if (y + R < gh) cnt += tmp[(y + R) * gw + x];
      if (y - R - 1 >= 0) cnt -= tmp[(y - R - 1) * gw + x];
      dil[y * gw + x] = cnt > 0;
    }
  }

  const seen = new Uint8Array(n), stack = new Int32Array(n);
  const comps = [];
  for (let s = 0; s < n; s++) {
    if (!dil[s] || seen[s]) continue;
    let sp = 0; stack[sp++] = s; seen[s] = 1;
    let x0 = Infinity, y0 = Infinity, x1 = -1, y1 = -1, sum = 0;
    while (sp) {
      const c = stack[--sp], cx = c % gw, cy = (c - cx) / gw;
      if (ink[c]) {
        sum += ink[c];
        if (cx < x0) x0 = cx; if (cx > x1) x1 = cx;
        if (cy < y0) y0 = cy; if (cy > y1) y1 = cy;
      }
      if (cx > 0 && dil[c - 1] && !seen[c - 1]) { seen[c - 1] = 1; stack[sp++] = c - 1; }
      if (cx < gw - 1 && dil[c + 1] && !seen[c + 1]) { seen[c + 1] = 1; stack[sp++] = c + 1; }
      if (cy > 0 && dil[c - gw] && !seen[c - gw]) { seen[c - gw] = 1; stack[sp++] = c - gw; }
      if (cy < gh - 1 && dil[c + gw] && !seen[c + gw]) { seen[c + gw] = 1; stack[sp++] = c + gw; }
    }
    if (sum) comps.push({ x0, y0, x1, y1, sum });
  }

  // Blöcke, die ganz in einem größeren liegen (z. B. die Adresse innerhalb
  // eines gerahmten Etiketts), gehören zu diesem.
  comps.sort((a, b) => (b.x1 - b.x0 + 1) * (b.y1 - b.y0 + 1) - (a.x1 - a.x0 + 1) * (a.y1 - a.y0 + 1));
  const kept = [];
  for (const c of comps) {
    const host = kept.find((k) => c.x0 >= k.x0 - 1 && c.y0 >= k.y0 - 1 && c.x1 <= k.x1 + 1 && c.y1 <= k.y1 + 1);
    if (host) host.sum += c.sum; else kept.push(c);
  }

  const blocks = kept
    .map((c) => {
      const ax = clamp(c.x0 * cellMm - PAD_MM, 0, wMm), ay = clamp(c.y0 * cellMm - PAD_MM, 0, hMm);
      const bx = clamp((c.x1 + 1) * cellMm + PAD_MM, 0, wMm), by = clamp((c.y1 + 1) * cellMm + PAD_MM, 0, hMm);
      return { x: ax / wMm, y: ay / hMm, w: (bx - ax) / wMm, h: (by - ay) / hMm, ink: c.sum, wMm: bx - ax, hMm: by - ay };
    })
    .filter((b) => (b.wMm >= 8 || b.hMm >= 8) && b.ink >= total * 0.01)
    .sort((a, b) => b.ink - a.ink)
    .slice(0, 12)
    .map(({ x, y, w, h, ink }) => ({ x, y, w, h, ink }));
  return { blocks, total };
}

// ---------------------------------------------------------------------------
// Geometrie: wie der Ausschnitt aufs Etikett kommt
// ---------------------------------------------------------------------------
function labelSize() {
  if (settings.format === "custom") {
    return [clamp(Number(settings.customW) || 100, 20, 400), clamp(Number(settings.customH) || 150, 20, 400)];
  }
  return settings.format.split("x").map(Number);
}

function layout(item) {
  const [LW, LH] = labelSize();
  const m = clamp(Number(settings.margin) || 0, 0, Math.min(LW, LH) / 2 - 5);
  const { w: pw, h: ph } = itemMm(item);
  const cw = item.crop.w * pw, ch = item.crop.h * ph;
  const aw = LW - 2 * m, ah = LH - 2 * m;
  const rots = settings.rotation === "auto" ? [0, 90] : [Number(settings.rotation)];
  let best = null;
  for (const rot of rots) {
    const [rw, rh] = rot % 180 ? [ch, cw] : [cw, ch];
    const fit = Math.min(aw / rw, ah / rh);
    const edge = Math.min(LW / rw, LH / rh);
    const s = settings.scaleMode === "fit" ? fit : settings.scaleMode === "original" ? 1 : Math.min(1, fit);
    const key = settings.scaleMode === "original" ? fit : s;
    if (!best || key > best.key * 1.005) best = { rot, s, fit, edge, key };
  }
  return { LW, LH, m, cw, ch, rot: best.rot, s: best.s, clipped: best.s > best.edge + 1e-6 };
}

// Position für pdf-lib: Objekt (sw × sh) wird um R Grad im Uhrzeigersinn
// gedreht und mittig auf (cx, cy) gesetzt. pdf-lib dreht gegen den Uhrzeigersinn.
function placement(R, sw, sh, cx, cy) {
  switch (R) {
    case 90: return { x: cx - sh / 2, y: cy + sw / 2 };
    case 180: return { x: cx + sw / 2, y: cy + sh / 2 };
    case 270: return { x: cx + sh / 2, y: cy - sw / 2 };
    default: return { x: cx - sw / 2, y: cy - sh / 2 };
  }
}

// ---------------------------------------------------------------------------
// Ergebnis-PDF bauen
// ---------------------------------------------------------------------------
async function libDocFor(file) {
  if (!file.libDoc) {
    file.libDoc = await PDFDocument.load(file.bytes, { ignoreEncryption: true, updateMetadata: false });
  }
  if (file.libDoc.isEncrypted) throw new Error("verschlüsselt");
  return file.libDoc;
}

async function drawVector(out, page, item, L) {
  const lib = await libDocFor(item.file);
  const src = lib.getPage(item.pageNo - 1);
  const pjPage = await item.file.pdf.getPage(item.pageNo);
  const vp = pjPage.getViewport({ scale: 1 });
  const c = item.crop;
  const [x1, y1] = vp.convertToPdfPoint(c.x * vp.width, c.y * vp.height);
  const [x2, y2] = vp.convertToPdfPoint((c.x + c.w) * vp.width, (c.y + c.h) * vp.height);
  const bb = { left: Math.min(x1, x2), right: Math.max(x1, x2), bottom: Math.min(y1, y2), top: Math.max(y1, y2) };
  const emb = await out.embedPage(src, bb);
  const R = (((pjPage.rotate || 0) + L.rot) % 360 + 360) % 360;
  const { x, y } = placement(R, emb.width * L.s, emb.height * L.s, (L.LW * MM) / 2, (L.LH * MM) / 2);
  page.drawPage(emb, { x, y, xScale: L.s, yScale: L.s, rotate: degrees(-R) });
}

async function cropCanvas(item, dpi) {
  const c = item.crop;
  if (item.file.kind === "pdf") {
    const pjPage = await item.file.pdf.getPage(item.pageNo);
    const crop = { x: c.x * item.wPt, y: c.y * item.hPt, w: c.w * item.wPt, h: c.h * item.hPt };
    const scale = Math.min(dpi / 72, 8000 / Math.max(crop.w, crop.h));
    return renderPdfPage(pjPage, scale, crop);
  }
  const b = item.file.bitmap;
  const sx = c.x * b.width, sy = c.y * b.height, sw = c.w * b.width, sh = c.h * b.height;
  return drawBitmap(b, Math.round(sw), Math.round(sh), sx, sy, sw, sh);
}

async function drawRaster(out, page, item, L) {
  const canvas = await cropCanvas(item, RASTER_DPI);
  const blob = await new Promise((res) => canvas.toBlob(res, "image/png"));
  const img = await out.embedPng(new Uint8Array(await blob.arrayBuffer()));
  const sw = L.cw * MM * L.s, sh = L.ch * MM * L.s;
  const { x, y } = placement(L.rot, sw, sh, (L.LW * MM) / 2, (L.LH * MM) / 2);
  page.drawImage(img, { x, y, width: sw, height: sh, rotate: degrees(-L.rot) });
}

async function buildPdf() {
  const chosen = items.filter((i) => i.include);
  if (!chosen.length) { toast("Kein Etikett ausgewählt. Setz in der Leiste oben mindestens einen Haken.", true); return null; }
  const out = await PDFDocument.create();
  out.setTitle(chosen.length > 1 ? "Etiketten" : "Etikett");
  out.setCreator("Label Converter");
  out.setProducer("Label Converter");
  let fallback = 0;
  for (const item of chosen) {
    const L = layout(item);
    const page = out.addPage([L.LW * MM, L.LH * MM]);
    let done = false;
    if (item.file.kind === "pdf" && !settings.raster) {
      try { await drawVector(out, page, item, L); done = true; }
      catch (e) { console.warn("Vektor-Ausgabe nicht möglich, nehme Bild:", e); fallback++; }
    }
    if (!done) await drawRaster(out, page, item, L);
  }
  if (fallback) toast(`${fallback === 1 ? "Ein Etikett wurde" : `${fallback} Etiketten wurden`} als Bild (300 dpi) eingefügt, weil das PDF geschützt oder ungewöhnlich aufgebaut ist.`);
  return out.save();
}

function outputName() {
  const chosen = items.filter((i) => i.include);
  const [LW, LH] = labelSize();
  const names = [...new Set(chosen.map((i) => i.file.name))];
  if (names.length === 1) return `${names[0].replace(/\.(pdf|png|jpe?g)$/i, "")}_etikett_${LW}x${LH}mm.pdf`;
  const d = new Date();
  const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return `etiketten_${day}_${LW}x${LH}mm.pdf`;
}

async function withBusy(btn, label, fn) {
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = label;
  try { return await fn(); }
  catch (e) { console.error(e); toast("Das Etikett konnte nicht erzeugt werden. Versuch es unter „Erweitert“ mit „Als Bild ausgeben“.", true); }
  finally { btn.disabled = false; btn.textContent = old; }
}

async function download() {
  await withBusy($("#download"), "Wird erstellt …", async () => {
    const bytes = await buildPdf();
    if (!bytes) return;
    const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
    const a = document.createElement("a");
    a.href = url; a.download = outputName();
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    const n = items.filter((i) => i.include).length;
    toast(n > 1 ? `${n} Etiketten als PDF gespeichert.` : "Etikett als PDF gespeichert.");
  });
}

async function printPdf() {
  await withBusy($("#print"), "Wird erstellt …", async () => {
    const bytes = await buildPdf();
    if (!bytes) return;
    const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
    const win = window.open(url, "_blank");
    if (!win) { toast("Der Browser hat den neuen Tab blockiert. Nimm stattdessen „PDF herunterladen“.", true); return; }
    toast("Das Etikett ist im neuen Tab geöffnet. Dort drucken, Skalierung 100 %.");
    setTimeout(() => URL.revokeObjectURL(url), 10 * 60_000);
  });
}

// ---------------------------------------------------------------------------
// Vorschau
// ---------------------------------------------------------------------------
// Zeichnet das Etikett auf canvas. src deckt die ganze (sichtbare) Seite ab.
function drawLabel(canvas, item, src, maxW, maxH, guides) {
  const L = layout(item);
  const k = Math.min(maxW / L.LW, maxH / L.LH); // CSS-Pixel pro mm
  const r = dpr();
  canvas.style.width = `${Math.round(L.LW * k)}px`;
  canvas.style.height = `${Math.round(L.LH * k)}px`;
  canvas.width = Math.round(L.LW * k * r);
  canvas.height = Math.round(L.LH * k * r);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(k * r, 0, 0, k * r, 0, 0);
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, L.LW, L.LH);
  ctx.save();
  ctx.beginPath(); ctx.rect(0, 0, L.LW, L.LH); ctx.clip();
  ctx.translate(L.LW / 2, L.LH / 2);
  ctx.rotate((L.rot * Math.PI) / 180);
  const dw = L.cw * L.s, dh = L.ch * L.s, c = item.crop;
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(src, c.x * src.width, c.y * src.height, c.w * src.width, c.h * src.height, -dw / 2, -dh / 2, dw, dh);
  ctx.restore();
  if (guides && L.m > 0) {
    ctx.setLineDash([1.6, 1.4]);
    ctx.lineWidth = 0.3;
    ctx.strokeStyle = "rgba(15, 118, 110, .55)";
    ctx.strokeRect(L.m, L.m, L.LW - 2 * L.m, L.LH - 2 * L.m);
  }
  return L;
}

function updateResult() {
  const item = items[current];
  if (!item) return;
  const [LW, LH] = labelSize();
  $("#resultSize").textContent = `${nf.format(LW)} × ${nf.format(LH)} mm`;
  const src = work && work.id === item.id ? work.canvas : item.det;
  const wrap = $(".labelwrap");
  const maxW = Math.max(160, Math.min(300, wrap.clientWidth - 28));
  const L = drawLabel($("#resultCanvas"), item, src, maxW, 340, true);
  const pct = Math.round(L.s * 100);
  $("#resultInfo").textContent =
    `Ausschnitt ${nf.format(L.cw)} × ${nf.format(L.ch)} mm · ${pct} % · ${L.rot ? `${L.rot === 270 ? "90° links" : L.rot === 90 ? "90° rechts" : "180°"} gedreht` : "nicht gedreht"}`;
  const warn = $("#resultWarn");
  if (L.clipped) {
    warn.hidden = false;
    warn.textContent = "Der Ausschnitt ist größer als das Etikett und wird an den Rändern abgeschnitten. Stell „Größe des Inhalts“ auf „nur verkleinern wenn nötig“ oder wähl einen kleineren Ausschnitt.";
  } else if (L.s < 0.995) {
    warn.hidden = false;
    warn.textContent = `Der Inhalt wird auf ${pct} % verkleinert, damit er aufs Etikett passt. Barcodes bleiben scharf, werden aber kleiner. Prüf beim ersten Druck, ob sie sich gut scannen lassen.`;
  } else {
    warn.hidden = true;
  }
  const n = items.filter((i) => i.include).length;
  $("#countInfo").textContent = items.length > 1 ? `${n} von ${items.length} Etiketten kommen ins PDF, jedes auf eine eigene Seite.` : "";
  $("#download").textContent = n > 1 ? `${n} Etiketten herunterladen` : "PDF herunterladen";
}

// ---------------------------------------------------------------------------
// Editor: Seite anzeigen und Ausschnitt bearbeiten
// ---------------------------------------------------------------------------
let renderToken = 0;
async function showItem() {
  const item = items[current];
  if (!item) return;
  const multiPage = item.file.pages > 1;
  $("#itemTitle").textContent = item.file.name + (multiPage ? ` · Seite ${item.pageNo}` : "");
  $("#itemTitle").title = $("#itemTitle").textContent;
  updateSourcePill();
  $("#pager").hidden = items.length < 2;
  $("#pagerText").textContent = `${current + 1} / ${items.length}`;
  $("#prevItem").disabled = current === 0;
  $("#nextItem").disabled = current === items.length - 1;

  const stage = $("#stage");
  const availW = Math.max(200, stage.clientWidth - 34);
  const availH = Math.max(380, window.innerHeight - ($("#strip").hidden ? 190 : 380));
  const scale = Math.min(availW / item.wPt, availH / item.hPt); // CSS-Pixel pro Punkt
  const cssW = Math.round(item.wPt * scale), cssH = Math.round(item.hPt * scale);
  const box = $("#pageBox");
  box.style.width = `${cssW}px`;
  box.style.height = `${cssH}px`;
  const pc = $("#pageCanvas");
  pc.style.width = `${cssW}px`;
  pc.style.height = `${cssH}px`;
  renderOverlay();

  const key = `${item.id}@${Math.round(scale * dpr() * 1000)}`;
  if (!work || work.key !== key) {
    // Erst die grobe Vorschau zeigen, dann scharf nachzeichnen
    blit(pc, item.det, cssW, cssH);
    const token = ++renderToken;
    // Mindestens ~115 dpi, damit auch die Ergebnis-Vorschau scharf bleibt
    const px = Math.max(scale, 1.6) * dpr();
    let canvas;
    if (item.file.kind === "pdf") canvas = await renderPdfPage(await item.file.pdf.getPage(item.pageNo), px);
    else canvas = drawBitmap(item.file.bitmap, Math.round(item.wPt * px), Math.round(item.hPt * px));
    if (token !== renderToken) return;
    work = { id: item.id, key, canvas };
  }
  blit(pc, work.canvas, cssW, cssH);
  updateResult();
}

function blit(target, src, cssW, cssH) {
  target.width = Math.round(cssW * dpr());
  target.height = Math.round(cssH * dpr());
  const ctx = target.getContext("2d");
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(src, 0, 0, target.width, target.height);
}

function updateSourcePill() {
  const item = items[current];
  const pill = $("#cropSource");
  const map = {
    auto: ["Automatisch erkannt", false], saved: ["Gemerkter Ausschnitt", false], manual: ["Eigener Ausschnitt", true],
    full: ["Ganze Seite", true], none: ["Keine Inhalte gefunden", true],
  };
  const [text, manual] = map[item.source] || map.manual;
  pill.textContent = text;
  pill.classList.toggle("manual", manual);
}

function iou(a, b) {
  const x = Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x));
  const y = Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
  const i = x * y;
  return i / (a.w * a.h + b.w * b.h - i);
}

function renderOverlay() {
  const item = items[current];
  const ov = $("#overlay");
  if (!item) { ov.innerHTML = ""; return; }
  const c = item.crop;
  const pct = (v) => `${(v * 100).toFixed(3)}%`;
  const rect = (r) => `left:${pct(r.x)};top:${pct(r.y)};width:${pct(r.w)};height:${pct(r.h)}`;
  const { w: pw, h: ph } = itemMm(item);
  const blocks = item.blocks
    .map((b, i) => (iou(b, c) > 0.9 ? "" : `<div class="blk" data-i="${i}" style="${rect(b)}" title="Diesen Block verwenden"></div>`))
    .join("");
  const handles = ["nw", "n", "ne", "e", "se", "s", "sw", "w"].map((h) => `<span class="h" data-h="${h}"></span>`).join("");
  ov.innerHTML =
    `<div class="shade" style="${rect(c)}"></div>${blocks}` +
    `<div class="sel${c.y < 0.04 ? " low" : ""}" style="${rect(c)}">${handles}<span class="dim">${nf.format(c.w * pw)} × ${nf.format(c.h * ph)} mm</span></div>`;
}

let drag = null;
let rafPending = false;
function scheduleLive() {
  if (rafPending) return;
  rafPending = true;
  requestAnimationFrame(() => { rafPending = false; renderOverlay(); updateResult(); });
}

function pointerPos(e) {
  const r = $("#overlay").getBoundingClientRect();
  return { x: clamp((e.clientX - r.left) / r.width, 0, 1), y: clamp((e.clientY - r.top) / r.height, 0, 1), r };
}

function onPointerDown(e) {
  const item = items[current];
  if (!item || e.button > 0) return;
  const p = pointerPos(e);
  const h = e.target.dataset && e.target.dataset.h;
  const onSel = e.target.closest(".sel");
  const blk = e.target.closest(".blk");
  drag = {
    mode: h ? "resize" : onSel ? "move" : "draw", handle: h, start: p, orig: { ...item.crop },
    moved: false, blk: blk ? Number(blk.dataset.i) : null, sx: e.clientX, sy: e.clientY,
  };
  $("#overlay").setPointerCapture(e.pointerId);
  e.preventDefault();
}

function onPointerMove(e) {
  if (!drag) return;
  const item = items[current];
  if (!drag.moved && Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) < 4) return;
  drag.moved = true;
  const p = pointerPos(e);
  const { w: pw, h: ph } = itemMm(item);
  const minW = MIN_CROP_MM / pw, minH = MIN_CROP_MM / ph;
  const o = drag.orig;
  let c;
  if (drag.mode === "move") {
    const dx = p.x - drag.start.x, dy = p.y - drag.start.y;
    c = { x: clamp(o.x + dx, 0, 1 - o.w), y: clamp(o.y + dy, 0, 1 - o.h), w: o.w, h: o.h };
  } else if (drag.mode === "resize") {
    let x0 = o.x, y0 = o.y, x1 = o.x + o.w, y1 = o.y + o.h;
    const hd = drag.handle;
    if (hd.includes("w")) x0 = clamp(p.x, 0, x1 - minW);
    if (hd.includes("e")) x1 = clamp(p.x, x0 + minW, 1);
    if (hd.includes("n")) y0 = clamp(p.y, 0, y1 - minH);
    if (hd.includes("s")) y1 = clamp(p.y, y0 + minH, 1);
    c = { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  } else {
    let x0 = Math.min(drag.start.x, p.x), x1 = Math.max(drag.start.x, p.x);
    let y0 = Math.min(drag.start.y, p.y), y1 = Math.max(drag.start.y, p.y);
    if (x1 - x0 < minW) x1 = Math.min(1, x0 + minW), x0 = x1 - minW;
    if (y1 - y0 < minH) y1 = Math.min(1, y0 + minH), y0 = y1 - minH;
    c = { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }
  item.crop = c;
  item.source = "manual";
  updateSourcePill();
  scheduleLive();
}

function onPointerUp() {
  if (!drag) return;
  const item = items[current];
  const d = drag;
  drag = null;
  if (!d.moved) {
    if (d.blk != null && item.blocks[d.blk]) setCrop(item, { ...item.blocks[d.blk] }, d.blk === 0 ? "auto" : "manual");
    return;
  }
  cropChanged(item);
}

function setCrop(item, crop, source) {
  item.crop = crop;
  item.source = source;
  cropChanged(item);
}

function cropChanged(item) {
  if (settings.remember) rememberCrop(item);
  updateSourcePill();
  renderOverlay();
  updateResult();
  updateThumb(item);
}

function rememberCrop(item) {
  const { w, h } = itemMm(item);
  settings.savedCrop = { ...item.crop, wMm: w, hMm: h };
  saveSettings();
}

// ---------------------------------------------------------------------------
// Etiketten-Leiste
// ---------------------------------------------------------------------------
function renderThumbs() {
  const strip = $("#strip");
  const show = items.length > 1;
  strip.hidden = !show;
  if (!show) return;
  const included = items.filter((i) => i.include).length;
  $("#stripCount").textContent = `${included} von ${items.length} ausgewählt`;
  const box = $("#thumbs");
  box.innerHTML = "";
  items.forEach((item, idx) => {
    const el = document.createElement("div");
    el.className = `thumb${item.include ? "" : " off"}`;
    el.dataset.id = item.id;
    el.setAttribute("aria-current", String(idx === current));
    el.tabIndex = 0;
    el.title = `${item.file.name}${item.file.pages > 1 ? `, Seite ${item.pageNo}` : ""}`;
    const cv = document.createElement("canvas");
    el.append(cv);
    const name = document.createElement("span");
    name.className = "tname";
    name.textContent = `${idx + 1}. ${item.file.name}${item.file.pages > 1 ? ` · S. ${item.pageNo}` : ""}`;
    const bar = document.createElement("div");
    bar.className = "tbar";
    bar.innerHTML = `<label><input type="checkbox" ${item.include ? "checked" : ""}> drucken</label><button class="x" type="button" aria-label="Etikett entfernen" title="Entfernen">×</button>`;
    el.append(name, bar);
    box.append(el);
    drawLabel(cv, item, item.det, 100, 110, false);
  });
}

function updateThumb(item) {
  const el = document.querySelector(`.thumb[data-id="${item.id}"] canvas`);
  if (el) drawLabel(el, item, item.det, 100, 110, false);
}

function select(idx) {
  if (idx < 0 || idx >= items.length) return;
  current = idx;
  document.querySelectorAll(".thumb").forEach((t, i) => t.setAttribute("aria-current", String(i === current)));
  const t = document.querySelectorAll(".thumb")[current];
  if (t) t.scrollIntoView({ block: "nearest", inline: "nearest" });
  showItem();
}

function removeItem(id) {
  const idx = items.findIndex((i) => i.id === id);
  if (idx < 0) return;
  items.splice(idx, 1);
  if (!items.length) { clearAll(); return; }
  if (idx < current) current--;
  current = clamp(current, 0, items.length - 1);
  renderAll();
}

function clearAll() {
  for (const f of files.splice(0)) { try { f.pdf && f.pdf.destroy(); } catch { /* egal */ } try { f.bitmap && f.bitmap.close(); } catch { /* egal */ } }
  items = []; current = 0; work = null;
  showEmpty();
}

// ---------------------------------------------------------------------------
// Ansichten, Formular, Ereignisse
// ---------------------------------------------------------------------------
function showWork() {
  $("#emptyView").hidden = true;
  $("#workView").hidden = false;
  $("#topActions").hidden = false;
}
function showEmpty() {
  $("#emptyView").hidden = false;
  $("#workView").hidden = true;
  $("#topActions").hidden = true;
}
function setLoading(on, text) {
  $("#loading").hidden = !on;
  if (text) $("#loadingText").textContent = text;
}
function renderAll() {
  renderThumbs();
  showItem();
}

let toastTimer;
function toast(msg, err = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("err", err);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, err ? 7000 : 4500);
}

function syncForm() {
  $("#format").value = settings.format;
  $("#customW").value = settings.customW;
  $("#customH").value = settings.customH;
  $("#customRow").hidden = settings.format !== "custom";
  $("#rotation").value = settings.rotation;
  $("#margin").value = settings.margin;
  $("#scaleMode").value = settings.scaleMode;
  $("#remember").checked = !!settings.remember;
  $("#raster").checked = !!settings.raster;
}

function onSettingChange() {
  settings.format = $("#format").value;
  settings.customW = clamp(Number($("#customW").value) || 100, 20, 400);
  settings.customH = clamp(Number($("#customH").value) || 150, 20, 400);
  settings.rotation = $("#rotation").value;
  settings.margin = clamp(Number($("#margin").value) || 0, 0, 20);
  settings.scaleMode = $("#scaleMode").value;
  settings.raster = $("#raster").checked;
  $("#customRow").hidden = settings.format !== "custom";
  saveSettings();
  if (items.length) { updateResult(); items.forEach(updateThumb); }
}

function bind() {
  const input = $("#fileInput");
  $("#pickFile").addEventListener("click", () => input.click());
  $("#addMore").addEventListener("click", () => input.click());
  input.addEventListener("change", () => { if (input.files.length) addFiles(input.files); input.value = ""; });
  $("#clearAll").addEventListener("click", clearAll);

  ["#format", "#rotation", "#scaleMode", "#raster"].forEach((s) => $(s).addEventListener("change", onSettingChange));
  ["#customW", "#customH", "#margin"].forEach((s) => $(s).addEventListener("input", onSettingChange));
  $("#remember").addEventListener("change", () => {
    settings.remember = $("#remember").checked;
    if (settings.remember && items[current]) {
      rememberCrop(items[current]);
      toast("Ausschnitt gemerkt. Neue Labels mit gleichem Seitenformat werden genauso zugeschnitten.");
    } else if (!settings.remember) settings.savedCrop = null;
    saveSettings();
  });

  $("#autoDetect").addEventListener("click", () => {
    const item = items[current];
    if (!item) return;
    if (item.blocks.length) setCrop(item, { ...item.blocks[0] }, "auto");
    else { setCrop(item, { x: 0, y: 0, w: 1, h: 1 }, "none"); toast("Auf dieser Seite wurde kein Inhalt gefunden.", true); }
  });
  $("#fullPage").addEventListener("click", () => { const item = items[current]; if (item) setCrop(item, { x: 0, y: 0, w: 1, h: 1 }, "full"); });
  $("#prevItem").addEventListener("click", () => select(current - 1));
  $("#nextItem").addEventListener("click", () => select(current + 1));
  $("#download").addEventListener("click", download);
  $("#print").addEventListener("click", printPdf);

  $("#applyAll").addEventListener("click", () => {
    const src = items[current];
    if (!src) return;
    const { w, h } = itemMm(src);
    let n = 0;
    for (const it of items) {
      if (it === src) continue;
      const m = itemMm(it);
      if (Math.abs(m.w - w) < 3 && Math.abs(m.h - h) < 3) { it.crop = { ...src.crop }; it.source = "manual"; n++; updateThumb(it); }
    }
    toast(n ? `Ausschnitt auf ${n} weitere${n === 1 ? "s Etikett" : " Etiketten"} übertragen.` : "Keine anderen Seiten mit gleichem Format.");
  });
  $("#duplicate").addEventListener("click", () => {
    const src = items[current];
    if (!src) return;
    const used = items.filter((i) => i.file === src.file && i.pageNo === src.pageNo).map((i) => i.crop);
    const next = src.blocks.find((b) => used.every((u) => iou(u, b) < 0.5));
    const copy = { ...src, id: ++uid, crop: next ? { ...next } : { ...src.crop }, source: next ? "auto" : "manual", include: true };
    items.splice(current + 1, 0, copy);
    current += 1;
    renderAll();
    if (!next) toast("Zieh auf der Seite einen Rahmen um das zweite Etikett.");
  });

  $("#thumbs").addEventListener("click", (e) => {
    const t = e.target.closest(".thumb");
    if (!t) return;
    const id = Number(t.dataset.id);
    const item = items.find((i) => i.id === id);
    if (e.target.closest(".x")) { removeItem(id); return; }
    if (e.target.matches("input[type=checkbox]")) {
      item.include = e.target.checked;
      t.classList.toggle("off", !item.include);
      $("#stripCount").textContent = `${items.filter((i) => i.include).length} von ${items.length} ausgewählt`;
      updateResult();
      return;
    }
    if (e.target.closest("label")) return;
    select(items.indexOf(item));
  });
  $("#thumbs").addEventListener("keydown", (e) => {
    const t = e.target.closest(".thumb");
    if (t && (e.key === "Enter" || e.key === " ") && e.target === t) { e.preventDefault(); select(items.findIndex((i) => i.id === Number(t.dataset.id))); }
  });

  const ov = $("#overlay");
  ov.addEventListener("pointerdown", onPointerDown);
  ov.addEventListener("pointermove", onPointerMove);
  ov.addEventListener("pointerup", onPointerUp);
  ov.addEventListener("pointercancel", onPointerUp);

  document.addEventListener("keydown", (e) => {
    if ($("#workView").hidden || e.target.closest("input, select, textarea")) return;
    if (e.key === "ArrowLeft") select(current - 1);
    if (e.key === "ArrowRight") select(current + 1);
  });

  // Ziehen und Ablegen im ganzen Fenster
  let depth = 0;
  const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
  window.addEventListener("dragenter", (e) => { if (!hasFiles(e)) return; depth++; $("#dropVeil").hidden = false; $("#dropCard").classList.add("over"); });
  window.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) { $("#dropVeil").hidden = true; $("#dropCard").classList.remove("over"); } });
  window.addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } });
  window.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0; $("#dropVeil").hidden = true; $("#dropCard").classList.remove("over");
    if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  });
  window.addEventListener("paste", (e) => {
    const fl = e.clipboardData && e.clipboardData.files;
    if (fl && fl.length) addFiles(fl);
  });

  let resizeTimer;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (items.length) showItem(); }, 150); });
}

await loadSettings();
syncForm();
bind();
if (location.hash === "#willkommen") {
  toast("Installiert. Tipp: Über das Puzzle-Symbol oben rechts in Chrome den Label Converter anheften.");
  history.replaceState(null, "", location.pathname);
}
// Zum Testen: window.labelConverter.addFiles([...])
window.labelConverter = { addFiles, get items() { return items; }, layout, buildPdf, settings: () => settings };
