import { useEffect, useId, useRef } from 'react';
import { age, date, label, safeExternal, text, timestamp, tone } from './data.js';

export function Status({ children, value, color }) { return <span className={'status ' + (color || tone(value))}>{children || label(value)}</span>; }
export function Empty({ title = 'No data available', children, action }) { return <div className="empty"><span className="empty-icon">◌</span><h3>{title}</h3>{children && <p>{children}</p>}{action}</div>; }
export function ErrorNotice({ error, retry }) { return error ? <div className="error-notice" role="alert"><span>{error.message || error}</span>{retry && <button className="text-button" onClick={retry}>Retry</button>}</div> : null; }
export function Future({ children, reason, className = 'filter' }) { return <span className="future-control"><button className={className} disabled title={reason}>{children}</button><span className="future-note">{reason}</span></span>; }
export function SourceLink({ url, children = 'View source ↗' }) { const href = safeExternal(url); return href ? <a className="text-button source-link" href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span className="muted">Source link unavailable</span>; }
export function Panel({ title, eyebrow, action, children, className = '' }) { return <section className={'panel ' + className}><div className="panel-heading"><div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h2>{title}</h2></div>{action}</div>{children}</section>; }
export function PageTitle({ eyebrow, title, description, children }) { return <div className="page-title"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p>{description}</p></div>{children}</div>; }
export function Metric({ label: name, value, sub, icon, color }) { return <section className="metric"><span className={'metric-icon ' + color}>{icon}</span><div><p>{name}</p><h2>{value ?? '—'}</h2><small>{sub}</small></div></section>; }
export function ScoreRing({ score, grade, size = '' }) { const valid = Number.isFinite(score); return <div className={'score-ring ' + size + ' ' + tone(grade)} style={{ '--score': valid ? Math.max(0, Math.min(100, score)) : 0 }} aria-label={valid ? 'Score ' + score + ', grade ' + (grade || 'unknown') : 'Score unavailable'}><span>{valid ? score : '—'}</span><small>{grade || '?'}</small></div>; }
export function HistoryChart({ history = [], small = false }) {
  const id = useId().replace(/:/g, '');
  const rows = history.filter(x => Number.isFinite(x.score));
  if (rows.length < 2) return small ? <span className="muted">No trend yet</span> : <Empty title="Trend needs two assessments">The first available score remains visible in the dossier.</Empty>;
  const width = 600, height = 150, pad = 16;
  const points = rows.map((row, i) => [pad + i / (rows.length - 1) * (width - pad * 2), pad + (100 - Math.max(0, Math.min(100, row.score))) / 100 * (height - pad * 2)]);
  const coords = points.map(p => p.join(',')).join(' ');
  return <svg className={small ? 'spark ' + (rows.at(-1).score < rows[0].score ? 'down' : 'up') : 'dossier-chart'} viewBox={'0 0 ' + width + ' ' + (height + 25)} role="img" aria-label={'Score history: ' + rows.map(r => date(r.checked_at) + ': ' + r.score).join('; ')}>
    <defs><linearGradient id={id} x1="0" x2="0" y1="0" y2="1"><stop stopColor="#63b0ff" stopOpacity=".22"/><stop offset="1" stopColor="#63b0ff" stopOpacity="0"/></linearGradient></defs>
    {!small && [25,50,75,100].map(n => <line key={n} x1={pad} x2={width-pad} y1={pad+(100-n)/100*(height-pad*2)} y2={pad+(100-n)/100*(height-pad*2)} stroke="#29415d" strokeDasharray="3 5"/>)}
    {!small && <polygon points={pad + ',' + height + ' ' + coords + ' ' + (width-pad) + ',' + height} fill={'url(#' + id + ')'}/>}
    <polyline points={coords} fill="none" stroke={small ? 'currentColor' : '#63b0ff'} strokeWidth={small ? 7 : 3}/>
    {!small && points.map(([x,y], i) => <g key={i}><circle cx={x} cy={y} r="4" fill="#10243a" stroke="#63b0ff" strokeWidth="2"><title>{rows[i].score} · {date(rows[i].checked_at)}</title></circle><text x={x} y={height+19} textAnchor={i === 0 ? 'start' : i === points.length-1 ? 'end' : 'middle'}>{timestamp(rows[i].checked_at)?.toLocaleDateString('en-GB', {timeZone:'Europe/London',day:'2-digit',month:'short'}) || 'Unknown'}</text></g>)}
  </svg>;
}
export function Details({ rows }) { return <dl className="detail-list">{rows.map(([name,value]) => <div key={name}><dt>{name}</dt><dd>{text(value)}</dd></div>)}</dl>; }
export function SourceRows({ rows = [] }) { return rows.length ? rows.map((row,i) => <div className="source-row" key={row.source + i}><span className={'source-icon ' + tone(row.state)}>{row.state === 'healthy' ? '✓' : '◌'}</span><div><b>{label(row.source)}</b><small>{label(row.state)}{row.company && ' · ' + row.company}{row.last_error && ' · ' + row.last_error}</small></div><span title={date(row.last_successful_sync)}>{age(row.last_successful_sync)}</span></div>) : <Empty title="Coverage not available">No source status has been returned.</Empty>; }
export function Modal({ title, close, children, wide = false, drawer = false }) {
  const ref = useRef(null), closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    const previous = document.activeElement, oldOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const selectable = () => [...ref.current.querySelectorAll('button:not(:disabled),input,select,textarea,a[href],[tabindex="0"]')];
    selectable()[0]?.focus();
    const key = e => {
      if (e.key === 'Escape') closeRef.current();
      if (e.key === 'Tab') {
        const items = selectable(), first = items[0], last = items.at(-1);
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener('keydown',key);
    return () => { document.removeEventListener('keydown',key); document.body.style.overflow = oldOverflow; previous?.focus?.(); };
  }, []);
  return <div className={'modal-backdrop ' + (drawer ? 'drawer-backdrop' : '')} onMouseDown={e => { if (e.target === e.currentTarget) closeRef.current(); }}><section className={'modal panel ' + (wide ? 'wide-modal' : '') + (drawer ? ' connected-drawer' : '')} ref={ref} role="dialog" aria-modal="true" aria-label={title}><button className="close" aria-label="Close dialog" onClick={close}>×</button><h2>{title}</h2>{children}</section></div>;
}
export function Skeleton() { return <div className="skeleton-grid" aria-label="Loading dashboard" aria-busy="true">{[1,2,3,4,5,6].map(n => <div className="skeleton" key={n}/>)}</div>; }
