import { useEffect, useRef, useState } from 'react';
import { dashboard } from './api.js';
import { ErrorNotice, Modal } from './ui.jsx';

function parseCsv(text) {
  const rows = text.replace(/^\uFEFF/, '').split(/\r?\n/).filter(row => row.trim());
  if (rows.length < 2) throw new Error('The CSV needs a header row and at least one company.');
  const parse = row => { const out=[]; let value='', quoted=false; for(let i=0;i<row.length;i+=1){const c=row[i];if(c==='"'){if(quoted&&row[i+1]==='"'){value+='"';i+=1;}else quoted=!quoted;}else if(c===','&&!quoted){out.push(value.trim());value='';}else value+=c;}out.push(value.trim());return out; };
  const headers = parse(rows[0]).map(x => x.toLowerCase().replace(/[^a-z0-9]/g,''));
  const numberIndex = headers.findIndex(x => ['companynumber','companyno','registrationnumber'].includes(x));
  const nameIndex = headers.findIndex(x => ['companyname','legalcompanyname','displayname','name'].includes(x));
  if (numberIndex < 0) throw new Error('Add a Company Number column to the CSV.');
  const seen = new Set();
  return rows.slice(1).flatMap((row, index) => {
    const values = parse(row), company_number = String(values[numberIndex] || '').trim().toUpperCase();
    if (!company_number || seen.has(company_number)) return [];
    seen.add(company_number);
    const display_name = nameIndex >= 0 ? String(values[nameIndex] || '').trim() : '';
    return [{ company_number, ...(display_name ? { display_name } : {}), csv_row: index + 2 }];
  });
}

export function BulkImportButton({ className='primary', label='+ Import CSV' }) {
  const fileRef = useRef(null); const [open,setOpen]=useState(false), [busy,setBusy]=useState(false), [error,setError]=useState(''), [job,setJob]=useState(null), [criticality,setCriticality]=useState('Medium');
  useEffect(()=>{if(!job || !['pending','processing'].includes(job.status))return;const timer=setTimeout(()=>dashboard.automationJob(job.job_id).then(setJob).catch(e=>setError(e.message)),4000);return()=>clearTimeout(timer);},[job]);
  const submit = async event => { event.preventDefault(); const file=fileRef.current?.files?.[0]; if(!file) return setError('Choose a CSV file first.'); if(file.size>1024*1024) return setError('CSV files must be 1 MB or smaller.'); setBusy(true);setError('');try{const companies=parseCsv(await file.text()).map(c=>({...c,supplier_criticality:criticality}));if(!companies.length)throw new Error('No unique company numbers were found.');setJob(await dashboard.bulkOnboard(companies));}catch(e){setError(e.message);}finally{setBusy(false);}};
  return <><button className={className} onClick={()=>{setOpen(true);setError('');setJob(null);}}>{label}</button>{open&&<Modal title="Import companies from CSV" close={()=>!busy&&setOpen(false)}>{job?<div className="action-form"><p>Import job #{job.job_id}</p><p>Status: <b>{job.status}</b></p>{['pending','processing'].includes(job.status)&&<p>The n8n worker is processing this upload. This window checks again automatically.</p>}{job.error&&<ErrorNotice error={job.error}/>} {job.result?.results?.length>0&&<div><p><b>Created:</b> {job.result.created||0} · <b>Refreshed:</b> {job.result.refreshed||0} · <b>Failed:</b> {job.result.failed||0}</p>{job.result.results.filter(x=>x.status==='onboarding_failed').map(x=><p key={x.row} className="panel-note">Row {x.row} · {x.company_number||'Unknown'} · {x.error}</p>)}</div>}<button className="primary" onClick={()=>setOpen(false)}>Done</button></div>:<form className="action-form" onSubmit={submit}><p>Use a <b>Company Number</b> column. Company names are optional. Invalid or unavailable companies will appear as onboarding failures, not monitored companies.</p><label>CSV file<input ref={fileRef} type="file" accept=".csv,text/csv" required disabled={busy}/></label><label>Default criticality<select value={criticality} onChange={e=>setCriticality(e.target.value)} disabled={busy}>{['Low','Medium','High','Critical'].map(x=><option key={x}>{x}</option>)}</select></label><ErrorNotice error={error}/><button className="primary" disabled={busy}>{busy?'Queueing…':'Queue import'}</button></form>}</Modal>}</>;
}

export function QuestionnaireButton({ vendor, className='primary', label='Send questionnaire' }) {
  const [open,setOpen]=useState(false), [busy,setBusy]=useState(false), [error,setError]=useState(''), [email,setEmail]=useState(vendor?.detail?.intake?.contact_email || ''), [name,setName]=useState(vendor?.detail?.intake?.contact_name || ''), [sent,setSent]=useState(null);
  const submit=async event=>{event.preventDefault();setBusy(true);setError('');try{if(email!==vendor?.detail?.intake?.contact_email)await dashboard.updateContact(vendor.vendor_id,{contact_email:email,contact_name:name,actor:'Dashboard user'});setSent(await dashboard.sendQuestionnaire(vendor.vendor_id,{contact_email:email,contact_name:name,actor:'Dashboard user'}));}catch(e){setError(e.message);}finally{setBusy(false);}};
  return <><button className={className} onClick={()=>{setOpen(true);setError('');}} disabled={!vendor}> {label}</button>{open&&<Modal title="Request company information" close={()=>!busy&&setOpen(false)}>{sent?<div className="action-form"><p>Questionnaire request #{sent.request.request_id} is queued for {sent.request.recipient_email}.</p><button className="primary" onClick={()=>setOpen(false)}>Done</button></div>:<form className="action-form" onSubmit={submit}><p>The questionnaire link expires after 30 days. It is sent by the n8n worker, not directly by this browser.</p><label>Company contact name<input value={name} onChange={e=>setName(e.target.value)} maxLength={200} disabled={busy}/></label><label>Company contact email<input type="email" value={email} onChange={e=>setEmail(e.target.value)} required disabled={busy}/></label><ErrorNotice error={error}/><button className="primary" disabled={busy}>{busy?'Queueing…':'Queue questionnaire'}</button></form>}</Modal>}</>;
}

export function EmailReportButton({ vendorId, className='filter', label='Email report' }) {
  const [open,setOpen]=useState(false), [email,setEmail]=useState(''), [busy,setBusy]=useState(false), [error,setError]=useState(''), [job,setJob]=useState(null);
  const submit=async event=>{event.preventDefault();setBusy(true);setError('');try{setJob(await dashboard.emailReport(vendorId,{recipient_email:email,actor:'Dashboard user'}));}catch(e){setError(e.message);}finally{setBusy(false);}};
  return <><button className={className} onClick={()=>setOpen(true)} disabled={!vendorId}>{label}</button>{open&&<Modal title="Email immutable report" close={()=>!busy&&setOpen(false)}>{job?<div className="action-form"><p>Report #{job.report.report_id} is saved and email job #{job.job.job_id} is queued.</p><button className="primary" onClick={()=>setOpen(false)}>Done</button></div>:<form className="action-form" onSubmit={submit}><label>Recipient email<input type="email" value={email} onChange={e=>setEmail(e.target.value)} required disabled={busy}/></label><ErrorNotice error={error}/><button className="primary" disabled={busy}>{busy?'Queueing…':'Save and queue email'}</button></form>}</Modal>}</>;
}
