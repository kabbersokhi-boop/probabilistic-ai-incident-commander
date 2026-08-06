import {cp, mkdir, rm} from 'node:fs/promises';
await rm('public/data',{recursive:true,force:true}); await mkdir('public/data',{recursive:true}); await cp('../.artifacts/web-bundle','public/data',{recursive:true});
