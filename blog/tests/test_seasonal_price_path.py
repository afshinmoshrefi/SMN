import csv
import hashlib
import io
import unittest
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from article_evidence import round_percent
from seasonal_price_path import derive, validate, sample_label
from visual_evidence import digest


def fixture():
    rows=[]
    day=date(2018,1,1)
    while day<=date(2022,9,2):
        if day.weekday()<5:
            value=Decimal(100+(day.year-2018)*20)+Decimal(day.timetuple().tm_yday)/10
            rows.append({'date':str(day),'open':str(value),'high':str(value+2),
                         'low':str(value-2),'close':str(value)})
        day+=timedelta(days=1)
    buffer=io.StringIO();writer=csv.DictWriter(buffer,fieldnames=list(rows[0]))
    writer.writeheader();writer.writerows(rows);blob=buffer.getvalue().encode()
    annual=[]
    for y in (2018,2020,2021):
        subset=[r for r in rows if f'{y}-09-12'<=r['date']<=f'{y}-10-01']
        start=Decimal(subset[0]['close'])
        values=[Decimal(subset[-1]['close']),max([start]+[Decimal(r['high']) for r in subset[1:]]),
                min([start]+[Decimal(r['low']) for r in subset[1:]])]
        annual.append({'year':y,**dict(zip(('net','mfe','mae'),[round_percent(100*(p-start)/start) for p in values]))})
    card={'symbol':'TEST','resource_id':'2','story_cell':{'per_year':annual,'years':'selected-3',
          'n':3,'anchor_date':'2022-09-12','days':20},'selection_evidence':{'baseline':{'per_year':deepcopy(annual)}},
          'instrument':{'semantics':{'measurement':'adjusted_price_return'}}}
    audit={'status':'passed','as_of':'2022-09-06T12:00:00+00:00','history':{
           'window_checks_passed':True,'expected_latest_date':'2022-09-02',
           'dataset_sha256':hashlib.sha256(blob).hexdigest()}}
    return card,blob,audit,rows


class PricePathTests(unittest.TestCase):
    def test_exact_selected_years_daily_median_and_inclusive_horizon(self):
        card,blob,audit,rows=fixture();result=derive(card,blob,audit)
        self.assertEqual(result['analysis_years'],[2018,2020,2021])
        self.assertEqual(len(result['projection']),60)
        self.assertEqual(result['projection'][-1]['date'],'2022-10-31')
        self.assertEqual(result['projection'][0]['median_return_pct'],0)
        self.assertEqual(len(result['annual_reconciliation']),3)
        # Independent date/close lookup, not the projection implementation.
        returns=[]
        for year in [2018,2020,2021]:
            first=max((r for r in rows if r['date']<=f'{year}-09-02'),key=lambda r:r['date'])
            last=max((r for r in rows if r['date']<=f'{year}-10-31'),key=lambda r:r['date'])
            returns.append(float(100*(Decimal(last['close'])/Decimal(first['close'])-1)))
        self.assertAlmostEqual(result['projection'][-1]['median_return_pct'],median(returns))
        self.assertIn('selected sample years',sample_label(result))
        validate(result,card)

    def test_annual_window_mismatch_is_rejected(self):
        card,blob,audit,_=fixture();card['story_cell']['per_year'][0]['net']+=1
        card['selection_evidence']['baseline']['per_year']=deepcopy(card['story_cell']['per_year'])
        with self.assertRaisesRegex(ValueError,'reproduce'):derive(card,blob,audit)

    def test_a_different_daily_snapshot_cannot_reuse_the_audit(self):
        card,blob,audit,_=fixture()
        with self.assertRaisesRegex(ValueError,'differs'):derive(card,blob+b'\n',audit)

    def test_held_calendar_does_not_silently_pass(self):
        card,blob,audit,_=fixture();audit['status']='held'
        with self.assertRaisesRegex(ValueError,'not passed'):derive(card,blob,audit)
        with self.assertRaisesRegex(ValueError,'GC'):derive(card,blob,audit,reference_only=True)

    def test_changed_card_and_path_are_rejected(self):
        card,blob,audit,_=fixture();result=derive(card,blob,audit)
        other=deepcopy(card);other['story_cell']['years']='20'
        with self.assertRaisesRegex(ValueError,'binding'):validate(result,other)
        result['projection'][-1]['illustrative_price']+=5
        with self.assertRaisesRegex(ValueError,'changed'):validate(result,card)

    def test_zero_days_or_duplicate_years_are_rejected(self):
        card,blob,audit,_=fixture()
        with self.assertRaisesRegex(ValueError,'horizon'):derive(card,blob,audit,horizon_days=0)
        card['story_cell']['per_year'][1]['year']=2018
        with self.assertRaisesRegex(ValueError,'sample'):derive(card,blob,audit)

    def test_rehashed_but_incorrect_plot_is_rejected(self):
        card,blob,audit,_=fixture();result=derive(card,blob,audit)
        result['projection'][20]['median_return_pct']+=1
        result.pop('evidence_sha256');result['evidence_sha256']=digest(result)
        with self.assertRaisesRegex(ValueError,'Plotted daily median'):validate(result,card)

    def test_reference_series_retains_held_status(self):
        card,blob,audit,_=fixture();card.update(symbol='GC',resource_id='7')
        card['instrument']['semantics']['measurement']='provider_reference_price_change'
        audit.update(status='reference_calculation_only',independent_calendar_validation='held',
                     production_release_allowed=False,dataset_sha256=audit['history']['dataset_sha256'])
        result=derive(card,blob,audit,reference_only=True)
        self.assertEqual(result['independent_calendar_validation'],'held')
        self.assertFalse(result['production_release_allowed'])


if __name__=='__main__':unittest.main()
