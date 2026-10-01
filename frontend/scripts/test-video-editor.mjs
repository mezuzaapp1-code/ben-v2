import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { writeFile, unlink, readFile } from 'node:fs/promises'
import { JSDOM } from 'jsdom'
import { parseSrt, toSrt, activeSubtitle } from '../src/lib/subtitleDraft.js'
const cues=parseSrt('1\n00:00:00,000 --> 00:00:01,500\nשלום <b>world</b>',3)
assert.equal(activeSubtitle(cues,0),'שלום <b>world</b>')
assert.equal(activeSubtitle(cues,1.5),'')
assert.deepEqual(parseSrt(toSrt(cues,3),3),cues)
assert.throws(()=>parseSrt('1\n00:00:00,000 --> 00:00:04,000\nx',3))
assert.throws(()=>parseSrt('1\n00:61:00,000 --> 00:62:00,000\nx',9999))
const dom=new JSDOM('<button id="trigger">Edit</button><div id="app"></div>',{url:'https://ben.test'})
Object.assign(globalThis,{window:dom.window,document:dom.window.document,IS_REACT_ACT_ENVIRONMENT:true})
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true})
const {default:React,act}=await import('react'); const {createRoot}=await import('react-dom/client')
const output=new URL('../.editor-test.mjs',import.meta.url)
const built=process.env.BEN_EDITOR_PREBUILT ? null : await build({entryPoints:[new URL('../src/components/VideoSubtitleEditor.jsx',import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1')],bundle:true,write:false,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'}})
if (built) await writeFile(output,built.outputFiles[0].text)
try {
 const {default:Editor}=await import(output.href);const root=createRoot(document.getElementById('app'));let closes=0
 const render=open=>act(async()=>root.render(React.createElement(Editor,{url:'blob:test',open,onClose:()=>closes++})))
 document.getElementById('trigger').focus();await render(true)
 assert.equal(document.activeElement.getAttribute('aria-label'),'Close video editor')
 const video=document.querySelector('video');Object.defineProperty(video,'duration',{value:3});Object.defineProperty(video,'videoWidth',{value:1280});Object.defineProperty(video,'videoHeight',{value:720})
 await act(async()=>video.dispatchEvent(new dom.window.Event('loadedmetadata')))
 const file=document.querySelector('input[type=file]');Object.defineProperty(file,'files',{configurable:true,value:[{size:60,text:async()=>toSrt(cues,3)}]})
 await act(async()=>file.dispatchEvent(new dom.window.Event('change',{bubbles:true})))
 assert.equal(document.querySelector('.ben-video-editor__caption').textContent,'שלום <b>world</b>')
 assert.equal(document.querySelector('.ben-video-editor__caption b'),null)
 const button=text=>[...document.querySelectorAll('button')].find(b=>b.textContent===text)
 await act(async()=>button('Hide controls').click());assert.equal(document.querySelector('aside'),null)
 await act(async()=>button('Show controls').click());assert(document.querySelector('aside'))
 const position=[...document.querySelectorAll('select')].at(-1)
 await act(async()=>{position.value='top';position.dispatchEvent(new dom.window.Event('change',{bubbles:true}))})
 assert(document.querySelector('.ben-video-editor__caption--top'))
 await act(async()=>button('Undo').click());assert(document.querySelector('.ben-video-editor__caption--bottom'))
 await act(async()=>button('Redo').click());assert(document.querySelector('.ben-video-editor__caption--top'))
 await act(async()=>document.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})));assert.equal(closes,1)
 await render(false);assert.equal(document.activeElement.id,'trigger');assert.equal(document.body.style.overflow,'')
 await render(true);assert(document.querySelector('.ben-video-editor__caption--top'))
 await act(async()=>root.unmount())
 const app=await readFile(new URL('../src/App.jsx',import.meta.url),'utf8');const menu=app.slice(app.indexOf('const attachMenuItems'),app.indexOf('const openFilesLibrary'))
 assert(!menu.includes('invoice'));assert(!menu.includes('credit'))
 console.log('PASS: timed RTL/literal text, corrupt/overlong SRT rejection, drawer close/reopen, collapse, undo/redo, focus restoration and clean + menu')
}finally{await unlink(output)}

