const assert = require('assert').strict;
const crypto = require('crypto');
const {verifyMemberHtml} = require('../cloudflare_email_bytes.cjs');

const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const original = Buffer.from('<main><a href="mailto:editor@example.com">Contact</a><p>Approved research.</p></main>');
const expected = sha(original);
const key = 0x52;
const encoded = Buffer.from([key, ...Buffer.from('editor@example.com').map(byte => byte ^ key)]).toString('hex');
const transformed = Buffer.from(original.toString().replace('href="mailto:editor@example.com"',
  `href="/cdn-cgi/l/email-protection#${encoded}"`).replace('</main>',
  '<script data-cfasync="false" src="/cdn-cgi/scripts/abcdef/cloudflare-static/email-decode.min.js"></script></main>'));

assert.deepEqual(verifyMemberHtml(original, expected),
  {passed:true, raw_sha256:expected, verified_sha256:expected, normalization:null});
const restored = verifyMemberHtml(transformed, expected);
assert.equal(restored.passed, true);
assert.equal(restored.raw_sha256, sha(transformed));
assert.equal(restored.verified_sha256, expected);
assert.equal(restored.normalization, 'cloudflare-email-link');
assert.equal(verifyMemberHtml(Buffer.from(transformed.toString().replace('Approved', 'Altered')), expected).passed, false);
assert.equal(verifyMemberHtml(Buffer.concat([transformed, Buffer.from('<p>Injected</p>')]), expected).passed, false);
assert.equal(verifyMemberHtml(Buffer.from('<main><p>Unrelated</p></main>'), expected).passed, false);
