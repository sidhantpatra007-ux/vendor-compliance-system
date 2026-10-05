import { useEffect, useRef, useState } from 'react';
import { dashboard, PRODUCT, WORKSPACE } from './api.js';
import { age, isOpen, sourceRows } from './data.js';
import { Empty, ErrorNotice, Skeleton } from './ui.jsx';
import { usePortfolio } from './usePortfolio.js';
import { Overview, Companies, Alerts, Intake, Reports, Operations, ReportSession } from './pages.jsx';
import { Dossier } from './Dossier.jsx';
import { AlertDrawer } from './actions.jsx';

const navigation = [['Overview','⌂'],['Companies','▦'],['Alerts','◉'],['Intake & requests','▤'],['Reports','▱'],['Operations','◌']];
export default function App() {
  const [auth,setAuth] = useState('checking'), [authError,setAuthError] = useState('');
  const [page,setPage] = useState('Overview'), [selected,setSelected] = useState(null);
  const [alertId,setAlertId] = useState(null), [search,setSearch] = useState(''), [toast,setToast] = useState('');
  const [reportCompany,setReportCompany] = useState(null), [loggingOut,setLoggingOut] = useState(false);
  const toastTimer = useRef(null);
  const reportSession = useState([]);
  useEffect(() => { if (auth !== 'in') reportSession[1]([]); }, [auth]);
  const store = usePortfolio(auth === 'in');
  const checkAuth = async () => { setAuth('checking'); setAuthError(''); try { const r = await dashboard.auth(); setAuth(r.authenticated ? 'in' : 'out'); } catch(e) { setAuthError(e.message); setAuth('out'); } };
  useEffect(() => { checkAuth(); const expire = () => { setAuth('out'); setAlertId(null); setAuthError('Your session expired. Sign in again.'); }; window.addEventListener('session-expired',expire); return () => { window.removeEventListener('session-expired',expire); clearTimeout(toastTimer.current); }; }, []);
  const notice = message => { setToast(message); clearTimeout(toastTimer.current); toastTimer.current = setTimeout(() => setToast(''),5000); };
  const go = name => { setPage(name); setAlertId(null); window.scrollTo({top:0}); };
  const choose = company => { setSelected(company.vendor_id); go('Company dossier'); };
  const showReport = id => { setReportCompany(id); go('Reports'); };
  const signOut = async () => { setLoggingOut(true); try { await dashboard.logout(); setAuth('out'); setPage('Overview'); setSelected(null); setSearch(''); setReportCompany(null); setAlertId(null); } catch(e) { notice(e.message); } finally { setLoggingOut(false); } };
  if (auth === 'checking') return <div className="login-shell"><div className="panel login-card"><h1>{PRODUCT}</h1><p>Checking your session…</p><Skeleton/></div></div>;
  if (auth !== 'in') return <Login error={authError} onLogin={() => { setAuthError(''); setAuth('in'); }} retry={checkAuth}/>;
  const company = store.companies.find(c => c.vendor_id === selected);
  const activeAlert = store.alerts.find(a => a.alert_id === alertId);
  const sources = sourceRows(store.companies);
  const sourceHealthy = sources.length && sources.every(s => s.state === 'healthy') && !store.enriching && !Object.keys(store.errors).length && store.companies.every(c => c.coverage && !c.errors.coverage);
  return <ReportSession.Provider value={reportSession}><div className="app-shell">
    <a className="skip-link" href="#content">Skip to main content</a>
    <aside className="sidebar"><div className="brand"><div className="brand-mark">E</div><div><b>{PRODUCT}</b><span>Procurement risk</span></div></div>
      <div className="workspace"><span className="tiny-label">WORKSPACE</span><b>{WORKSPACE}</b></div>
      <nav aria-label="Main navigation">{navigation.map(([name,icon]) => <button key={name} className={page === name || (name === 'Companies' && page === 'Company dossier') ? 'nav-active' : ''} onClick={() => go(name)}><i>{icon}</i>{name}{name === 'Alerts' && <em>{store.errors.alerts ? '?' : store.alerts.filter(isOpen).length}</em>}</button>)}</nav>
      <div className="sidebar-footer"><div className="profile"><div>CU</div><span><b>Client user</b><small>Shared workspace access</small></span></div><button className="text-button signout" disabled={loggingOut} onClick={signOut}>{loggingOut ? 'Signing out…' : 'Sign out ↗'}</button></div>
    </aside>
    <main><header className="topbar"><div className="crumb"><span>{WORKSPACE}</span><b>/</b><strong>{page}</strong></div><div className="top-actions"><div className={'sync ' + (sourceHealthy ? '' : 'unknown-sync')}><i/>{sourceHealthy ? 'Reported sources healthy' : 'Coverage needs attention'}<small>Dashboard {age(store.updated)}</small></div><button className="icon-button" aria-label="Search companies" onClick={() => go('Companies')}>⌕</button><button className="refresh" disabled={store.loading || store.enriching} title="Reload saved backend data. Use a company's Refresh action to run new checks." onClick={store.reload}>{store.loading || store.enriching ? '↻ Loading…' : '↻ Refresh'}</button></div></header>
      <div className="demo-banner live-banner"><span>◈</span><b>Connected workspace</b><span>Saved backend data · Auto-refresh every 90 seconds · Times shown in UK time</span></div>
      <section className="content" id="content">
        {Object.entries(store.errors).map(([name,error]) => <ErrorNotice key={name} error={name + ': ' + error + (store.updated ? ' Previously loaded data may be stale.' : '')} retry={store.reload}/>)}
        {!store.loaded ? <Skeleton/> : store.errors.rows && !store.updated ? <Empty title="Company data is unavailable">Restore the backend connection and retry. An unavailable portfolio is not an empty portfolio.</Empty> : <>
          {page === 'Overview' && <Overview store={store} choose={choose} go={go} openAlert={setAlertId}/>}
          {page === 'Companies' && <Companies companies={store.companies} search={search} setSearch={setSearch} choose={choose} enriching={store.enriching}/>}
          {page === 'Company dossier' && (company ? <Dossier key={company.vendor_id} company={company} store={store} back={() => go('Companies')} openAlert={setAlertId} report={() => showReport(company.vendor_id)} notice={notice}/> : <ErrorNotice error="Company not available in this portfolio." retry={() => go('Companies')}/>)}
          {page === 'Alerts' && <Alerts alerts={store.alerts} companies={store.companies} openAlert={setAlertId}/>}
          {page === 'Intake & requests' && <Intake companies={store.companies} choose={choose}/>}
          {page === 'Reports' && <Reports companies={store.companies} initialId={reportCompany} notice={notice}/>}
          {page === 'Operations' && <Operations store={store}/>}
        </>}
      </section>
    </main>
    {activeAlert && <AlertDrawer alert={activeAlert} company={store.companies.find(c=>c.vendor_id===activeAlert.vendor_id)} close={() => setAlertId(null)} onSaved={() => {setAlertId(null);store.reload();}} notice={notice}/>}
    {toast && <div className="toast" role="status">{toast}</div>}
  </div></ReportSession.Provider>;
}
function Login({ error, onLogin, retry }) {
  const [password,setPassword] = useState(''), [message,setMessage] = useState(''), [busy,setBusy] = useState(false), [show,setShow] = useState(false);
  const submit = async e => { e.preventDefault(); if(busy) return; setBusy(true); setMessage(''); try { await dashboard.login(password); setPassword(''); onLogin(); } catch(e) { setMessage(e.message); } finally {setBusy(false);} };
  return <main className="login-shell"><section className="login-intro"><div className="brand"><div className="brand-mark">E</div><b>{PRODUCT}</b></div><p className="eyebrow">COMPANY MONITORING</p><h1>A clearer view.<br/><span>A considered decision.</span></h1><p>Company intelligence, review queues, and evidence in one calm workspace.</p><div className="login-decoration"><span>Corporate</span><span>Ownership</span><span>Compliance</span><span>Financial evidence</span></div></section><section className="panel login-card"><span className="eyebrow">{WORKSPACE}</span><h2>Welcome back.</h2><p>Enter the workspace password to access your monitored companies.</p><ErrorNotice error={message || error} retry={error ? retry : undefined}/><form onSubmit={submit}><label>Workspace password<div className="password-field"><input autoComplete="current-password" type={show ? 'text' : 'password'} value={password} onChange={e => setPassword(e.target.value)} required autoFocus disabled={busy}/><button type="button" className="text-button" onClick={() => setShow(!show)}>{show ? 'Hide' : 'Show'}</button></div></label><button className="primary full-width" disabled={busy || !password}>{busy ? 'Signing in…' : 'Sign in →'}</button></form><small>Anyone with the workspace password can sign in. Review actions ask for a recorded-by name.</small></section></main>;
}
