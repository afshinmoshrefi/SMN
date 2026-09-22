// Page-level defects a person would see. Runs inside the page.
module.exports = () => {
  const out = [], W = innerWidth;
  for (const el of document.querySelectorAll('h1,h2,h3,p,li,td,th,figcaption,summary,figure,img,table,.study-link')) {
    if (el.closest('details:not([open])')) continue;
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    if (!r.width || cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (el.closest('[style*="overflow"],.table-scroll') && ['TD','TH','TABLE'].includes(el.tagName)) continue;
    if (r.right > W + 1 || r.left < -1) out.push('off-screen ' + el.tagName + ': ' + (el.textContent || el.src || '').trim().slice(0, 60));
    if (['P','H1','H2','H3','LI','FIGCAPTION','SUMMARY'].includes(el.tagName) && el.scrollWidth > el.clientWidth + 1 && cs.overflowX !== 'visible')
      out.push('clipped text ' + el.tagName + ': ' + el.textContent.trim().slice(0, 60));
    if (el.tagName === 'IMG' && el.naturalWidth && r.width > el.naturalWidth * 1.25)
      out.push('image stretched ' + Math.round(r.width) + 'px from ' + el.naturalWidth + 'px: ' + el.src.split('/').pop());
  }
  const figs = [...document.querySelectorAll('figure')].map(f => f.getBoundingClientRect()).filter(r => r.height);
  for (let i = 1; i < figs.length; i++) if (figs[i].top < figs[i - 1].bottom - 2 && figs[i].left < figs[i - 1].right - 2 && figs[i].right > figs[i - 1].left + 2) out.push('overlapping figures');
  return out;
};
