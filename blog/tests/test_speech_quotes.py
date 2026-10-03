from datetime import datetime,timezone,timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from daily_briefing import digest
from subscription_writer import save_json,sha256
import speech_quotes
import elevenlabs_client


class Quotes(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        evidence={}
        for key in ('account_pricing','model','voice'):
            path=self.root/(key+'.json');path.write_bytes(b'TEST ONLY verified tariff evidence')
            evidence[key]={'path':str(path),'sha256':sha256(path.read_bytes())}
        now=datetime.now(timezone.utc)
        account_path=self.root/'account_pricing.json'
        save_json(account_path,{'provider':'elevenlabs','authenticated':True,'account_sha256':'a'*64,
            'credential_sha256':'d'*64,'observed_at':now.isoformat()})
        evidence['account_pricing']['sha256']=sha256(account_path.read_bytes())
        self.tariff={'version':1,'provider':'elevenlabs','verified':True,'verified_by':'Test operator',
            'unit':'credits','billing_basis':'input_unicode_codepoints','model_id':'test-model','voice_id':'test-voice',
            'account_sha256':'a'*64,'credential_sha256':'d'*64,'settings_sha256':digest({}),'credits_per_character_upper_bound':'1.1',
            'verified_at':now.isoformat(),'valid_until':(now+timedelta(hours=1)).isoformat(),'evidence':evidence}
        self.path=self.root/'tariff.json';save_json(self.path,self.tariff)

    def quote(self,text='Test'):
        return speech_quotes.quote(self.path,text,'test-voice','test-model',{},'a'*64,'d'*64)

    def test_varying_scripts_round_up_to_distinct_exact_requests_without_network(self):
        with patch.object(elevenlabs_client,'_request') as request:
            a=self.quote('Test');b=self.quote('Test longer')
            self.assertEqual(a['credits'],5);self.assertEqual(b['credits'],13)
            self.assertNotEqual(a['request_sha256'],b['request_sha256']);request.assert_not_called()
            self.assertNotIn(str(self.root),str(a))

    def test_expired_or_incomplete_or_changed_account_evidence_holds(self):
        for field,value in [('voice_id','other'),('account_sha256','b'*64),('credential_sha256','c'*64),('unit','usd'),('billing_basis','token_cost_factor'),
                            ('valid_until','2020-01-01T00:00:00Z')]:
            changed=dict(self.tariff,**{field:value});save_json(self.path,changed)
            with self.assertRaises(ValueError):self.quote()
        save_json(self.path,self.tariff)
        Path(self.tariff['evidence']['voice']['path']).write_bytes(b'Changed')
        with self.assertRaises(ValueError):self.quote()

    def test_authenticated_account_evidence_cannot_be_rebound_to_a_rotated_key(self):
        account=Path(self.tariff['evidence']['account_pricing']['path'])
        save_json(account,{'provider':'elevenlabs','authenticated':True,'account_sha256':'a'*64,
            'credential_sha256':'c'*64,'observed_at':self.tariff['verified_at']})
        self.tariff['evidence']['account_pricing']['sha256']=sha256(account.read_bytes())
        save_json(self.path,self.tariff)
        with self.assertRaisesRegex(ValueError,'authenticated account evidence'):self.quote()

    def test_avatar_ceilings_are_api_bound_and_round_seconds_without_network(self):
        tariff={k:v for k,v in self.tariff.items() if k not in ('voice_id','settings_sha256','credits_per_character_upper_bound')}
        tariff.update(model_id='creatify-aurora',billing_basis='ceil_input_audio_seconds',api_route='/v1/flows/video',
                      resolution='720p',reference_sha256='b'*64,credits_per_second_upper_bound='100',
                      evidence={k:v for k,v in self.tariff['evidence'].items() if k!='voice'})
        save_json(self.path,tariff)
        quote=speech_quotes.avatar_quote(self.path,'b'*64,'c'*64,'720p',3.2,'a'*64,'d'*64)
        self.assertEqual(quote['credits'],400)
        with self.assertRaises(ValueError):speech_quotes.avatar_quote(self.path,'b'*64,'c'*64,'480p',3.2,'a'*64,'d'*64)
        tariff['api_route']='manual_ui';save_json(self.path,tariff)
        with self.assertRaises(ValueError):speech_quotes.avatar_quote(self.path,'b'*64,'c'*64,'720p',3.2,'a'*64,'d'*64)

    def test_existing_credit_ledger_is_used_and_never_reset(self):
        quote=self.quote('Test')
        save_json(self.root/'elevenlabs-budget.json',{'ceiling':5000,'reservations':[
            {'request_sha256':'old','quoted_credits':4998,'status':'received'}]})
        before=(self.root/'elevenlabs-budget.json').read_bytes()
        with self.assertRaises(elevenlabs_client.Held):elevenlabs_client._reserve(self.root,quote['request_sha256'],quote)
        self.assertEqual((self.root/'elevenlabs-budget.json').read_bytes(),before)

if __name__=='__main__':unittest.main()
