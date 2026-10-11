import httpx
q = httpx.QueryParams('a=1&a=2&b=3')
result = q.add('a', True)
assert result.get_list('a') == ['1','2','true']
assert q.get_list('a') == ['1','2']
assert result.get('b') == '3'
assert q.add('new', None).get('new') == ''
url = httpx.URL('https://example.test/?a=1').copy_add_param('a', 2)
assert url.params.get_list('a') == ['1','2']

