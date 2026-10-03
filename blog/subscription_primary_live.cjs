const {chromium}=require(process.env.SMN_PLAYWRIGHT || 'playwright');
const fs=require('fs'),path=require('path'),crypto=require('crypto');
const R=path.resolve(process.argv[2]),read=p=>JSON.parse(fs.readFileSync(p,'utf8'));
const root=path.join(R,'publication-package'),manifest=read(path.join(root,'manifest.json')),entries=read(path.join(root,'entries.json'));
const base=manifest.target_origin,date=manifest.edition_date;let browser;
if(!['https://smn-dev.trxstat.com','https://seasonalmarketnews.com'].includes(base))throw Error('Unsupported public verification origin');
const production=base==='https://seasonalmarketnews.com',robots=production?'index,follow':'noindex,nofollow';
const assert=(ok,msg)=>{if(!ok)throw Error(msg)};
(async()=>{
 const activation=read(path.join(R,'primary-activation.json'));
 const membership=activation.membership_publication;
 if(membership&&!process.env.SMN_READER_VERIFICATION_STATE)throw Error('Gated publication requires a verified reader browser session');
 browser=await chromium.launch({...(process.env.SMN_BROWSER_CHANNEL==='bundled'?{}:{channel:process.env.SMN_BROWSER_CHANNEL||'chrome'}),headless:true});let page=await browser.newPage({viewport:{width:1440,height:1050}});
 const home=await page.goto(base+'/',{waitUntil:'domcontentloaded',timeout:60000});assert(home.status()===200,'Dev home HTTP');
 if(!production)assert(await page.locator('meta[name="robots"]').getAttribute('content')==='noindex,nofollow','Dev homepage noindex');
 assert(new URL(page.url()).pathname==='/','Cumulative home must not redirect to one edition');
 assert(await page.locator('.wire-lead').count()===1,'Production wire homepage');
 const catalog=await page.evaluate(async ()=>(await fetch('/posts.json',{cache:'no-store'})).json());
 const homeManifest=await page.evaluate(async ()=>(await fetch('/home-manifest.json',{cache:'no-store'})).json());
 const catalogUrls=catalog.map(e=>e.url);
 assert(new Set(catalogUrls).size===catalogUrls.length&&homeManifest.article_count===catalog.length,'Unique cumulative catalog');
 assert(entries.every(e=>catalogUrls.includes(e.url)),'All new articles retained');
 assert(homeManifest.previous_article_urls.every(url=>catalogUrls.includes(url)),'All prior articles retained');
 assert(homeManifest.source_commit===manifest.source_commit,'Homepage source provenance');
 const expectedPins=(activation.expected_pins||[]).filter(p=>!p.expires_at||Date.parse(p.expires_at)>Date.now());
 const ordered=await page.locator('.wire-lead h2 a,.wire-lead h1 a,.wire-headline-item h3 a,.wire-headline-item h2 a').evaluateAll(a=>a.map(x=>x.href));
 for(const pin of expectedPins){const entry=catalog.find(p=>p.slug===pin.slug);assert(entry,'Pinned catalog entry retained');if(pin.position===1)assert(await page.locator('.wire-lead a').evaluateAll((a,url)=>a.some(x=>x.href===url),entry.url),'Pinned lead retained');}
 const editionHomeLinks=async()=>page.locator('.wire-container a').evaluateAll((anchors,edition)=>edition.map(e=>{
  const a=anchors.find(x=>x.href===e.url&&x.getClientRects().length&&getComputedStyle(x).visibility!=='hidden');
  const section=a?.closest('.wire-headlines')?'Latest Patterns':a?.closest('.wire-lead')?'Lead':
    a?.closest('.wire-section')?.querySelector('.wire-section-title')?.textContent.trim()||null;
  return {symbol:e.symbol,url:e.url,visible:!!a,section};
 }),entries);
 const desktopHomeLinks=await editionHomeLinks();
 assert(desktopHomeLinks.every(e=>e.visible),'Latest edition visible across desktop homepage sections');
 assert((await page.locator('.logo').innerText()).replace(/\s+/g,'')==='SeasonalMarketNews','SMN site brand');
 assert(await page.locator('header nav a').innerText()==='TradeWave','SMN site navigation');
 assert((await page.locator('footer').innerText()).includes('Tara Data Research LLC'),'SMN site footer');
 const landingText=await page.locator('body').innerText();
 assert(!/\bDev\b|Development edition|Updated SMN Edition|recreated with the updated editorial workflow/i.test(landingText),'Production presentation copy');
 // Only the top of the cumulative homepage (where the new edition sits): forcing every image of
 // hundreds of past articles to decode, plus full-page shots, exhausted Dev's memory on Sept 29.
 const TOP=3200;
 await page.evaluate(async(top)=>{const near=[...document.images].filter(i=>i.getBoundingClientRect().top+scrollY<top);
   for(const i of near)i.loading='eager';
   await Promise.race([Promise.all(near.map(i=>i.decode().catch(()=>null))),new Promise(r=>setTimeout(r,20000))]);},TOP);
 const clip=async(w)=>({x:0,y:0,width:w,height:Math.min(TOP,await page.evaluate(()=>document.documentElement.scrollHeight))});
 await page.screenshot({path:path.join(R,'live-edition-desktop.png'),fullPage:true,clip:await clip(1440)});
 await page.setViewportSize({width:390,height:844});
 const mobileHomeLinks=await editionHomeLinks();
 assert(mobileHomeLinks.every(e=>e.visible),'Latest edition visible across mobile homepage sections');
 await page.screenshot({path:path.join(R,'live-edition-mobile.png'),fullPage:true,clip:await clip(390)});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile homepage fits viewport');
 await page.goto(base+'/search.html',{waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>document.querySelectorAll('#resultsList a[href]').length>0);
 const archived=catalog.find(e=>!entries.some(n=>n.url===e.url));
 if(archived){
  await page.locator('#searchQuery').fill(archived.title);
  await page.locator('#searchQuery').press('Enter');
  const oldPath=new URL(archived.url).pathname;
  await page.waitForFunction(p=>[...document.querySelectorAll('#resultsList a[href]')].some(a=>new URL(a.href).pathname===p),oldPath);
  await page.goto(base+oldPath,{waitUntil:'domcontentloaded'});
  const titleText=t=>t.replace(/[—–]/g,'-').replace(/\s+/g,' ').trim();
  assert(titleText(await page.locator('h1').innerText())===titleText(archived.title),'Older article content retained');
 }
 const anonymousPage=page;
 const memberContext=membership?await browser.newContext({storageState:process.env.SMN_READER_VERIFICATION_STATE}):null;
 const memberPage=memberContext?await memberContext.newPage():null;
 if(memberPage)page=memberPage;
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
   assert(state.noindex===robots&&!state.body.includes('Editorial review has not passed')&&state.brand==='SeasonalMarketNews'&&state.footer.includes('Tara Data Research LLC'),e.symbol+' final site presentation');
   if(production)assert(await page.locator('link[rel="canonical"]').getAttribute('href')===e.url,e.symbol+' production canonical');
   if(kind==='mobile')assert(state.images.filter(u=>u.includes('tradewave-')).every(u=>u.includes('-mobile.png')),e.symbol+' responsive native images');
   await page.locator('.history-comparison>summary').click();assert(await page.locator('.history-comparison table').isVisible(),e.symbol+' expandable comparisons');
   await page.locator('[data-native-chart="bars"] details>summary').click();assert(await page.locator('[data-native-chart="bars"] table').isVisible(),e.symbol+' year data');
   await page.locator('.data-figure details>summary').click();
   await page.screenshot({path:path.join(local,'live-'+kind+'-top.png')});
   pages.push({symbol:e.symbol,kind,title:state.title,rows:state.rows.length,study_url:native.study_url,passed:true});
  }
 }
 if(membership)page=anonymousPage;
 const membershipArticles=[];
 if(membership){
  const selected=[...membership.new,...[...membership.retained].sort((a,b)=>a.canonical.localeCompare(b.canonical)).slice(0,40)];
  const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
  for(const expected of selected){
   const res=await page.goto(expected.url,{waitUntil:'domcontentloaded',timeout:60000});
   assert(res.status()===200,'Canonical public preview HTTP');
   const shown=await page.evaluate(()=>({headline:document.querySelector('main>h1')?.textContent,preview:[...document.querySelectorAll('main>p:not(.qualification):not([role=status])')].map(p=>p.textContent),qualification:document.querySelector('main>.qualification')?.textContent,full_article_value:document.querySelector('main>aside>p')?.textContent}));
   const copy=expected.preview_content;
   assert(shown.headline===copy.headline.text&&JSON.stringify(shown.preview)===JSON.stringify(copy.preview.map(p=>p.text))&&shown.qualification===copy.qualification.text&&shown.full_article_value===copy.full_article_value.text,'Public preview exact source-bound copy');
   const anonymousHash=sha(await res.body());
   assert(anonymousHash!==expected.full_html_sha256&&await page.locator('[data-native-chart]').count()===0,'Anonymous full article absent');
   const full=await memberContext.request.get(expected.url,{timeout:60000,headers:{'Cache-Control':'no-cache'}});
   assert(full.status()===200&&sha(await full.body())===expected.full_html_sha256,'Member full private bytes');
   const assets=[];
   for(const asset of expected.assets){
    const member=await memberContext.request.get(base+asset.url,{timeout:60000});
    assert(member.status()===200&&sha(await member.body())===asset.sha256,'Member native asset exact bytes');
    const anon=await page.context().request.get(base+asset.url,{timeout:60000});
    if(asset.public)assert(anon.status()===200&&sha(await anon.body())===asset.sha256,'Approved public hero bytes');
    else assert([401,403,404].includes(anon.status()),'Anonymous native asset denied');
    assets.push({name:asset.name,sha256:asset.sha256,member_passed:true,public_passed:asset.public,anonymous_denied:!asset.public});
   }
   const rawAssets=[];
   for(const url of expected.raw_asset_urls||[]){const res=await page.context().request.get(url,{timeout:60000});assert([401,403,404].includes(res.status()),'Raw engine asset denied');rawAssets.push({url,denied:true,status:res.status()});}
   membershipArticles.push({canonical:expected.canonical,revision:expected.revision,preview_sha256:expected.preview_sha256,full_html_sha256:expected.full_html_sha256,public_preview_passed:true,member_full_passed:true,anonymous_full_absent:true,assets,raw_assets:rawAssets});
  }
 }
 const homeManifestBytes=await page.evaluate(async ()=>Array.from(new Uint8Array(await (await fetch('/home-manifest.json',{cache:'no-store'})).arrayBuffer())));
 const homeManifestHash=crypto.createHash('sha256').update(Buffer.from(homeManifestBytes)).digest('hex');
 // Every file of this edition, plus a fixed sample of the retained archive. Hashing all ~1,400
 // retained articles and heroes through the public site ran ~20 h and exhausted Dev (Sept 29-30);
 // this publish does not modify them, and earlier editions already verified them.
 const sample=(o,n)=>{const e=Object.entries(o).sort(([a],[b])=>a<b?-1:1);const step=Math.max(1,Math.floor(e.length/n));return e.filter((_,i)=>i%step===0).slice(0,n);};
 const files=[...Object.entries(activation.files),...sample(activation.retained_articles,40),...sample(activation.retained_heroes,40)];
 const checked=[];
 for(let i=0;i<files.length;i+=6){
  const results=await page.evaluate(async ({batch,base,retained})=>Promise.race([new Promise((_,no)=>setTimeout(()=>no(new Error('public asset batch timed out')),90000)),Promise.all(batch.map(async ([rel,expected])=>{
   const response=await fetch(base+'/'+rel,{cache:'no-store'});const bytes=await response.arrayBuffer();
   const sha=async b=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',b))).map(b=>b.toString(16).padStart(2,'0')).join('');
   const publicHash=await sha(bytes);let hash=publicHash,normalization=null;
   if(hash!==expected&&retained.includes(rel)&&rel.endsWith('.html')){
    // Reverse only Cloudflare's observed email-link delivery transform. The
    // complete restored HTML must still match the retained origin byte hash.
    const text=new TextDecoder().decode(bytes).replace(/href="\/cdn-cgi\/l\/email-protection#([a-f0-9]+)"/gi,(_,hex)=>{
     const key=parseInt(hex.slice(0,2),16),out=[];for(let j=2;j<hex.length;j+=2)out.push(parseInt(hex.slice(j,j+2),16)^key);
     return 'href="mailto:'+new TextDecoder().decode(new Uint8Array(out))+'"';
    }).replace(/<script data-cfasync="false" src="\/cdn-cgi\/scripts\/[a-f0-9]+\/cloudflare-static\/email-decode\.min\.js"><\/script>/gi,'');
    hash=await sha(new TextEncoder().encode(text));normalization='cloudflare-email-link';
   }
   return {rel,status:response.status,passed:response.status===200&&hash===expected,sha256:hash,public_sha256:publicHash,normalization};
  }))]),{batch:files.slice(i,i+6),base,retained:Object.keys(activation.retained_articles)});
  for(const r of results)assert(r.passed,'Public asset hash: '+r.rel);checked.push(...results);
 }
 if(!membership){const provenance=await page.evaluate(async url=>(await fetch(url,{cache:'no-store'})).json(),base+'/editions/'+date+'/provenance.json');assert(provenance.source_commit===manifest.source_commit,'Live source provenance');}
 const proof={passed:true,verified_at:new Date().toISOString(),source_commit:manifest.source_commit,origin:base,edition_date:date,membership_articles:membershipArticles,home_redirect:false,home_links:{desktop:desktopHomeLinks,mobile:mobileHomeLinks},archive_article_count:catalog.length,archive_search_verified:true,home_manifest_sha256:homeManifestHash,pages,public_files:checked,preserved_prior_articles:true};
 fs.writeFileSync(path.join(R,'live-verification.json'),JSON.stringify(proof,null,2));console.log(JSON.stringify({passed:true,article_layouts:pages.length,public_files:checked.length,source_commit:manifest.source_commit}));
 await browser.close();
})().catch(async e=>{console.error(e.message);if(browser)await browser.close();process.exitCode=1});
