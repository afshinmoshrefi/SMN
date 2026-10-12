import copy,unittest
from subscription_edition import source_word_counts

class InternalSourceBudgetPolicyTests(unittest.TestCase):
    def counts(self,policy=None):
        source={'id':'issuer','title':'Q2 results','max_derived_words':200}
        if policy:source['word_budget_policy']=policy
        article={'title':'word '*59,'title_source_ids':['issuer'],'dek':'','dek_source_ids':[],
                 'sections':[{'heading':'Current business results now','paragraphs':[]}],'takeaways':[]}
        # 59 words prose + 140 chart + 6 heading/citation in the real incident.
        article['sections'][0]['paragraphs']=[{'text':'','source_ids':['issuer']}]
        return source_word_counts(article,{'sources':[source]},{'issuer':140})['issuer']
    def test_five_word_internal_overage_warns_without_holding_facts(self):
        row=self.counts('internal_editing_target_v1')
        self.assertEqual(row['total'],205);self.assertEqual(row['maximum'],200)
        self.assertTrue(row['passed']);self.assertEqual(row['over_target_words'],5)
        self.assertIn('factual/source checks',row['warning'])
    def test_legacy_explicit_source_limit_still_holds(self):
        self.assertFalse(self.counts()['passed'])
    def test_contractual_or_unknown_policy_cannot_be_silently_relaxed(self):
        for policy in ['contractual','typo-in-policy']:
            self.assertFalse(self.counts(policy)['passed'])

if __name__=='__main__':unittest.main()
