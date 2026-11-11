function deviceLabel() {
  const ua = navigator.userAgent;
  const browser = /Firefox\//.test(ua) ? 'Firefox' : /Edg\//.test(ua) ? 'Edge' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'browser';
  const os = /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS' : /Mac/.test(ua) ? 'macOS' : /Linux/.test(ua) ? 'Linux' : /Windows/.test(ua) ? 'Windows' : '';
  return browser + (os ? ' on ' + os : '');
}
