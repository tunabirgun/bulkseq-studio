(() => {
  const root = document.documentElement;
  const device = matchMedia('(prefers-color-scheme: dark)');
  const key = 'bulkseq-appearance';
  let choice;
  try { choice = localStorage.getItem(key); } catch {}
  if (!['light', 'dark'].includes(choice)) choice = null;
  function apply() {
    root.dataset.theme = choice || (device.matches ? 'dark' : 'light');
    document.querySelectorAll('[data-appearance]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.appearance === root.dataset.theme));
    });
  }
  apply();
  device.addEventListener('change', () => { if (!choice) apply(); });
  addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    choice = ['light', 'dark'].includes(event.newValue) ? event.newValue : null;
    apply();
  });
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    document.querySelectorAll('[data-appearance]').forEach(button => {
      button.addEventListener('click', () => {
        choice = button.dataset.appearance;
        try { localStorage.setItem(key, choice); } catch {}
        apply();
      });
    });
  });
})();
