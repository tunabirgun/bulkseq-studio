(() => {
  'use strict';

  const canvas = document.querySelector('.parameter-canvas');
  if (!canvas) return;
  const PLOT = Object.fromEntries(['left', 'right', 'top', 'bottom'].map(key => [key, Number(canvas.getAttribute(`data-plot-${key}`))]));
  const DEFAULTS = { alpha: .05, cutoff: 1, comparison: 'b-a' };
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');

  function formatAlpha(value) {
    return Number(value).toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
  }

  function foldChangeMeaning(cutoff) {
    if (cutoff === 0) return 'No fold-change screen';
    return `At least a ${Math.pow(2, cutoff).toFixed(2).replace(/\.00$/, '')}-fold difference`;
  }

  function xAt(effect, domain) {
    return PLOT.left + ((effect + domain) / (domain * 2)) * (PLOT.right - PLOT.left);
  }

  function yAt(padj, domain) {
    return PLOT.bottom + Math.log10(padj) / domain * (PLOT.bottom - PLOT.top);
  }

  function summariseResults(rows, alpha, cutoff, comparison) {
    const sign = comparison === 'a-b' ? -1 : 1;
    return rows.map(row => {
      const effect = row.log2fc * sign;
      const belowP = row.padj < alpha;
      const selected = belowP && Math.abs(effect) >= cutoff;
      const higher = selected && effect > 0;
      const lower = selected && effect < 0;
      return { ...row, effect, belowP, higher, lower, selected };
    });
  }

  window.parameterLabSummarise = summariseResults;

  function initialise(root) {
    const svg = root.querySelector('.parameter-canvas');
    const alphaInput = root.querySelector('#p-cutoff');
    const cutoffInput = root.querySelector('#fc-cutoff');
    const comparisonInput = root.querySelector('#comparison-view');
    const reset = root.querySelector('#parameter-reset');
    const pointElements = [...root.querySelectorAll('[data-parameter-point]')];
    if (!svg || !alphaInput || !cutoffInput || !comparisonInput || !pointElements.length) return;

    const domainX = Number(svg.dataset.domainX);
    const domainY = Number(svg.dataset.domainY);
    const rows = pointElements.map((element, index) => ({
      element,
      index,
      id: element.dataset.id,
      log2fc: Number(element.dataset.log2fc),
      padj: Number(element.dataset.padj),
    }));
    const get = selector => root.querySelector(selector);
    const alphaOutput = get('[data-alpha-output]');
    const cutoffOutput = get('[data-fc-output]');
    const belowCount = get('[data-below-count]');
    const selectedCount = get('[data-selected-count]');
    const higherCount = get('[data-higher-count]');
    const lowerCount = get('[data-lower-count]');
    const belowRule = get('[data-below-rule]');
    const foldRule = get('[data-fc-rule]');
    const status = get('[data-parameter-status]');
    const xAxisLabel = get('[data-x-axis-label]');
    const tooltip = get('[data-parameter-tooltip]');
    const tooltipBox = tooltip?.querySelector('rect');
    const tooltipId = get('[data-tooltip-id]');
    const tooltipEffect = get('[data-tooltip-effect]');
    const tooltipPadj = get('[data-tooltip-padj]');
    const tooltipState = get('[data-tooltip-state]');
    const pLine = get('[data-p-line]');
    const leftLine = get('[data-left-cut]');
    const rightLine = get('[data-right-cut]');
    const leftArea = get('[data-selection-left]');
    const rightArea = get('[data-selection-right]');
    const phone = matchMedia('(max-width:540px)');
    const sizeToggle = get('[data-volcano-size]');
    const scroller = get('.parameter-scroll');
    function sizeFigure() {
      const enlarged = sizeToggle.getAttribute('aria-pressed') === 'true';
      const compact = phone.matches && !enlarged;
      scroller.classList.toggle('is-enlarged', enlarged);
      scroller.classList.toggle('is-fit', !enlarged);
      scroller.classList.toggle('is-compact', compact);
      sizeToggle.textContent = enlarged ? 'Fit figure' : 'Enlarge figure';
      get('[data-volcano-size-note]').textContent = enlarged ? 'Swipe horizontally to explore. Tap a point to inspect values.' : 'Full figure shown. Tap a point to inspect values.';
      xAxisLabel.textContent = `${compact?'log2FC':'LOG2 FOLD CHANGE'} (${state.comparison === 'b-a' ? 'B vs A' : 'A vs B'})`;
      xAxisLabel.setAttribute('y',compact?'490':'450');
      get('[data-y-axis-label]').style.display=compact?'none':'';
      svg.setAttribute('viewBox',`0 0 720 ${compact?520:480}`);
      svg.querySelectorAll('.parameter-grid text[text-anchor="middle"]').forEach(label=>label.setAttribute('y',String(PLOT.bottom+(compact?42:22))));
      if(!enlarged)scroller.scrollLeft=0;
    }
    let state = { ...DEFAULTS };
    let summary = [];
    let activeIndex = -1;
    let mirrorFrame = 0;
    let guideFrame = 0;
    let currentXs = rows.map(row => xAt(row.log2fc, domainX));
    let currentGuides = null;

    function announceState() {
      const moving = Boolean(mirrorFrame || guideFrame);
      svg.setAttribute('aria-busy', String(moving));
      status.textContent = moving
        ? 'Transitioning to the selected setting. Counts and controls show the destination; point inspection resumes when movement ends.'
        : `${summary.filter(row => row.belowP).length} examples are below the adjusted p-value cutoff; ${summary.filter(row => row.selected).length} pass both screens, with ${summary.filter(row => row.higher).length} higher and ${summary.filter(row => row.lower).length} lower in ${state.comparison === 'b-a' ? 'B versus A' : 'A versus B'}. Tap a point or focus the figure and use arrow keys to inspect values.`;
      if(moving)hideTooltip();
    }

    function cancelMirror() {
      if (mirrorFrame) cancelAnimationFrame(mirrorFrame);
      mirrorFrame = 0;
    }

    function cancelGuides() {
      if (guideFrame) cancelAnimationFrame(guideFrame);
      guideFrame = 0;
    }

    function stateText(row) {
      if (row.higher) return 'Passes both · higher';
      if (row.lower) return 'Passes both · lower';
      if (row.selected) return 'Passes both · no direction';
      if (row.belowP) return 'Only p cutoff passes';
      return 'Does not pass p cutoff';
    }

    function hideTooltip() {
      if (!tooltip) return;
      tooltip.style.display = 'none';
      tooltip.setAttribute('aria-hidden', 'true');
    }

    function inspect(index) {
      if (svg.getAttribute('aria-busy') === 'true') return;
      activeIndex = Math.max(0, Math.min(rows.length - 1, index));
      pointElements.forEach((element, itemIndex) => element.classList.toggle('is-inspected', itemIndex === activeIndex));
      const row = summary[activeIndex];
      if (!row || !tooltip || !tooltipBox) return;
      const width = Number(tooltipBox.getAttribute('width'));
      const height = Number(tooltipBox.getAttribute('height'));
      const x = Math.max(PLOT.left, Math.min(PLOT.right - width, currentXs[activeIndex] + 10));
      const y = Math.max(PLOT.top, Math.min(PLOT.bottom - height, yAt(row.padj, domainY) - height - 8));
      tooltip.setAttribute('transform', `translate(${x} ${y})`);
      tooltip.style.display = '';
      tooltip.setAttribute('aria-hidden', 'false');
      tooltipId.textContent = `${row.id} · ${state.comparison === 'b-a' ? 'B vs A' : 'A vs B'}`;
      tooltipEffect.textContent = `displayed log2FC ${row.effect.toFixed(2)}`;
      const adjustedP = Number(row.padj.toPrecision(3)).toString();
      tooltipPadj.textContent = `adjusted p-value ${adjustedP}`;
      tooltipState.textContent = stateText(row);
      status.textContent = `${row.id}: displayed log2 fold change ${row.effect.toFixed(2)}, adjusted p-value ${adjustedP}; ${stateText(row)}.`;
      const scroller = svg.closest('.parameter-scroll');
      const box = row.element.getBoundingClientRect(), view = scroller.getBoundingClientRect();
      if (box.left < view.left) scroller.scrollLeft += box.left - view.left - 16;
      else if (box.right > view.right) scroller.scrollLeft += box.right - view.right + 16;
    }

    function paintPoints(xs) {
      rows.forEach((row, index) => {
        const result = summary[index];
        const element = row.element;
        element.setAttribute('cx', xs[index].toFixed(2));
        element.setAttribute('cy', yAt(row.padj, domainY).toFixed(2));
        element.setAttribute('r', result.selected ? '4' : result.belowP ? '3.1' : '2.35');
        element.setAttribute('fill', result.higher ? '#1c4ed8' : result.lower ? '#d45a3e' : '#101010');
        element.classList.toggle('is-higher', result.higher);
        element.classList.toggle('is-lower', result.lower);
        element.classList.toggle('is-below', result.belowP && !result.selected);
        element.classList.toggle('is-selected', result.selected);
      });
      if (activeIndex >= 0) inspect(activeIndex);
    }

    function movePoints(target, animate) {
      cancelMirror();
      if (!animate || reduceMotion.matches) {
        currentXs = target.slice();
        paintPoints(currentXs);
        return;
      }
      const start = currentXs.slice();
      const started = performance.now();
      const step = now => {
        const progress = Math.min(1, (now - started) / 620);
        const eased = 1 - Math.pow(1 - progress, 3);
        currentXs = start.map((value, index) => value + (target[index] - value) * eased);
        paintPoints(currentXs);
        if (progress < 1 && !reduceMotion.matches) mirrorFrame = requestAnimationFrame(step);
        else {
          currentXs = target.slice();
          paintPoints(currentXs);
          mirrorFrame = 0;
          announceState();
          if(activeIndex >= 0)inspect(activeIndex);
        }
      };
      mirrorFrame = requestAnimationFrame(step);
    }

    function guideValues() {
      const pY = yAt(state.alpha, domainY);
      const leftX = xAt(-state.cutoff, domainX);
      const rightX = xAt(state.cutoff, domainX);
      return { pY, leftX, rightX, leftWidth: leftX - PLOT.left, rightWidth: PLOT.right - rightX, height: pY - PLOT.top };
    }

    function drawGuides(values) {
      pLine.setAttribute('y1', values.pY.toFixed(2));
      pLine.setAttribute('y2', values.pY.toFixed(2));
      leftLine.setAttribute('x1', values.leftX.toFixed(2));
      leftLine.setAttribute('x2', values.leftX.toFixed(2));
      rightLine.setAttribute('x1', values.rightX.toFixed(2));
      rightLine.setAttribute('x2', values.rightX.toFixed(2));
      leftArea.setAttribute('width', Math.max(0, values.leftWidth).toFixed(2));
      leftArea.setAttribute('height', Math.max(0, values.height).toFixed(2));
      rightArea.setAttribute('x', values.rightX.toFixed(2));
      rightArea.setAttribute('width', Math.max(0, values.rightWidth).toFixed(2));
      rightArea.setAttribute('height', Math.max(0, values.height).toFixed(2));
    }

    function moveGuides(target, animate) {
      cancelGuides();
      if (!animate || reduceMotion.matches || !currentGuides) {
        currentGuides = target;
        drawGuides(currentGuides);
        return;
      }
      const start = currentGuides;
      const started = performance.now();
      const step = now => {
        const progress = Math.min(1, (now - started) / 200);
        const eased = 1 - Math.pow(1 - progress, 2);
        currentGuides = Object.fromEntries(Object.keys(target).map(key => [key, start[key] + (target[key] - start[key]) * eased]));
        drawGuides(currentGuides);
        if (progress < 1 && !reduceMotion.matches) guideFrame = requestAnimationFrame(step);
        else {
          currentGuides = target;
          drawGuides(currentGuides);
          guideFrame = 0;
          announceState();
          if(activeIndex >= 0)inspect(activeIndex);
        }
      };
      guideFrame = requestAnimationFrame(step);
    }

    function update({ mirror = false, animateGuides = false } = {}) {
      root.dataset.comparison = state.comparison;
      alphaOutput.textContent = formatAlpha(state.alpha);
      cutoffOutput.textContent = state.cutoff.toFixed(2);
      belowRule.textContent = `padj < ${formatAlpha(state.alpha)}`;
      foldRule.textContent = `|log2FC| ≥ ${state.cutoff.toFixed(2)} · ${foldChangeMeaning(state.cutoff)}`;
      sizeFigure();
      summary = summariseResults(rows, state.alpha, state.cutoff, state.comparison);
      const below = summary.filter(row => row.belowP).length;
      const higher = summary.filter(row => row.higher).length;
      const lower = summary.filter(row => row.lower).length;
      const selected = summary.filter(row => row.selected).length;
      belowCount.textContent = String(below);
      selectedCount.textContent = String(selected);
      higherCount.textContent = String(higher);
      lowerCount.textContent = String(lower);
      moveGuides(guideValues(), animateGuides);
      const sign = state.comparison === 'a-b' ? -1 : 1;
      movePoints(rows.map(row => xAt(row.log2fc * sign, domainX)), mirror);
      announceState();
    }

    [alphaInput, cutoffInput, comparisonInput, reset].forEach(control => { control.disabled = false; });
    sizeToggle.disabled=false;
    sizeToggle.addEventListener('click', () => {sizeToggle.setAttribute('aria-pressed',String(sizeToggle.getAttribute('aria-pressed')!=='true'));sizeFigure();});
    phone.addEventListener('change',sizeFigure);
    alphaInput.addEventListener('input', () => {
      state.alpha = Number(alphaInput.value);
      update({ animateGuides: true });
    });
    cutoffInput.addEventListener('input', () => {
      state.cutoff = Number(cutoffInput.value);
      update({ animateGuides: true });
    });
    comparisonInput.addEventListener('change', () => {
      state.comparison = comparisonInput.value;
      update({ mirror: true });
    });
    reset.addEventListener('click', () => {
      const mirror = state.comparison !== DEFAULTS.comparison;
      state = { ...DEFAULTS };
      alphaInput.value = String(DEFAULTS.alpha);
      cutoffInput.value = String(DEFAULTS.cutoff);
      comparisonInput.value = DEFAULTS.comparison;
      update({ mirror });
    });
    svg.addEventListener('pointermove', event => {
      const point = event.target.closest('[data-parameter-point]');
      const index = pointElements.indexOf(point);
      if (index >= 0) inspect(index);
    });
    svg.addEventListener('click', event => {
      const point = event.target.closest('[data-parameter-point]');
      const index = pointElements.indexOf(point);
      if (index >= 0) { svg.focus({ preventScroll: true }); inspect(index); }
    });
    svg.addEventListener('pointerleave', () => {
      if (!svg.matches(':focus')) {
        activeIndex = -1;
        pointElements.forEach(element => element.classList.remove('is-inspected'));
        hideTooltip();
      }
    });
    svg.addEventListener('blur', () => {
      activeIndex = -1;
      pointElements.forEach(element => element.classList.remove('is-inspected'));
      hideTooltip();
    });
    svg.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      if (activeIndex < 0 || event.key === 'Home') inspect(0);
      else if (event.key === 'End') inspect(rows.length - 1);
      else inspect(activeIndex + (event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 1));
    });
    reduceMotion.addEventListener?.('change', event => {
      if (!event.matches) return;
      cancelMirror();
      cancelGuides();
      update();
    });
    update();
  }

  const boot = () => document.querySelectorAll('[data-parameter-lab]').forEach(initialise);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();
