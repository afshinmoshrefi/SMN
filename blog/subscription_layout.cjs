const {chromium}=require(process.env.SMN_PLAYWRIGHT || 'playwright');
const fs=require('fs'),path=require('path'),crypto=require('crypto');
const R=path.resolve(process.argv[2]),read=p=>JSON.parse(fs.readFileSync(p,'utf8'));
const sha=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const symbols=process.argv.slice(3);let browser;
(async()=>{
 browser=await chromium.launch({...(process.env.SMN_BROWSER_CHANNEL==='bundled'?{}:{channel:process.env.SMN_BROWSER_CHANNEL||'chrome'}),headless:true});
 const page=await browser.newPage();
 for(const sym of symbols){
  const dir=path.join(R,'results',sym),a=read(path.join(dir,'article.json'));
  const native=read(path.join(dir,'seasonal-manifest.json'));const layouts=[];
  for(const [kind,width,height] of [['desktop',1440,1050],['mobile',390,844]]){
   await page.setViewportSize({width,height});await page.goto('file:///'+path.join(dir,'article.html').replaceAll('\\','/'));
   await page.evaluate(()=>{for(const i of document.images)i.loading='eager';});
   await page.waitForFunction(()=>[...document.images].every(i=>i.complete&&i.naturalWidth>0));
   const state=await page.evaluate(()=>({width:innerWidth,pageWidth:document.documentElement.scrollWidth,
    title:document.querySelector('h1').textContent,
    roles:[...document.querySelectorAll('section[data-role]')].map(s=>s.dataset.role),
    priceRole:document.querySelector('[data-native-chart="price_projection"]').closest('section').dataset.role,
    priceHasIntroduction:!!document.querySelector('[data-native-chart="price_projection"]').previousElementSibling?.matches('p'),
    businessRole:document.querySelector('.data-figure').closest('section').dataset.role,
    imageSources:[...document.images].map(i=>i.currentSrc),
    studyLinks:[...document.querySelectorAll('.study-link')].map(a=>a.href),
    nativeCharts:[...document.querySelectorAll('[data-native-chart]')].map(f=>f.dataset.nativeChart),
    evidenceRows:document.querySelector('[data-native-chart="bars"] tbody').rows.length,
    noindex:document.querySelector('meta[name="robots"]').content}));
   if(state.pageWidth>width||state.title!==a.title||state.priceRole!=='outlook'||!state.priceHasIntroduction||state.businessRole!=='current_context'||state.studyLinks.length!==2||state.studyLinks.some(x=>x!==native.study_url)||state.evidenceRows!==native.card.story_cell.n||state.noindex!=='noindex,nofollow')throw Error(sym+' '+kind+' layout/contract failure '+JSON.stringify(state));
   if(kind==='mobile'&&!state.imageSources.filter(x=>x.includes('tradewave-')).every(x=>x.includes('-mobile.png')))throw Error('Responsive native source not selected');
   await page.screenshot({path:path.join(dir,'qa-'+kind+'-top.png')});
   await page.screenshot({path:path.join(dir,'qa-'+kind+'-full.png'),fullPage:true});
   for(const variant of ['bars','bars_mae_mfe','price_projection'])await page.locator('[data-native-chart="'+variant+'"]').screenshot({path:path.join(dir,'qa-'+kind+'-'+variant+'.png')});
   await page.locator('.data-figure').screenshot({path:path.join(dir,'qa-'+kind+'-business.png')});
   layouts.push({kind,...state});
  }
  // Pixel judgment is recorded separately only after the captured images are inspected.
  fs.writeFileSync(path.join(dir,'layout-checks.json'),JSON.stringify({passed:true,article_html_sha256:sha(path.join(dir,'article.html')),layouts,pixel_inspection_pending:true},null,2));
  console.log(JSON.stringify({symbol:sym,layout_passed:true,pixel_inspection_pending:true}));
 }
 await browser.close();
})().catch(async e=>{console.error(e.message);if(browser)await browser.close();process.exitCode=1;});
