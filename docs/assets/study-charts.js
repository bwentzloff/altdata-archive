/**
 * study-charts.js — renders charts on study pages from window._studyCharts.
 * Supports type: "bar" (single dataset) and "stacked-bar" (multi dataset).
 */
(function () {
  'use strict';

  var _charts = [];

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  // Distinct, accessible palette that works on light + dark themes.
  var PALETTE = [
    '#3b82f6', // blue
    '#f97316', // orange
    '#10b981', // green
    '#a855f7', // purple
    '#ef4444', // red
    '#eab308', // yellow
    '#06b6d4', // cyan
    '#ec4899', // pink
  ];

  function destroyAll() {
    _charts.forEach(function (c) { c.destroy(); });
    _charts = [];
  }

  function baseOpts(textDim, borderColor, suffix) {
    var font = { family: 'Inter, Helvetica Neue, Arial, sans-serif', size: 11 };
    return {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, labels: { color: textDim, font: font } },
        tooltip: {
          backgroundColor: css('--bg2'),
          borderColor: borderColor,
          borderWidth: 1,
          titleColor: css('--text'),
          bodyColor: textDim,
          callbacks: suffix ? {
            label: function (ctx) {
              var lbl = ctx.dataset.label || '';
              return (lbl ? lbl + ': ' : '') + ctx.parsed.x + suffix;
            },
          } : undefined,
        },
      },
      scales: {
        x: {
          ticks: { color: textDim, font: font,
                   callback: function (v) { return v + (suffix || ''); } },
          grid:  { color: borderColor },
          border:{ color: borderColor },
          beginAtZero: true,
        },
        y: {
          ticks: { color: textDim, font: font },
          grid:  { color: borderColor, display: false },
          border:{ color: borderColor },
        },
      },
      animation: { duration: 250 },
    };
  }

  function renderBar(spec, indexAxis) {
    var el = document.getElementById(spec.id);
    if (!el) return;
    var textDim = css('--text-dim');
    var borderColor = css('--border');
    var stacked = spec.type === 'stacked-bar';
    var opts = baseOpts(textDim, borderColor, spec.value_suffix);
    opts.indexAxis = indexAxis || 'x';
    if (stacked) {
      opts.scales.x.stacked = true;
      opts.scales.y.stacked = true;
    } else {
      opts.plugins.legend.display = (spec.datasets || []).length > 1;
    }

    var datasets = (spec.datasets || []).map(function (ds, i) {
      var c = PALETTE[i % PALETTE.length];
      return {
        label: ds.label,
        data: ds.data,
        backgroundColor: c + 'cc',
        borderColor: c,
        borderWidth: 1,
      };
    });

    var chart = new Chart(el, {
      type: 'bar',
      data: { labels: spec.labels, datasets: datasets },
      options: opts,
    });
    _charts.push(chart);
  }

  function initAll() {
    if (typeof Chart === 'undefined') return;
    destroyAll();
    var specs = window._studyCharts || [];
    specs.forEach(function (spec) {
      if (spec.type === 'network') {
        renderNetwork(spec);
        return;
      }
      var idx = spec.indexAxis || 'x';
      renderBar(spec, idx);
    });
  }

  // ── Force-directed network of leagues ──────────────────────────────
  function renderNetwork(spec) {
    if (typeof d3 === 'undefined') return;
    var container = document.getElementById(spec.id + '-host');
    if (!container) return;

    var accent      = css('--accent') || '#3b82f6';
    var textColor   = css('--text')   || '#e5e7eb';
    var textDim     = css('--text-dim') || '#9ca3af';
    var borderColor = css('--border') || '#374151';
    var bg2         = css('--bg2')    || '#1f2937';

    container.innerHTML = '';

    var width  = container.clientWidth || 800;
    var height = Math.max(420, Math.min(560, width * 0.65));

    var svg = d3.select(container).append('svg')
      .attr('width',  width)
      .attr('height', height)
      .attr('viewBox', '0 0 ' + width + ' ' + height);

    // Tooltip
    var tip = d3.select(container).append('div')
      .attr('class', 'study-network-tip')
      .style('opacity', 0);

    var nodes = (spec.nodes || []).map(function (n) { return Object.assign({}, n); });
    var edges = (spec.edges || []).map(function (e) { return Object.assign({}, e); });

    if (!nodes.length) return;

    var maxNodeVal = d3.max(nodes, function (n) { return n.value; }) || 1;
    var minNodeVal = d3.min(nodes, function (n) { return n.value; }) || 0;
    var nodeRadius = d3.scaleSqrt()
      .domain([0, maxNodeVal])
      .range([8, 42]);

    var maxEdgeVal = d3.max(edges, function (e) { return e.value; }) || 1;
    var minEdgeVal = d3.min(edges, function (e) { return e.value; }) || 0;
    var edgeWidth = d3.scaleSqrt()
      .domain([0, maxEdgeVal])
      .range([0.5, 8]);
    var edgeOpacity = d3.scaleLinear()
      .domain([minEdgeVal, maxEdgeVal])
      .range([0.18, 0.85]);

    // Mark edges that are reciprocal (A→B and B→A both exist) so we can
    // curve them in opposite directions to keep the arrows visible.
    var edgeKey = function (s, t) { return s + '||' + t; };
    var keySet = {};
    edges.forEach(function (e) { keySet[edgeKey(e.source, e.target)] = true; });
    edges.forEach(function (e) {
      e._reciprocal = !!keySet[edgeKey(e.target, e.source)];
    });

    // Arrow markers — markerUnits='strokeWidth' makes the head scale with line
    // thickness automatically. We still emit a few buckets at decreasing
    // relative sizes so very thick lines don't get a comically huge head.
    var defs = svg.append('defs');
    var widthBuckets = [1, 2, 3, 4, 6, 8];
    function arrowSizeFor(w) {
      // Head dims are in stroke-widths. Smaller multiplier for thick lines.
      if (w <= 1)  return 7;
      if (w <= 2)  return 5.5;
      if (w <= 3)  return 4.5;
      if (w <= 4)  return 3.8;
      if (w <= 6)  return 3.0;
      return 2.4;
    }
    widthBuckets.forEach(function (w) {
      var sz = arrowSizeFor(w);
      defs.append('marker')
        .attr('id', spec.id + '-arrow-' + w)
        .attr('viewBox', '0 0 10 10')
        .attr('refX', 9)
        .attr('refY', 5)
        .attr('markerWidth',  sz)
        .attr('markerHeight', sz)
        .attr('orient', 'auto-start-reverse')
        .attr('markerUnits', 'strokeWidth')
        .append('path')
          .attr('d', 'M0,0 L10,5 L0,10 Z')
          .attr('fill', accent);
    });

    function bucketFor(w) {
      for (var i = 0; i < widthBuckets.length; i++) {
        if (w <= widthBuckets[i]) return widthBuckets[i];
      }
      return widthBuckets[widthBuckets.length - 1];
    }

    var sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(edges)
        .id(function (d) { return d.id; })
        .distance(function (d) {
          // Heavier edges = shorter (pulled together)
          var t = (d.value - minEdgeVal) / Math.max(1, (maxEdgeVal - minEdgeVal));
          return 220 - 140 * t;
        })
        .strength(function (d) {
          var t = (d.value - minEdgeVal) / Math.max(1, (maxEdgeVal - minEdgeVal));
          return 0.15 + 0.6 * t;
        }))
      .force('charge', d3.forceManyBody().strength(-260))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collide', d3.forceCollide().radius(function (d) {
        return nodeRadius(d.value) + 6;
      }));

    var linkSel = svg.append('g')
      .attr('fill', 'none')
      .attr('stroke', accent)
      .selectAll('path')
      .data(edges)
      .enter().append('path')
        .attr('stroke-width', function (d) { return edgeWidth(d.value); })
        .attr('stroke-opacity', function (d) { return edgeOpacity(d.value); })
        .attr('stroke-dasharray', function (d) { return d.directed ? null : '4,4'; })
        .attr('marker-end', function (d) {
          if (!d.directed) return null;
          return 'url(#' + spec.id + '-arrow-' + bucketFor(edgeWidth(d.value)) + ')';
        })
        .style('cursor', 'pointer')
        .on('mouseover', function (event, d) {
          d3.select(this).attr('stroke-opacity', 1);
          var dirText = d.directed
            ? nameOf(d.source) + ' → ' + nameOf(d.target)
            : nameOf(d.source) + ' ↔ ' + nameOf(d.target) + ' <span style="opacity:0.7">(direction unknown)</span>';
          tip.style('opacity', 1)
             .html('<strong>' + dirText + '</strong><br>' +
                   d.value.toLocaleString() + ' player' + (d.value === 1 ? '' : 's'));
        })
        .on('mousemove', function (event) {
          var rect = container.getBoundingClientRect();
          tip.style('left', (event.clientX - rect.left + 12) + 'px')
             .style('top',  (event.clientY - rect.top  + 12) + 'px');
        })
        .on('mouseout', function (event, d) {
          d3.select(this).attr('stroke-opacity', edgeOpacity(d.value));
          tip.style('opacity', 0);
        });

    var nodeG = svg.append('g')
      .selectAll('g')
      .data(nodes)
      .enter().append('g')
        .style('cursor', 'grab')
        .call(d3.drag()
          .on('start', function (event, d) {
            if (!event.active) sim.alphaTarget(0.3).restart();
            d.fx = d.x; d.fy = d.y;
          })
          .on('drag', function (event, d) {
            d.fx = event.x; d.fy = event.y;
          })
          .on('end', function (event, d) {
            if (!event.active) sim.alphaTarget(0);
            d.fx = null; d.fy = null;
          }));

    nodeG.append('circle')
      .attr('r', function (d) { return nodeRadius(d.value); })
      .attr('fill', function (d) { return d.is_nfl ? accent : bg2; })
      .attr('stroke', function (d) { return d.is_nfl ? '#fff' : accent; })
      .attr('stroke-width', function (d) { return d.is_nfl ? 2 : 1.5; })
      .on('mouseover', function (event, d) {
        tip.style('opacity', 1)
           .html('<strong>' + d.label + '</strong><br>' +
                 d.value.toLocaleString() + ' player' + (d.value === 1 ? '' : 's'));
      })
      .on('mousemove', function (event) {
        var rect = container.getBoundingClientRect();
        tip.style('left', (event.clientX - rect.left + 12) + 'px')
           .style('top',  (event.clientY - rect.top  + 12) + 'px');
      })
      .on('mouseout', function () { tip.style('opacity', 0); });

    nodeG.append('text')
      .text(function (d) { return d.label; })
      .attr('text-anchor', 'middle')
      .attr('dy', function (d) { return nodeRadius(d.value) + 14; })
      .attr('fill', textColor)
      .style('font', '600 12px Inter, Helvetica Neue, Arial, sans-serif')
      .style('pointer-events', 'none');

    function nameOf(ref) {
      if (typeof ref === 'object' && ref) return ref.label || ref.id;
      var match = nodes.find(function (n) { return n.id === ref; });
      return match ? match.label : ref;
    }

    sim.on('tick', function () {
      linkSel.attr('d', function (d) {
        var sx = d.source.x, sy = d.source.y;
        var tx = d.target.x, ty = d.target.y;
        var dx = tx - sx, dy = ty - sy;
        var dist = Math.sqrt(dx * dx + dy * dy) || 1;
        // Shorten line so the arrowhead lands on the node edge, not its center.
        var w = edgeWidth(d.value);
        var arrowPx = d.directed ? arrowSizeFor(bucketFor(w)) * w * 0.6 : 0;
        var tr = nodeRadius(d.target.value) + arrowPx + 2;
        var sr = nodeRadius(d.source.value);
        var ux = dx / dist, uy = dy / dist;
        var x1 = sx + ux * sr;
        var y1 = sy + uy * sr;
        var x2 = tx - ux * tr;
        var y2 = ty - uy * tr;

        if (d._reciprocal) {
          // Curve outward so the two opposite directions don't overlap.
          var mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
          // Perpendicular offset; sign chosen by source < target alphabetical
          // so each direction picks a consistent side.
          var perpSign = d.source.id < d.target.id ? 1 : -1;
          var off = Math.min(40, dist * 0.18) * perpSign;
          var cx = mx + (-uy) * off;
          var cy = my + ( ux) * off;
          return 'M' + x1 + ',' + y1 + 'Q' + cx + ',' + cy + ' ' + x2 + ',' + y2;
        }
        return 'M' + x1 + ',' + y1 + 'L' + x2 + ',' + y2;
      });
      nodeG.attr('transform', function (d) {
        // Keep nodes within viewport
        d.x = Math.max(nodeRadius(d.value) + 4, Math.min(width  - nodeRadius(d.value) - 4, d.x));
        d.y = Math.max(nodeRadius(d.value) + 4, Math.min(height - nodeRadius(d.value) - 18, d.y));
        return 'translate(' + d.x + ',' + d.y + ')';
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }

  new MutationObserver(function (mutations) {
    mutations.forEach(function (m) {
      if (m.attributeName === 'data-theme') initAll();
    });
  }).observe(document.documentElement, { attributes: true });
})();
