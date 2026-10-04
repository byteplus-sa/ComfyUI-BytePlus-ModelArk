// Full-size viewer for screenshots: <a class="shot-zoom" href="full.webp" data-width data-height>.
// Phones open at actual size (pan with a finger, pinch to zoom); wide screens open fitted to the window.
(function () {
  var ZOOM_STEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 1, 1.5, 2, 3];
  var overlay = null;
  var lastFocus = null;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function open(link) {
    var naturalWidth = Number(link.dataset.width) || 0;
    var naturalHeight = Number(link.dataset.height) || 0;
    var figure = link.closest('figure');
    var caption = figure && figure.querySelector('figcaption');
    var alt = (link.querySelector('img') || {}).alt || '';
    lastFocus = document.activeElement;

    overlay = el('div', 'lb');
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', 'Image viewer');

    var bar = el('div', 'lb-bar');
    var title = el('span', 'lb-title', caption ? caption.textContent : alt);
    var controls = el('div', 'lb-controls');
    var out = el('button', 'lb-btn', '−');
    out.setAttribute('aria-label', 'Zoom out');
    var level = el('button', 'lb-btn lb-level');
    level.setAttribute('aria-label', 'Fit to window');
    var zoomIn = el('button', 'lb-btn', '+');
    zoomIn.setAttribute('aria-label', 'Zoom in');
    var openTab = el('a', 'lb-btn', 'Open');
    openTab.href = link.href;
    openTab.target = '_blank';
    openTab.rel = 'noopener';
    var close = el('button', 'lb-btn lb-close', '×');
    close.setAttribute('aria-label', 'Close');
    controls.append(out, level, zoomIn, openTab, close);
    bar.append(title, controls);

    var stage = el('div', 'lb-stage');
    var image = el('img', 'lb-img');
    image.alt = alt;
    image.src = link.href;
    image.draggable = false;
    stage.append(image);
    overlay.append(bar, stage);
    document.body.append(overlay);
    document.documentElement.classList.add('lb-open');

    var scale = 1;
    var fit = true;

    function fitScale() {
      var width = stage.clientWidth - 16;
      var height = stage.clientHeight - 16;
      if (!naturalWidth || !naturalHeight) return 1;
      return Math.min(width / naturalWidth, height / naturalHeight, 1);
    }

    function apply(newScale, isFit, anchor) {
      var before = scale;
      scale = newScale;
      fit = isFit;
      var width = Math.round(naturalWidth * scale);
      image.style.width = width + 'px';
      image.style.height = Math.round(naturalHeight * scale) + 'px';
      stage.classList.toggle('lb-fit', fit);
      level.textContent = fit ? 'Fit' : Math.round(scale * 100) + '%';
      if (anchor && !fit && before) {
        var ratio = scale / before;
        stage.scrollLeft = (stage.scrollLeft + anchor.x) * ratio - anchor.x;
        stage.scrollTop = (stage.scrollTop + anchor.y) * ratio - anchor.y;
      } else if (!fit) {
        stage.scrollLeft = 0;
        stage.scrollTop = 0;
      }
    }

    function step(direction) {
      var current = scale;
      var anchor = { x: stage.clientWidth / 2, y: stage.clientHeight / 2 };
      var next;
      if (direction > 0) {
        next = ZOOM_STEPS.find(function (z) { return z > current + 0.001; }) || ZOOM_STEPS[ZOOM_STEPS.length - 1];
      } else {
        next = ZOOM_STEPS.slice().reverse().find(function (z) { return z < current - 0.001; }) || ZOOM_STEPS[0];
      }
      apply(next, false, anchor);
    }

    var phone = window.matchMedia('(max-width: 50rem)').matches || window.matchMedia('(pointer: coarse)').matches;
    var initial = fitScale();
    if (phone || initial >= 1) apply(1, false);
    else apply(initial, true);

    out.addEventListener('click', function () { step(-1); });
    zoomIn.addEventListener('click', function () { step(1); });
    level.addEventListener('click', function () {
      if (fit) apply(1, false);
      else apply(fitScale(), true);
    });
    image.addEventListener('dblclick', function (event) {
      if (fit) apply(1, false, { x: event.offsetX, y: event.offsetY });
      else apply(fitScale(), true);
    });
    stage.addEventListener('click', function (event) {
      if (event.target === stage) shut();
    });
    close.addEventListener('click', shut);
    overlay.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') shut();
      else if (event.key === '+' || event.key === '=') step(1);
      else if (event.key === '-') step(-1);
      else if (event.key === '0') apply(fitScale(), true);
    });
    window.addEventListener('resize', onResize);
    function onResize() { if (fit) apply(fitScale(), true); }
    overlay._cleanup = function () { window.removeEventListener('resize', onResize); };
    close.focus();
  }

  function shut() {
    if (!overlay) return;
    if (overlay._cleanup) overlay._cleanup();
    overlay.remove();
    overlay = null;
    document.documentElement.classList.remove('lb-open');
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  document.addEventListener('click', function (event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var link = event.target.closest && event.target.closest('a.shot-zoom');
    if (!link) return;
    event.preventDefault();
    open(link);
  });
})();
