(() => {
  const origin = 'https://synapse.razer.com';
  const port = chrome.runtime.connect({name: 'huntsman-page'});
  port.onMessage.addListener(message => {
    if (message?.kind !== 'huntsman-game-mode' || typeof message.enabled !== 'boolean') return;
    window.postMessage({channel: 'huntsman-game-mode', enabled: message.enabled, seq: message.seq}, origin);
  });
  window.addEventListener('message', event => {
    if (event.source !== window || event.origin !== origin || event.data?.channel !== 'huntsman-request') return;
    const {id, op, reportId, data, filters, text} = event.data;
    if (typeof id !== 'string' || id.length > 100) return;
    chrome.runtime.sendMessage({kind: 'huntsman-native', op, reportId, data, filters, text}, response => {
      const error = chrome.runtime.lastError;
      // The native host uses numeric IDs; the page uses nonce-prefixed IDs.
      // Do not let the native response overwrite the page's correlation ID.
      window.postMessage({channel: 'huntsman-response', id,
        ok: !error && response?.ok === true,
        result: response?.result,
        error: error?.message || response?.error || (response ? undefined : 'No reply')
      }, origin);
    });
  });
})();
