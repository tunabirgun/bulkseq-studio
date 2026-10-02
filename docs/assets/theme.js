(() => {
  const root = document.documentElement;
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  // No stored choice means follow the operating system; the toggle then stores an explicit one.
  let preference = 'system';
  const stored = value => value === 'light' || value === 'dark';
  try { const saved = localStorage.getItem('bulkseq-theme'); if (stored(saved)) preference = saved; } catch {}
  const resolved = () => (preference === 'system' ? (media.matches ? 'dark' : 'light') : preference);
  const controls = () => {
    const buttons = [...document.querySelectorAll?.('#theme-trigger, #theme-trigger-sidebar') || []];
    return buttons.length ? buttons : [document.getElementById('theme-trigger')].filter(Boolean);
  };
  // The label names the theme now showing; the hidden half says what a click does.
  const label = () => {
    const dark = resolved() === 'dark';
    controls().forEach(button => {
      button.querySelector('.theme-name').textContent = dark ? 'Dark theme' : 'Light theme';
      button.querySelector('.visually-hidden').textContent = dark ? ' — switch to light' : ' — switch to dark';
      button.title = dark ? 'Switch to the light theme' : 'Switch to the dark theme';
    });
  };
  // Every path that changes the theme relabels the button, an operating-system switch included.
  const apply = () => { root.dataset.theme = resolved(); label(); };
  apply();
  media.addEventListener('change', () => { if (preference === 'system') apply(); });
  window.addEventListener('storage', event => {
    if (event.key !== 'bulkseq-theme' && event.key !== null) return;
    preference = stored(event.newValue) ? event.newValue : 'system';
    apply();
  });
  const toggle = () => {
    preference = resolved() === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem('bulkseq-theme', preference); } catch {}
    apply();
  };
  window.addEventListener('DOMContentLoaded', () => {
    label();
    controls().forEach(button => button.addEventListener('click', toggle));
  });
})();
