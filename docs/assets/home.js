(() => {
  'use strict';

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const animations = new Set();

  function play(element, keyframes, options) {
    if (reduceMotion.matches || !element?.animate) return;
    const animation = element.animate(keyframes, options);
    animations.add(animation);
    animation.addEventListener('finish', () => animations.delete(animation), { once: true });
    animation.addEventListener('cancel', () => animations.delete(animation), { once: true });
  }

  function stopAnimations() {
    animations.forEach(animation => animation.cancel());
    animations.clear();
  }

  function titlesFor(route, matcher, fallback) {
    const titles = (route.steps || [])
      .filter(step => matcher.test(step.title || ''))
      .map(step => step.title)
      .slice(0, 2);
    return titles.length ? titles.join(' · ') : fallback;
  }

  function renderRouteStages(route, track) {
    if (!track) return;
    const readsSkipped = !(route.steps || []).some(step => /choose read processing/i.test(step.title || ''));
    const modelSkipped = !(route.steps || []).some(step => /define the comparison/i.test(step.title || ''));
    const stages = [
      {
        name: 'Prepare input',
        detail: route.steps[1]?.title || 'Prepare the input and project.'
      },
      {
        name: 'Process reads',
        detail: titlesFor(route, /^Choose read processing$/i, 'Process route-specific reads.'),
        skipped: readsSkipped
      },
      {
        name: 'Fit model',
        detail: titlesFor(route, /^Define the comparison$/i, 'Define, validate and run the model.'),
        skipped: modelSkipped
      },
      {
        name: 'Explore results',
        detail: titlesFor(route, /^Read and export results$/i, 'Review route-appropriate outputs.')
      }
    ];
    track.replaceChildren();
    stages.forEach((stage, index) => {
      const item = document.createElement('li');
      item.className = 'route-track-stage';
      if (stage.skipped) item.classList.add('is-skipped');
      const number = document.createElement('span');
      number.className = 'route-node';
      number.textContent = String(index + 1);
      const title = document.createElement('strong');
      const detail = document.createElement('small');
      title.textContent = stage.name;
      detail.textContent = stage.skipped ? (stage.name === 'Process reads' ? 'Skipped: No read processing' : 'Skipped: Uses supplied statistics') : stage.detail;
      item.append(number, title, detail);
      track.append(item);
    });
  }

  function setupRoutes() {
    const routes = window.walkthroughRoutes || [];
    const tabs = [...document.querySelectorAll('.route-tab[data-route]')];
    const panel = document.querySelector('#route-panel');
    if (!routes.length || !tabs.length || !panel) return;

    const title = panel.querySelector('#route-title');
    const description = panel.querySelector('#route-description');
    const track = panel.querySelector('.route-track');
    const boundary = panel.querySelector('.route-boundary');
    const guide = panel.querySelector('.route-guide');
    let activeRoute = routes.find(route => route.id === 'public') || routes[0];

    function selectRoute(id, shouldAnimate = false) {
      const route = routes.find(item => item.id === id);
      if (!route) return;
      activeRoute = route;
      panel.setAttribute('aria-labelledby', `tab-${route.id}`);
      tabs.forEach(tab => {
        const selected = tab.dataset.route === route.id;
        tab.setAttribute('aria-selected', String(selected));
        tab.tabIndex = selected ? 0 : -1;
      });
      if (title) title.textContent = route.label;
      if (description) description.textContent = route.summary;
      if (boundary) boundary.textContent = route.skipped || '';
      if (guide) guide.href = `walkthrough.html?route=${encodeURIComponent(route.id)}`;
      renderRouteStages(route, track);
      if (boundary) boundary.hidden = !track.querySelector('.is-skipped');
      if (shouldAnimate) {
        const content = panel.querySelector('.route-panel-content') || panel;
        play(content, [
          { opacity: 0.45, transform: 'translateY(6px)' },
          { opacity: 1, transform: 'translateY(0)' }
        ], { duration: 200, easing: 'ease-out' });
      }
    }

    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => selectRoute(tab.dataset.route, true));
      tab.addEventListener('keydown', event => {
        let nextIndex = index;
        if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
        else if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
        else if (event.key === 'Home') nextIndex = 0;
        else if (event.key === 'End') nextIndex = tabs.length - 1;
        else return;
        event.preventDefault();
        tabs[nextIndex].focus();
        selectRoute(tabs[nextIndex].dataset.route, true);
      });
    });
    selectRoute(activeRoute.id);
  }

  function setupReadingProgress() {
    const progress = document.querySelector('.reading-progress');
    if (!progress) return;
    let scheduled = false;
    const render = () => {
      scheduled = false;
      const maximum = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
      const value = maximum ? Math.min(1, Math.max(0, window.scrollY / maximum)) : 0;
      progress.style.setProperty('--reading-progress', String(value));
    };
    const requestRender = () => {
      if (!scheduled) {
        scheduled = true;
        requestAnimationFrame(render);
      }
    };
    window.addEventListener('scroll', requestRender, { passive: true });
    window.addEventListener('resize', requestRender, { passive: true });
    render();
  }

  function setupEntranceReveals() {
    if (reduceMotion.matches || !('IntersectionObserver' in window)) return;
    const targets = document.querySelectorAll('.hero-intro, .home-section .section-heading, .output-card, .learning-link, .download-card');
    const observed = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        observed.unobserve(entry.target);
        play(entry.target, [
          { opacity: 0.15, transform: 'translateY(10px)' },
          { opacity: 1, transform: 'translateY(0)' }
        ], { duration: 220, easing: 'ease-out', fill: 'none' });
      });
    }, { threshold: 0.12 });
    targets.forEach(target => observed.observe(target));
  }

  function setupSectionNavigation() {
    const links = [...document.querySelectorAll('.side-nav a')];
    if (!links.length) return;
    const targets = links.map(link => document.querySelector(link.getAttribute('href')));
    let scheduled = false;
    const update = () => {
      scheduled = false;
      let active = 0;
      targets.forEach((target, index) => { if (target?.getBoundingClientRect().top <= innerHeight * .4) active = index; });
      links.forEach((link, index) => {
        if (index === active) link.setAttribute('aria-current', 'location');
        else link.removeAttribute('aria-current');
      });
    };
    window.addEventListener('scroll', () => {
      if (!scheduled) { scheduled = true; requestAnimationFrame(update); }
    }, { passive: true });
    update();
  }

  function initialise() {
    setupSectionNavigation();
    setupRoutes();
    setupReadingProgress();
    setupEntranceReveals();
  }

  reduceMotion.addEventListener?.('change', event => {
    if (event.matches) stopAnimations();
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialise, { once: true });
  else initialise();
})();
