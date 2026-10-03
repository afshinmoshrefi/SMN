# Offline public derivative foundation

`blog/public_derivative.py` prepares a private writing handoff from an already reviewed full article. It imports manually authored JSON and performs mechanical checks. It never invokes a model/provider, updates settings, publishes, or grants editorial approval. Existing full-article generation remains unchanged.

From `blog`, prepare a new directory:

```powershell
python public_derivative.py prepare --article-dir <retained-result> --review-job <retained-review-job> --article-id <canonical-url> --revision <explicit-revision> --output <new-private-directory>
python public_derivative.py validate --prepared <new-private-directory>/prepared.json --copy <draft-copy.json>
python public_derivative.py receive --prepared <new-private-directory>/prepared.json --copy <draft-copy.json> --output <new-private-receipt-directory>
python -m unittest tests.test_public_derivative -v
```

The result directory must retain `article.json`, `bundle.json`, `source.json`, `review-binding.json`, `mechanical-checks.json` and `chart-words.json`. The review job must retain its successful output, receipt, job manifest and immutable inputs. An expired generation assignment can be reused as completed evidence; this tool does not restart it. The canonical URL must match `source.card.production_original`; revision is an explicit operator-selected identifier, bound subsequently to immutable hashes.

The generated schema enumerates sources, approved article passages and native charts. The writer supplies copy only. Application-owned provenance preserves canonical URL, revision, article/bundle/evidence/review/engine hashes, exact study identity and source timestamp. Validation reopens retained inputs and reconstructs prepared metadata to reject drift or editing. It verifies review-output and input receipts, successful full-article checks, and the approved engine card. All original financial values remain unchanged.

Every statement names article passage locators and sources used by those passages. This is traceability, not proof of meaning. Cumulative source allowances count original prose, headings, attribution and chart wording plus every new headline, preview, value promise, qualification, social draft, narration and on-screen statement. Shared source attribution counts conservatively against each named source. No allowance is invented for a source that has none in the existing bundle.

`mechanical_passed` never means `approved`: numerical fidelity, material qualifications, direction/sign interpretation, the value promise, source-derived wording and public usefulness require editorial review. The mandatory qualification must appear alongside the preview in any future reader integration. Word count is reported as guidance, without rejecting useful exceptions. Public text must be escaped by future renderers; HTML is rejected here. The free-development invitation is application-owned and cannot authorize a live commercial offer.

`receive` refuses an existing output directory. Prepared files and receipts are private and reference local evidence paths; do not put them under a public document root. This milestone adds no publication/content-store integration, access control, derivative approval stage, source revision catalog, media generation, timing QA or external distribution.

## Retained pilot

`blog/examples/public-derivative/omc-copy.json` is a manually authored, reviewable archival draft based on the September 17 approved OMC article. It preserves the selected long study and includes the materially weaker consecutive history beside the favorable record. It avoids the company source, whose full article already uses 179 of 200 allowed derived words. The video selects the existing range chart; proposed storyboard is subject/opening, range chart during the middle, then the article invitation. No audio/video was generated or timed, and chart pixels have not been inspected for this derivative.

Local retained source: `smn-review-20260917/engine-editorial/results/OMC`; reviewer: `jobs/OMC-20260917-review`. Full article canonical URL: `https://seasonalmarketnews.com/articles/US/2026/09/22/omnicom-group-omc-has-risen-in-10-straight-midterm-fall-windows-averaging-11-14-gains.html`. This older evidence is suitable for an archival pilot, not fresh current-news promotion. The committed draft contains no copied full evidence bundle. Prepare it against retained files before use.

`blog/examples/public-derivative/len-copy.json` is the second archival pilot, opening with the September 16 delivery-outlook cut, followed by the approved October 5, 2026-January 8, 2027 long study. It puts nine of 10 selected midterm gains beside the weaker 50.0% consecutive record, preserves the latest matched window's exact gain and adverse excursion, and states that the move order is unknown and the histories overlap. Its brief company headline/opening stays within the 26 company-source words remaining after the original 174 of 200 words. No social/video copy is supplied. Retained result is `results/LEN` and reviewer is `jobs/LEN-20260917-copy-review` in the same September 17 edition; full article canonical URL is `https://seasonalmarketnews.com/articles/US/2026/10/05/lennar-len-has-rallied-in-9-of-10-midterm-fall-windows-averaging-18-35-gains.html`. It requires separate derivative semantic review and is not fresh news promotion.

A qualified bearish retained artifact remains missing. Two mechanically checked real pilots plus synthetic short-direction tests do not fulfill the planned three-real-article qualification. Publication remains blocked pending the bearish sample and semantic review.
