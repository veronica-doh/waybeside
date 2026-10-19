// 测试产物目录：WAYBESIDE_TEST_ARTIFACTS，缺省为系统临时目录下的子目录（不写仓库以外的固定位置）。
const os=require('os'),path=require('path');
module.exports=process.env.WAYBESIDE_TEST_ARTIFACTS||path.join(os.tmpdir(),'waybeside-test-artifacts');
