import { describe, expect, it } from 'vitest';
import { integrity } from './identity';
const bundle={source_commit:'a'.repeat(40)} as never;
describe('deployment identity',()=>{it('distinguishes matching, mismatched, and local identity',()=>{expect(integrity(bundle,'a'.repeat(40))).toBe('verified');expect(integrity(bundle,'b'.repeat(40))).toBe('warning');expect(integrity(bundle)).toBe('local')})});
