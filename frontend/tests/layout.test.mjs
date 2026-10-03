import assert from 'node:assert/strict';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from 'playwright';

test('video layouts stay stable on pause, stick while scrolling and fit narrow screens',async()=>{
 const server=await createServer({configFile:false,root:fileURLToPath(new URL('..',import.meta.url)),server:{host:'127.0.0.1',port:0},plugins:[{name:'layout-test',configureServer(server){server.middlewares.use('/layout-test',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/layout-test','<div id="root"></div><script type="module" src="/tests/fixtures/motion.tsx"></script>'));}catch(e){next(e);}});}}]});
 let browser;
 try{
  await server.listen();browser=await chromium.launch({channel:'chrome',headless:true});const page=await browser.newPage({viewport:{width:1440,height:800}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/api/qc/media/video/video')return route.fulfill({contentType:'video/mp4',path:fileURLToPath(new URL('./fixtures/review.mp4',import.meta.url))});
   if(url.pathname==='/api/media/video/frames'){
    const offset=url.searchParams.has('timestamp')?Math.ceil(Number(url.searchParams.get('timestamp'))*10):Number(url.searchParams.get('offset')||0),limit=Number(url.searchParams.get('limit')||1);
    return route.fulfill({json:{items:Array.from({length:Math.min(limit,30-offset)},(_,i)=>({id:'frame'+(offset+i),ordinal:offset+i,relative_seconds:(offset+i)/10,pts:offset+i,image_url:'/api/frames/frame'+(offset+i)+'/image'}))}});
   }
   if(url.pathname.endsWith('/image'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#285a82"/></svg>'});
   return route.abort();
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/layout-test`);
  await page.getByRole('button',{name:'Test review',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.frame-visual')?.getAttribute('aria-busy')==='false');
  const bounds=()=>page.locator('.frame-visual').boundingBox();const before=await bounds();
  const canvas=await page.locator('.canvas-area').boundingBox();assert.equal(canvas.height,before.height);
  await page.getByRole('button',{name:'Phát video',exact:true}).click();
  await page.waitForFunction(()=>{const v=document.querySelector('.frame-playback video');return v&&!v.paused&&v.currentTime>0;});
  const playing=await bounds();assert.equal(playing.height,before.height);assert.equal(playing.width,before.width);
  await page.getByRole('button',{name:'Tạm dừng',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.compact-canvas')&&document.querySelector('.frame-visual')?.getAttribute('aria-busy')==='false');
  assert.equal((await bounds()).height,playing.height);
  assert.equal((await page.locator('.canvas-area').boundingBox()).height,playing.height);
  // Long evidence panels must not pull the player out of view. Normal wheel scrolls, Ctrl-wheel zooms.
  await page.locator('.frame-inspector').evaluate(el=>el.style.minHeight='1600px');
  await page.locator('.canvas-area').hover();await page.mouse.wheel(0,350);
  await page.waitForFunction(()=>window.scrollY>150);
  const sticky=await page.locator('.frame-player-sticky').boundingBox();assert.ok(Math.abs(sticky.y-10)<2,`sticky top ${sticky.y}`);
  await page.screenshot({path:fileURLToPath(new URL('../../.runtime/layout-review.png',import.meta.url)),fullPage:false});
  await page.evaluate(()=>window.scrollTo(0,0));await page.getByRole('button',{name:'Test analysis',exact:true}).click();
  await page.getByText('Advanced · modules & ngưỡng',{exact:true}).click();
  await page.locator('.analysis-panel').evaluate(el=>el.style.minHeight='1600px');
  await page.evaluate(()=>window.scrollTo(0,300));
  assert.ok(Math.abs((await page.locator('.viewer').boundingBox()).y-10)<2);
  await page.screenshot({path:fileURLToPath(new URL('../../.runtime/layout-analysis.png',import.meta.url)),fullPage:false});
  await page.setViewportSize({width:390,height:844});await page.evaluate(()=>window.scrollTo(0,0));
  await page.evaluate(()=>window.scrollTo(0,300));
  assert.ok(Math.abs((await page.locator('.viewer').boundingBox()).y-6)<2,'analysis video stays visible above mobile settings');
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.getByRole('button',{name:'Test review',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.frame-visual')?.getAttribute('aria-busy')==='false');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:fileURLToPath(new URL('../../.runtime/layout-mobile.png',import.meta.url)),fullPage:false});
  assert.deepEqual(errors,[]);
 }finally{await browser?.close();await server.close();}
});
