import httpx
q = httpx.QueryParams('a=1&a=2&b=3')
values = q.get_list('a'); values.append('bad')
assert q.get_list('a') == ['1','2'] and str(q) == 'a=1&a=2&b=3'
missing = q.get_list('absent'); missing.append('bad')
assert q.get_list('absent') == []
url = httpx.URL('https://example.test/?a=1&a=2')
url.params.get_list('a').clear()
assert url.params.get_list('a') == ['1','2']

