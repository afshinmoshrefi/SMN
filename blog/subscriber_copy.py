"""Keep incident diagnostics in operator records, not subscriber narrative."""
import re

INTERNAL = re.compile(r'\b(?:required source and review corrections|review corrections|source corrections|internal review|publication pipeline|our pipeline|model jobs?|editorial failures?|held articles?)\b',re.I)


def validate(subject,narrative):
    if INTERNAL.search(subject+' '+narrative):
        raise ValueError('Subscriber copy contains internal incident diagnostics')
    return subject,narrative
