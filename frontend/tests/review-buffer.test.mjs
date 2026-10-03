import assert from 'node:assert/strict';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from 'playwright';

test('review retains decoded pixels, preloads neighbors and rejects stale navigation',async()=>{
 const server=await createServer({configFile:false,root:fileURLToPath(new URL('..',import.meta.url)),server:{host:'127.0.0.1',port:0},
  plugins:[{name:'review-buffer-test',configureServer(server){server.middlewares.use('/buffer-test',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/buffer-test','<div id="root"></div><script type="module" src="/tests/fixtures/motion.tsx"></script>'));}catch(e){next(e);}});}}]});
 let browser;let fail25=true;const counts=new Map(),releases=new Map(),timestamps=[],aborted=[];
 try{
  await server.listen();browser=await chromium.launch({channel:'chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1500,height:1100}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('requestfailed',r=>{if(r.failure()?.errorText.includes('ABORTED'))aborted.push(r.url());});
  await page.route('**/api/**',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/api/media/video/frames'){
    const stamp=url.searchParams.get('timestamp');if(stamp!==null)timestamps.push(Number(stamp));
    const offset=stamp!==null?Math.min(29,Math.ceil(Number(stamp)*10)):Number(url.searchParams.get('offset')||0),limit=Number(url.searchParams.get('limit')||1);
    return route.fulfill({json:{items:Array.from({length:Math.min(limit,30-offset)},(_,i)=>({id:'frame'+(offset+i),ordinal:offset+i,relative_seconds:(offset+i)/10,pts:(offset+i)*100,image_url:'/api/frames/frame'+(offset+i)+'/image'}))}});
   }
   if(url.pathname.endsWith('/image')){
    const ordinal=Number(url.pathname.match(/frame(\d+)/)[1]);counts.set(ordinal,(counts.get(ordinal)||0)+1);
    if((ordinal===15||ordinal===20)&&counts.get(ordinal)===1)await new Promise(resolve=>releases.set(ordinal,resolve));
    if(ordinal===25&&fail25)return route.abort();
    return route.fulfill({contentType:'image/svg+xml',body:`<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="${ordinal%2?'#a34242':'#426ca3'}"/></svg>`});
   }
   return route.abort();
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/buffer-test`);
  await page.getByRole('button',{name:'Test review',exact:true}).click();
  const visual=page.locator('.frame-visual');
  const displayed=n=>page.waitForFunction(n=>document.querySelector('.frame-visual')?.getAttribute('data-displayed-frame')===String(n)&&document.querySelector('.frame-visual')?.getAttribute('aria-busy')==='false',n);
  await displayed(0);
  // The camera fixture may have loaded frame 0 via <img> before entering review;
  // review now uses abortable fetch. Measure reuse within the review session.
  const initialFrame0Requests=counts.get(0);
  const canvas=await page.locator('.konvajs-content canvas').elementHandle();
  await page.waitForFunction(()=>document.querySelector('.frame-visual canvas')!=null);
  // Warmup requests actually happen before navigation, then reuse decoded images.
  for(let i=0;i<100&&!counts.has(1);i++)await new Promise(r=>setTimeout(r,20));
  assert.equal(counts.get(1),1);
  await page.getByRole('button',{name:'Frame sau',exact:true}).click();await displayed(1);
  assert.equal(counts.get(1),1);
  await page.getByRole('button',{name:'Frame trước',exact:true}).click();await displayed(0);
  assert.equal(counts.get(0),initialFrame0Requests);
  await page.getByLabel('Số frame',{exact:true}).fill('16');
  await page.waitForFunction(()=>document.querySelector('.frame-visual')?.getAttribute('aria-busy')==='true');
  assert.equal(await visual.getAttribute('data-displayed-frame'),'0');
  assert.ok(await canvas.evaluate(node=>node.isConnected));
  assert.equal(await page.locator('.canvas-loading,.frame-placeholder').count(),0);
  assert.ok(await page.getByRole('button',{name:'Lưu frame',exact:false}).isDisabled());
  await page.getByLabel('Số frame',{exact:true}).fill('21');
  for(let i=0;i<100&&(!releases.has(15)||!releases.has(20));i++)await new Promise(r=>setTimeout(r,20));
  assert.ok(releases.has(15)&&releases.has(20));
  releases.get(20)();await displayed(20);
  releases.get(15)();await new Promise(r=>setTimeout(r,150));
  assert.equal(await visual.getAttribute('data-displayed-frame'),'20');
  assert.ok(await canvas.evaluate(node=>node.isConnected));
  assert.ok(aborted.some(url=>url.includes('frame15/image')),'stale image transfer is aborted');
  // A failed decode keeps the last frame visible and editing disabled.
  await page.getByLabel('Số frame',{exact:true}).fill('26');
  await page.getByRole('button',{name:'Thử tải lại frame',exact:true}).waitFor();
  assert.equal(await visual.getAttribute('data-displayed-frame'),'20');
  assert.ok(await page.getByRole('button',{name:'Lưu frame',exact:false}).isDisabled());
  await page.getByRole('button',{name:'Thử tải lại frame',exact:true}).click();
  for(let i=0;i<100&&(counts.get(25)||0)<2;i++)await new Promise(r=>setTimeout(r,20));
  assert.equal(counts.get(25),2);
  await page.getByLabel('Số frame',{exact:true}).fill('1');await displayed(0);
  assert.equal(await page.locator('.frame-preload').count(),0);
  const previousSeeks=timestamps.length;
  await page.getByLabel('Vị trí video',{exact:true}).evaluate(input=>{
   const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
   for(const value of ['1.2','1.6','1.8']){setter.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));}
  });
  await displayed(18);
  assert.deepEqual(timestamps.slice(previousSeeks),[1.8],'scrubbing only resolves the latest target');
  assert.deepEqual(errors,[]);
  await page.screenshot({path:fileURLToPath(new URL('../../.runtime/review-buffer.png',import.meta.url)),fullPage:true});
 }finally{for(const resolve of releases.values())resolve();await browser?.close();await server.close();}
});
