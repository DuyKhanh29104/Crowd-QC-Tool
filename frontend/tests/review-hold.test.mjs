import assert from 'node:assert/strict';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from 'playwright';

test('fixed 2x hold follows variable timestamps in both directions and waits for slow frames',async()=>{
 const server=await createServer({configFile:false,root:fileURLToPath(new URL('..',import.meta.url)),server:{host:'127.0.0.1',port:0},plugins:[{name:'hold-test',configureServer(server){server.middlewares.use('/hold-test',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/hold-test','<div id="root"></div><script type="module" src="/tests/fixtures/motion.tsx"></script>'));}catch(e){next(e);}});}}]});
 let browser,releaseSlow;const counts=new Map();let abortedSlow=0;
 try{
  await server.listen();browser=await chromium.launch({channel:'chrome',headless:true});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  page.on('requestfailed',r=>{if(r.url().includes('frame8/image')&&r.failure()?.errorText.includes('ABORTED'))abortedSlow++;});
  await page.route('**/api/**',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/api/media/video/frames'){
    const offset=Number(url.searchParams.get('offset')||0),limit=Number(url.searchParams.get('limit')||1);
    return route.fulfill({json:{items:Array.from({length:Math.min(limit,30-offset)},(_,i)=>({id:'frame'+(offset+i),ordinal:offset+i,relative_seconds:Math.floor((offset+i)/2)*.6+((offset+i)%2)*.2,pts:offset+i,image_url:'/api/frames/frame'+(offset+i)+'/image'}))}});
   }
   if(url.pathname.endsWith('/image')){
    const i=Number(url.pathname.match(/frame(\d+)/)[1]);counts.set(i,(counts.get(i)||0)+1);
    if(i===8)await new Promise(resolve=>releaseSlow=resolve);
    return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#426ca3"/></svg>'});
   }
   return route.abort();
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/hold-test`);
  await page.getByRole('button',{name:'Test review',exact:true}).click();
  const ordinal=()=>page.locator('.frame-visual').getAttribute('data-displayed-frame').then(Number);
  const displayed=n=>page.waitForFunction(n=>document.querySelector('.frame-visual')?.getAttribute('data-displayed-frame')===String(n)&&document.querySelector('.frame-visual')?.getAttribute('aria-busy')==='false',n);
  await displayed(0);
  await page.evaluate(()=>{
   window.changes=[];const element=document.querySelector('.frame-visual');
   new MutationObserver(()=>{const n=Number(element.getAttribute('data-displayed-frame'));if(window.changes.at(-1)?.n!==n)window.changes.push({n,t:performance.now()});}).observe(element,{attributes:true,attributeFilter:['data-displayed-frame']});
  });
  await page.keyboard.down('ArrowRight');
  for(let i=0;i<30;i++)await page.keyboard.down('ArrowRight');
  await new Promise(r=>setTimeout(r,820));await page.keyboard.up('ArrowRight');
  const stopped=await ordinal();assert.ok(stopped>=5&&stopped<=7,`paced ordinal ${stopped}`);
  await new Promise(r=>setTimeout(r,400));assert.equal(await ordinal(),stopped);
  const changes=await page.evaluate(()=>window.changes);
  const stamp=n=>Math.floor(n/2)*.6+(n%2)*.2;
  const checkTiming=changes=>{for(let i=1;i<changes.length;i++){
   const expected=Math.abs(stamp(changes[i].n)-stamp(changes[i-1].n))*500;
   const actual=changes[i].t-changes[i-1].t;
   assert.ok(actual>=expected-25&&actual<expected+180,`2x VFR interval: expected ${expected}, got ${actual}`);
  }};
  checkTiming(changes);
  assert.equal(await page.getByLabel('Nhịp giữ chuyển frame',{exact:true}).count(),0);
  await page.evaluate(()=>window.changes=[]);
  const button=page.getByRole('button',{name:'Frame trước',exact:true}),box=await button.boundingBox();
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();
  await new Promise(r=>setTimeout(r,410));await page.mouse.up();
  const reverseStopped=await ordinal();assert.ok(reverseStopped<stopped&&reverseStopped>0);
  checkTiming(await page.evaluate(()=>window.changes));
  await new Promise(r=>setTimeout(r,600));assert.equal(await ordinal(),reverseStopped,'pointer release stops repeats');
  await page.getByLabel('Số frame',{exact:true}).fill('8');await displayed(7);
  await page.evaluate(()=>document.activeElement.blur());
  await page.keyboard.down('ArrowRight');
  await new Promise(r=>setTimeout(r,650));
  assert.equal(await ordinal(),7);assert.equal(await page.getByLabel('Số frame',{exact:true}).inputValue(),'9');
  // Direction changes cancel speculative downloads. Only the current slow
  // target may remain active; cancelled transfers can be retried on return.
  assert.equal(counts.get(8),abortedSlow+1);assert.equal(counts.get(9),undefined,'no queue beyond the slow target');
  await page.keyboard.up('ArrowRight');releaseSlow();await displayed(8);
  await new Promise(r=>setTimeout(r,400));assert.equal(await ordinal(),8);
  // Losing focus cancels a hold even without a keyup event.
  await page.keyboard.down('ArrowLeft');await displayed(7);
  await page.evaluate(()=>window.dispatchEvent(new Event('blur')));
  await new Promise(r=>setTimeout(r,400));assert.equal(await ordinal(),7);await page.keyboard.up('ArrowLeft');
  await page.getByLabel('Số frame',{exact:true}).fill('1');await displayed(0);
  await page.evaluate(()=>document.activeElement.blur());
  await page.keyboard.down('Shift');await page.keyboard.down('ArrowRight');await displayed(10);
  await new Promise(r=>setTimeout(r,350));assert.equal(await ordinal(),10,'10-frame steps use their actual source-time gap');
  await page.keyboard.up('ArrowRight');await page.keyboard.up('Shift');
  assert.deepEqual(errors,[]);
 }finally{releaseSlow?.();await browser?.close();await server.close();}
});
