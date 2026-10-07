let csrfToken;
let bootstrapPromise;

async function bootstrap() {
  if (!bootstrapPromise) {
    bootstrapPromise = fetch('/api/bootstrap', { credentials: 'same-origin' }).then(async (response) => {
      if (!response.ok) throw new Error('Could not initialize a secure local connection. Reload the page and try again.');
      const value = await response.json();
      csrfToken = value.csrf_token;
      if (!csrfToken) throw new Error('The server did not return a security token.');
      return csrfToken;
    }).catch((error) => { bootstrapPromise = null; throw error; });
  }
  return bootstrapPromise;
}

export async function api(path, options = {}) {
  const method = options.method || 'GET';
  const headers = { Accept: 'application/json', ...options.headers };
  if (method !== 'GET') {
    await bootstrap();
    headers['Content-Type'] = 'application/json';
    headers['X-VIPMBB-CSRF'] = csrfToken;
  }
  let response;
  try {
    response = await fetch(`/api${path}`, { ...options, method, headers, credentials: 'same-origin', body: options.body === undefined ? undefined : JSON.stringify(options.body) });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('Cannot reach the local server. Check that the application is running, then try again.');
  }
  let value;
  try { value = await response.json(); } catch { throw new Error('The local server returned an unreadable response. Check System status and try again.'); }
  if (!response.ok) {
    if (response.status === 403) { csrfToken = null; bootstrapPromise = null; }
    const detail = typeof value.detail === 'string' ? value.detail : value.detail ? JSON.stringify(value.detail) : value.message;
    throw new Error(detail || `The request could not be completed (${response.status}).`);
  }
  return value;
}

export const post = (path, body = {}) => api(path, { method: 'POST', body });
