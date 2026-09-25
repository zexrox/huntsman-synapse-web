(() => {
  if (window.__huntsmanReadOnly) return;
  window.__huntsmanReadOnly = true;
  const origin = 'https://synapse.razer.com';
  let sequence = 0;
  const nonce = crypto.randomUUID();
  const pending = new Map();
  const history = [];
  window.huntsmanDiagnose = () => JSON.stringify(history, null, 2);
  function record(message) {
    history.push({time: new Date().toISOString(), message});
    if (history.length > 150) history.shift();
    const target = window.top && window.top !== window ? window.top : window;
    try {
      target.dispatchEvent(new target.CustomEvent('huntsman-status', {detail: message}));
    } catch (error) {
      window.dispatchEvent(new CustomEvent('huntsman-status', {detail: message}));
    }
  }
  let gameModePush = 0;
  let gameModeSeq = -1;
  function applyGameMode(enabled) {
    if (!location.pathname.includes('/products/')) return true;
    const store = window.reduxStore;
    if (!store || typeof store.dispatch !== 'function' || typeof store.getState !== 'function') return false;
    let state;
    try {
      state = store.getState();
    } catch (error) {
      return false;
    }
    const current = state && state.gameMode;
    if (!current || typeof current !== 'object') return false;
    const on = enabled === true;
    // from:device is the hardware-notification path. It updates this switch
    // and does not send another HID set.
    store.dispatch({
      type: 'GAME_MODE',
      from: 'device',
      payload: {
        gameMode: on ? 1 : 0,
        gamingMode: {
          ...current,
          state: on ? 1 : 0,
          isWindowsKeyDisabled: on,
          isAltTabDisabled: on,
          isAltF4Disabled: on
        }
      }
    });
    return true;
  }
  window.addEventListener('message', event => {
    if (event.source !== window || event.origin !== origin || event.data?.channel !== 'huntsman-game-mode') return;
    if (!location.pathname.includes('/products/')) return;
    const seq = Number.isInteger(event.data.seq) ? event.data.seq : 0;
    if (seq < gameModeSeq) return;
    gameModeSeq = seq;
    const enabled = event.data.enabled === true;
    const token = ++gameModePush;
    const delays = [400, 1200, 2500];
    let tries = 0;
    const tick = (delayIndex) => {
      if (token !== gameModePush) return;
      if (!applyGameMode(enabled)) {
        if (++tries >= 20) return;
        setTimeout(() => tick(0), 250);
        return;
      }
      if (delayIndex >= delays.length) return;
      setTimeout(() => tick(delayIndex + 1), delays[delayIndex]);
    };
    tick(0);
  });
  window.addEventListener('message', event => {
    if (event.source !== window || event.origin !== origin || event.data?.channel !== 'huntsman-response') return;
    const entry = pending.get(event.data.id);
    if (!entry) return;
    pending.delete(event.data.id);
    clearTimeout(entry.timer);
    if (event.data.ok) entry.resolve(event.data.result);
    else {
      if (!entry.quiet) record(event.data.error);
      entry.reject(new DOMException(event.data.error, 'NotAllowedError'));
    }
  });
  function rpc(op, extra = {}, quiet = false) {
    return new Promise((resolve, reject) => {
      const id = `${nonce}:${++sequence}`;
      const timer = setTimeout(() => {
        pending.delete(id);
        if (!quiet) record('No bridge reply. Check the install and diagnose.log.');
        reject(new Error('Bridge timeout'));
      }, 12000);
      pending.set(id, {resolve, reject, timer, quiet});
      window.postMessage({channel: 'huntsman-request', id, op, ...extra}, origin);
    });
  }
  // Get Analog Report Mode (02:aa) returns status 5. The page does not catch
  // that throw, then posts DEVICE_CONNECT_STATE isConnected false. The bytes
  // stay unchanged; only the uncaught throw is wrapped, and only after the
  // loaded file is searched. Parser-inserted scripts do not use the src setter.
  function patchSynapseBundle(code) {
    const lines = [];
    let next = code;
    const marker = 'load runtime state: failed to get rzDevice';
    const loop = 'for(const n of t)await n(e)';
    if (!code.includes(marker)) lines.push('PATCH runtime marker=missed rewritten=no');
    else {
      const at = next.indexOf(marker);
      const loopAt = next.indexOf(loop, at);
      if (loopAt === -1 || loopAt - at >= 400) lines.push('PATCH runtime marker=found rewritten=no');
      else {
        const guarded = 'for(const n of t)try{await n(e)}catch(huntsmanRuntime){console.error("Synapse Web bridge: command not supported, connection stays",huntsmanRuntime)}';
        next = next.slice(0, loopAt) + guarded + next.slice(loopAt + loop.length);
        lines.push(next.includes('huntsmanRuntime') ? 'PATCH runtime marker=found rewritten=yes' : 'PATCH runtime marker=found rewritten=no');
      }
    }
    const analog = '"Analog V3 ADC Report"';
    if (!code.includes(analog)) lines.push('PATCH analog marker=missed rewritten=no');
    else {
      const analogAt = next.indexOf(analog);
      const start = next.lastIndexOf('async function', analogAt);
      const brace = start === -1 ? -1 : next.indexOf('{', start);
      let end = -1;
      if (start !== -1 && analogAt - start < 250 && brace !== -1) {
        let depth = 0;
        for (let index = brace; index < next.length; index += 1) {
          const char = next[index];
          if (char === '{') depth += 1;
          else if (char === '}') {
            depth -= 1;
            if (depth === 0) {
              end = index;
              break;
            }
          }
        }
      }
      if (end === -1) lines.push('PATCH analog marker=found rewritten=no');
      else {
        const body = next.slice(brace + 1, end);
        next = `${next.slice(0, brace + 1)}try{${body}}catch(huntsmanAnalog){return console.error("Synapse Web bridge: analog report not supported, connection stays",huntsmanAnalog),!1}${next.slice(end)}`;
        lines.push(next.includes('huntsmanAnalog') ? 'PATCH analog marker=found rewritten=yes' : 'PATCH analog marker=found rewritten=no');
      }
    }
    return {code: next, lines};
  }
  function publish(lines) {
    record(lines.join(' | '));
    for (const text of lines) {
      rpc('note', {text}, true).catch(() => record(`PATCH log=failed ${text}`));
    }
  }
  const originalPostMessage = window.postMessage.bind(window);
  window.postMessage = function postMessage(message, targetOrigin, transfer) {
    if (message && message.type === 'DEVICE_CONNECT_STATE' && message.payload && Object.prototype.hasOwnProperty.call(message.payload, 'isConnected')) {
      publish([`CONNECT type=DEVICE_CONNECT_STATE isConnected=${message.payload.isConnected}`]);
    }
    return transfer === undefined ? originalPostMessage(message, targetOrigin) : originalPostMessage(message, targetOrigin, transfer);
  };
  function isProductMain(value) {
    try {
      const url = new URL(value, location.href);
      return url.origin === origin && /\/static\/js\/main\.[^/]+\.js$/.test(url.pathname);
    } catch (error) {
      return false;
    }
  }
  const seenScripts = new WeakSet();
  let sawProductMain = false;
  function considerScript(node) {
    if (!(node instanceof HTMLScriptElement) || seenScripts.has(node) || !node.src || !isProductMain(node.src)) return;
    seenScripts.add(node);
    sawProductMain = true;
    const source = node.src;
    let body = '';
    try {
      const xhr = new XMLHttpRequest();
      xhr.open('GET', source, false);
      xhr.send();
      if (xhr.status !== 200 || !xhr.responseText) throw new Error(String(xhr.status));
      body = xhr.responseText;
    } catch (error) {
      publish(['PATCH runtime marker=unknown rewritten=no', 'PATCH analog marker=unknown rewritten=no', 'PATCH inject=xhr-failed']);
      return;
    }
    const patched = patchSynapseBundle(body);
    const rewritten = patched.lines.some(line => line.endsWith('rewritten=yes'));
    if (!rewritten) {
      publish(patched.lines.concat('PATCH inject=unchanged'));
      return;
    }
    node.src = URL.createObjectURL(new Blob([patched.code], {type: 'text/javascript'}));
    publish(patched.lines.concat('PATCH inject=replaced'));
  }
  const scriptObserver = new MutationObserver(mutations => {
    for (const mutation of mutations) {
      if (mutation.type === 'attributes') considerScript(mutation.target);
      for (const node of mutation.addedNodes) {
        considerScript(node);
        if (node.querySelectorAll) node.querySelectorAll('script').forEach(considerScript);
      }
    }
  });
  scriptObserver.observe(document, {childList: true, subtree: true, attributes: true, attributeFilter: ['src']});
  document.querySelectorAll('script').forEach(considerScript);
  document.addEventListener('DOMContentLoaded', () => {
    if (!location.pathname.includes('/products/') || sawProductMain) return;
    publish(['PATCH runtime marker=unknown rewritten=no', 'PATCH analog marker=unknown rewritten=no', 'PATCH inject=not-seen']);
  }, {once: true});
  // The product document at /products/ owns main.*.js. Other child frames
  // must not see the virtual device, or each of them repeats the same query.
  // The top window still needs it: getDevices there creates the product frame.
  if (window.top !== window && !location.pathname.includes('/products/')) return;
  class VirtualDevice extends EventTarget {
    constructor() {
      super();
      this.vendorId = 0x1532;
      this.productId = 0x02d0;
      this.productName = 'Razer Huntsman V3 Pro Tenkeyless 8KHz';
      this.opened = false;
      this.oninputreport = null;
      // One collection, usage page 12: that is the feature-report device.
      // A non-empty inputReports list lets the events listener attach here.
      // A second collection would be ignored by the page filter.
      this.collections = [{
        usagePage: 12,
        usage: 1,
        type: 1,
        children: [],
        inputReports: [{reportId: 1, items: [{reportSize: 8, reportCount: 16, isConstant: false, usages: [0xff000001], logicalMinimum: 0, logicalMaximum: 255}]}],
        outputReports: [],
        featureReports: [{reportId: 0, items: [{reportSize: 8, reportCount: 90, isConstant: true, usages: [0xff000002], logicalMinimum: 0, logicalMaximum: 1}]}]
      }];
    }
    async open() { await rpc('open'); this.opened = true; record('Opened 1532:02a7, interface 3. The page sees 1532:02d0.'); }
    async close() { await rpc('close'); this.opened = false; }
    async forget() { await this.close(); }
    async sendFeatureReport(reportId, buffer) {
      if (!this.opened) throw new DOMException('Device closed', 'InvalidStateError');
      const bytes = ArrayBuffer.isView(buffer) ? new Uint8Array(buffer.buffer, buffer.byteOffset, buffer.byteLength) : new Uint8Array(buffer);
      await rpc('send', {reportId, data: Array.from(bytes)});
    }
    async receiveFeatureReport(reportId) {
      if (!this.opened) throw new DOMException('Device closed', 'InvalidStateError');
      const data = await rpc('receive', {reportId});
      return new DataView(Uint8Array.from(data).buffer);
    }
    async sendReport() { throw new DOMException('Output reports are not forwarded. This bridge uses feature reports.', 'NotAllowedError'); }
  }
  const device = new VirtualDevice();
  function connectionEvent(type) {
    try {
      if (typeof HIDConnectionEvent === 'function') return new HIDConnectionEvent(type, {device});
    } catch (error) {}
    const event = new Event(type);
    Object.defineProperty(event, 'device', {value: device});
    return event;
  }
  class VirtualHID extends EventTarget {
    constructor() {
      super();
      this._onconnect = null;
      this._ondisconnect = null;
      this._announced = false;
    }
    get onconnect() { return this._onconnect; }
    set onconnect(fn) {
      if (this._onconnect) this.removeEventListener('connect', this._onconnect);
      this._onconnect = typeof fn === 'function' ? fn : null;
      if (this._onconnect) this.addEventListener('connect', this._onconnect);
      if (this._onconnect) this.announce();
    }
    get ondisconnect() { return this._ondisconnect; }
    set ondisconnect(fn) {
      if (this._ondisconnect) this.removeEventListener('disconnect', this._ondisconnect);
      this._ondisconnect = typeof fn === 'function' ? fn : null;
      if (this._ondisconnect) this.addEventListener('disconnect', this._ondisconnect);
    }
    announce() {
      if (this._announced || device.opened) return;
      this._announced = true;
      rpc('status').then(state => {
        if (!state.present || device.opened) return;
        this.dispatchEvent(connectionEvent('connect'));
      }).catch(() => {});
    }
    async getDevices() {
      const state = await rpc('status');
      return state.present ? [device] : [];
    }
    async requestDevice(options = {}) {
      const filters = (options.filters || []).map(f => ({
        vendorId: typeof f.vendorId === 'number' ? f.vendorId : null,
        productId: typeof f.productId === 'number' ? f.productId : null,
        usagePage: typeof f.usagePage === 'number' ? f.usagePage : null,
        usage: typeof f.usage === 'number' ? f.usage : null
      }));
      try {
        await rpc('filters', {filters});
      } catch (error) {
        record('Could not write the requestDevice filter to the log.');
      }
      const absent = value => value === undefined || value === null;
      const matches = f => (absent(f.vendorId) || f.vendorId === device.vendorId) &&
        (absent(f.productId) || f.productId === device.productId) &&
        (absent(f.usagePage) || f.usagePage === 12) && (absent(f.usage) || f.usage === 1);
      if (filters.length && !filters.some(matches)) {
        record('Filter does not match 1532:02d0.');
        return [];
      }
      if (options.exclusionFilters?.some(matches)) return [];
      return this.getDevices();
    }
  }
  Object.defineProperty(navigator, 'hid', {configurable: true, value: new VirtualHID()});
  record('The page sees 1532:02d0. Only 1532:02a7, interface 3, is opened.');
  if (window.top === window) {
    const mount = () => {
      const identity = 'Synapse Web bridge: the page sees 1532:02d0 (RZ03-0552). The keyboard stays 1532:02a7';
      const badge = document.createElement('div');
      badge.textContent = identity;
      badge.style.cssText = 'position:fixed;bottom:8px;left:8px;z-index:2147483647;background:#222;color:#ffcf70;padding:10px;font:12px sans-serif;max-width:650px;pointer-events:none';
      document.body.appendChild(badge);
      window.addEventListener('huntsman-status', e => { badge.textContent = `${identity} — ${e.detail}`; });
    };
    if (document.body) mount(); else window.addEventListener('DOMContentLoaded', mount, {once: true});
  }
})();
