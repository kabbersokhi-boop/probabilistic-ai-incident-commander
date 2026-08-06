import type { Bundle } from './schema';
export type Integrity = 'verified'|'warning'|'local';
export function integrity(bundle: Bundle, deployed?: string): Integrity { if (!deployed) return 'local'; return bundle.source_commit === deployed ? 'verified' : 'warning'; }
export async function loadSourceCommit(fetcher: typeof fetch = fetch): Promise<string | undefined> { try { const r=await fetcher('SOURCE_COMMIT'); if(!r.ok)return undefined; const text=(await r.text()).trim(); return /^[0-9a-f]{40}$/.test(text)?text:undefined; } catch { return undefined; } }
