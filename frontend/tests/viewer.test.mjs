import assert from 'node:assert/strict';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from 'playwright';

test('frame stepping advances within a single 0.1-second interval at 30 FPS', async () => {
  const server = await createServer({
    configFile:false, root:fileURLToPath(new URL('..',import.meta.url)),
    server:{host:'127.0.0.1',port:0},
    plugins:[{name:'viewer-test-page',configureServer(server) {
      server.middlewares.use('/viewer-test', async (_request,response,next) => {
        try {
          response.setHeader('Content-Type','text/html');
          response.end(await server.transformIndexHtml('/viewer-test','<div id="root"></div><script type="module" src="/tests/fixtures/viewer.tsx"></script>'));
        } catch (error) { next(error); }
      });
    }}],
  });
  let browser;
  try {
    await server.listen();
    browser = await chromium.launch({channel:'chrome',headless:true});
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/media/video/frames') {
        const ordinal = url.searchParams.has('offset') ? Number(url.searchParams.get('offset')) : Math.round(Number(url.searchParams.get('timestamp'))*30);
        await route.fulfill({json:{items:[{id:String(ordinal),ordinal,pts:ordinal,relative_seconds:ordinal/30,image_url:'/api/frame.png'}]}});
      } else if (url.pathname.endsWith('.png')) {
        await route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64')});
      } else await route.abort();
    });
    const address = server.httpServer.address();
    await page.goto(`http://127.0.0.1:${address.port}/viewer-test`);
    await page.waitForFunction(()=>document.querySelector('[data-testid="ordinal"]')?.textContent==='0').catch(error=>{
      throw new Error(`Viewer did not load. Browser errors: ${errors.join('; ')}`,{cause:error});
    });
    for (const ordinal of [1,2,3]) {
      await page.getByRole('button',{name:'Frame sau',exact:true}).click();
      await page.waitForFunction(expected=>document.querySelector('[data-testid="ordinal"]')?.textContent===String(expected),ordinal,{timeout:5000});
    }
    await page.getByRole('button',{name:'Frame trước',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('[data-testid="ordinal"]')?.textContent==='2');
    assert.deepEqual(errors,[]);
  } finally {
    await browser?.close();
    await server.close();
  }
});
