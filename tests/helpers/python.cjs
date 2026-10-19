// 解析测试用 Python：WAYBESIDE_PYTHON 优先，否则按平台选 venv 解释器。
const path=require('path'),fs=require('fs');
module.exports=function pythonPath(root){
  if(process.env.WAYBESIDE_PYTHON)return process.env.WAYBESIDE_PYTHON;
  const venv=process.platform==='win32'?path.join(root,'.venv','Scripts','python.exe'):path.join(root,'.venv','bin','python');
  return fs.existsSync(venv)?venv:(process.platform==='win32'?'python':'python3');
};
