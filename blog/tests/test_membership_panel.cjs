// Deterministic browser contract test: actual panel/client, mocked HTTP authority.
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const folder = path.resolve(__dirname, '..');
  const template = fs.readFileSync(path.join(folder, 'templates/membership_panel.html'), 'utf8').replace('{{ base }}', '');
  const dashboard = fs.readFileSync(path.join(folder, 'templates/pub_dashboard.html'), 'utf8');
  const css = dashboard.match(/<style>([\s\S]*?)<\/style>/)[1];
  const script = fs.readFileSync(path.join(folder, 'static/membership.js'), 'utf8');
  const statement = text => ({text, source_ids:['history'], article_refs:['title']});
  let offer = {mode:'free',currency:'usd',monthly_amount:0,annual_mode:'explicit',annual_amount:0,annual_discount_bps:null,trial_days:0,intervals:[]};
  let settings = {settings_version:1,active_offer:offer,draft_offers:[],readiness:{billing_enabled:true,stripe_mode:'test'},member_counts:{total:5,free_launch:5}};
  let preview = {slug:'test',canonical:'/editions/test/article.html',revision:'r1',payload_sha256:'a'.repeat(64),review_status:'pending',full_html:'<p>Complete source evidence</p><script>window.top.document.body.dataset.unsafe="yes"</script>',preview:{content:{headline:statement('Original headline'),preview:[statement('Useful original lead')],full_article_value:statement('Read the risk evidence'),qualification:statement('History is not a forecast'),social:[],video:null},provenance:{revision:'r1'}}};
  let job = {id:'job1',kind:'article_video',subject_label:'Test article <img src=x>',status:'generated',version:2,source_revision:'r1',generation_status:'complete',review_status:'pending',payload_sha256:'c'.repeat(64),artifacts:[{name:'clip.mp4'}],holds:[],attempts:1};
  let copy = {...job,id:'copy1',kind:'derivative',artifacts:[{name:'derivative.json',media_type:'application/json'}]};
  let daily = {...job,id:'daily1',kind:'daily_briefing',subject_label:'October 3 market wrap',payload_sha256:'d'.repeat(64),artifacts:[{name:'output.json',media_type:'application/json'}]};
  let controls = {all:false,kinds:{},providers:{codex:{enabled:true},elevenlabs:{enabled:false,reason:'Missing credential'}}};
  const requests = [];
  const browser = await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL || 'chrome'});
  try {
    const page = await browser.newPage({viewport:{width:1280,height:1100}});
    await page.route('http://smn.test/**', async route => {
      const request = route.request(), url = new URL(request.url()), method = request.method();
      if (url.pathname === '/') return route.fulfill({contentType:'text/html',body:'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+css+'</style></head><body><main>'+template+'</main></body></html>'});
      const body = request.postDataJSON(); if (method !== 'GET') requests.push({path:url.pathname,body,header:request.headers()['x-smn-dashboard']});
      let data;
      if (url.pathname === '/api/whoami') data = {kind:'admin'};
      else if (url.pathname === '/api/membership/settings') {
        if (method === 'PUT') { assert.equal(body.offer.monthly_amount,1000); assert.equal(body.offer.annual_discount_bps,5000); assert.equal(body.offer.annual_amount,null); offer={...body.offer,annual_amount:6000,version:1,draft_id:1}; settings={...settings,settings_version:2,draft_offers:[offer]}; data={...settings,draft_id:1}; }
        else data=settings;
      } else if (url.pathname === '/api/membership/activate') { assert.equal(body.expected_version,2); assert.equal(body.draft_id,1); settings={...settings,active_offer:offer,settings_version:3,draft_offers:[]}; data=settings; }
      else if (url.pathname === '/api/membership/articles') data=[{slug:'test',title:'Test article <img src=x>'}];
      else if (url.pathname === '/api/membership/articles/test/preview') {
        if (method === 'PUT') { assert.equal(body.expected_revision,'r1'); preview={...preview,revision:'r2',payload_sha256:'b'.repeat(64),preview:{...preview.preview,content:body.content}}; }
        data=preview;
      } else if (url.pathname === '/api/membership/articles/test/review') { assert.equal(body.expected_revision,'r2'); assert.equal(body.payload_sha256,'b'.repeat(64)); preview.review_status='approved'; data=preview; }
      else if (url.pathname === '/api/promotion/briefings') data=[{briefing_id:'2026-10-03-source',date:'2026-10-03',kind:'source_bundle',title:'Selected sources <img src=x>'}];
      else if (url.pathname === '/api/promotion/controls') { if(method==='PUT'){assert.deepEqual(body,{scope:'all',paused:true});controls={...controls,all:true};}data=controls; }
      else if (url.pathname === '/api/promotion/jobs') { if(method==='POST'){assert.deepEqual(body,{kind:'daily_briefing',briefing_id:'2026-10-03-source'});data=copy;} else data=[job,copy,daily]; }
      else if (url.pathname === '/api/promotion/jobs/job1') data=job;
      else if (url.pathname === '/api/promotion/jobs/job1/review') { assert.deepEqual(body,{expected_version:2,data:{decision:'approved',payload_sha256:'c'.repeat(64)}}); job={...job,status:'reviewed',review_status:'approved',version:3}; data=job; }
      else if (url.pathname === '/api/promotion/jobs/copy1/import') { assert.deepEqual(body,{expected_version:2}); preview={...preview,revision:'r3',review_status:'pending',payload_sha256:'e'.repeat(64)};data={slug:'test',revision:'r3',review_status:'pending'}; }
      else if (url.pathname === '/api/promotion/jobs/daily1') data=daily;
      else if (url.pathname === '/api/promotion/jobs/daily1/import') { assert.deepEqual(body,{expected_version:2});data={briefing_id:'2026-10-03-source',revision:'dated-r1',review_status:'pending'};daily={...daily,version:3,imported_draft:data}; }
      else if (url.pathname === '/api/promotion/jobs/daily1/review') { assert.deepEqual(body,{expected_version:3,data:{decision:'approved',payload_sha256:'d'.repeat(64)}});daily={...daily,status:'reviewed',version:4,review_status:'approved',imported_draft:{...daily.imported_draft,review_status:'approved'}};data=daily; }
      else return route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({ok:false,error:{message:'Unknown test route'}})});
      return route.fulfill({contentType:'application/json',body:JSON.stringify({ok:true,data})});
    });
    await page.goto('http://smn.test/'); await page.addScriptTag({content:script});
    await page.locator('#membership-summary').filter({hasText:'Free registered'}).waitFor();
    await page.locator('.job-subject').filter({hasText:'Test article <img src=x>'}).first().waitFor();
    await page.getByText('October 3 market wrap',{exact:true}).waitFor();
    assert.equal(await page.locator('.job-card img').count(),0);
    await page.selectOption('#membership-mode','paid'); await page.fill('#membership-monthly','10.00'); await page.selectOption('#membership-annual-mode','discount'); await page.fill('#membership-discount','50.00'); await page.fill('#membership-trial','28'); await page.check('#membership-month'); await page.check('#membership-year');
    await page.click('#membership-save'); await page.locator('#membership-draft-summary').filter({hasText:'$60.00'}).waitFor();
    await page.click('#membership-activate'); await page.locator('#membership-summary').filter({hasText:'$10.00'}).waitFor();
    await page.selectOption('#membership-article','test'); await page.click('#membership-preview-open'); await page.locator('#membership-preview[open]').waitFor();
    assert.equal(await page.locator('#preview-full-article').getAttribute('sandbox'),''); assert.equal(await page.locator('body').getAttribute('data-unsafe'),null);
    await page.fill('#preview-headline','Edited safe headline'); await page.check('#preview-reviewed'); assert(await page.locator('#preview-approve').isDisabled());
    await page.locator('#membership-preview-form button[type=submit]').click(); await page.locator('#membership-preview-state').filter({hasText:'r2'}).waitFor();
    await page.check('#preview-reviewed'); await page.click('#preview-approve'); await page.locator('#membership-preview-state').filter({hasText:'approved'}).waitFor(); await page.click('#preview-close');
    await page.locator('.job-card').filter({hasText:'article video'}).getByText('Inspect & review',{exact:true}).click(); await page.locator('#promotion-review[open]').waitFor();
    assert((await page.locator('#promotion-review-artifacts a').getAttribute('href')).endsWith('/api/promotion/jobs/job1/artifacts/clip.mp4'));
    await page.check('#promotion-reviewed'); await page.click('#promotion-approve'); await page.getByText('article video · Media approved; distribution remains disabled',{exact:true}).waitFor();
    await page.locator('#promotion-providers').filter({hasText:'ElevenLabs: Disabled (Missing credential)'}).waitFor();
    await page.click('#promotion-pause'); await page.locator('#promotion-controls-state').filter({hasText:'Generation paused'}).waitFor();
    await page.selectOption('#promotion-kind','daily_briefing'); await page.selectOption('#promotion-briefing','2026-10-03-source');
    await page.locator('#promotion-create-form button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#promotion-status').textContent.includes('saved jobs'));
    await page.click('text=Import preview draft'); await page.locator('#membership-preview[open]').waitFor();
    await page.locator('#membership-preview-state').filter({hasText:'r3'}).waitFor();assert(await page.locator('#preview-approve').isDisabled());await page.click('#preview-close');
    const dailyCard=page.locator('.job-card').filter({hasText:'daily briefing'});
    await dailyCard.getByText('Inspect & review',{exact:true}).click();await page.locator('#promotion-review[open]').waitFor();await page.check('#promotion-reviewed');assert(await page.locator('#promotion-approve').isDisabled());await page.click('#promotion-review-close');
    await dailyCard.getByText('Save briefing draft',{exact:true}).click();await dailyCard.getByText(/Saved draft:.*pending/).waitFor();
    await dailyCard.getByText('Inspect & review',{exact:true}).click();await page.locator('#promotion-review[open]').waitFor();await page.check('#promotion-reviewed');await page.getByRole('button',{name:'Approve briefing',exact:true}).click();await page.locator('#promotion-status').filter({hasText:'Exact briefing draft approved'}).waitFor();
    assert(requests.every(item => item.header === '1')); assert.equal(await page.locator('#membership-panel img').count(),0);
    const output=process.argv[2];
    if(output){fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,'membership-desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(output,'membership-mobile.png'),fullPage:true});}
    assert(requests.some(item=>item.path==='/api/promotion/jobs'&&item.body.briefing_id==='2026-10-03-source'));
    console.log(JSON.stringify({passed:true,checks:['integer cents and basis points','server annual amount','activation version','immutable preview review hash','generated media review binding','dated briefing selector','pause control','provider readiness','imported draft requires review','XSS-safe labels','dashboard mutation header']}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error.message);process.exit(1);});
