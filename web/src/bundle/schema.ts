export type RecordValue = Record<string, unknown>;
export type PublicFile = { path: string; sha256: string; size: number; content: unknown };
export type Bundle = { schema_version: string; bundle_kind: string; disclaimer: string; workspace_id: string; display_name: string; source_commit?: string | null; stages: {key:string;title:string;status:string;summary:string;details:string[]}[]; files: PublicFile[]; presentation?: Record<string, unknown> };
export const unavailable = 'Unavailable in exported bundle';
export const isRecord = (value: unknown): value is RecordValue => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
export const asRows = (value: unknown): RecordValue[] => Array.isArray(value) ? value.filter(isRecord) : [];
