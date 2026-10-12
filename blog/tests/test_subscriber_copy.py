from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from subscriber_copy import validate

class SubscriberCopyTests(unittest.TestCase):
    def test_incident_explanation_is_rejected(self):
        for text in ['required source and review corrections are completed','our publication pipeline failed','internal review remains pending','model jobs exceeded budget']:
            with self.subTest(text=text),self.assertRaises(ValueError):validate('Daily briefing',text)

    def test_business_context_and_honest_subset_are_allowed(self):
        for text in ['Today covers CPRT, XLK and JPM.','JNJ discussed its product pipeline.','Additional coverage may follow.']:
            self.assertEqual(validate('Daily briefing',text),('Daily briefing',text))

if __name__=='__main__':unittest.main()
