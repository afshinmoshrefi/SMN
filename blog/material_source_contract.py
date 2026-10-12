"""Explicit newer primary restatements; no inferred financial equivalence."""
from datetime import datetime
import re

def accepted_source_ids(item, sources, documents):
    original=item['source_id'];result=[original]
    if 'alternative_sources' not in item:return tuple(result)
    alternatives=item['alternative_sources']
    if not isinstance(alternatives,list) or len(alternatives)>3:
        raise ValueError('At most three explicit primary restatements are allowed')
    original_doc=documents.get(sources.get(original,{}).get('url'))
    if not original_doc:raise ValueError('Original primary source unavailable')
    for alternative in alternatives:
        if not isinstance(alternative,dict) or set(alternative)!={'source_id','quote','same_fact_reason'}:
            raise ValueError('Restatement needs source, exact quote and equivalence reason')
        identifier=alternative['source_id'];quote=alternative['quote'];reason=alternative['same_fact_reason']
        document=documents.get(sources.get(identifier,{}).get('url'))
        if identifier in result or not document:raise ValueError('Restatement source missing or duplicated')
        if not isinstance(quote,str) or len(quote.strip())<20 or re.sub(r'\s+',' ',quote).strip() not in re.sub(r'\s+',' ',document['text']):
            raise ValueError('Restatement quote must match captured primary text')
        if not isinstance(reason,str) or len(reason.strip())<25:
            raise ValueError('Restatement must identify the same fact, period, measure and basis')
        for date in (original_doc.get('date',''),document.get('date','')):
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date):raise ValueError('Restatement dates must be established')
            datetime.strptime(date,'%Y-%m-%d')
        if document['date']<original_doc['date']:raise ValueError('Alternative must be an equally current or newer primary restatement')
        result.append(identifier)
    return tuple(result)
