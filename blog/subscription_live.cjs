const {chromium}=require(process.env.SMN_PLAYWRIGHT || 'playwright');
const fs=require('fs'),path=require('path'),crypto=require('crypto');
const R=path.resolve(process.argv[2]),read=p=>JSON.parse(fs.readFileSync(p,'utf8'));
const root=path.join(R,'publication-package'),manifest=read(path.join(root,'manifest.json')),entries=read(path.join(root,'entries.json'));
const base='https://smn-dev.trxstat.com',date=manifest.edition_date;let browser;
const assert=(ok,msg)=>{if(!ok)throw Error(msg)};
(async()=>{
 browser=await chromium.launch({...(process.env.SMN_BROWSER_CHANNEL==='bundled'?{}:{channel:process.env.SMN_BROWSER_CHANNEL||'chrome'}),headless:true});const page=await browser.newPage({viewport:{width:1440,height:1050}});
 const home=await page.goto(base+'/',{waitUntil:'domcontentloaded',timeout:60000});assert(home.status()===200,'Dev home HTTP');
 assert(new URL(page.url()).pathname==='/','Cumulative home must not redirect to one edition');
 assert(await page.locator('.wire-lead').count()===1&&await page.locator('.wire-headline-item').count()===5,'Production wire homepage');
 const catalog=await page.evaluate(async ()=>(await fetch('/posts.json',{cache:'no-store'})).json());
 const homeManifest=await page.evaluate(async ()=>(await fetch('/home-manifest.json',{cache:'no-store'})).json());
 const catalogUrls=catalog.map(e=>e.url);
 assert(new Set(catalogUrls).size===catalogUrls.length&&homeManifest.article_count===catalog.length,'Unique cumulative catalog');
 assert(entries.every(e=>catalogUrls.includes(e.url)),'All new articles retained');
 assert(homeManifest.previous_article_urls.every(url=>catalogUrls.includes(url)),'All prior articles retained');
 assert(homeManifest.source_commit===manifest.source_commit,'Homepage source provenance');
 const homeLinks=await page.locator('.wire-container a').evaluateAll(a=>a.map(x=>x.href));
 assert(entries.every(e=>homeLinks.includes(e.url)),'Latest edition in cumulative homepage');
 assert((await page.locator('.logo').innerText()).replace(/\s+/g,'')==='SeasonalMarketNews','SMN site brand');
 assert(await page.locator('header nav a').innerText()==='TradeWave','SMN site navigation');
 assert((await page.locator('footer').innerText()).includes('Tara Data Research LLC'),'SMN site footer');
 const landingText=await page.locator('body').innerText();
 assert(!/\bDev\b|Development edition|Updated SMN Edition|recreated with the updated editorial workflow/i.test(landingText),'Production presentation copy');
 await page.evaluate(async()=>{for(const i of document.images)i.loading='eager';await Promise.all([...document.images].map(i=>i.decode()));});
 await page.screenshot({path:path.join(R,'live-edition-desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(R,'live-edition-mobile.png'),fullPage:true});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile homepage fits viewport');
 await page.goto(base+'/search.html',{waitUntil:'domcontentloaded'});
 await page.waitForFunction(n=>document.querySelectorAll('[data-archive-result]').length===n,catalog.length);
 const archived=catalog.find(e=>!entries.some(n=>n.url===e.url));
 if(archived){
  await page.locator('#searchQuery').fill(archived.symbol);
  await page.waitForFunction(url=>[...document.querySelectorAll('[data-archive-result]')].some(a=>a.href===url),archived.url);
  await page.locator('[data-archive-result]').filter({hasText:archived.title}).first().click();
  assert(page.url()===archived.url,'Older article opens from search');
  assert(await page.locator('h1').innerText()===archived.title,'Older article content retained');
 }
 const pages=[];
 for(const e of entries){
  const local=path.join(R,'results',e.symbol),native=read(path.join(local,'seasonal-manifest.json'));
  for(const [kind,width,height] of [['desktop',1440,1050],['mobile',390,844]]){
   await page.setViewportSize({width,height});const res=await page.goto(e.url,{waitUntil:'domcontentloaded',timeout:60000});assert(res.status()===200,e.symbol+' HTTP');
   await page.evaluate(()=>{for(const i of document.images)i.loading='eager'});
   await page.waitForFunction(()=>[...document.images].every(i=>i.complete&&i.naturalWidth>0));
   let state=await page.evaluate(()=>({title:document.querySelector('h1').textContent,width:innerWidth,pageWidth:document.documentElement.scrollWidth,images:[...document.images].map(x=>x.currentSrc),links:[...document.querySelectorAll('.study-link')].map(a=>a.href),rows:[...document.querySelectorAll('[data-native-chart="bars"] tbody tr')].map(r=>[...r.cells].map(c=>c.textContent)),priceRole:document.querySelector('[data-native-chart="price_projection"]').closest('section').dataset.role,introduced:document.querySelector('[data-native-chart="price_projection"]').previousElementSibling.matches('p'),businessRole:document.querySelector('.data-figure').closest('section').dataset.role,noindex:document.querySelector('meta[name="robots"]').content,brand:document.querySelector('.masthead .brand').textContent,footer:document.querySelector('footer').textContent,body:document.body.innerText}));
   assert(state.title===e.title&&state.pageWidth<=width,e.symbol+' title/width');
   assert(state.links.length===2&&state.links.every(l=>l===native.study_url),e.symbol+' exact study links');
   assert(state.rows.length===native.card.story_cell.n&&state.rows.map(r=>Number(r[0])).join(',')===native.card.engine_results.cohort.years.join(','),e.symbol+' exact historical cohort');
   assert(state.priceRole==='outlook'&&state.introduced&&state.businessRole==='current_context',e.symbol+' chart placement');
   assert(state.noindex==='noindex,nofollow'&&!state.body.includes('Editorial review has not passed')&&state.brand==='SeasonalMarketNews'&&state.footer.includes('Tara Data Research LLC'),e.symbol+' final site presentation');
   if(kind==='mobile')assert(state.images.filter(u=>u.includes('tradewave-')).every(u=>u.includes('-mobile.png')),e.symbol+' responsive native images');
   await page.locator('.history-comparison>summary').click();assert(await page.locator('.history-comparison table').isVisible(),e.symbol+' expandable comparisons');
   await page.locator('[data-native-chart="bars"] details>summary').click();assert(await page.locator('[data-native-chart="bars"] table').isVisible(),e.symbol+' year data');
   await page.locator('.data-figure details>summary').click();
   await page.screenshot({path:path.join(local,'live-'+kind+'-top.png')});
   pages.push({symbol:e.symbol,kind,title:state.title,rows:state.rows.length,study_url:native.study_url,passed:true});
  }
 }
 const homeManifestBytes=await page.evaluate(async ()=>Array.from(new Uint8Array(await (await fetch('/home-manifest.json',{cache:'no-store'})).arrayBuffer())));
 const homeManifestHash=crypto.createHash('sha256').update(Buffer.from(homeManifestBytes)).digest('hex');
 const files=Object.entries({...Object.fromEntries(Object.entries(manifest.files).filter(([rel])=>rel.startsWith('editions/'))),...homeManifest.files,...homeManifest.articles,'home-manifest.json':homeManifestHash});
 const checked=[];
 for(let i=0;i<files.length;i+=6){
  const results=await page.evaluate(async ({batch,base})=>Promise.all(batch.map(async ([rel,expected])=>{
   const response=await fetch(base+'/'+rel,{cache:'no-store'});const bytes=await response.arrayBuffer();
   const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(b=>b.toString(16).padStart(2,'0')).join('');
   return {rel,status:response.status,passed:response.status===200&&hash===expected,sha256:hash};
  })),{batch:files.slice(i,i+6),base});
  for(const r of results)assert(r.passed,'Public asset hash: '+r.rel);checked.push(...results);
 }
 const provenance=await page.evaluate(async url=>(await fetch(url,{cache:'no-store'})).json(),base+'/editions/'+date+'/provenance.json');assert(provenance.source_commit===manifest.source_commit,'Live source provenance');
 const old=await page.goto(base+'/editions/2026-09-08/',{waitUntil:'domcontentloaded'});assert(old.status()===200&&await page.locator('.edition-card').count()===6,'Preserved prior edition');
 const proof={passed:true,verified_at:new Date().toISOString(),source_commit:manifest.source_commit,origin:base,edition_date:date,home_redirect:false,archive_article_count:catalog.length,archive_search_verified:true,home_manifest_sha256:homeManifestHash,pages,public_files:checked,preserved_prior_edition:true};
 fs.writeFileSync(path.join(R,'live-browser-verification.json'),JSON.stringify(proof,null,2));console.log(JSON.stringify({passed:true,article_layouts:pages.length,public_files:checked.length,source_commit:manifest.source_commit}));
 await browser.close();
})().catch(async e=>{console.error(e.message);if(browser)await browser.close();process.exitCode=1});
