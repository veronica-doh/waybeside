// 首次启动门（免责确认 → 选使用方式 → 问卷向导）之后才有主界面。既有的浏览器测试关心的是主界面，
// 这里用与界面相同的接口一次性放行（免责 + 选「我自己记」+ 四块全部跳过）；向导本身由 browser-onboarding.cjs 走真实点击验证。
module.exports=async function passGate(p){
  await p.waitForFunction(()=>document.querySelector('#main')?.dataset.view||(document.querySelector('#gate')&&!document.querySelector('#gate').hidden));
  if(await p.evaluate(()=>!document.querySelector('#gate').hidden)){
    await p.evaluate(async()=>{
      if(!gateInfo.disclaimer)await api('disclaimer',{accepted:true});
      await refreshState();
      if(assistantMode==='unset'){await api('assistant-mode',{mode:'menu_only',version:assistantModeVersion});await refreshState();}
      for(const b of BLOCK_ORDER)if(!settingsData.completed[b]){await api('onboarding/'+b,{answers:{},version:settingsData.versions[b],skipped:true});await refreshState();}
      render();
    });
    await p.waitForFunction(()=>document.querySelector('#main')?.dataset.view);
  }
};
