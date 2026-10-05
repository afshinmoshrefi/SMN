const crypto = require('crypto');

const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

function verifyMemberHtml(raw, expected) {
  const rawSha256 = sha256(raw);
  if (rawSha256 === expected) {
    return {passed: true, raw_sha256: rawSha256, verified_sha256: rawSha256, normalization: null};
  }
  let changed = false;
  const restored = raw.toString('utf8').replace(
    /href="\/cdn-cgi\/l\/email-protection#((?:[a-f0-9]{2}){2,})"/gi,
    (_, hex) => {
      changed = true;
      const encoded = Buffer.from(hex, 'hex');
      const decoded = Buffer.from(encoded.subarray(1).map(byte => byte ^ encoded[0])).toString('utf8');
      return 'href="mailto:' + decoded + '"';
    }
  ).replace(
    /<script data-cfasync="false" src="\/cdn-cgi\/scripts\/[a-f0-9]+\/cloudflare-static\/email-decode\.min\.js"><\/script>/gi,
    () => { changed = true; return ''; }
  );
  const verifiedSha256 = changed ? sha256(Buffer.from(restored, 'utf8')) : rawSha256;
  return {passed: changed && verifiedSha256 === expected, raw_sha256: rawSha256,
          verified_sha256: verifiedSha256, normalization: changed ? 'cloudflare-email-link' : null};
}

module.exports = {verifyMemberHtml};
