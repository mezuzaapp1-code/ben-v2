import assert from 'node:assert/strict'
import {readFile, writeFile} from 'node:fs/promises'
import {test} from 'node:test'
import {validateEditDocument, editorDraftToDocument, documentToEditorDraft} from '../src/lib/editDocument.js'
const fixture=JSON.parse(await readFile(new URL('../../tests/fixtures/editor_document_v1.json',import.meta.url),'utf8'))
const source=fixture.document.source
const options={documentId:fixture.document.document_id,resourceId:source.resource_id,durationSeconds:source.duration_seconds}
const clone=()=>structuredClone(fixture.document)
function patched(patch){const doc=clone();const last=patch.path.at(-1);let node=doc;for(const p of patch.path.slice(0,-1))node=node[p];node[last]=patch.value;return doc}

test('roundtrip rich editor draft preserves text, precision and independent layer styles',()=>{
 const doc=editorDraftToDocument(fixture.document.body,options)
 const body=documentToEditorDraft(doc,options)
 assert.deepEqual(editorDraftToDocument(body,options),doc)
 assert.equal(body.cues[0].text,'יוסטון, טקסס\n03.10.2026')
 assert.equal(body.cues[1].start,16.05)
 assert.equal(body.style.backgroundMode,'none');assert.equal(body.style.x,51.25)
 assert.equal(body.textLayers[0].style.font,'Georgia');assert.equal(body.textLayers[0].style.shadow,'depth')
 assert.equal(body.textLayers[1].text,'')
 body.style.x=20;body.cues[0].text='changed';body.textLayers[0].style.y=10
 assert.equal(doc.body.style.x,51.25);assert.equal(doc.body.textLayers[0].style.y,35)
 assert.equal(fixture.document.body.cues[0].text,'יוסטון, טקסס\n03.10.2026')
})
test('old editor draft with no layers or alignment migrates explicitly',()=>{
 const doc=clone();delete doc.body.textLayers;delete doc.body.style.alignment;delete doc.source.timebase
 const result=validateEditDocument(doc)
 assert.deepEqual(result.body.textLayers,[]);assert.equal(result.body.style.alignment,'auto');assert.equal(result.source.timebase,'seconds')
})
for(const patch of fixture.rejectedPatches)test(patch.name,()=>assert.throws(()=>validateEditDocument(patched(patch))))
test('selected video mismatch cannot silently restore another document',()=>{
 assert.throws(()=>documentToEditorDraft(clone(),{...options,resourceId:'33333333-3333-4333-8333-333333333333'}))
 assert.throws(()=>documentToEditorDraft(clone(),{...options,durationSeconds:19}))
})
test('budgets reject excess items and astral characters count as Unicode code points',()=>{
 for(const [key,count] of [['cues',301],['textLayers',21]]){
  const doc=clone();doc.body[key]=Array.from({length:count},(_,i)=>({...structuredClone(doc.body[key][0]),id:`n-${i}`}));assert.throws(()=>validateEditDocument(doc))
 }
 const doc=clone();doc.body.cues[0].text='🚀'.repeat(1000);assert.equal(validateEditDocument(doc).body.cues[0].text,doc.body.cues[0].text)
 doc.body.cues[0].text+='🚀';assert.throws(()=>validateEditDocument(doc))
 const large=clone();large.body.cues=Array.from({length:300},(_,i)=>({id:`x-${i}`,text:'🚀'.repeat(1000),start:0,end:1}));assert.throws(()=>validateEditDocument(large),/too large/)
})
test('rejects non-JSON, nonfinite, prototypes and unknown fields rather than dropping them',()=>{
 for(const value of [NaN,Infinity,-Infinity,undefined]){const doc=clone();doc.source.duration_seconds=value;assert.throws(()=>validateEditDocument(doc))}
 const bad=clone();bad.body.style.extra=undefined;assert.throws(()=>validateEditDocument(bad))
 const cycle=clone();cycle.body.style.extra=cycle;assert.throws(()=>validateEditDocument(cycle))
 const getter=clone();Object.defineProperty(getter.body.style,'size',{enumerable:true,get(){throw new Error('must not run')}});assert.throws(()=>validateEditDocument(getter),/JSON properties/)
 const prototype=clone();prototype.body.style=Object.create({font:'Arial'});assert.throws(()=>validateEditDocument(prototype))
 const malformed=clone();malformed.body.cues[0].text='\ud800';assert.throws(()=>validateEditDocument(malformed),/Unicode/)
 const draft={...fixture.document.body,hidden:undefined};assert.throws(()=>editorDraftToDocument(draft,options))
})
// CI compares independently produced Python and JS documents, not only acceptance booleans.
if(process.env.EDIT_DOCUMENT_RESULT)await writeFile(process.env.EDIT_DOCUMENT_RESULT,JSON.stringify(validateEditDocument(clone())))
