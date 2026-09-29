import unittest
from unittest.mock import patch

import article_workflow as wf


class PlainDomainTests(unittest.TestCase):
    """Sept 28-29: Grok path/wildcard domains made Tavily return HTTP 400; QQQ and VIX got no article."""

    def test_paths_wildcards_and_schemes_become_hostnames(self):
        self.assertEqual(wf.plain_domains([
            'invesco.com/us/etfs/us/products/qqq', 'investor.cboe.com/volatility-index', '*.cboe.com',
            'https://www.sec.gov/edgar', 'cboe.com', 'not a domain', '', 'reuters.com:443']),
            ['invesco.com', 'investor.cboe.com', 'cboe.com', 'sec.gov', 'reuters.com'])

    def test_rejected_domain_search_falls_back_to_open_search(self):
        calls = []
        def search(query, include_domains, days):
            calls.append(include_domains)
            if include_domains:
                raise RuntimeError('Tavily search failed: HTTP 400')
            return {'results': [{'url': 'https://x.com'}]}
        with patch.object(wf.AI_tools, 'search_tavily', side_effect=search):
            self.assertEqual(wf._tavily('QQQ news', ['invesco.com']), {'results': [{'url': 'https://x.com'}]})
        self.assertEqual(calls, [['invesco.com'], None])


if __name__ == '__main__':
    unittest.main()
