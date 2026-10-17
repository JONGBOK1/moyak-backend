// Keep the same request ID when a message is retried after a lost response.
(() => {
  const original = window.fetch.bind(window);
  const pending = new Map();
  window.fetch = async (input, init = {}) => {
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    if (url.origin !== location.origin || !/^\/consultations\/[^/]+\/messages$/.test(url.pathname)
        || (init.method || 'GET').toUpperCase() !== 'POST' || typeof init.body !== 'string') {
      return original(input, init);
    }
    const body = JSON.parse(init.body);
    const key = JSON.stringify([url.pathname, body.sender_id, body.sender_role, body.content]);
    if (!body.client_id) {
      if (!pending.has(key)) pending.set(key, crypto.randomUUID());
      body.client_id = pending.get(key);
    }
    const result = await original(input, {...init, body: JSON.stringify(body)});
    if (result.ok) pending.delete(key);
    return result;
  };
})();
