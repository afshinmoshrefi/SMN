/* Membership/promotion panel: amounts and evidence bindings remain server-owned. */
(function () {
  'use strict';
  const panel = document.getElementById('membership-panel');
  if (!panel) return;
  const base = panel.dataset.base || '';
  const $ = id => document.getElementById(id);
  let settings = null, currentPreview = null, reviewJob = null, dirty = false;
  const busy = new Set();
  async function api(path, method = 'GET', payload) {
    const options = {method, credentials: 'same-origin', headers: {'X-SMN-Dashboard': '1'}};
    if (payload !== undefined) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(payload); }
    const response = await fetch(base + path, options);
    let body; try { body = await response.json(); } catch (_) { throw new Error('The service did not return a usable response. Reload and retry.'); }
    if (!response.ok || body.ok === false) throw new Error(body.error?.message || 'This operation could not be completed.');
    return body.data;
  }
  function status(id, text, bad = false) { $(id).textContent = text; $(id).classList.toggle('bad', bad); }
  async function run(key, id, work) {
    if (busy.has(key)) return;
    busy.add(key);
    try { await work(); } catch (error) { status(id, error.message, true); }
    finally { busy.delete(key); }
  }
  function minor(value, label) {
    const text = String(value).trim();
    if (!/^(0|[1-9]\d*)(\.\d{1,2})?$/.test(text)) throw new Error(label + ' needs a nonnegative amount with at most two decimal places.');
    const parts = text.split('.');
    const result = Number(parts[0] + (parts[1] || '').padEnd(2, '0'));
    if (!Number.isSafeInteger(result) || result > 1000000000) throw new Error(label + ' is outside the allowed range.');
    return result;
  }
  function dollars(value) { return '$' + (Number(value || 0) / 100).toFixed(2); }
  function plainMoney(value) { return (Number(value || 0) / 100).toFixed(2); }
  function offerLabel(offer) {
    if (!offer) return 'No active offer.';
    if (offer.mode === 'free') return 'Free registered membership. No card and no trial countdown.';
    return 'Monthly ' + dollars(offer.monthly_amount) + '; annual ' + dollars(offer.annual_amount) + ' charged yearly; ' + offer.trial_days + ' trial days.';
  }
  function togglePricing() {
    const paid = $('membership-mode').value === 'paid';
    panel.querySelectorAll('[data-paid-field]').forEach(node => { node.hidden = !paid; node.querySelectorAll('input,select').forEach(input => { input.disabled = !paid; }); });
    $('membership-free-note').hidden = paid;
    $('membership-annual-price-field').hidden = !paid || $('membership-annual-mode').value !== 'explicit';
    $('membership-discount-field').hidden = !paid || $('membership-annual-mode').value !== 'discount';
  }
  function draftChoice() {
    const draft = settings?.draft_offers?.find(item => String(item.draft_id ?? item.version) === $('membership-draft').value);
    $('membership-draft-summary').textContent = draft ? offerLabel(draft) : 'Save an offer draft before activation.';
    const paidUnavailable = draft?.mode === 'paid' && !settings?.readiness?.billing_enabled;
    $('membership-activate').disabled = !draft || paidUnavailable;
    if (paidUnavailable) $('membership-draft-summary').textContent += ' Paid activation is unavailable until billing is configured.';
  }
  function renderSettings(value, selectedId) {
    settings = value.settings || value;
    const offer = settings.active_offer || {mode: 'free', annual_mode: 'explicit', trial_days: 0, intervals: []};
    $('membership-mode').value = offer.mode;
    $('membership-monthly').value = plainMoney(offer.monthly_amount);
    $('membership-annual-mode').value = offer.annual_mode || 'explicit';
    $('membership-annual').value = plainMoney(offer.annual_amount);
    $('membership-discount').value = plainMoney(offer.annual_discount_bps);
    $('membership-trial').value = offer.trial_days || 0;
    $('membership-month').checked = (offer.intervals || []).includes('month');
    $('membership-year').checked = (offer.intervals || []).includes('year');
    $('membership-summary').textContent = 'Active offer: ' + offerLabel(settings.active_offer);
    const counts = settings.member_counts || {};
    $('membership-counts').textContent = 'Members: ' + (counts.total || 0) + ' · Free launch: ' + (counts.free_launch || 0) + ' · Trial: ' + (counts.trial || 0) + ' · Paid: ' + (counts.paid || 0) + ' · Suspended: ' + (counts.suspended || 0);
    $('membership-draft').replaceChildren();
    for (const draft of settings.draft_offers || []) {
      const option = document.createElement('option'); option.value = draft.draft_id ?? draft.version;
      option.textContent = 'Draft ' + option.value + ' — ' + offerLabel(draft); $('membership-draft').append(option);
    }
    if (selectedId != null) $('membership-draft').value = String(selectedId);
    togglePricing(); draftChoice();
    status('membership-status', 'Settings loaded. Billing: ' + (settings.readiness?.stripe_mode || 'not configured') + '.');
  }
  async function loadSettings() { renderSettings(await api('/api/membership/settings')); }
  $('membership-mode').addEventListener('change', togglePricing);
  $('membership-annual-mode').addEventListener('change', togglePricing);
  $('membership-draft').addEventListener('change', draftChoice);
  $('membership-refresh').onclick = () => run('settings', 'membership-status', loadSettings);
  $('membership-settings-form').onsubmit = event => {
    event.preventDefault(); run('settings', 'membership-status', async () => {
      if (!settings) throw new Error('Load settings before saving.');
      const paid = $('membership-mode').value === 'paid', annualMode = paid ? $('membership-annual-mode').value : 'explicit';
      const trialText = $('membership-trial').value;
      if (paid && (!/^\d+$/.test(trialText) || Number(trialText) > 365)) throw new Error('Trial days must be a whole number from 0 to 365.');
      const offer = {mode: paid ? 'paid' : 'free', currency: 'usd', monthly_amount: paid ? minor($('membership-monthly').value, 'Monthly price') : 0,
        annual_mode: annualMode, annual_amount: !paid ? 0 : annualMode === 'explicit' ? minor($('membership-annual').value, 'Annual price') : null,
        annual_discount_bps: paid && annualMode === 'discount' ? minor($('membership-discount').value, 'Annual discount') : null,
        trial_days: paid ? Number(trialText) : 0, intervals: paid ? ['month','year'].filter(interval => $('membership-' + interval).checked) : []};
      if (offer.annual_discount_bps > 9999) throw new Error('Annual discount must be below 100%.');
      const saved = await api('/api/membership/settings', 'PUT', {expected_version: settings.settings_version, offer});
      renderSettings(saved, saved.draft_id); status('membership-status', 'Offer draft saved. Review its server-calculated annual amount before activation.');
    });
  };
  $('membership-activate').onclick = () => run('settings', 'membership-status', async () => {
    const id = Number($('membership-draft').value);
    if (!settings || !Number.isSafeInteger(id) || id <= 0) throw new Error('Choose a saved offer draft.');
    renderSettings(await api('/api/membership/activate', 'POST', {expected_version: settings.settings_version, draft_id: id}));
    status('membership-status', 'Offer activated for new enrollments. Existing grants remain unchanged.');
  });
  async function loadArticles() {
    const rows = await api('/api/membership/articles');
    const selected = $('membership-article').value;
    $('membership-article').replaceChildren(); const empty = document.createElement('option'); empty.value = ''; empty.textContent = 'Choose an article'; $('membership-article').append(empty);
    for (const article of rows) { const option = document.createElement('option'); option.value = article.slug; option.textContent = article.title || article.slug; $('membership-article').append(option); }
    $('membership-article').value = selected;
  }
  function previewPath(suffix = '') { return '/api/membership/articles/' + encodeURIComponent(currentPreview.slug) + '/preview' + suffix; }
  function renderPreview(value) {
    currentPreview = value; const content = value.preview.content || value.preview;
    $('preview-headline').value = content.headline.text;
    $('preview-lead').value = content.preview[0].text;
    $('preview-key-points').value = content.preview.slice(1).map(item => item.text).join('\n');
    $('preview-value').value = content.full_article_value.text;
    $('preview-qualification').value = content.qualification.text;
    $('preview-cta').value = value.cta || 'Register or sign in to read the complete article';
    $('preview-full-article').srcdoc = typeof value.full_html === 'string' ? value.full_html : '<p>The complete source article is unavailable here.</p>';
    $('preview-full-note').textContent = value.full_html ? 'Read-only source view; scripts and navigation are disabled.' : 'Open the full article through its existing administrator preview before approval.';
    $('preview-reviewed').checked = false; dirty = false; approvalState();
    $('membership-preview-state').textContent = 'Revision ' + value.revision + ' · ' + (value.review_status || 'Draft; review required');
  }
  function approvalState() { $('preview-approve').disabled = dirty || !$('preview-reviewed').checked || !currentPreview?.payload_sha256; }
  $('membership-preview-open').onclick = () => run('preview', 'membership-status', async () => {
    const slug = $('membership-article').value;
    if (!slug) throw new Error('Choose an article first.');
    renderPreview(await api('/api/membership/articles/' + encodeURIComponent(slug) + '/preview'));
    $('membership-preview').showModal();
  });
  $('preview-close').onclick = () => $('membership-preview').close();
  for (const id of ['preview-headline','preview-lead','preview-key-points','preview-value','preview-qualification']) $(id).addEventListener('input', () => { dirty = true; approvalState(); });
  $('preview-reviewed').onchange = approvalState;
  $('membership-preview-form').onsubmit = event => {
    event.preventDefault(); run('preview', 'membership-preview-state', async () => {
      const old = currentPreview.preview.content || currentPreview.preview;
      const content = JSON.parse(JSON.stringify(old));
      content.headline.text = $('preview-headline').value.trim(); content.full_article_value.text = $('preview-value').value.trim(); content.qualification.text = $('preview-qualification').value.trim();
      const extra = $('preview-key-points').value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
      if (extra.length > 2) throw new Error('Use at most two additional public paragraphs.');
      content.preview = [$('preview-lead').value.trim(), ...extra].map((text, index) => Object.assign({}, old.preview[index] || old.preview[0], {text}));
      renderPreview(await api(previewPath(), 'PUT', {expected_revision: currentPreview.revision, content}));
      $('membership-preview-state').textContent += ' · New draft saved; prior approval and media bindings require review.';
    });
  };
  $('preview-approve').onclick = () => run('preview', 'membership-preview-state', async () => {
    if (dirty || !$('preview-reviewed').checked || !currentPreview.payload_sha256) throw new Error('Save and inspect this exact revision before approval.');
    renderPreview(await api('/api/membership/articles/' + encodeURIComponent(currentPreview.slug) + '/review', 'POST',
      {expected_revision: currentPreview.revision, payload_sha256: currentPreview.payload_sha256, passed: true, note: $('preview-review-note').value}));
  });
  $('preview-generate').onclick = () => run('preview', 'membership-preview-state', async () => {
    if (dirty) throw new Error('Save your edits before requesting a generated draft.');
    await api('/api/membership/articles/' + encodeURIComponent(currentPreview.slug) + '/generate', 'POST', {expected_revision: currentPreview.revision});
    $('membership-preview-state').textContent = 'Draft generation requested. Refresh jobs to follow progress.'; await loadJobs();
  });
  const labels = {draft:'Ready for generation',held:'Held — action required',failed:'Generation failed',running:'Generation in progress',generated:'Generated — inspect before approval',reviewed:'Media approved; distribution remains disabled',canceled:'Canceled',superseded:'Replaced by a new revision'};
  function button(label, callback, disabled = false) { const node = document.createElement('button'); node.type = 'button'; node.textContent = label; node.disabled = disabled; node.onclick = callback; return node; }
  function artifactLinks(container, job) {
    for (const artifact of job.artifacts || []) { const link = document.createElement('a'); link.textContent = 'Download ' + artifact.name; link.href = base + '/api/promotion/jobs/' + encodeURIComponent(job.id) + '/artifacts/' + encodeURIComponent(artifact.name); link.style.marginRight = '14px'; container.append(link); }
  }
  async function jobAction(job, action, data) {
    const payload = {expected_version: job.version}; if (data) payload.data = data;
    await api('/api/promotion/jobs/' + encodeURIComponent(job.id) + '/' + action, 'POST', payload); await loadJobs();
  }
  async function loadJobs() {
    const jobs = await api('/api/promotion/jobs'); $('promotion-jobs').replaceChildren();
    if (!jobs.length) status('promotion-status', 'No jobs yet. Choose a format and create one from approved sources.');
    else status('promotion-status', jobs.length + ' saved jobs.');
    for (const job of jobs) {
      const card = document.createElement('article'); card.className = 'job-card';
      const title = document.createElement('strong'); title.textContent = job.kind.replaceAll('_',' ') + ' · ' + (labels[job.status] || job.status); card.append(title);
      const details = document.createElement('p'); details.textContent = 'Source revision ' + job.source_revision + ' · Generation: ' + job.generation_status + ' · Review: ' + job.review_status; card.append(details);
      if (job.error || job.holds?.length) { const note = document.createElement('p'); note.textContent = [job.error, ...(job.holds || [])].filter(Boolean).join(' · '); card.append(note); }
      const actions = document.createElement('div'); actions.className = 'row';
      if (job.status === 'draft') actions.append(button('Generate', () => run('job-' + job.id, 'promotion-status', () => jobAction(job, 'generate'))));
      if (['held','failed'].includes(job.status)) actions.append(button('Retry', () => run('job-' + job.id, 'promotion-status', () => jobAction(job, 'retry')), job.generation_status === 'unknown_outcome' || job.attempts >= 2));
      if (job.status === 'generated') actions.append(button('Inspect & review', () => run('review', 'promotion-status', async () => {
        reviewJob = await api('/api/promotion/jobs/' + encodeURIComponent(job.id));
        $('promotion-review-state').textContent = 'Inspect each artifact before approving generated media.';
        $('promotion-review-artifacts').replaceChildren(); artifactLinks($('promotion-review-artifacts'), reviewJob);
        $('promotion-reviewed').checked = false; $('promotion-approve').disabled = true; $('promotion-review').showModal();
      })));
      if (!['running','canceled','superseded'].includes(job.status)) actions.append(button('Cancel job', () => run('job-' + job.id, 'promotion-status', () => jobAction(job, 'cancel'))));
      card.append(actions); const artifacts = document.createElement('p'); artifactLinks(artifacts, job); card.append(artifacts); $('promotion-jobs').append(card);
    }
  }
  $('promotion-create-form').onsubmit = event => { event.preventDefault(); run('create-job', 'promotion-status', async () => {
    const kind = $('promotion-kind').value, slug = $('membership-article').value;
    const body = {kind}; if (!['daily_briefing','daily_avatar'].includes(kind)) { if (!slug) throw new Error('Choose an article first.'); body.slug = slug; }
    await api('/api/promotion/jobs', 'POST', body); await loadJobs();
  }); };
  $('promotion-refresh').onclick = () => run('jobs', 'promotion-status', loadJobs);
  $('promotion-reviewed').onchange = () => { $('promotion-approve').disabled = !$('promotion-reviewed').checked || !reviewJob?.payload_sha256; };
  for (const [id, decision] of [['promotion-approve','approved'],['promotion-reject','rejected']]) $(id).onclick = () => run('review', 'promotion-review-state', async () => {
    if (!reviewJob?.payload_sha256 || (decision === 'approved' && !$('promotion-reviewed').checked)) throw new Error('Inspect the exact saved artifacts before review.');
    await jobAction(reviewJob, 'review', {decision, payload_sha256: reviewJob.payload_sha256}); $('promotion-review').close();
  });
  $('promotion-review-close').onclick = () => $('promotion-review').close();
  (async () => {
    try {
      const identity = await api('/api/whoami'); if (identity.kind !== 'admin') return;
      const toggle = $('btn-membership');
      if (toggle) {
        toggle.style.display = '';
        toggle.onclick = async () => {
          panel.hidden = !panel.hidden;
          if (!panel.hidden) await Promise.allSettled([run('settings','membership-status',loadSettings), run('articles','membership-status',loadArticles), run('jobs','promotion-status',loadJobs)]);
        };
        return;
      }
      panel.hidden = false;
    }
    catch (_) { return; }
    await Promise.allSettled([run('settings','membership-status',loadSettings), run('articles','membership-status',loadArticles), run('jobs','promotion-status',loadJobs)]);
  })();
})();
