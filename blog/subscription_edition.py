"""Receive a subscription writer's private seasonal draft in SMN's renderer.

This deliberately does not import configuration, Redis, the API writer, or the
publisher. A source adapter supplies the existing checked bundle and visuals.
"""
from pathlib import Path

import seasonal_edition as seasonal
from visual_evidence import digest, validate_bundle
from visual_editorial import render_edition
from subscription_writer import load_json, save_json, sha256, verify_job


def source_word_counts(article, bundle, chart_words):
    counts = {s['id']: {'prose':0, 'chart':int(chart_words.get(s['id'],0)),
                       'headings_and_citation':0, 'maximum':s['max_derived_words']}
              for s in bundle['sources'] if s.get('max_derived_words')}
    entries = [(article['title'], article['title_source_ids']),
               (article['dek'], article['dek_source_ids'])]
    entries += [(p['text'],p['source_ids']) for s in article['sections'] for p in s['paragraphs']]
    entries += [(t['text'],t['source_ids']) for t in article['takeaways']]
    for text, refs in entries:
        for sid in set(refs) & counts.keys():
            counts[sid]['prose'] += len(text.split())
    for section in article['sections']:
        refs = {sid for p in section['paragraphs'] for sid in p['source_ids']}
        for sid in refs & counts.keys():
            counts[sid]['headings_and_citation'] += len((section['heading'] or '').split())
    for source in bundle['sources']:
        if source['id'] in counts:
            counts[source['id']]['headings_and_citation'] += len(source['title'].split())
    for row in counts.values():
        row['total'] = row['prose'] + row['chart'] + row['headings_and_citation']
        row['passed'] = row['total'] <= row['maximum']
    return counts


def receive_draft(job, bundle, output, *, charts, native, hero, chart_words):
    job, output = Path(job), Path(output)
    manifest = verify_job(job)
    receipt = load_json(job/'receipt.json')
    if receipt['output_sha256'] != sha256((job/'output.json').read_bytes()):
        raise ValueError('Returned article differs from the saved writer receipt')
    if manifest['evidence_sha256'] != bundle['evidence_sha256']:
        raise ValueError('Writer and renderer evidence versions differ')
    validate_bundle(bundle)
    article = load_json(job/'output.json')
    normalization = []
    # The commissioned identifier belongs to SMN. A model sometimes appends
    # its explanation after the exact identifier. Bind that unambiguous case
    # without changing a word of reader copy, and retain the raw receipt.
    expected = bundle['seasonal_contract']['angle']
    returned = article.get('angle_delivery', {}).get('angle', '')
    if returned.startswith(expected + ':'):
        normalization.append({'field':'angle_delivery.angle', 'original':returned,
                              'bound':expected, 'reader_copy_changed':False})
        article['angle_delivery']['angle'] = expected
    try:
        structure = seasonal.check_article(article,bundle)
    except ValueError as exc:
        structure = {'passed':False, 'reason':str(exc)}
    counts = source_word_counts(article,bundle,chart_words)
    result = {'structure':structure,'source_words':counts,
              'article_sha256':digest(article),'evidence_sha256':bundle['evidence_sha256'],
              'passed':structure['passed'] and all(r['passed'] for r in counts.values()),
              'metadata_normalization':normalization,
              'numeric_claim_review':'Required separately; structural validation is not semantic fact checking',
              'publish':False}
    output.mkdir(parents=True,exist_ok=True)
    save_json(output/'article.json',article)
    save_json(output/'mechanical-checks.json',result)
    # A valid structure can be viewed even when a source allowance needs repair.
    # Every render is an explicitly private review draft.
    if structure['passed']:
        html = render_edition(article,bundle,charts,hero,held=True,seasonal=native)
        (output/'article.html').write_text(html,encoding='utf-8')
    return result
