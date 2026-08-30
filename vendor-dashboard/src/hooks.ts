import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'; import { dashboard } from './api';
const poll=90_000;
export const useAuth=()=>useQuery({queryKey:['auth'],queryFn:dashboard.auth,retry:false});
export const useVendors=()=>useQuery({queryKey:['vendors'],queryFn:dashboard.vendors,refetchInterval:poll});
export const useAlerts=()=>useQuery({queryKey:['alerts'],queryFn:dashboard.alerts,refetchInterval:poll});
export const useAudit=()=>useQuery({queryKey:['audit'],queryFn:dashboard.audit,refetchInterval:poll});
export const useVendor=(id:string)=>useQuery({queryKey:['vendor',id],queryFn:()=>dashboard.vendor(id),enabled:!!id,refetchInterval:poll});
export function useInvalidate(){const qc=useQueryClient();return()=>qc.invalidateQueries();}
