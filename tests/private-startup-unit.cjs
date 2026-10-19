'use strict';
// 延迟已认证状态读取时，任何导航均不得先画出预置事实。
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
(async()=>{
 let finish,calls=0,renders=0;const ready=new Promise(resolve=>finish=resolve);
 const c=vm.createContext({stateReady:ready,csrf:'test',decorateLocal:()=>{},window:{addEventListener:()=>{}},setInterval:()=>{},page:()=> 'labs',api:async()=>{calls++;return {json:async()=>({enabled:false})};},render:()=>renders++});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../waybeside/static/menu.js'),'utf8'),c);
 await new Promise(resolve=>setImmediate(resolve));assert.equal(calls,0);assert.equal(renders,0);
 finish();await new Promise(resolve=>setImmediate(resolve));assert.equal(calls,1);assert.equal(renders,1);
 const main={textContent:''};const early=vm.createContext({stateLoaded:false,main});
 const line=fs.readFileSync(path.join(__dirname,'../waybeside/static/app.js'),'utf8').split('\n').find(s=>s.startsWith('function render(){'));
 vm.runInContext(line,early);early.render();assert.equal(main.textContent,'正在读取本机记录…');
 console.log('延迟state时菜单等待，提前导航只显示加载态：通过');
})().catch(e=>{console.error(e);process.exitCode=1;});
