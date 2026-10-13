"""Bounded literal English calendar-date grammar, not a semantic factcheck."""
from datetime import date
import re

MONTHS={name:month for month,names in enumerate([
    (),('january','jan'),('february','feb'),('march','mar'),('april','apr'),('may',),
    ('june','jun'),('july','jul'),('august','aug'),('september','sep','sept'),
    ('october','oct'),('november','nov'),('december','dec')]) for name in names}
TOKENS=re.compile(r'[^\W\d_]+|\d+|\s+|[^\w\s]|_',re.UNICODE)
DASHES={'-','\u2010','\u2011','\u2012','\u2013','\u2014','\u2212'}
QUOTES={"'",'\u2019'}

def ordinal(day):
    return 'th' if 10<=day%100<=20 else {1:'st',2:'nd',3:'rd'}.get(day%10,'th')

def contains_date(text,event,*,allow_yearless=False):
    # ISO tokens must be complete; an extra digit cannot be a partial match.
    if re.search(r'(?<![\w-])'+re.escape(event.isoformat())+r'(?![\w-])',text):return True
    tokens=list(TOKENS.finditer(text))
    def value(i):return tokens[i].group() if i<len(tokens) else ''
    def space(i):
        while i<len(tokens) and value(i).isspace():i+=1
        return i
    def day_at(i):
        i=space(i);number=value(i)
        if not number.isdigit() or len(number)>2 or not 1<=int(number)<=31:return None
        d=int(number);i+=1
        suffix=value(i).lower()
        if suffix in {'st','nd','rd','th'}:
            if suffix!=ordinal(d):return None
            i+=1
        elif value(i) and value(i)[0].isalpha() and i<len(tokens) and tokens[i].start()==tokens[i-1].end():
            return None  # A number inside a word/address is not a date.
        return d,i
    def year_at(i):
        i=space(i);apostrophe=False;slash=False
        while i<len(tokens):
            token=value(i)
            if token.isspace():i+=1;continue
            if token in {'.','!','?'}:
                # A period followed by whitespace ends this date's sentence.
                if token!='.' or i+1==len(tokens) or value(i+1).isspace():return 'none',None
                # A directly adjoining four-digit year is dotted date syntax.
                if not value(i+1).isdigit() or len(value(i+1))!=4:return 'none',None
                i+=1;continue
            if token.isdigit():
                if i+1<len(tokens) and tokens[i].end()==tokens[i+1].start() and value(i+1)[0].isalpha():
                    return 'invalid',None  # A year inside a word is not a date.
                after=space(i+1)
                if len(token)<=2 and value(after)==':' and value(after+1).isdigit():
                    return 'none',None  # Clock time, not a year.
                if len(token)==4:return 'full',int(token)
                if len(token)==2 and (apostrophe or slash):return 'short',int(token)
                return 'invalid',None
            if token[0].isalpha():
                if token.lower() in {'in','of','year'}:
                    connector=token.lower()
                    i=space(i+1)
                    if value(i).lower()=='the':i=space(i+1)
                    explicit_year=value(i).lower()=='year'
                    if explicit_year:i=space(i+1)
                    if value(i).isdigit():continue
                    if connector=='in' and not explicit_year:
                        # "in New York/the morning/person" is ordinary prose.
                        # Punctuation may still introduce an explicit numeric
                        # year; keep parsing it rather than using yearless.
                        if value(i) and not value(i)[0].isalpha():continue
                        return 'none',None
                    return 'invalid',None
                return 'none',None
            apostrophe|=token in QUOTES;slash|=token=='/'
            i+=1
        return 'none',None
    for n,token in enumerate(tokens):
        month=MONTHS.get(token.group().lower())
        if month!=event.month:continue
        i=space(n+1)
        if value(i)=='.':i=space(i+1)
        parsed=day_at(i)
        if parsed is None:continue
        first,i=parsed;days={first};i=space(i)
        # A dash followed by a day is a range, not a two-digit year. A quoted
        # short year and an explicit four-digit year remain year annotations.
        if value(i) in DASHES or value(i).lower() in {'to','through'}:
            end=space(i+1)
            if value(end).isdigit() and len(value(end))<=2:
                parsed_end=day_at(end)
                if parsed_end is None:continue
                last,i=parsed_end
                if last<first:continue
                days.add(last);i=space(i)
            elif value(end).lower() in MONTHS:
                continue  # Cross-month/year ranges need explicit separate dates.
        kind,year=year_at(i)
        if event.day not in days or kind=='invalid':continue
        try:
            for d in days:date(event.year,month,d)
        except ValueError:continue
        if kind=='full' and year==event.year:return True
        if kind=='short' and allow_yearless and year==event.year%100:return True
        if kind=='none' and allow_yearless:
            return True
    return False
