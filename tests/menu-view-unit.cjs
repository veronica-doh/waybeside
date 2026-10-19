'use strict';
// 纯节点单元测试：不启动浏览器，不使用浏览器自动化或真实资料。
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
class Node {
  constructor(tag){this.tag=tag;this.children=[];this.attrs={};this.textContent='';}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(key,value){this.attrs[key]=value;}
  focus(){this.focused=true;}
  scrollIntoView(){}
}
const flatten=node=>[node,...node.children.flatMap(flatten)];
const main=new Node('main');
const identity=new Node('span');
const live=new Node('div');
const context=vm.createContext({console,main,demoMode:false,activityData:null,t:key=>key,tm:key=>key,loadActivity:async()=>{},decorateLocal:()=>{},TODAY:'2026-10-17',TOMORROW:'2026-10-18',
  document:{createElement:tag=>new Node(tag),querySelectorAll:()=>[],querySelector:()=>identity,getElementById:id=>id==='agent-status'?live:flatten(main).find(n=>n.id===id)||null},toast:message=>{throw Error(message);},
  confirmAction:(title,message,callback)=>{context.confirmed=message;return callback();}});
const source=fs.readFileSync(path.join(__dirname,'../waybeside/static/menu.js'),'utf8');
vm.runInContext(source.split("window.addEventListener('focus'")[0],context);
vm.runInContext(`globalThis.realMenuAction=menuAction;menuAction=async(action,extra={})=>{globalThis.lastAction={action,...extra};};`,context);
const nutrition={energy_kcal:10,protein_g:1,fat_g:1,carbohydrate_g:1,fibre_g:null};
function group(id){return {group_id:id,library_hash:'test',library_version:'test',nutrition,
  meals:['早餐','上午加餐','午餐','下午加餐','晚餐'].map(meal=>({meal,title:id+'组'+meal,nutrition,
    ingredients:[{g:10,food:{name:'自造食材',weight_basis:'自造重量',source:'测试'}}],steps:['自造步骤']}))};}
(async()=>{
  for(const page of ['today','plan','tomorrow']){
    const target=page==='tomorrow'?'2026-10-18':'2026-10-17';
    context.fixture={enabled:true,synthetic:false,groups:Array.from({length:20},(_,i)=>group(String(i+1).padStart(2,'0'))),preferences:[],history:[],
      state:{target_date:target,library_hash:'test',adopted:group('01'),draft:group('02'),recommendation:{group_id:'01'},locks:{},actual:{},previous:[null]}};
    vm.runInContext(`menuData=fixture;menuDate=fixture.state.target_date;renderMenu('${page}');`,context);
    assert.equal(identity.textContent,'本机私人记录 · 不调用模型');
    if(page==='today'){assert(!flatten(main).some(n=>n.className==='menu-group-view'||n.className==='menu-meal'),'首页不展开五餐');continue;}
    const nodes=flatten(main),draft=nodes.find(n=>n.attrs['data-menu-role']==='draft'),adopted=nodes.find(n=>n.attrs['data-menu-role']==='adopted');
    assert.equal(draft.attrs['data-group-id'],'02');assert.equal(adopted.attrs['data-group-id'],'01');
    assert.equal(flatten(draft).filter(n=>n.tag==='h3').length,5);
    assert.equal(flatten(draft).filter(n=>['button','select','input'].includes(n.tag)).length,0);
    assert.equal(flatten(adopted).filter(n=>n.tag==='button'&&n.textContent==='锁定此餐').length,5);
    assert.equal(flatten(adopted).filter(n=>n.tag==='select').length,page==='tomorrow'?0:5);
    assert.equal(nodes.filter(n=>n.className==='menu-group').length,6);
    assert.equal(nodes.some(n=>n.id==='menu-import'),page==='plan');
    assert.equal(nodes.some(n=>n.id==='menu-history'),page==='plan');
    assert.equal(nodes.some(n=>n.id==='menu-backup'),page==='plan');
    assert.equal(nodes.filter(n=>n.tag==='h1').length,1);
    assert(nodes.filter(n=>n.tag==='details').every(n=>n.id));
    await nodes.find(n=>n.tag==='button'&&n.textContent==='采用这一整组').onclick();
    assert.equal(context.lastAction.action,'adopt');assert.equal(context.lastAction.group_id,'02');assert.match(context.confirmed,/第 02 组/);
    await nodes.find(n=>n.tag==='button'&&n.textContent==='取消预览，返回已采用菜单').onclick();
    assert.equal(context.lastAction.action,'cancel_draft');
  }
  await context.nextMenuGroup();assert.equal(context.lastAction.action,'select');assert.equal(context.lastAction.group_id,'03');
  vm.runInContext(`menuData.synthetic=true;renderMenu('plan');`,context);
  assert.equal(identity.textContent,'本地记录 · 虚构数据 · 不自动调用模型');
  context.render=()=>vm.runInContext("renderMenu('plan');",context);
  for(const expected of [12,18,19]){
    await flatten(main).find(n=>n.id==='menu-show-more').onclick();
    assert.equal(flatten(main).filter(n=>n.className==='menu-group').length,expected);
  }
  assert(!flatten(main).some(n=>n.id==='menu-show-more'));
  vm.runInContext("menuData.preferences=[{group_id:'06',favorite:true}];",context);
  const filter=flatten(main).find(n=>n.id==='menu-favorite-filter');filter.checked=true;filter.onchange();
  assert.equal(flatten(main).filter(n=>n.className==='menu-group').length,1);
  assert(flatten(main).some(n=>n.id==='menu-select-06'));
  vm.runInContext("menuData.state.draft=null;menuData.history=[{target_date:'2026-10-17',adopted:menuData.state.adopted}];renderMenu('plan');",context);
  assert(!flatten(main).some(n=>n.id==='menu-adopt'));
  const collect=flatten(main).find(n=>n.id==='menu-shopping-collect');assert(collect.disabled);
  const check=flatten(main).find(n=>n.id==='menu-shopping-2026-10-17');check.checked=true;check.onchange();
  assert(!collect.disabled);assert.equal(flatten(main).find(n=>n.id==='menu-shopping-count').textContent,'已选择 1 天');
  context.api=async(path,data)=>{if(data?.action==='select')context.fixture.state.draft=group(data.group_id);if(data?.action==='cancel_draft')context.fixture.state.draft=null;return {json:async()=>context.fixture};};
  await context.realMenuAction('select',{group_id:'02'});
  assert(flatten(main).find(n=>n.id==='menu-preview-title').focused);
  assert.match(live.textContent,/第 02 组，尚未采用/);
  await context.realMenuAction('cancel_draft');
  assert(flatten(main).find(n=>n.id==='menu-change').focused);
  assert.match(live.textContent,/已取消预览/);
  console.log('三个页面的草稿、采用对象与事实控件隔离：通过');
  console.log('菜单分页、收藏筛选、管理入口、采购选择与预览焦点：通过');
})().catch(error=>{console.error(error);process.exitCode=1;});
