import assert from 'node:assert/strict';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from 'playwright';

test('exact bbox rendering distinguishes unsampled, empty AI and saved event corrections',async()=>{
 const server=await createServer({configFile:false,root:fileURLToPath(new URL('..',import.meta.url)),server:{host:'127.0.0.1',port:0},plugins:[{name:'annotation-test',configureServer(server){server.middlewares.use('/annotation-test',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/annotation-test','<div id="root"></div><script type="module" src="/tests/fixtures/annotations.tsx"></script>'));}catch(e){next(e);}});}}]});
 let browser,page;
 try{
  await server.listen();browser=await chromium.launch({channel:'chrome',headless:true});page=await browser.newPage({viewport:{width:1440,height:800}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/api/media/video/frames'){
    const offset=Number(url.searchParams.get('offset')||0),limit=Number(url.searchParams.get('limit')||1);
    return route.fulfill({json:{items:Array.from({length:Math.min(limit,12-offset)},(_,i)=>({id:'frame'+(offset+i),ordinal:offset+i,relative_seconds:(offset+i)/10,image_url:'/api/frames/frame'+(offset+i)+'/image'}))}});
   }
   if(url.pathname.endsWith('/image'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#285a82"/></svg>'});
   return route.abort();
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/annotation-test`);
  const displayed=n=>page.waitForFunction(n=>document.querySelector('.frame-visual')?.getAttribute('data-displayed-frame')===String(n)&&document.querySelector('.frame-visual')?.getAttribute('aria-busy')==='false',n);
  const expectBox=async x=>{
   await page.waitForFunction(x=>{
    const canvas=document.querySelector('.konvajs-content canvas'),r=canvas.getBoundingClientRect(),s=Math.min(r.width/640,r.height/360),dpr=canvas.width/r.width;
    const px=((r.width-640*s)/2+(x+10)*s)*dpr,py=((r.height-360*s)/2+20*s)*dpr;
    const pixels=canvas.getContext('2d').getImageData(Math.floor(px)-2,Math.floor(py)-2,5,5).data;
    for(let i=0;i<pixels.length;i+=4)if(pixels[i]>190&&pixels[i+1]>210&&pixels[i+2]>210)return true;return false;
   },x);
  };
  await displayed(0);await expectBox(10);
  assert.ok((await page.locator('.frame-visual').boundingBox()).height<=420);
  // Source image is centered in a wider viewport; bbox uses the same transform.
  await page.getByRole('button',{name:'Frame sau',exact:true}).click();await displayed(1);
  await page.getByText(/Frame này chưa được AI phân tích/).waitFor();assert.equal(await page.getByLabel('Track ID',{exact:true}).count(),0);
  await page.getByLabel('Số frame',{exact:true}).fill('3');await displayed(2);await expectBox(50);
  assert.equal(await page.getByLabel('Track ID',{exact:true}).inputValue(),'manual2');
  await page.getByRole('button',{name:'Mẫu AI sau ›',exact:true}).click();await displayed(5);
  assert.equal(await page.getByLabel('Track ID',{exact:true}).count(),0,'explicit empty QC correction overrides AI bbox');
  await page.getByRole('button',{name:'Mẫu AI sau ›',exact:true}).click();await displayed(10);
  await page.getByText('AI đã chạy trên frame này và không phát hiện đối tượng trong ROI.',{exact:true}).waitFor();
  await page.getByRole('button',{name:'‹ Mẫu AI trước',exact:true}).click();await displayed(5);
  await page.getByRole('button',{name:'‹ Mẫu AI trước',exact:true}).click();await displayed(0);await expectBox(10);
  await page.screenshot({path:fileURLToPath(new URL('../../.runtime/centered-bbox-review.png',import.meta.url)),fullPage:false});
  assert.deepEqual(errors,[]);
 }catch(e){await page?.screenshot({path:fileURLToPath(new URL('../../.runtime/bbox-failure.png',import.meta.url)),fullPage:true});throw e;}finally{await browser?.close();await server.close();}
});
