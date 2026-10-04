import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { writeFile,unlink } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { JSDOM } from 'jsdom'
const dom=new JSDOM('<div id="app"></div>',{url:'https://ben.test'})
Object.assign(globalThis,{window:dom.window,document:dom.window.document,IS_REACT_ACT_ENVIRONMENT:true})
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true})
const {default:React,act}=await import('react'),{createRoot}=await import('react-dom/client')
const output=new URL('../.personal-entry-test.mjs',import.meta.url)
const mock=`let auth={};export function setAuth(value){auth=value}
export function useAuth(){return auth}
export function useOrganization(){if(!auth.orgId)throw Error('Organizations feature required');return {membership:{role:auth.orgRole},isLoaded:true}}`
if (!process.env.BEN_ENTRY_PREBUILT) {
 const result=await build({stdin:{contents:`import {ProjectCreatePrivilegeProvider,useProjectCreatePrivilege} from './src/hooks/useProjectCreatePrivilege.jsx';export {setAuth} from '@clerk/clerk-react';function State(){return <output>{JSON.stringify(useProjectCreatePrivilege())}</output>}export default function Probe(){return <ProjectCreatePrivilegeProvider><State/></ProjectCreatePrivilegeProvider>}`,resolveDir:fileURLToPath(new URL('..',import.meta.url)),loader:'jsx'},bundle:true,write:false,format:'esm',platform:'node',jsx:'automatic',external:['react','react/jsx-runtime'],define:{'import.meta.env':JSON.stringify({VITE_CLERK_PUBLISHABLE_KEY:'pk_test_fixture'})},plugins:[{name:'clerk-mock',setup(b){b.onResolve({filter:/^@clerk\/clerk-react$/},()=>({path:'clerk-mock',namespace:'test'}));b.onLoad({filter:/.*/,namespace:'test'},()=>({contents:mock,loader:'js'}))}}]})
 await writeFile(output,result.outputFiles[0].text)
}
try {
 const {default:Probe,setAuth}=await import(output.href),root=createRoot(document.getElementById('app'))
 for(const [auth,expected] of [
  [{isLoaded:false,isSignedIn:false},false],
  [{isLoaded:true,isSignedIn:false},false],
  [{isLoaded:true,isSignedIn:true,orgId:null},true],
  [{isLoaded:true,isSignedIn:true,orgId:'org',orgRole:'org:member'},false],
  [{isLoaded:true,isSignedIn:true,orgId:'org',orgRole:'org:admin'},true],
  [{isLoaded:true,isSignedIn:true,orgId:null},true],
 ]){
  setAuth(auth);await act(async()=>root.render(React.createElement(Probe)))
  assert.equal(JSON.parse(document.querySelector('output').textContent).canCreate,expected)
 }
 await act(async()=>root.unmount())
 console.log('PASS: personal sign-in never calls organization-only hooks; loading/signed-out stay denied; member/admin permissions and org-to-personal switching retained')
}finally{await unlink(output)}
