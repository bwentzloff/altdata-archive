/**
 * charts.js — Chart.js wrapper for AltSports Archive
 * Reads CSS custom properties for theming; redraws on data-theme changes.
 *
 * Pages embed data before this script runs:
 *   window._careerChartData  = { labels, values, stat }      (player pages)
 *   window._leagueChartData  = { labels, values, stat }      (league pages)
 */
(function () {
  'use strict';

  var _charts = [];

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  var STAT_LABELS = {
    passing_yards:  'Pass Yds',  rushing_yards:    'Rush Yds',
    receiving_yards:'Rec Yds',   def_tackles:      'Tackles',
    def_sacks:      'Sacks',     def_int:          'INTs',
    extra_points:   'XP Made',   passing_tds:      'Pass TD',
    rushing_tds:    'Rush TD',   receiving_tds:    'Rec TD',
    yardsThrown:    'Yds Thrown',yardsReceived:    'Yds Received',
    assists:        'Assists',   goals:            'Goals',
  };

  function label(key) {
    return STAT_LABELS[key] || key.replace(/_/g, ' ');
  }

  function baseOpts(accent, textDim, borderColor) {
    var font = { family: 'Inter, Helvetica Neue, Arial, sans-serif', size: 11 };
    return {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: css('--bg2'),
          borderColor: borderColor,
          borderWidth: 1,
          titleColor: css('--text'),
          bodyColor: textDim,
        },
      },
      scales: {
        x: {
          ticks: { color: textDim, font: font },
          grid:  { color: borderColor },
          border:{ color: borderColor },
        },
        y: {
          ticks: { color: textDim, font: font },
          grid:  { color: borderColor },
          border:{ color: borderColor },
          beginAtZero: true,
        },
      },
      animation: { duration: 250 },
    };
  }

  function destroyAll() {
    _charts.forEach(function (c) { c.destroy(); });
    _charts = [];
  }

  function initAll() {
    if (typeof Chart === 'undefined') return;
    destroyAll();

    var accent      = css('--accent');
    var textDim     = css('--text-dim');
    var borderColor = css('--border');
    var fillColor   = accent + '44';   // 27% opacity hex

    // ── Player career chart ─────────────────────────────────────────────
    var cd = window._careerChartData;
    if (cd) {
      var el = document.getElementById('career-chart');
      if (el) {
        var opts = baseOpts(accent, textDim, borderColor);
        opts.scales.x.ticks.maxRotation = 45;
        var chart = new Chart(el, {
          type: 'bar',
          data: {
            labels: cd.labels,
            datasets: [{
              label: label(cd.stat),
              data:  cd.values,
              backgroundColor: fillColor,
              borderColor:     accent,
              borderWidth:     1,
            }],
          },
          options: opts,
        });
        _charts.push(chart);
      }
    }

    // ── League top-10 chart ─────────────────────────────────────────────
    var ld = window._leagueChartData;
    if (ld) {
      var el2 = document.getElementById('league-chart');
      if (el2) {
        var opts2 = baseOpts(accent, textDim, borderColor);
        opts2.indexAxis = 'y';
        opts2.scales.x.grid.display = true;
        opts2.scales.y.grid.display = false;
        opts2.scales.x.ticks.maxTicksLimit = 6;
        var chart2 = new Chart(el2, {
          type: 'bar',
          data: {
            labels: ld.labels,
            datasets: [{
              label: label(ld.stat),
              data:  ld.values,
              backgroundColor: fillColor,
              borderColor:     accent,
              borderWidth:     1,
            }],
          },
          options: opts2,
        });
        _charts.push(chart2);
      }
    }
  }

  // Run after DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }

  // Redraw when theme switches
  new MutationObserver(function (mutations) {
    mutations.forEach(function (m) {
      if (m.attributeName === 'data-theme') initAll();
    });
  }).observe(document.documentElement, { attributes: true });

})();
