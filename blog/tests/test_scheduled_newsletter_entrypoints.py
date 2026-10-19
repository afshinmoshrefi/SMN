"""Run real scheduler/sender CLI processes against private Linux fixtures.

Only provider and model boundaries are faked. Path redirection plus a write
audit hook prevents the child from changing anything outside its fixture.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.newsletter_publication_fixture import publication, write


GUARD = r'''
import builtins, datetime, io, json, os, pathlib, socket, subprocess, sys, types
root = os.environ['SMN_CLI_FIXTURE']
source = os.environ['SMN_OFFLINE_SOURCE_ROOT']
mapping = {
 '/var/lib/tradewave/smn-daily/subscription-primary':root+'/controller',
 '/var/lib/tradewave/release-state':root+'/release',
 '/var/lib/smn-dashboard':root+'/dashboard',
 '/var/www/smn':root+'/web',
 '/home/flask/blog/logs':root+'/logs',
 '/etc/SMN':root+'/etc',
}
def redirect(value):
 if isinstance(value,(str,bytes,os.PathLike)):
  text=os.fsdecode(value)
  for original,target in mapping.items():
   if text==original or text.startswith(original+'/'):return target+text[len(original):]
 return value
original_new=pathlib.Path.__new__
def path_new(cls,*args,**kwargs):
 if args:args=(redirect(args[0]),*args[1:])
 return original_new(cls,*args,**kwargs)
pathlib.Path.__new__=path_new
original_init=pathlib.Path.__init__
if original_init is not object.__init__:
 def path_init(self,*args,**kwargs):
  if args:args=(redirect(args[0]),*args[1:])
  original_init(self,*args,**kwargs)
 pathlib.Path.__init__=path_init
original_open=builtins.open
def safe_open(file,*args,**kwargs):return original_open(redirect(file),*args,**kwargs)
builtins.open=safe_open
def permitted_path(value):
 if isinstance(value,int):return
 target=os.path.realpath(os.fsdecode(value))
 if target!=root and not target.startswith(root+'/'):
  raise AssertionError('CLI fixture attempted an outside write: '+target)
def audit(event,args):
 if event=='open' and len(args)>2 and isinstance(args[2],int) and args[2] & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):permitted_path(args[0])
 elif event in {'os.mkdir','os.remove','os.rmdir','os.chmod','os.utime'}:permitted_path(args[0])
 elif event in {'os.rename','os.link','os.symlink'}:
  permitted_path(args[0]);permitted_path(args[1])
sys.addaudithook(audit)
def deny(*args,**kwargs):raise AssertionError('CLI fixture forbids network/model calls')
socket.socket.connect=deny
socket.socket.connect_ex=deny
socket.create_connection=deny
socket.getaddrinfo=deny
original_popen=subprocess.Popen
def popen(args,*rest,**kwargs):
 allowed={source+'/blog/send_smn_emails.py',source+'/blog/operational_schedule.py',source+'/blog/smn_operational_alerts.py'}
 if not isinstance(args,list) or args[0]!=sys.executable or len(args)<2 or args[1] not in allowed:raise AssertionError('CLI fixture forbids unapproved subprocess')
 return original_popen(args,*rest,**kwargs)
subprocess.Popen=popen
real_datetime=datetime.datetime
class FrozenDateTime(real_datetime):
 @classmethod
 def now(cls,tz=None):
  instant=real_datetime.fromisoformat(os.environ['SMN_CLI_INSTANT'])
  return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
datetime.datetime=FrozenDateTime
config=types.ModuleType('config')
config.news_root_folder=root+'/web'
config.news_website_url='https://seasonalmarketnews.com'
config.smn_from_name='SMN offline qualification'
config.smn_from_email='sender@example.invalid'
config.smn_favicon='https://seasonalmarketnews.com/favicon.png'
config.mailerlite_token='offline-test-token'
sys.modules['config']=config
def append_provider(value):
 with original_open(root+'/provider.jsonl','a') as output:output.write(json.dumps(value)+'\n')
def create_campaign(name,subject,from_name,from_email,group,html):
 with original_open(root+'/logs/sent_smn_emails.json') as stream:state=json.load(stream)
 key=('weekly:' if 'Weekly' in name else 'daily:')+name[-10:]
 record=state['campaigns'][key]
 assert record['phase']=='create_unknown' and record['submitted_html']==html
 append_provider({'action':'create','name':name,'html':html,'group':group,'intent_before_post':True})
 if os.environ.get('SMN_CLI_PROVIDER_FAILURE')=='create_unknown':raise RuntimeError('Synthetic unknown provider result')
 return ('offline-123','created')
email=types.ModuleType('email_tools')
email.get_email_groups=lambda:{'SMN-DAILY':'offline-daily','SMN-WEEKLY':'offline-weekly'}
email.create_campaign=create_campaign
for name in ['create_mailerlite_group','create_subscriber','assign_subscriber_to_a_group','get_subscriber_by_email']:setattr(email,name,deny)
sys.modules['email_tools']=email
ai=types.ModuleType('AI_tools')
def narrative(*args,**kwargs):
 append_provider({'action':'mock_weekly_narrative'})
 return json.dumps({'subject':'Weekly market coverage','narrative':'Read the recent market articles below.'})
ai.send_openai_prompt=narrative
sys.modules['AI_tools']=ai
import requests
class Response:
 def __init__(self,data):self.data=data
 def raise_for_status(self):pass
 def json(self):return self.data
def get(url,**kwargs):
 assert url=='https://connect.mailerlite.com/api/timezones'
 return Response({'data':[{'name':'America/New_York','id':'156'}]})
def post(url,**kwargs):
 assert url=='https://connect.mailerlite.com/api/campaigns/offline-123/schedule'
 append_provider({'action':'schedule','payload':kwargs['json']})
 return Response({'data':{'id':'offline-123','status':'ready'}})
requests.get=get
requests.post=post
import urllib.request
class RelayResponse(io.BytesIO):
 status=200
def relay(request,**kwargs):
 assert request.full_url.startswith('https://api.resend.com/emails')
 if request.get_method()=='POST':
  payload=json.loads(request.data)
  assert payload['to']==['operator@example.invalid']
  append_provider({'action':'alert_create','payload':payload,'idempotency':request.get_header('Idempotency-key')})
  if os.environ.get('SMN_CLI_PROVIDER_FAILURE')=='alert':raise OSError('Synthetic relay unavailable')
  with original_open(root+'/relay.json','w') as output:json.dump(payload,output)
  return RelayResponse(json.dumps({'id':'offline-alert'}).encode())
 assert request.full_url=='https://api.resend.com/emails/offline-alert'
 with original_open(root+'/relay.json') as stream:payload=json.load(stream)
 append_provider({'action':'alert_get'})
 return RelayResponse(json.dumps({'id':'offline-alert','to':payload['to'],'subject':payload['subject'],'last_event':'delivered'}).encode())
urllib.request.urlopen=relay
'''


@unittest.skipUnless(os.name == 'posix', 'Real scheduled subprocesses require Linux')
class ScheduledNewsletterEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='smn-cli-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = Path(__file__).resolve().parents[2]
        for name in ['controller', 'release', 'web', 'dashboard', 'logs', 'etc', 'guard']:
            (self.root/name).mkdir()
        (self.root/'guard/sitecustomize.py').write_text(GUARD)
        self.env = os.environ.copy()
        self.env.update(SMN_CLI_FIXTURE=str(self.root), SMN_OFFLINE_SOURCE_ROOT=str(self.source),
            PYTHONPATH=str(self.root/'guard')+':'+str(self.source/'blog'),
            PYTHONDONTWRITEBYTECODE='1', SMN_CLI_INSTANT='2026-10-19T18:30:00+00:00',
            TZ='UTC')
        self.rows = []

    def add_day(self, day, symbols, missing=0, published=None):
        rows = [{'symbol':symbol,'slug':day+'-'+symbol,'title':symbol+' market analysis',
            'url':'https://seasonalmarketnews.com/editions/'+day+'/'+symbol+'/article.html',
            'published_date':day,'hero_status':'unavailable' if index<missing else 'available',
            'hero_image':None if index<missing else 'https://seasonalmarketnews.com/assets/'+symbol+'.jpg'}
            for index,symbol in enumerate(symbols)]
        publication(self.root/'controller',self.root/'web',self.root/'release',day,rows,published)
        self.rows.extend(row for row in rows if published is None or row['symbol'] in published)
        write(self.root/'web/posts.json',self.rows)

    def cli(self, script='operational_schedule.py', args=('--target','production','--newsletter-only'), expected=0):
        result = subprocess.run([sys.executable,str(self.source/'blog'/script),*args],
            cwd=self.source/'blog',env=self.env,capture_output=True,text=True,timeout=45)
        self.assertEqual(result.returncode,expected,result.stdout+'\n'+result.stderr)
        self.assertNotIn('outside write',result.stderr)
        self.assertNotIn('forbids',result.stderr)
        return result

    def events(self):
        file=self.root/'provider.jsonl'
        return [json.loads(line) for line in file.read_text().splitlines()] if file.exists() else []

    def state(self):return json.loads((self.root/'logs/sent_smn_emails.json').read_text())

    def enable_alert_fixture(self):
        from operational_settings import DEFAULTS
        settings=json.loads(json.dumps(DEFAULTS))
        settings.update(alerts_enabled=True,alert_recipients=['operator@example.invalid'])
        write(self.root/'dashboard/operational-settings.json',settings)
        write(self.root/'dashboard/schedule-runs/production-weekday_newsletter-2026-10-19.json',
            {'target':'production','phase':'weekday_newsletter','date':'2026-10-19',
             'status':'failed','exit_code':1})
        self.env['RESEND_API_KEY']='offline-test-key'

    def alert_cli(self):
        return json.loads(self.cli('smn_operational_alerts.py',
            ('--root','/var/lib/tradewave/smn-daily/subscription-primary','--target','production')).stdout)

    def test_actual_weekday_scheduler_and_sender_restart_create_one_real_render(self):
        self.add_day('2026-10-19',['QQQ','SPY','XLF','AMZN','CSX','CCK'])
        self.cli()
        before=(self.root/'logs/sent_smn_emails.json').read_bytes()
        self.cli()
        self.cli('send_smn_emails.py',('--verified-reader-date','2026-10-19'))
        self.assertEqual((self.root/'logs/sent_smn_emails.json').read_bytes(),before)
        events=self.events()
        self.assertEqual([row['action'] for row in events],['create','schedule'])
        self.assertEqual(events[0]['html'].count(' seasonal pattern"'),6)
        record=self.state()['campaigns']['daily:2026-10-19']
        self.assertEqual(record['phase'],'scheduled')
        self.assertEqual(len(record['reader_snapshot']['article_data']),6)
        self.assertEqual(record['submitted_html_sha256'],hashlib.sha256(events[0]['html'].encode()).hexdigest())

    def test_actual_deadline_partial_edition_sends_five_without_waiting_for_sixth(self):
        self.add_day('2026-10-19',['QQQ','SPY','XLF','AMZN','CSX','CCK'],published=['QQQ','SPY','XLF','AMZN','CSX'])
        self.cli()
        record=self.state()['campaigns']['daily:2026-10-19']
        self.assertEqual(len(record['slugs']),5)
        self.assertNotIn('2026-10-19-CCK',record['slugs'])
        self.assertEqual(len([e for e in self.events() if e['action']=='create']),1)

    def test_actual_weekly_scheduler_retains_five_fallbacks_eleven_images_and_deduplicates(self):
        self.add_day('2026-10-16',['GS','PGR','C','XLK','MRK'],missing=5)
        self.add_day('2026-10-15',['SPX','OZK','ASML'])
        self.add_day('2026-10-14',['MSFT','NVDA','AMZN','QQQ','SPY','GC'])
        self.add_day('2026-10-13',['WFC','CSX'])
        self.env['SMN_CLI_INSTANT']='2026-10-18T09:00:00+00:00'
        self.cli(args=('--target','production'))
        self.cli(args=('--target','production'))
        self.cli('send_smn_emails.py',())
        self.assertEqual([row['action'] for row in self.events()],['mock_weekly_narrative','create','schedule'])
        record=self.state()['campaigns']['weekly:2026-10-18']
        self.assertEqual(len(record['slugs']),16)
        self.assertEqual(record['submitted_html'].count(' seasonal pattern"'),11)
        self.assertEqual([row['symbol'] for row in record['media_warnings']],['GS','PGR','C','XLK','MRK'])

    def test_uncertain_provider_create_is_not_repeated_by_scheduler_or_sender_restart(self):
        self.add_day('2026-10-19',['QQQ','SPY','XLF','AMZN','CSX','CCK'])
        self.env['SMN_CLI_PROVIDER_FAILURE']='create_unknown'
        self.cli(expected=1)
        before=(self.root/'logs/sent_smn_emails.json').read_bytes()
        self.env['SMN_CLI_INSTANT']='2026-10-19T18:36:00+00:00'
        self.cli()
        self.cli('send_smn_emails.py',('--verified-reader-date','2026-10-19'))
        self.assertEqual((self.root/'logs/sent_smn_emails.json').read_bytes(),before)
        self.assertEqual([row['action'] for row in self.events()],['create'])
        self.assertEqual(self.state()['campaigns']['daily:2026-10-19']['phase'],'create_unknown')

    def test_actual_alert_cli_distinguishes_acceptance_from_delivery_and_deduplicates(self):
        self.enable_alert_fixture()
        first=self.alert_cli()
        self.assertEqual((first['accepted'],first['delivered']),(1,0))
        self.env['SMN_CLI_INSTANT']='2026-10-19T18:31:00+00:00'
        second=self.alert_cli()
        self.assertEqual((second['accepted'],second['delivered']),(0,1))
        self.alert_cli()
        self.assertEqual([row['action'] for row in self.events()],['alert_create','alert_get'])
        state=json.loads((self.root/'controller/operational-alerts-state.json').read_text())
        self.assertEqual(len(state['accepted']),1)
        self.assertEqual(len(state['delivered']),1)
        self.assertTrue(self.events()[0]['idempotency'].startswith('smn-alert-'))

    def test_actual_alert_cli_relay_failure_is_visible_and_retry_attempts_are_bounded(self):
        self.enable_alert_fixture()
        self.env['SMN_CLI_PROVIDER_FAILURE']='alert'
        for instant in ['18:30','18:36','18:42','18:48']:
            self.env['SMN_CLI_INSTANT']='2026-10-19T'+instant+':00+00:00'
            result=self.alert_cli()
            self.assertEqual((result['accepted'],result['delivered']),(0,0))
        events=self.events()
        self.assertEqual([row['action'] for row in events],['alert_create']*3)
        self.assertEqual(len({row['idempotency'] for row in events}),1)
        status=json.loads((self.root/'dashboard/operational-alerts-status.json').read_text())
        self.assertEqual(status['delivery_state'],'needs_attention')
        self.assertIn('retry limit reached',status['last_delivery_error'])


if __name__ == '__main__':
    unittest.main()
