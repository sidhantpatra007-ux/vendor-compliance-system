import { useState } from 'react';
import { dashboard } from './api.js';
import { date, isClosed, label } from './data.js';
import { Details, ErrorNotice, Modal, SourceLink, Status } from './ui.jsx';

export function ReviewForm({ title, choices, initial, submit, close, onSaved, kind = 'review', extra }) {
  const [choice,setChoice] = useState(initial || choices[0][0]), [actor,setActor] = useState(''), [note,setNote] = useState('');
  const [assigned,setAssigned] = useState(''), [nextReview,setNextReview] = useState(''), [error,setError] = useState(''), [busy,setBusy] = useState(false);
  const save = async e => {
    e.preventDefault(); if (busy) return;
    if (!actor.trim() || (choice === 'assign' && !assigned.trim()) || (['resolve','false_positive','confirmed_match','rejected'].includes(choice) && !note.trim())) { setError('Complete the required fields with more than spaces.'); return; }
    setBusy(true); setError('');
    try {
      const result = await submit({ choice, actor: actor.trim(), note: note.trim(), assigned: assigned.trim(), nextReview: nextReview ? new Date(nextReview).toISOString() : undefined });
      onSaved(result);
    } catch(e) { setError(e.message); } finally { setBusy(false); }
  };
  return <Modal title={title} close={() => { if (!busy) close(); }}>
    {extra}<form onSubmit={save} className="action-form">
      <label>{kind === 'alert' ? 'Action' : 'Decision'}<select aria-label={kind === 'alert' ? 'Action' : 'Decision'} value={choice} onChange={e=>setChoice(e.target.value)} disabled={busy}>{choices.map(([value,name])=><option value={value} key={value}>{name}</option>)}</select></label>
      <label>Recorded by<input required maxLength={200} value={actor} onChange={e=>setActor(e.target.value)} disabled={busy} placeholder="Your name"/></label>
      {choice === 'assign' && <label>Assign to<input required maxLength={200} value={assigned} onChange={e=>setAssigned(e.target.value)} disabled={busy} placeholder="Reviewer name"/></label>}
      <label>Note{['resolve','false_positive','confirmed_match','rejected'].includes(choice) ? ' (required)' : ''}<textarea required={['resolve','false_positive','confirmed_match','rejected'].includes(choice)} maxLength={2000} rows={4} value={note} onChange={e=>setNote(e.target.value)} disabled={busy}/></label>
      {kind === 'company' && <label>Next review (optional, your local time)<input type="datetime-local" value={nextReview} onChange={e=>setNextReview(e.target.value)} disabled={busy}/></label>}
      <small className="muted">Recorded-by names are self-declared in this shared-password workspace.</small>
      <ErrorNotice error={error}/>
      <div className="form-actions"><button type="button" className="filter" onClick={close} disabled={busy}>Cancel</button><button className="primary" disabled={busy || !actor.trim()}>{busy ? 'Saving…' : 'Save decision'}</button></div>
    </form>
  </Modal>;
}
export function AlertDrawer({ alert, company, close, onSaved, notice }) {
  const [action,setAction] = useState(null);
  if (action) return <ReviewForm title={'Alert #' + alert.alert_id + ' · ' + label(action)} kind="alert" choices={[[action,label(action)]]} close={()=>setAction(null)}
    submit={form=>dashboard.action(alert.alert_id,{action:form.choice,actor:form.actor,note:form.note,...(form.choice === 'assign' ? {assigned_to:form.assigned} : {})})}
    onSaved={()=>{notice('Alert updated in the backend.');onSaved();}}/>;
  return <Modal drawer title={'Alert #' + alert.alert_id} close={close}><div className="drawer-title"><Status value={alert.severity}/><h2>{alert.title}</h2><p>{company?.name || 'Company #' + alert.vendor_id}</p></div>
    <div className="drawer-block"><span>Why this needs review</span><p>{alert.reason}</p></div>
    <div className="drawer-block"><span>Evidence</span>{(alert.evidence||[]).length ? alert.evidence.map((e,i)=><div key={i}><p>{label(e.event_type || e.type || 'Evidence')}{e.risk_event_id && ' · Event #' + e.risk_event_id}</p><SourceLink url={e.source_url}/></div>) : <p>No source evidence attached.</p>}</div>
    <Details rows={[[ 'Owner',alert.assigned_to || 'Unassigned'],['Status',label(alert.status)],['SLA due',date(alert.sla_due_at)],['Created',date(alert.created_at)],['Updated',date(alert.updated_at)],['Acknowledged by',alert.acknowledged_by],['Acknowledged at',date(alert.acknowledged_at)],['Resolved by',alert.resolved_by],['Resolved at',date(alert.resolved_at)],['Resolution note',alert.resolution_note],['Escalated at',date(alert.escalated_at)],['Notification',label(alert.notification_state)],['Delivery attempts',alert.notification_count],['Last notification',date(alert.last_notified_at)],['Delivery error',alert.notification_last_error]]}/>
    <div className="drawer-actions">{[['acknowledge','Acknowledge'],['assign','Assign'],['resolve','Resolve alert'],['false_positive','False positive'],['escalate','Escalate']].map(([value,name])=><button key={value} className={value==='resolve'?'primary':'filter'} disabled={isClosed(alert)} onClick={()=>setAction(value)}>{name}</button>)}</div>
    {isClosed(alert) && <p className="muted">This alert is closed.</p>}<p className="drawer-foot">Review the evidence before making a business decision.</p>
  </Modal>;
}
