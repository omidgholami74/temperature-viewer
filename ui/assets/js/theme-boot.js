/**
 * Runs synchronously in <head>, before the stylesheets paint.
 *
 * The stored value is the operator's choice ("dark", "light" or "system"); the
 * attribute is always the resolved theme so CSS only needs two palettes.
 */
(function () {
  'use strict';

  var preference = 'system';

  try {
    var saved = localStorage.getItem('furnace.theme');
    if (saved === 'dark' || saved === 'light' || saved === 'system') {
      preference = saved;
    }
  } catch (error) {
    // Private mode: fall back to the system setting.
  }

  var dark = preference === 'dark'
    || (preference === 'system'
      && !(window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches));

  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  document.documentElement.dataset.themePreference = preference;
})();
