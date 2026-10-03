import copy
from pathlib import Path
import tempfile
import unittest

from public_derivative import prepare_derivative, validate_derivative, receive_derivative, prepare_prompt
from subscription_writer import save_json, sha256
from visual_evidence import digest
from subscription_edition import source_word_counts
from subscription_publication import CHECKS


class DerivativeTests(unittest.TestCase):
    def test_crowded_dates_fail_character_preflight_but_short_qualified_script_passes(self):
        from public_derivative import validate_video_script, prepare_script_prompt
        approved = copy.deepcopy(self.copy); approved['video'] = None
        script = copy.deepcopy(self.copy['video'])
        script['narration']['text'] = ('TradeWave\u2019s long study of ADP common stock (Nasdaq: ADP) for October 11\u201330, 2026 found nine winners in 10 selected midterm-election years, 1986\u20132022. Consecutive histories were weaker; historical, not forecast.')
        self.assertTrue(24 <= len(script['narration']['text'].split()) <= 32)
        self.assertEqual(len(script['narration']['text']),210)
        with self.assertRaisesRegex(ValueError, '195-character'):
            validate_video_script(script,self.prepared,approved)
        script['narration']['text'] = ('Stock price weakness can favor this short study, but rebounds can hurt. See the full risk study and careful comparison; '
                                     'these past results are not a future forecast.')
        checks = validate_video_script(script,self.prepared,approved)
        self.assertLessEqual(checks['narration_characters'],195)
        self.assertTrue(160 <= checks['narration_characters'] <= 190)
        bounded = copy.deepcopy(script)
        bounded['narration']['text'] = bounded['narration']['text'].replace('this short study', 'this 1986-2022 study')
        with self.assertRaisesRegex(ValueError, 'numeric date/year ranges'):
            validate_video_script(bounded,self.prepared,approved)
        bounded = copy.deepcopy(script)
        bounded['narration']['text'] = bounded['narration']['text'].replace('this short study', 'this (ADP) study')
        with self.assertRaisesRegex(ValueError, 'ticker parentheticals'):
            validate_video_script(bounded,self.prepared,approved)
        prompt = prepare_script_prompt(self.prepared,approved)
        self.assertIn('not a duration claim',prompt)
        self.assertIn('numeric year/date ranges',prompt)

    def test_editor_rejected_generated_script_retry_retains_first_attempt(self):
        from unittest.mock import patch
        import promotion_jobs as jobs
        from public_derivative_jobs import generate_script
        approved = copy.deepcopy(self.copy); approved['video'] = None
        script = copy.deepcopy(self.copy['video'])
        script['narration']['text'] = ('Stock price weakness can favor this short study, but rebounds can hurt. See the full risk study and careful comparison; '
                                     'these past results are not a future forecast.')
        provenance=self.prepared['provenance']
        inputs={'article_id':provenance['article_id'],'source_revision':provenance['revision'],
                'source_hash':provenance['article_sha256'],'payload_sha256':digest(approved)}
        job=jobs.create(self.root,'article_script',inputs,'editor')
        def run(path,codex):
            save_json(path/'output.json',script)
            return {'status':'output_ready_for_smn_validation'}
        with patch('public_derivative_jobs.writer.run_job',side_effect=run) as provider:
            first=generate_script(self.root,self.prepared,approved,{'model':'gpt-5.6-sol','effort':'medium'},'unused',job_id=job['id'])
            path=jobs._folder(self.root)/'artifacts'/job['id']/'model-job'/'output.json'
            original=path.read_bytes()
            held=jobs.transition(self.root,job['id'],'review',first['version'],'editor',
                                 {'decision':'rejected','payload_sha256':digest(inputs)})
            jobs.transition(self.root,job['id'],'retry',held['version'],'editor')
            second=generate_script(self.root,self.prepared,approved,{'model':'gpt-5.6-sol','effort':'medium'},'unused',job_id=job['id'])
            self.assertEqual(second['status'],'generated'); self.assertEqual(second['attempts'],2)
            self.assertEqual(path.read_bytes(),original)
            self.assertTrue((path.parent.parent/'model-job-2'/'output.json').is_file())
            self.assertEqual(provider.call_count,2)

    def test_script_retry_retains_first_output_and_cannot_exceed_two_attempts(self):
        from unittest.mock import patch
        import promotion_jobs as jobs
        from public_derivative_jobs import generate_script
        approved = copy.deepcopy(self.copy); approved['video'] = None
        valid = copy.deepcopy(self.copy['video'])
        valid['narration']['text'] = ('Positive short results reflect stock price declines. Adverse rebounds can hurt this historical study; '
                                     'selected years are historical evidence, never a reliable future forecast.')
        provenance = self.prepared['provenance']
        inputs = {'article_id':provenance['article_id'], 'source_revision':provenance['revision'],
                  'source_hash':provenance['article_sha256'], 'payload_sha256':digest(approved)}
        job = jobs.create(self.root, 'article_script', inputs, 'editor')
        def run(path, codex):
            output = copy.deepcopy(valid)
            if path.name == 'model-job': output['narration']['text'] = 'Too short.'
            save_json(path / 'output.json', output)
            return {'status':'output_ready_for_smn_validation'}
        with patch('public_derivative_jobs.writer.run_job', side_effect=run) as provider:
            with self.assertRaises(ValueError):
                generate_script(self.root,self.prepared,approved,{'model':'gpt-5.6-sol','effort':'medium'},'unused',job_id=job['id'])
            first = jobs._folder(self.root)/'artifacts'/job['id']/'model-job'/'output.json'
            original = first.read_bytes()
            held = jobs.get_job(self.root, job['id'])
            jobs.transition(self.root,job['id'],'retry',held['version'],'editor')
            result = generate_script(self.root,self.prepared,approved,{'model':'gpt-5.6-sol','effort':'medium'},'unused',job_id=job['id'])
            self.assertEqual(result['status'],'generated'); self.assertEqual(result['attempts'],2)
            self.assertEqual(first.read_bytes(),original)
            generate_script(self.root,self.prepared,approved,{'model':'gpt-5.6-sol','effort':'medium'},'unused',job_id=job['id'])
            self.assertEqual(provider.call_count,2)

    def test_separate_script_binding_length_and_cumulative_budget(self):
        from public_derivative import validate_video_script
        approved = copy.deepcopy(self.copy); approved['video'] = None
        original = copy.deepcopy(approved)
        script = copy.deepcopy(self.copy['video'])
        script['narration']['text'] = ('Positive short results reflect stock price declines. Adverse rebounds can hurt this historical study; '
                                      'selected years are historical evidence, never a reliable future forecast.')
        self.assertEqual(validate_video_script(script, self.prepared, approved)['narration_words'], 24)
        self.assertEqual(approved, original)
        bad = copy.deepcopy(script); bad['native_chart_id'] = 'unknown'
        with self.assertRaises(ValueError): validate_video_script(bad, self.prepared, approved)
        bad = copy.deepcopy(script); bad['narration']['text'] = 'Too short.'
        with self.assertRaises(ValueError): validate_video_script(bad, self.prepared, approved)
        tight = copy.deepcopy(self.prepared)
        # Give history a finite allowance that admits the original copy alone.
        checks = validate_derivative(approved, tight)
        tight['full_source_words']['news']['maximum'] = checks['source_words']['news']['combined_total'] + 1
        script['narration']['source_ids'].append('news')
        script['narration']['article_refs'].append('sections/1/paragraphs/0')
        with self.assertRaises(ValueError): validate_video_script(script, tight, approved)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.article_dir, self.job = self.root / 'article', self.root / 'review'
        self.article_dir.mkdir(); self.job.mkdir()
        self.url = 'https://seasonalmarketnews.com/articles/test.html'
        card = {'production_original': self.url,
                'production_identity': {'symbol': 'TEST', 'direction': 'short', 'years': 'pe2-10', 'days': 20}}
        article = {'title': 'Company faces pressure', 'title_source_ids': ['news'],
                   'dek': 'The historical short window has risks', 'dek_source_ids': ['history'],
                   'sections': [{'role': 'risk', 'heading': 'Historical risk', 'native_chart_id': 'bars_mae_mfe',
                                 'paragraphs': [{'text': 'Positive short results reflect stock price declines and rebounds can hurt.',
                                                 'source_ids': ['history'], 'kind': 'fact'}]},
                                {'role': 'current_context', 'heading': 'Company context',
                                 'paragraphs': [{'text': 'Company cut its delivery outlook.', 'source_ids': ['news'], 'kind': 'fact'}]}],
                   'takeaways': [{'text': 'History is not a forecast.', 'source_ids': ['history']}]}
        bundle = {'schema_version': 1, 'story_id': 'TEST', 'as_of': '2026-09-17T00:00:00Z',
                  'sources': [{'id': sid, 'url': 'https://example.com/' + sid, 'title': sid,
                               'excerpt': 'Inspected evidence', **({'max_derived_words': 50} if sid == 'news' else {})}
                              for sid in ('news', 'history')],
                  'records': [{'id': f'r{i}', 'value': i, 'unit': 'percent', 'period': '2025',
                               'locator': 'table 1', 'source_id': 'news', 'status': 'reported'} for i in (1, 2)],
                  'charts': [{'id': 'context', 'kind': 'bars', 'title': 'Context', 'subtitle': 'Reported facts',
                              'question': 'What changed?', 'note': 'Primary release', 'unit': 'percent',
                              'rows': [{'record_id': f'r{i}', 'label': str(i)} for i in (1, 2)]}],
                  'primary_chart_id': 'context', 'seasonal_contract': {'card_sha256': digest(card), 'history_source_id': 'history'}}
        bundle['sources'][1]['payload'] = {'window': {'start_date': '2026-09-22', 'end_date': '2026-10-11', 'calendar_days': 20},
                                          'cohort': {'label': 'Synthetic selected years', 'years': [2018, 2022]}}
        bundle['evidence_sha256'] = digest(bundle)
        review = {'passed': True, 'issues': [], 'checks': {name: {'passed': True} for name in CHECKS}}
        save_json(self.job / 'output.json', review)
        for name in ('prompt.txt', 'schema.json'):
            (self.job / name).write_text('{}', encoding='utf-8')
        hashes = {n: sha256((self.job / n).read_bytes()) for n in ('prompt.txt', 'schema.json')}
        receipt = {'stage': 'review', 'status': 'output_ready_for_smn_validation', 'publish': False,
                   'input_hashes': hashes, 'output_sha256': sha256((self.job / 'output.json').read_bytes()),
                   'evidence_sha256': bundle['evidence_sha256']}
        manifest = {'stage': 'review', 'publish': False, 'input_hashes': hashes,
                    'evidence_sha256': bundle['evidence_sha256'], 'valid_until': '2000-01-01T00:00:00Z'}
        for name, data in {'article.json': article, 'bundle.json': bundle, 'source.json': {'card': card},
                           'review-binding.json': {'article_sha256': digest(article), 'engine_card_sha256': digest(card),
                                                   'review_sha256': receipt['output_sha256'], 'review_stage': 'review'},
                           'mechanical-checks.json': {'passed': True, 'article_sha256': digest(article),
                                                      'source_words': source_word_counts(article, bundle, {'news': 10}),
                                                      'evidence_sha256': bundle['evidence_sha256']},
                           'chart-words.json': {'news': 10}}.items():
            save_json(self.article_dir / name, data)
        save_json(self.job / 'receipt.json', receipt)
        save_json(self.job / 'job.json', manifest)
        self.prepared = prepare_derivative(self.article_dir, self.job, self.url, 'v1')
        def statement(text):
            return {'text': text, 'source_ids': ['history'], 'article_refs': ['sections/0/paragraphs/0', 'takeaways/0']}
        self.copy = {'headline': statement('Weak history has a difficult path'),
                     'preview': [statement('Positive short results reflect price declines, while adverse rebounds can hurt.')],
                     'full_article_value': statement('Explore the historical risks in the complete article.'),
                     'qualification': statement('Historical evidence is not a forecast.'), 'social': [],
                     'video': {'narration': statement('Stock weakness can favor this short study, but rebounds can hurt.'),
                               'on_screen': statement('Historical short study'), 'native_chart_id': 'bars_mae_mfe'}}

    def _bind_review(self, review):
        from subscription_writer import load_json
        save_json(self.job / 'output.json', review)
        output_hash = sha256((self.job / 'output.json').read_bytes())
        for path, field in ((self.job / 'receipt.json', 'output_sha256'),
                            (self.article_dir / 'review-binding.json', 'review_sha256')):
            value = load_json(path); value[field] = output_hash; save_json(path, value)

    def test_authoritative_advisory_style_review_is_retained_exactly(self):
        from subscription_writer import load_json
        review = load_json(self.job / 'output.json')
        review['issues'] = [{'severity': 'minor', 'category': 'style', 'problem': 'Explain an abbreviation.'}]
        self._bind_review(review)
        original = (self.job / 'output.json').read_bytes()
        prepared = prepare_derivative(self.article_dir, self.job, self.url, 'v1')
        self.assertEqual(prepared['provenance']['review_sha256'], sha256(original))
        self.assertEqual((self.job / 'output.json').read_bytes(), original)

    def test_prompt_exposes_exact_cumulative_remaining_budgets(self):
        import json
        prepared = copy.deepcopy(self.prepared)
        prepared['full_source_words']['news'].update(total=131, maximum=200)
        prompt = prepare_prompt(prepared)
        body = json.loads(prompt.split('\n\n', 1)[1])
        self.assertEqual(body['remaining_source_word_budgets']['news'], 69)
        self.assertEqual(body['full_source_words']['news']['total'], 131)
        self.assertIn('EACH source ID', prompt)
        self.assertIn('Never omit a source needed for a claim', prompt)
        self.assertIn('65-100 preview words', prompt)

    def test_minor_hard_issues_and_missing_checks_cannot_qualify(self):
        from subscription_writer import load_json
        original = load_json(self.job / 'output.json')
        for category in ('factual', 'temporal', 'numeric', 'coverage'):
            review = copy.deepcopy(original)
            review['issues'] = [{'severity': 'minor', 'category': category, 'problem': 'Correction needed.'}]
            self._bind_review(review)
            with self.assertRaisesRegex(ValueError, 'reviewer receipt'):
                prepare_derivative(self.article_dir, self.job, self.url, 'v1')
        review = copy.deepcopy(original); review['checks'].pop('facts_and_sources')
        self._bind_review(review)
        with self.assertRaisesRegex(ValueError, 'reviewer receipt'):
            prepare_derivative(self.article_dir, self.job, self.url, 'v1')

    def test_valid_retained_review_keeps_direction_without_generation_or_approval(self):
        result = validate_derivative(self.copy, self.prepared)
        self.assertTrue(result['mechanical_passed'])
        self.assertFalse(result['approved'])
        self.assertFalse(result['publish'])
        self.assertTrue(result['semantic_review_required'])
        self.assertEqual(result['provenance']['study_identity']['direction'], 'short')

    def test_publication_alias_keeps_source_and_rebuilds_exact_binding(self):
        canonical='https://seasonalmarketnews.com/editions/2026-10-03/TEST/article.html'
        mapping={'canonical_id':canonical,'source_original_id':self.url,
            'article_sha256':self.prepared['provenance']['article_sha256'],'revision':'v2'}
        mapping['binding_sha256']=digest(mapping)
        original=(self.article_dir/'source.json').read_bytes()
        prepared=prepare_derivative(self.article_dir,self.job,canonical,'v2',publication_identity=mapping)
        self.assertTrue(validate_derivative(self.copy,prepared)['mechanical_passed'])
        self.assertEqual((self.article_dir/'source.json').read_bytes(),original)
        self.assertEqual(prepared['publication_identity']['source_original_id'],self.url)
        for field,value in [('source_original_id','https://seasonalmarketnews.com/other.html'),
                            ('article_sha256','a'*64),('revision','wrong'),('canonical_id',self.url)]:
            changed=dict(mapping);changed[field]=value
            changed['binding_sha256']=digest({k:v for k,v in changed.items() if k!='binding_sha256'})
            with self.assertRaises(ValueError):prepare_derivative(self.article_dir,self.job,canonical,'v2',publication_identity=changed)
        edited=copy.deepcopy(prepared);edited['publication_identity']['source_original_id']='other'
        with self.assertRaises(ValueError):validate_derivative(self.copy,edited)

    def test_stale_full_article_and_evidence_are_rejected(self):
        for name in ('article.json', 'bundle.json', 'source.json', 'output.json'):
            path = (self.job if name == 'output.json' else self.article_dir) / name
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.assertRaisesRegex(ValueError, 'input changed'):
                validate_derivative(self.copy, self.prepared)
            path.write_bytes(original)

    def test_unknown_refs_and_chart_and_writer_provenance_rejected(self):
        for field in ('source_ids', 'article_refs'):
            value = copy.deepcopy(self.copy)
            value['headline'][field] = ['unknown']
            with self.assertRaises(ValueError):
                validate_derivative(value, self.prepared)
        value = copy.deepcopy(self.copy); value['video']['native_chart_id'] = 'invented'
        with self.assertRaises(ValueError):
            validate_derivative(value, self.prepared)
        value = dict(self.copy, provenance=self.prepared['provenance'])
        with self.assertRaisesRegex(ValueError, 'extra fields'):
            validate_derivative(value, self.prepared)

    def test_material_qualification_cannot_be_omitted(self):
        value = dict(self.copy); del value['qualification']
        with self.assertRaisesRegex(ValueError, 'missing required'):
            validate_derivative(value, self.prepared)

    def test_changed_engine_direction_cannot_reuse_review(self):
        source = {'card': {'production_original': self.url, 'production_identity': {'direction': 'long'}}}
        save_json(self.article_dir / 'source.json', source)
        with self.assertRaisesRegex(ValueError, 'Engine card differs'):
            prepare_derivative(self.article_dir, self.job, self.url, 'v1')

    def test_cumulative_source_allowance_includes_social_and_video(self):
        value = copy.deepcopy(self.copy)
        value['social'] = [{'text': 'word ' * 40, 'source_ids': ['news'], 'article_refs': ['title']}]
        with self.assertRaisesRegex(ValueError, 'exceeds source allowance'):
            validate_derivative(value, self.prepared)

    def test_chart_allowance_changed_before_prepare_rejected(self):
        save_json(self.article_dir / 'chart-words.json', {'news': 0})
        with self.assertRaisesRegex(ValueError, 'approved mechanical receipt'):
            prepare_derivative(self.article_dir, self.job, self.url, 'v1')

    def test_unsupported_claim_binding_and_markup_rejected(self):
        for text, refs in [('Safe copy', ['news']), ('<script>alert(1)</script>', ['history'])]:
            value = copy.deepcopy(self.copy); value['headline'].update(text=text, source_ids=refs)
            with self.assertRaises(ValueError):
                validate_derivative(value, self.prepared)

    def test_prepared_provenance_and_budget_cannot_be_edited(self):
        for key in ('provenance', 'full_source_words'):
            prepared = copy.deepcopy(self.prepared); prepared[key] = {}
            with self.assertRaises((ValueError, KeyError)):
                validate_derivative(self.copy, prepared)

    def test_review_receipt_and_failed_semantic_review_rejected(self):
        (self.job / 'prompt.txt').write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Reviewer input changed'):
            prepare_derivative(self.article_dir, self.job, self.url, 'v1')
        (self.job / 'prompt.txt').write_text('{}', encoding='utf-8')
        save_json(self.job / 'output.json', {'passed': False, 'checks': {}, 'issues': ['wrong sign']})
        with self.assertRaisesRegex(ValueError, 'reviewer receipt'):
            prepare_derivative(self.article_dir, self.job, self.url, 'v1')

    def test_receive_cannot_overwrite_private_artifact(self):
        output = self.root / 'received'
        receive_derivative(self.copy, self.prepared, output)
        self.assertTrue((output / 'derivative.json').exists())
        with self.assertRaises(FileExistsError):
            receive_derivative(self.copy, self.prepared, output)


if __name__ == '__main__':
    unittest.main()
