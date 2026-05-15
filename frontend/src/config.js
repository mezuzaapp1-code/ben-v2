/** BEN API base URL (no trailing slash). Empty = same-origin (Vite dev proxy). */
const envBase = import.meta.env.VITE_BEN_API_BASE
export const BEN_API_BASE =
  envBase !== undefined && envBase !== ''
    ? envBase.replace(/\/$/, '')
    : import.meta.env.DEV
      ? ''
      : 'https://ben-v2-production.up.railway.app'

export const DEFAULT_TENANT_ID = '00000000-0000-0000-0000-000000000001'
