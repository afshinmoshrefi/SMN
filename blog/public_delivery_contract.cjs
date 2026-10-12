/* Exact delivery transforms plus a byte-bound numeric homepage quote contract. */
const SMNDeliveryContract=(()=>{
 const encoder=new TextEncoder(),decoder=new TextDecoder('utf-8',{fatal:true});
 const cryptoAPI=globalThis.crypto || (typeof require==='function'?require('node:crypto').webcrypto:null);
 const sha=async raw=>Array.from(new Uint8Array(await cryptoAPI.subtle.digest('SHA-256',raw))).map(x=>x.toString(16).padStart(2,'0')).join('');
 const ANALYTICS_SHA='44fd2f240b3fe347f9d82ec0ca5b27363b1986fe05f604de031009add7106f78';
 async function normalizeAnalytics(raw){
  const text=decoder.decode(raw),tags=text.match(/<script\b[^>]*\bsrc="https:\/\/static\.cloudflareinsights\.com\/beacon\.min\.js[^\"]*"[^>]*><\/script>\n/g)||[];
  if(tags.length>1)throw Error('Repeated analytics delivery insertion');
  if(!tags.length)return {bytes:raw,normalization:null};
  if(await sha(encoder.encode(tags[0]))!==ANALYTICS_SHA)throw Error('Unrecognized analytics delivery insertion');
  return {bytes:encoder.encode(text.replace(tags[0],'')),normalization:'exact-cloudflare-analytics-v1'};
 }
 async function quoteContract(raw){
  const text=decoder.decode(raw),matches=[...text.matchAll(/<div class="market-bar">[\s\S]*?<\/div>\s*<\/div>/g)];
  if(matches.length!==1)throw Error('Expected one complete market quote span');
  const m=matches[0],span=m[0];
  const price=/(<span class="market-price"(?: style="color:#818cf8")?>)([-+]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)(<sup style='font-size:9px;color:#6366f1;margin-left:1px'>F<\/sup>)?(<\/span>)/g;
  const change=/(<span class='market-change )(up|down|flat)('>)([-+]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?%?)(<\/span>)/g;
  for(const field of span.match(/<span\b[^>]*class="market-price"[^>]*>[\s\S]*?<\/span>/g)||[])if(!new RegExp('^(?:'+price.source+')$').test(field))throw Error('Invalid quote price field');
  for(const field of span.match(/<span\b[^>]*class='market-change [^']*'[^>]*>[\s\S]*?<\/span>/g)||[])if(!new RegExp('^(?:'+change.source+')$').test(field))throw Error('Invalid quote change field');
  for(const item of span.matchAll(/class="market-item ([^"]*)"/g))if(!['up','down','flat'].includes(item[1]))throw Error('Invalid quote item direction');
  const stable=span.replace(price,(_,open,value,marker,close)=>open+'PRICE'+(marker||'')+close)
   .replace(change,(_,open,direction,end,number,close)=>open+'DIRECTION'+end+'CHANGE'+close)
   .replace(/class="market-item (?:up|down|flat)"/g,'class="market-item DIRECTION"');
  return {version:2,kind:'market-bar-numeric-quotes-v2',outside_sha256:await sha(encoder.encode(text.slice(0,m.index)+text.slice(m.index+span.length))),markup_sha256:await sha(encoder.encode(stable)),observed_sha256:await sha(raw),quote_sha256:await sha(encoder.encode(span))};
 }
 function matches(expected,observed){return !!(expected&&observed&&expected.version===2&&expected.kind==='market-bar-numeric-quotes-v2'&&['version','kind','outside_sha256','markup_sha256'].every(k=>expected[k]===observed[k]));}
 return {sha,normalizeAnalytics,quoteContract,matches,ANALYTICS_SHA};
})();
if(typeof module!=='undefined')module.exports=SMNDeliveryContract;
else globalThis.SMNDeliveryContract=SMNDeliveryContract;
