import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from 'playwright';

test('head calibration, enabled analysis and corrected-frame motion are usable',async()=>{
 const server=await createServer({configFile:false,root:fileURLToPath(new URL('..',import.meta.url)),server:{host:'127.0.0.1',port:0},
  plugins:[{name:'motion-test',configureServer(server){server.middlewares.use('/motion-test',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/motion-test','<div id="root"></div><script type="module" src="/tests/fixtures/motion.tsx"></script>'));}catch(e){next(e);}});}}]});
 let browser;let saved,previewInput,analysisInput,measurement;
 try {
  await server.listen();browser=await chromium.launch({channel:'chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1500,height:1100}});const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',async route=>{
   const url=new URL(route.request().url()),body=route.request().postDataJSON();
   if(url.pathname==='/api/qc/media/video/video')return route.abort();
   if(url.pathname==='/api/media/video/frames'){
    const ordinal=Number(url.searchParams.get('offset')||0);
    return route.fulfill({json:{items:[{id:'frame'+ordinal,ordinal,relative_seconds:ordinal/10,pts:ordinal*100,image_url:'/api/frames/frame'+ordinal+'/image'}]}});
   }
   if(url.pathname.includes('/image')||url.pathname.includes('/bird-eye/'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#61746f"/></svg>'});
   if(url.pathname==='/api/qc/calibration-preview'){previewInput=body;return route.fulfill({json:{...body,distance_m:1,error_percent:0,distance_verified:true,
    construction:body.shape==='right_angle'?{right_angle_at:'A',assumed_angle_degrees:90,derived_diagonals_m:{ac:Math.sqrt(45),bd:Math.sqrt(41)}}:null,
    measurement_check:body.shape==='quadrilateral'?{derived_m:Math.sqrt(32),measured_m:body.lengths_m.bd,error_m:0}:null}});}
   if(url.pathname==='/api/qc/profiles'){saved={id:'saved',payload:body};return route.fulfill({status:201,json:saved});}
   if(url.pathname==='/api/test/profile')return route.fulfill({json:saved});
   if(url.pathname.endsWith('/analyze')){analysisInput=body;return route.fulfill({status:202,json:{job_id:'test'}});}
   if(url.pathname.endsWith('/motion-preview')){
    measurement=body;
    return route.fulfill({json:{status:'MANUAL_PAIR_ESTIMATE_NOT_GT',motion:{plane:'head',enabled:true,metric_available:true,reference_height_m:1.65},rows:[{}, {speed_valid_count:1,objects:[{id:'box',track_id:'one',speed:.8,speed_px_s:40}]}]}});
   }
   throw new Error('Unexpected request '+url.pathname);
  });
  const address=server.httpServer.address();await page.goto(`http://127.0.0.1:${address.port}/motion-test`);
  await page.getByRole('button',{name:'Hiệu chuẩn tâm đầu',exact:true}).click();
  assert.equal(await page.getByLabel('Dạng vùng hiệu chuẩn',{exact:true}).inputValue(),'right_angle');
  await page.getByLabel('Dạng vùng hiệu chuẩn',{exact:true}).selectOption('rectangle');
  await page.getByLabel('Độ cao tâm đầu tham chiếu (m)',{exact:true}).fill('1.65');
  await page.getByText('Nhập tọa độ điểm bằng bàn phím',{exact:true}).click();
  for(const [label,x,y] of [['A',0,0],['B',640,0],['C',640,360],['D',0,360],['Kiểm 1',50,50],['Kiểm 2',150,50]]){
   await page.getByLabel(label+' X (px)',{exact:true}).fill(String(x));await page.getByLabel(label+' Y (px)',{exact:true}).fill(String(y));
  }
  await page.getByLabel('AB (m)',{exact:true}).fill('6.4');await page.getByLabel('BC (m)',{exact:true}).fill('3.6');
  await page.getByRole('button',{name:'Kiểm tra hiệu chuẩn',exact:true}).click();
  await page.getByText(/Đạt kiểm tra khoảng cách/).waitFor();
  assert.equal(previewInput.plane,'head');assert.equal(previewInput.reference_height_m,1.65);
  assert.equal(previewInput.shape,'rectangle');
  await page.getByLabel('Dạng vùng hiệu chuẩn',{exact:true}).selectOption('right_angle');
  for(const [label,value] of [['AB',5],['BC',Math.sqrt(10)],['CD',Math.sqrt(37)],['DA',4]]){
   await page.getByLabel(label+' (m)',{exact:true}).fill(String(value));
  }
  for(const [label,x,y] of [['A',50,50],['B',300,50],['C',350,200],['D',50,250],['Kiểm 1',150,100],['Kiểm 2',200,100]]){
   await page.getByLabel(label+' X (px)',{exact:true}).fill(String(x));await page.getByLabel(label+' Y (px)',{exact:true}).fill(String(y));
  }
  await page.getByRole('button',{name:'Kiểm tra hiệu chuẩn',exact:true}).click();
  await page.getByText(/Đã dựng tứ giác với góc A/).waitFor();
  assert.equal(previewInput.shape,'right_angle');assert.deepEqual(Object.keys(previewInput.lengths_m).sort(),['ab','bc','cd','da']);
  assert.equal(await page.getByLabel('AC (m)',{exact:true}).count(),0);
  assert.equal(await page.getByLabel('BD (m)',{exact:true}).count(),0);
  assert.equal(await page.getByLabel('Kích thước đầu vào YOLO',{exact:true}).count(),0);
  await page.getByRole('button',{name:'Lưu Camera Profile',exact:true}).click();await page.getByText('Đã lưu Camera Profile.',{exact:false}).waitFor();
  assert.equal(saved.payload.settings.imgsz,640);assert.equal(saved.payload.calibration.plane,'ground');assert.equal(saved.payload.head_calibration.plane,'head');
  assert.equal(saved.payload.head_calibration.shape,'right_angle');
  await page.screenshot({path:fileURLToPath(new URL('../../.runtime/right-angle-calibration.png',import.meta.url)),fullPage:true});
  await page.getByRole('button',{name:'Test analysis',exact:true}).click();
  await page.getByLabel('Loại detection',{exact:true}).selectOption('head');await page.getByText('Advanced · modules & ngưỡng',{exact:true}).click();
  assert.ok(await page.getByRole('checkbox',{name:'Speed & stationary estimation'}).isEnabled());
  assert.equal(await page.getByLabel('Kích thước đầu vào YOLO',{exact:true}).inputValue(),'640');
  await page.getByLabel('Kích thước đầu vào YOLO',{exact:true}).selectOption('1280');
  const launched=page.waitForResponse(r=>r.url().endsWith('/analyze'));
  await page.getByRole('button',{name:'Run analysis',exact:true}).click();await launched;
  assert.equal(analysisInput.settings.imgsz,1280);assert.equal(analysisInput.settings.detection_target,'head');assert.equal(analysisInput.settings.speed,true);
  await page.getByRole('button',{name:'Test review',exact:true}).click();
  await page.getByText('Đo vận tốc từ box đã chỉnh',{exact:true}).click();
  await page.getByRole('button',{name:'Đặt frame hiện tại làm mốc',exact:true}).click();
  await page.getByLabel('Số frame',{exact:true}).fill('6');
  await page.getByRole('button',{name:'Tính vận tốc với frame mốc',exact:true}).click();
  await page.getByRole('button',{name:'Tải phép đo JSON',exact:true}).waitFor();
  assert.deepEqual(measurement.frames.map(f=>f.frame_id),['frame0','frame5']);
  assert.ok(measurement.frames.every(f=>f.objects[0].track_id==='one'&&f.objects[0].label_type==='head'));
  const download=page.waitForEvent('download');await page.getByRole('button',{name:'Tải phép đo JSON',exact:true}).click();
  const file=await download;assert.equal(JSON.parse(await fs.readFile(await file.path(),'utf8')).status,'MANUAL_PAIR_ESTIMATE_NOT_GT');
  const screenshot=fileURLToPath(new URL('../../.runtime/motion-review.png',import.meta.url));await page.screenshot({path:screenshot,fullPage:true});
  assert.deepEqual(errors,[]);
 } finally {await browser?.close();await server.close();}
});
