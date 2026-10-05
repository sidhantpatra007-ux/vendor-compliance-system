export const present = value => value !== null && value !== undefined && value !== '';
export const text = value => !present(value) ? 'Not provided' : typeof value === 'boolean' ? (value ? 'Yes' : 'No') : typeof value === 'object' ? JSON.stringify(value) : String(value);
export const label = value => String(value || 'Unknown').replace(/_/g, ' ').toLowerCase().replace(/(^|\s)\S/g, x => x.toUpperCase());
export function timestamp(value) {
  if (!value) return null;
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : value + 'Z';
  const result = new Date(normalized);
  return Number.isNaN(result.getTime()) ? null : result;
}
export const date = value => timestamp(value)?.toLocaleString('en-GB', { timeZone: 'Europe/London', dateStyle: 'medium', timeStyle: 'short' }) || 'Not available';
export function age(value) {
  const parsed = timestamp(value);
  if (!parsed) return 'Not checked';
  const minutes = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 60000));
  return minutes < 1 ? 'Just now' : minutes < 60 ? minutes + 'm ago' : minutes < 1440 ? Math.floor(minutes / 60) + 'h ago' : Math.floor(minutes / 1440) + 'd ago';
}
export const isOpen = item => ['open', 'acknowledged', 'escalated'].includes(item.status);
export const isClosed = item => ['resolved', 'resolve', 'false_positive'].includes(item.status);
export const tone = value => ({ healthy: 'good', complete: 'good', reviewed: 'good', approved: 'good', active: 'good', assessed: 'good', a: 'good', b: 'watch', c: 'risk', d: 'danger', e: 'danger', blocked: 'danger', critical: 'danger', high: 'high', medium: 'medium', low: 'blue', pending: 'watch', review_required: 'watch', stale: 'watch', failed: 'danger', unknown: 'neutral', unavailable: 'neutral' }[String(value || '').toLowerCase()] || 'neutral');
export const intakeFields = [
  ['trading_name', 'Trading name'], ['address_street', 'Street address'], ['address_city', 'City'], ['address_postcode', 'Postcode'],
  ['contact_name', 'Contact name'], ['contact_email', 'Contact email'], ['contact_phone', 'Contact phone'],
  ['vendor_category', 'Company category'], ['goods_or_services', 'Goods or services'], ['supplier_criticality', 'Client-assessed criticality'],
  ['annual_spend_band', 'Estimated annual spend'], ['access_to_client_systems_or_data', 'Access to client systems or data'],
  ['processes_personal_data', 'Processes personal data'], ['delivery_countries', 'Delivery countries'],
  ['uses_subcontractors', 'Uses subcontractors'], ['supplier_declaration_accepted', 'Declaration accepted'],
];
const assessmentFields = ['vendor_category','goods_or_services','supplier_criticality','access_to_client_systems_or_data','processes_personal_data','delivery_countries','uses_subcontractors','supplier_declaration_accepted'];
export function intakeState(intake) {
  if (!intake) return 'Unknown';
  const count = assessmentFields.filter(key => present(intake[key])).length;
  return count === 0 ? 'Not supplied' : count === assessmentFields.length ? 'Fields supplied' : 'Intake incomplete';
}
export function delta(history = []) {
  const valid = history.filter(row => Number.isFinite(row.score));
  return valid.length < 2 ? null : valid.at(-1).score - valid.at(-2).score;
}
export function normalizeVendor(row, bundle, alerts) {
  const detail = bundle?.detail;
  return { ...row, detail, risk: bundle?.risk, coverage: bundle?.coverage, errors: bundle?.errors || {},
    criticality: detail?.intake?.supplier_criticality || 'Unknown', intakeState: intakeState(detail?.intake),
    history: detail?.score_history || [], delta: delta(detail?.score_history),
    openAlerts: alerts.filter(a => a.vendor_id === row.vendor_id && isOpen(a)),
    signals: detail?.latest?.signals || {},
  };
}
export const sourceRows = companies => companies.flatMap(c => (c.coverage?.data_freshness || []).map(s => ({ ...s, vendor_id: c.vendor_id, company: c.name })));
export function safeExternal(url) {
  try { const parsed = new URL(url); return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : null; } catch { return null; }
}
export const chUrl = (number, suffix = '') => 'https://find-and-update.company-information.service.gov.uk/company/' + encodeURIComponent(number) + suffix;
export function csvCell(value) {
  let cell = String(value ?? '');
  if (/^[\s]*[=+\-@]/.test(cell)) cell = "'" + cell;
  return '"' + cell.replace(/"/g, '""') + '"';
}
export function download(name, content, type = 'application/json') {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function exportCsv(name, rows) {
  download(name, '\uFEFF' + rows.map(row => row.map(csvCell).join(',')).join('\r\n'), 'text/csv;charset=utf-8');
}
export const severityRank = value => ({ critical: 4, high: 3, medium: 2, low: 1 }[String(value).toLowerCase()] || 0);
