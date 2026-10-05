export const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');
export const WORKSPACE = import.meta.env.VITE_WORKSPACE_NAME || 'Client workspace';
export const PRODUCT = import.meta.env.VITE_PRODUCT_NAME || 'EstateWatch';
export const POLL_MS = Math.max(60000, Number(import.meta.env.VITE_POLL_INTERVAL_MS) || 90000);

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}
export async function api(path, { body, method = 'GET', timeout = 30000, signal } = {}) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timer = setTimeout(abort, timeout);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method, credentials: 'include', signal: controller.signal,
      headers: { Accept: 'application/json', ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    const content = await response.text();
    let result;
    try { result = content ? JSON.parse(content) : null; } catch { throw new ApiError('The API returned an unexpected response. Check the API address and service status.', response.status); }
    if (!response.ok) {
      if (response.status === 401 && path !== '/dashboard/auth/login') window.dispatchEvent(new Event('session-expired'));
      const detail = typeof result?.detail === 'string' ? result.detail : Array.isArray(result?.detail) ? result.detail.map(x => x.msg).join('; ') : null;
      throw new ApiError(detail || `Request failed (${response.status}).`, response.status);
    }
    return result;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (controller.signal.aborted) throw new ApiError(method === 'GET' ? 'Request timed out. Try again.' : 'The response timed out. The action may still finish on the server. Reload the data before retrying.', 0);
    throw new ApiError('Cannot reach the backend. Check that the Python service is running.', 0);
  } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
}

export function evidenceUrl(value) {
  return typeof value === 'string' && /^\/dashboard\/evidence\/\d+\/image$/.test(value) ? `${API_BASE}${value}` : null;
}
export const dashboard = {
  auth: () => api('/dashboard/auth/status'),
  login: password => api('/dashboard/auth/login', { method: 'POST', body: { password } }),
  logout: () => api('/dashboard/auth/logout', { method: 'POST' }),
  vendors: () => api('/dashboard/vendors'),
  detail: id => api(`/dashboard/vendors/${id}`),
  risk: id => api(`/dashboard/vendors/${id}/risk`),
  coverage: id => api(`/dashboard/vendors/${id}/coverage`),
  alerts: () => api('/dashboard/alerts/operations'),
  audit: () => api('/dashboard/audit-logs?limit=100'),
  refresh: id => api(`/dashboard/vendors/${id}/refresh`, { method: 'POST', timeout: 600000 }),
  action: (id, body) => body.action === 'resolve'
    ? api('/dashboard/alerts/' + id + '/status', { method: 'POST', body: { status: 'resolved', actor: body.actor, resolution_note: body.note, assigned_to: body.assigned_to } })
    : api('/dashboard/alerts/' + id + '/actions', { method: 'POST', body }),
  decision: (id, body) => api(`/dashboard/vendors/${id}/review-decisions`, { method: 'POST', body }),
  financialReview: (id, body) => api(`/dashboard/vendors/${id}/financial-review`, { method: 'POST', body }),
  eventReview: (id, body) => api(`/dashboard/risk-events/${id}/review`, { method: 'POST', body }),
  report: id => api(`/dashboard/vendors/${id}/reports/current`),
  createReport: (id, generated_by) => api(`/dashboard/vendors/${id}/reports`, { method: 'POST', body: { generated_by } }),
  bulkOnboard: companies => api('/dashboard/bulk-onboarding-jobs', { method: 'POST', body: { companies, actor: 'Dashboard user' } }),
  automationJobs: (limit=50, job_type='') => api(`/dashboard/automation-jobs?limit=${limit}${job_type ? `&job_type=${encodeURIComponent(job_type)}` : ''}`),
  automationJob: id => api(`/dashboard/automation-jobs/${id}`),
  updateContact: (id, body) => api(`/dashboard/vendors/${id}/contact`, { method: 'PATCH', body }),
  sendQuestionnaire: (id, body) => api(`/dashboard/vendors/${id}/intake-requests`, { method: 'POST', body }),
  intakeRequests: id => api(`/dashboard/vendors/${id}/intake-requests`),
  emailReport: (id, body) => api(`/dashboard/vendors/${id}/reports/email`, { method: 'POST', body }),
};
