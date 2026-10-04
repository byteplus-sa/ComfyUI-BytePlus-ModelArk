// In-page screenshot helpers for the docs. Load into a ComfyUI page that has this pack installed:
//   const s = document.createElement('script'); s.src = 'http://127.0.0.1:8300/capture.js'; document.head.append(s);
// then
//   await DOCS.shotNode('BytePlusSeedream', 'nodes/seedream.png', { prompt: 'A glossy 3D badge ...' });
//   await DOCS.shotWorkflow('seedream');          // -> templates/_wf/seedream.png (convert to .webp, see README.md)
(() => {
  const BASE = 'http://127.0.0.1:8300';
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const DOCS = (window.DOCS = {});

  const save = async (rel, blob) => {
    const r = await fetch(`${BASE}/save/${rel}`, { method: 'POST', body: blob });
    if (!r.ok) throw new Error('save failed: ' + rel);
  };
  const toBlob = (canvas) => new Promise((res) => canvas.toBlob(res, 'image/png'));

  // Multi-line text boxes are DOM elements, not canvas drawing: paint them by hand.
  function paintTextAreas(ctx, s, offset) {
    for (const n of app.graph._nodes) {
      for (const w of n.widgets || []) {
        if (!w.element || w.hidden) continue;
        const area = w.element.tagName === 'TEXTAREA' ? w.element : w.element.querySelector?.('textarea');
        if (!area) continue;
        const x = (n.pos[0] + 12 + offset[0]) * s, y = (n.pos[1] + (w.last_y ?? 0) + 2 + offset[1]) * s;
        const width = (n.size[0] - 24) * s, height = Math.max(50, (w.computedHeight || area.clientHeight || 80) - 4) * s;
        ctx.save();
        ctx.fillStyle = '#222'; ctx.strokeStyle = '#555'; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.roundRect(x, y, width, height, 5 * s); ctx.fill(); ctx.stroke();
        ctx.beginPath(); ctx.rect(x, y, width, height); ctx.clip();
        const fs = 13 * s; ctx.font = fs + 'px sans-serif'; ctx.fillStyle = area.value ? '#ddd' : '#888';
        let yy = y + fs * 1.5;
        for (const raw of String(area.value || area.placeholder || w.name).split('\n')) {
          let line = '';
          for (const word of raw.split(' ')) {
            if (ctx.measureText(line + word).width > width - 16 * s && line) { ctx.fillText(line, x + 8 * s, yy); yy += fs * 1.35; line = word + ' '; } else line += word + ' ';
          }
          ctx.fillText(line, x + 8 * s, yy); yy += fs * 1.35;
        }
        ctx.restore();
      }
    }
  }

  // Markdown notes are DOM widgets too.
  function paintNotes(ctx, s, offset) {
    for (const n of app.graph._nodes) {
      if (n.type !== 'MarkdownNote' && n.type !== 'Note') continue;
      const text = String(n.widgets?.[0]?.value || ''); if (!text) continue;
      const x = (n.pos[0] + offset[0]) * s, y = (n.pos[1] + offset[1]) * s, w = n.size[0] * s, h = n.size[1] * s;
      ctx.save(); ctx.beginPath(); ctx.rect(x, y, w, h); ctx.clip(); ctx.fillStyle = '#2b2b24'; ctx.fillRect(x, y, w, h);
      const fs = Math.max(11, 15 * s); let yy = y + fs * 1.6; ctx.fillStyle = '#eee';
      for (const raw of text.split('\n')) {
        const head = /^#+\s/.test(raw); const line = raw.replace(/^#+\s*/, '').replace(/[*`]/g, '');
        ctx.font = (head ? 'bold ' : '') + (head ? fs * 1.25 : fs) + 'px sans-serif';
        let cur = '';
        for (const word of line.split(' ')) {
          if (ctx.measureText(cur + word).width > w - 24 * s && cur) { ctx.fillText(cur, x + 12 * s, yy); yy += fs * 1.35; cur = word + ' '; } else cur += word + ' ';
        }
        ctx.fillText(cur, x + 12 * s, yy); yy += fs * (head ? 1.7 : 1.5);
      }
      ctx.restore();
    }
  }

  // Draw the whole graph (nodes and groups) at its natural size, at most 2400 px on a side.
  async function drawGraph(scaleCap = 1, pad = 40) {
    const c = app.canvas, cv = c.canvas, bg = c.bgcanvas;
    const old = { w: cv.width, h: cv.height, bw: bg?.width, bh: bg?.height };
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const n of app.graph._nodes) { x0 = Math.min(x0, n.pos[0]); y0 = Math.min(y0, n.pos[1] - 30); x1 = Math.max(x1, n.pos[0] + n.size[0]); y1 = Math.max(y1, n.pos[1] + n.size[1]); }
    for (const g of app.graph._groups || []) { const b = g._bounding || g.bounding; if (b) { x0 = Math.min(x0, b[0]); y0 = Math.min(y0, b[1]); x1 = Math.max(x1, b[0] + b[2]); y1 = Math.max(y1, b[1] + b[3]); } }
    const bw = x1 - x0, bh = y1 - y0;
    const s = Math.min(scaleCap, 2400 / (bw + 2 * pad), 2400 / (bh + 2 * pad));
    const W = Math.round((bw + 2 * pad) * s), H = Math.round((bh + 2 * pad) * s);
    for (const n of app.graph._nodes) n.has_errors = false; // no red "missing file" outlines
    app.lastNodeErrors = null;
    const dpr = Object.getOwnPropertyDescriptor(window, 'devicePixelRatio');
    Object.defineProperty(window, 'devicePixelRatio', { value: 1, configurable: true });
    try {
      c.show_info = false; c.low_quality_zoom_threshold = 0.05;
      cv.width = W; cv.height = H; if (bg) { bg.width = W; bg.height = H; }
      c.ds.scale = s; c.ds.offset = [pad - x0, pad - y0];
      c.setDirty(true, true); c.draw(true, true); await sleep(400); c.draw(true, true);
      const out = document.createElement('canvas'); out.width = W; out.height = H;
      const ctx = out.getContext('2d'); ctx.fillStyle = '#1e1e1e'; ctx.fillRect(0, 0, W, H); ctx.drawImage(cv, 0, 0);
      paintNotes(ctx, s, c.ds.offset); paintTextAreas(ctx, s, c.ds.offset);
      return out;
    } finally {
      if (dpr) Object.defineProperty(window, 'devicePixelRatio', dpr); else delete window.devicePixelRatio;
      cv.width = old.w; cv.height = old.h; if (bg) { bg.width = old.bw; bg.height = old.bh; }
      c.setDirty(true, true); c.draw(true, true);
    }
  }

  // One node with default values (and an example prompt), drawn at 2x.
  DOCS.shotNode = async (cls, rel, { prompt, setup } = {}) => {
    app.graph.clear();
    const n = LiteGraph.createNode(cls); app.graph.add(n); n.pos = [0, 0];
    if (setup) await setup(n);
    for (const w of n.widgets || []) {
      const area = w.element && (w.element.tagName === 'TEXTAREA' ? w.element : w.element.querySelector?.('textarea'));
      if (area && prompt && !area.value) { w.value = prompt; area.value = prompt; break; }
    }
    await sleep(300);
    const size = n.computeSize(); n.size = [Math.max(size[0], 420), size[1]];
    app.graph.setDirtyCanvas(true, true); await sleep(300);
    const out = await drawGraph(2, 40);
    await save(rel, await toBlob(out));
    return `${rel} ${out.width}x${out.height}`;
  };

  // The graph currently on the canvas (build it by hand first), saved to src/assets/<rel>.
  DOCS.shotCurrent = async (rel, scale = 1) => {
    const out = await drawGraph(scale, 40);
    await save(rel, await toBlob(out));
    return `${rel} ${out.width}x${out.height}`;
  };

  // A template from public/workflows/<slug>.json as the user sees it.
  DOCS.shotWorkflow = async (slug) => {
    const wf = await (await fetch(`${BASE}/workflows/${slug}.json`)).json();
    await app.loadGraphData(wf, true, true, slug); await sleep(1500);
    const out = await drawGraph(1, 40);
    await save(`templates/_wf/${slug}.png`, await toBlob(out));
    return `${slug} ${out.width}x${out.height}`;
  };
})();
