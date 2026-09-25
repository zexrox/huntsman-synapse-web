let port;
let sequence = 0;
const pending = new Map();
function connect() {
  if (port) return port;
  const current = chrome.runtime.connectNative('local.huntsman.readonly');
  port = current;
  current.onMessage.addListener(message => {
    if (message && message.op === 'game-mode' && typeof message.enabled === 'boolean') {
      const notice = {kind: 'huntsman-game-mode', enabled: message.enabled, seq: message.seq};
      for (const page of pages) {
        try {
          page.postMessage(notice);
        } catch (error) {
          pages.delete(page);
        }
      }
      return;
    }
    const entry = pending.get(message.id);
    if (!entry) return;
    clearTimeout(entry.timer);
    pending.delete(message.id);
    entry.reply(message);
  });
  current.onDisconnect.addListener(() => {
    const error = chrome.runtime.lastError?.message || 'Local helper stopped.';
    if (port === current) port = undefined;
    for (const entry of pending.values()) {
      clearTimeout(entry.timer);
      entry.reply({ok: false, error});
    }
    pending.clear();
  });
  return current;
}
const pages = new Set();
chrome.runtime.onConnect.addListener(port => {
  if (port.name !== 'huntsman-page') return;
  let origin = '';
  try {
    origin = port.sender?.url ? new URL(port.sender.url).origin : '';
  } catch (error) {
    origin = '';
  }
  if (port.sender?.id !== chrome.runtime.id || origin !== 'https://synapse.razer.com') {
    port.disconnect();
    return;
  }
  pages.add(port);
  port.onDisconnect.addListener(() => pages.delete(port));
});
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id || !sender.url ||
      new URL(sender.url).origin !== 'https://synapse.razer.com') return false;
  if (message?.kind !== 'huntsman-native' ||
      !['status', 'open', 'close', 'send', 'receive', 'filters', 'note'].includes(message.op)) return false;
  if (message.op === 'send' && (!Array.isArray(message.data) || message.data.length !== 90)) {
    reply({ok: false, error: 'Expected 90 bytes'});
    return false;
  }
  if (message.op === 'filters' && (!Array.isArray(message.filters) || message.filters.length > 32)) {
    reply({ok: false, error: 'Invalid filters'});
    return false;
  }
  if (message.op === 'note' && (typeof message.text !== 'string' || message.text.length === 0 || message.text.length > 180 || /[\r\n]/.test(message.text))) {
    reply({ok: false, error: 'Invalid note'});
    return false;
  }
  const id = ++sequence;
  const client = `${sender.tab?.id}:${sender.frameId}:${sender.documentId || ''}`;
  const timer = setTimeout(() => {
    pending.delete(id);
    reply({ok: false, error: 'HID timed out. Close Synapse and the helper.'});
  }, 10000);
  pending.set(id, {reply, timer});
  try {
    const payload = {id, client, op: message.op, reportId: message.reportId, data: message.data};
    if (message.op === 'filters') payload.filters = message.filters;
    if (message.op === 'note') payload.text = message.text;
    connect().postMessage(payload);
  } catch (error) {
    clearTimeout(timer);
    pending.delete(id);
    reply({ok: false, error: error.message});
  }
  return true;
});
