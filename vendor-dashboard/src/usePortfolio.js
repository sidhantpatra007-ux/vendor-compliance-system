import { useCallback, useEffect, useRef, useState } from 'react';
import { dashboard, POLL_MS } from './api.js';
import { normalizeVendor } from './data.js';

export function usePortfolio(enabled) {
  const [state, setState] = useState({ rows: [], bundles: {}, alerts: [], audit: [], loaded: false, loading: false, enriching: false, errors: {}, updated: null });
  const lock = useRef(null), queued = useRef(false), generation = useRef(0), enabledRef = useRef(enabled);
  enabledRef.current = enabled;
  const reload = useCallback(async () => {
    if (!enabledRef.current) return;
    if (lock.current === generation.current) { queued.current = true; return; }
    lock.current = generation.current;
    queued.current = false;
    const token = generation.current;
    const alive = () => enabledRef.current && token === generation.current;
    setState(s => ({ ...s, loading: true }));
    try {
      const result = await Promise.allSettled([dashboard.vendors(), dashboard.alerts(), dashboard.audit()]);
      if (!alive()) return;
      const keys = ['rows','alerts','audit'], patch = {}, errors = {};
      result.forEach((r,i) => { if (r.status === 'fulfilled' && Array.isArray(r.value)) patch[keys[i]] = r.value; else errors[keys[i]] = r.reason?.message || 'Unexpected data returned.'; });
      setState(s => ({ ...s, ...patch, errors, loaded: true, loading: false, enriching: !!patch.rows?.length, updated: !errors.rows && !errors.alerts ? new Date().toISOString() : s.updated }));
      if (!patch.rows) return;
      let cursor = 0;
      const bundles = {};
      await Promise.all(Array.from({ length: Math.min(3, patch.rows.length) }, async () => {
        while (cursor < patch.rows.length && alive()) {
          const row = patch.rows[cursor++], id = row.vendor_id;
          const values = await Promise.allSettled([dashboard.detail(id), dashboard.risk(id), dashboard.coverage(id)]);
          const bundle = { errors: {} };
          ['detail','risk','coverage'].forEach((key,i) => {
            if (values[i].status === 'fulfilled') bundle[key] = values[i].value;
            else bundle.errors[key] = values[i].reason?.message || 'Request failed.';
          });
          bundles[id] = bundle;
          if (alive()) setState(s => ({ ...s, bundles: { ...s.bundles, [id]: bundle } }));
        }
      }));
      if (alive()) setState(s => ({ ...s, bundles }));
    } finally {
      if (alive()) setState(s => ({ ...s, loading: false, enriching: false }));
      if (lock.current === token) {
        lock.current = null;
        if (queued.current && alive()) { queued.current = false; queueMicrotask(reload); }
      }
    }
  }, []);
  useEffect(() => {
    if (!enabled) { generation.current++; setState({ rows: [], bundles: {}, alerts: [], audit: [], loaded: false, loading: false, enriching: false, errors: {}, updated: null }); return; }
    reload();
    const interval = setInterval(() => { if (!document.hidden) reload(); }, POLL_MS);
    const focus = () => { if (!document.hidden) reload(); };
    document.addEventListener('visibilitychange', focus);
    return () => { clearInterval(interval); document.removeEventListener('visibilitychange', focus); generation.current++; };
  }, [enabled, reload]);
  return { ...state, companies: state.rows.map(row => normalizeVendor(row, state.bundles[row.vendor_id], state.alerts)), reload };
}
