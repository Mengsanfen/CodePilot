import httpx
q = httpx.QueryParams([('a',''),('a','1'),('a','2'),('b',''),('c',' ')])
r = q.without_empty()
assert r.multi_items() == [('a','1'),('a','2'),('c',' ')]
assert q.get_list('a') == ['', '1', '2']
assert str(httpx.QueryParams().without_empty()) == ''
assert r is not q

