/**
 * drive-viz.js — Animated football drive visualization for AltSports Archive
 *
 * Reads window._driveVizData = {
 *   away: 'BHAM',
 *   home: 'MICH',
 *   drives: [{ team, result, plays: [{startSide, startYard, endSide, endYard, type, desc}] }]
 * }
 *
 * Renders an SVG football field; animates each drive's path on demand.
 */
(function () {
  'use strict';

  var data = window._driveVizData;
  if (!data || !data.drives || !data.drives.length) return;

  var host = document.getElementById('drive-viz');
  if (!host) return;

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  // ── Geometry ──────────────────────────────────────────────────────────────
  // Field is 0..120 yards (10yd endzone + 100 + 10yd endzone).
  // We map the AWAY team's goal line at x=10, HOME team's goal line at x=110.
  // For a play with start_side/yardline, compute absolute x:
  //   if side === HOME: x = 110 - yardline   (yardline 1 is at HOME goal)
  //   if side === AWAY: x = 10 + yardline
  var W = 1200;          // SVG viewport width
  var H = 360;           // SVG viewport height
  var FIELD_X0 = 0;
  var FIELD_X1 = W;
  var EZ = W * (10 / 120);            // endzone pixel width
  var YDPX = (W - 2 * EZ) / 100;      // pixels per yard

  var away = (data.away || '').toUpperCase();
  var home = (data.home || '').toUpperCase();

  function yardToX(side, yardline) {
    side = (side || '').toUpperCase();
    if (yardline == null) return null;
    if (side === home) return (FIELD_X1 - EZ) - yardline * YDPX;
    if (side === away) return FIELD_X0 + EZ + yardline * YDPX;
    return null;
  }

  // ── Build drive geometry ──────────────────────────────────────────────────
  // For each drive, compute possession side (whose offense), color, and a
  // sequence of (x, y) points tracing field position over plays.
  function buildDrives() {
    var out = [];
    data.drives.forEach(function (d, di) {
      var team = ((d.team && (d.team.alias || d.team.name)) || d.team || '').toString().toUpperCase();
      var isHome = team === home;
      // Lane: home drives below midline, away above
      var laneY = isHome ? H * 0.62 : H * 0.38;
      var pts = [];
      (d.plays || []).forEach(function (p) {
        // Support both pre-shaped and raw shapes
        var ss = p.start_situation || {};
        var es = p.end_situation || {};
        var sLoc = ss.location || {};
        var eLoc = es.location || {};
        var startSide = p.startSide || sLoc.alias;
        var startYard = (p.startYard != null) ? p.startYard : sLoc.yardline;
        var endSide = p.endSide || eLoc.alias;
        var endYard = (p.endYard != null) ? p.endYard : eLoc.yardline;
        var x0 = yardToX(startSide, startYard);
        var x1 = yardToX(endSide, endYard);
        if (x0 != null) pts.push({ x: x0, y: laneY, kind: 'start', play: p });
        if (x1 != null) pts.push({ x: x1, y: laneY, kind: 'end', play: p });
      });
      // Dedupe consecutive identical x
      var clean = [];
      pts.forEach(function (pt) {
        var last = clean[clean.length - 1];
        if (!last || Math.abs(last.x - pt.x) > 0.5) clean.push(pt);
      });
      if (clean.length < 2) return;
      out.push({
        index: di,
        team: team,
        isHome: isHome,
        result: d.result || '',
        points: clean,
        startX: clean[0].x,
        endX: clean[clean.length - 1].x,
        laneY: laneY,
        plays: d.plays || [],
      });
    });
    return out;
  }

  var drives = buildDrives();
  if (!drives.length) return;

  // ── SVG construction ──────────────────────────────────────────────────────
  var SVG_NS = 'http://www.w3.org/2000/svg';
  function el(name, attrs) {
    var n = document.createElementNS(SVG_NS, name);
    if (attrs) for (var k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  }

  function render() {
    host.innerHTML = '';

    var accent = css('--accent') || '#4ea1ff';
    var accent2 = css('--accent-2') || css('--accent') || '#ff7a59';
    var border = css('--border') || '#333';
    var bg = css('--bg2') || '#111';
    var textDim = css('--text-dim') || '#999';
    var text = css('--text') || '#eee';

    // Field colors
    var fieldFill = bg;
    var stripeFill = mix(bg, text, 0.04);

    var wrap = document.createElement('div');
    wrap.className = 'drive-viz-wrap';
    host.appendChild(wrap);

    var svg = el('svg', {
      viewBox: '0 0 ' + W + ' ' + H,
      preserveAspectRatio: 'xMidYMid meet',
      class: 'drive-viz-svg',
      role: 'img',
      'aria-label': 'Animated drive chart: ' + away + ' at ' + home,
    });
    wrap.appendChild(svg);

    // Background field
    svg.appendChild(el('rect', {
      x: 0, y: 0, width: W, height: H,
      fill: fieldFill,
    }));

    // Alternating 10yd stripes (subtle)
    for (var s = 0; s < 10; s++) {
      if (s % 2 === 0) {
        svg.appendChild(el('rect', {
          x: EZ + s * 10 * YDPX, y: 0,
          width: 10 * YDPX, height: H,
          fill: stripeFill,
        }));
      }
    }

    // Endzones
    svg.appendChild(el('rect', {
      x: 0, y: 0, width: EZ, height: H,
      fill: mix(bg, accent, 0.18),
    }));
    svg.appendChild(el('rect', {
      x: W - EZ, y: 0, width: EZ, height: H,
      fill: mix(bg, accent2, 0.18),
    }));

    // Yard lines (every 10yd)
    for (var y = 0; y <= 10; y++) {
      var lx = EZ + y * 10 * YDPX;
      svg.appendChild(el('line', {
        x1: lx, y1: 18, x2: lx, y2: H - 18,
        stroke: border, 'stroke-width': 1, opacity: 0.7,
      }));
      // Labels (10..50..10)
      var num = y <= 5 ? y * 10 : (10 - y) * 10;
      if (y > 0 && y < 10) {
        var t1 = el('text', {
          x: lx, y: 30, 'text-anchor': 'middle',
          fill: textDim, 'font-size': 14,
          'font-family': 'Inter, Helvetica Neue, Arial, sans-serif',
        });
        t1.textContent = String(num);
        svg.appendChild(t1);
        var t2 = el('text', {
          x: lx, y: H - 14, 'text-anchor': 'middle',
          fill: textDim, 'font-size': 14,
          'font-family': 'Inter, Helvetica Neue, Arial, sans-serif',
        });
        t2.textContent = String(num);
        svg.appendChild(t2);
      }
    }

    // Midline accent
    svg.appendChild(el('line', {
      x1: W / 2, y1: 18, x2: W / 2, y2: H - 18,
      stroke: text, 'stroke-width': 1.5, opacity: 0.45,
    }));

    // Endzone labels
    var ezA = el('text', {
      x: EZ / 2, y: H / 2 + 6, 'text-anchor': 'middle',
      fill: text, 'font-size': 22, 'font-weight': 700,
      'font-family': 'Inter, Helvetica Neue, Arial, sans-serif',
      transform: 'rotate(-90 ' + (EZ / 2) + ' ' + (H / 2) + ')',
      opacity: 0.85,
    });
    ezA.textContent = away;
    svg.appendChild(ezA);

    var ezH = el('text', {
      x: W - EZ / 2, y: H / 2 + 6, 'text-anchor': 'middle',
      fill: text, 'font-size': 22, 'font-weight': 700,
      'font-family': 'Inter, Helvetica Neue, Arial, sans-serif',
      transform: 'rotate(90 ' + (W - EZ / 2) + ' ' + (H / 2) + ')',
      opacity: 0.85,
    });
    ezH.textContent = home;
    svg.appendChild(ezH);

    // Lane separator (faint horizontal at midfield-y)
    svg.appendChild(el('line', {
      x1: EZ, y1: H / 2, x2: W - EZ, y2: H / 2,
      stroke: border, 'stroke-width': 1, 'stroke-dasharray': '4 6', opacity: 0.5,
    }));

    // ── Drive paths ─────────────────────────────────────────────────────────
    var drivesGroup = el('g', { class: 'drives' });
    svg.appendChild(drivesGroup);

    var driveEls = [];
    drives.forEach(function (d) {
      var color = d.isHome ? accent2 : accent;
      var dirSign = d.isHome ? -1 : 1; // home advances right→left; away left→right
      var pathStr = pointsToPath(d.points);
      var path = el('path', {
        d: pathStr,
        fill: 'none',
        stroke: color,
        'stroke-width': 3,
        'stroke-linecap': 'round',
        'stroke-linejoin': 'round',
        opacity: 0.85,
      });
      path.classList.add('drive-path');
      drivesGroup.appendChild(path);

      // Compute path length for animation
      var len = 0;
      try { len = path.getTotalLength(); } catch (e) { len = 1000; }
      path.style.strokeDasharray = len;
      path.style.strokeDashoffset = len;

      // Start marker
      var start = el('circle', {
        cx: d.startX, cy: d.laneY, r: 5,
        fill: bg, stroke: color, 'stroke-width': 2,
        opacity: 0,
      });
      drivesGroup.appendChild(start);

      // End marker (result)
      var endMark = el('circle', {
        cx: d.endX, cy: d.laneY, r: 7,
        fill: color, stroke: bg, 'stroke-width': 2,
        opacity: 0,
      });
      drivesGroup.appendChild(endMark);

      // Result label near end
      var lblY = d.isHome ? d.laneY + 22 : d.laneY - 12;
      var label = el('text', {
        x: d.endX, y: lblY, 'text-anchor': 'middle',
        fill: text, 'font-size': 12, 'font-weight': 600,
        'font-family': 'Inter, Helvetica Neue, Arial, sans-serif',
        opacity: 0,
      });
      label.textContent = shortResult(d.result);
      drivesGroup.appendChild(label);

      driveEls.push({ d: d, path: path, len: len, start: start, endMark: endMark, label: label });
    });

    // ── Controls ────────────────────────────────────────────────────────────
    var controls = document.createElement('div');
    controls.className = 'drive-viz-controls';

    var playBtn = document.createElement('button');
    playBtn.type = 'button';
    playBtn.className = 'drive-viz-btn';
    playBtn.textContent = '▶ Play drives';

    var resetBtn = document.createElement('button');
    resetBtn.type = 'button';
    resetBtn.className = 'drive-viz-btn drive-viz-btn-secondary';
    resetBtn.textContent = '↺ Reset';

    var status = document.createElement('span');
    status.className = 'drive-viz-status';
    status.textContent = drives.length + ' scoring drive' + (drives.length === 1 ? '' : 's');

    var legend = document.createElement('div');
    legend.className = 'drive-viz-legend';
    legend.innerHTML =
      '<span class="drive-viz-legend-item"><span class="drive-viz-swatch" style="background:' + accent + '"></span>' + away + '</span>' +
      '<span class="drive-viz-legend-item"><span class="drive-viz-swatch" style="background:' + accent2 + '"></span>' + home + '</span>';

    controls.appendChild(playBtn);
    controls.appendChild(resetBtn);
    controls.appendChild(status);
    controls.appendChild(legend);
    wrap.appendChild(controls);

    // Show all drives instantly (initial static state)
    function showAll() {
      driveEls.forEach(function (e) {
        e.path.style.transition = 'none';
        e.path.style.strokeDashoffset = 0;
        e.start.setAttribute('opacity', '1');
        e.endMark.setAttribute('opacity', '1');
        e.label.setAttribute('opacity', '0.95');
      });
    }

    function reset() {
      driveEls.forEach(function (e) {
        e.path.style.transition = 'none';
        e.path.style.strokeDashoffset = e.len;
        e.start.setAttribute('opacity', '0');
        e.endMark.setAttribute('opacity', '0');
        e.label.setAttribute('opacity', '0');
      });
      status.textContent = drives.length + ' scoring drive' + (drives.length === 1 ? '' : 's');
    }

    var playing = false;
    function play() {
      if (playing) return;
      playing = true;
      playBtn.disabled = true;
      reset();
      var i = 0;
      function next() {
        if (i >= driveEls.length) {
          playing = false;
          playBtn.disabled = false;
          status.textContent = 'Done — replay or reset';
          return;
        }
        var e = driveEls[i];
        var dur = Math.max(700, Math.min(2200, e.len * 3));
        status.textContent = 'Drive ' + (i + 1) + ' of ' + driveEls.length + ' — ' +
          e.d.team + ' (' + shortResult(e.d.result) + ')';
        e.start.setAttribute('opacity', '1');
        e.path.style.transition = 'stroke-dashoffset ' + dur + 'ms cubic-bezier(.4,.0,.2,1)';
        // Force reflow
        // eslint-disable-next-line no-unused-expressions
        e.path.getBoundingClientRect();
        e.path.style.strokeDashoffset = 0;
        setTimeout(function () {
          e.endMark.setAttribute('opacity', '1');
          e.label.setAttribute('opacity', '0.95');
          i++;
          setTimeout(next, 220);
        }, dur);
      }
      next();
    }

    playBtn.addEventListener('click', play);
    resetBtn.addEventListener('click', function () {
      reset();
      playing = false;
      playBtn.disabled = false;
    });

    // Initial state: show all drives (static), let user replay
    showAll();
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function pointsToPath(pts) {
    if (!pts.length) return '';
    var d = 'M ' + pts[0].x.toFixed(1) + ' ' + pts[0].y.toFixed(1);
    for (var i = 1; i < pts.length; i++) {
      // Slight vertical wiggle for visual interest
      var prev = pts[i - 1];
      var cur = pts[i];
      var midX = (prev.x + cur.x) / 2;
      var bulge = (i % 2 === 0 ? -1 : 1) * Math.min(8, Math.abs(cur.x - prev.x) * 0.05);
      var midY = prev.y + bulge;
      d += ' Q ' + midX.toFixed(1) + ' ' + midY.toFixed(1) +
           ' ' + cur.x.toFixed(1) + ' ' + cur.y.toFixed(1);
    }
    return d;
  }

  function shortResult(r) {
    if (!r) return '—';
    var s = String(r).toLowerCase();
    if (s.indexOf('touchdown') >= 0) return 'TD';
    if (s.indexOf('field goal') >= 0 || s === 'fg') return 'FG';
    if (s.indexOf('safety') >= 0) return 'SAF';
    if (s.indexOf('punt') >= 0) return 'PUNT';
    if (s.indexOf('downs') >= 0) return 'DOWNS';
    if (s.indexOf('intercept') >= 0) return 'INT';
    if (s.indexOf('fumble') >= 0) return 'FUM';
    if (s.indexOf('missed') >= 0) return 'MISS';
    if (s.indexOf('end of') >= 0) return 'EOP';
    return r.length > 8 ? r.slice(0, 8).toUpperCase() : r.toUpperCase();
  }

  function mix(a, b, t) {
    var ca = parseColor(a);
    var cb = parseColor(b);
    if (!ca || !cb) return a;
    var r = Math.round(ca[0] + (cb[0] - ca[0]) * t);
    var g = Math.round(ca[1] + (cb[1] - ca[1]) * t);
    var bl = Math.round(ca[2] + (cb[2] - ca[2]) * t);
    return 'rgb(' + r + ',' + g + ',' + bl + ')';
  }

  function parseColor(c) {
    if (!c) return null;
    c = c.trim();
    if (c[0] === '#') {
      if (c.length === 4) {
        return [parseInt(c[1] + c[1], 16), parseInt(c[2] + c[2], 16), parseInt(c[3] + c[3], 16)];
      }
      if (c.length === 7) {
        return [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16)];
      }
    }
    var m = c.match(/rgba?\(([^)]+)\)/);
    if (m) {
      var p = m[1].split(',').map(function (x) { return parseFloat(x); });
      return [p[0], p[1], p[2]];
    }
    return null;
  }

  // Render and re-render on theme changes
  render();
  var mo = new MutationObserver(function (muts) {
    for (var i = 0; i < muts.length; i++) {
      if (muts[i].attributeName === 'data-theme') { render(); break; }
    }
  });
  mo.observe(document.documentElement, { attributes: true });
})();
