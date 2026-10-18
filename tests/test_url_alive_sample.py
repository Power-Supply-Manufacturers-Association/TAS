"""scripts/url_alive_sample.py: API citations are skipped, echoing URL patterns do not score ok. No network."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import url_alive_sample as u

KSIM = 'https://ksim3.kemet.com/api/csv/'
PAGE = 'https://example.com/part/{}'


def net(monkeypatch, pages):
    """pages: url -> (status, body bytes, content type); anything else 404. Counts fetches."""
    seen = []

    def fetch(url):
        seen.append(url)
        st, body, ct = pages.get(url, (404, b'', ''))
        return 'http', st, body, ct
    monkeypatch.setattr(u, 'fetch', fetch)
    monkeypatch.setattr(u, 'ECHO', {}); monkeypatch.setattr(u, 'RENDER_CONTROL', {})
    return seen


def test_api_endpoint_is_not_a_candidate_next_citation_is():
    prov = [{'sourceUrl': KSIM, 'verification': 'valuesReadFromSource'},
            {'sourceUrl': 'https://yageogroup.com/search/unique-parts/X1', 'verification': 'partNamed'}]
    c = u.candidates(prov, 'https://example.com/ds.pdf')
    assert [x[0] for x in c] == ['https://yageogroup.com/search/unique-parts/X1', 'https://example.com/ds.pdf']
    for api in (KSIM, 'https://ds.yuden.co.jp/TYCOMPAS/eu/graphRest',
                'https://www.wago.com/wagoapi/v2/global-wago/products/2104-5205/classifications?x=1',
                'https://pimapi.murata.com/public/api/pim/v1/products/search/X', 'https://api.harwin.com/v1/x',
                'https://product.tdk.com/pdc_api/en/search/x'):
        assert u.is_api_url(api), api
    for doc in ('https://www.vishay.com/docs/91938/sihp065n60e.pdf', 'https://www.wago.com/global/p/2104-5205',
                'https://yageogroup.com/search/unique-parts/X1', 'https://www.rapidapart.com/x/y.pdf'):
        assert not u.is_api_url(doc), doc


def test_post_only_and_json_citations_skip_to_next(monkeypatch):
    seen = net(monkeypatch, {'https://a.example/doc1': (405, b'', ''),
                             'https://b.example/doc2': (200, b'{"categories": []}', 'application/json'),
                             'https://c.example/doc3.html': (200, b'<p>PN123X datasheet</p>', 'text/html')})
    v, why, url, skipped = u.resolve('PN123X', [('https://a.example/doc1', None), ('https://b.example/doc2', None),
                                               ('https://c.example/doc3.html', None)])
    assert (v, url) == ('ok', 'https://c.example/doc3.html') and len(skipped) == 2
    v, *_ = u.resolve('PN123X', [('https://a.example/doc1', None)])
    assert v == 'skip'  # nothing scorable: not booked as dead


def test_echoing_pattern_scores_zero_not_ok(monkeypatch):
    # the page prints whatever number is in the URL (an SPA shell); no browser available
    class Echo(dict):
        def get(self, url, d=None):
            return 200, ('<html>Part ' + url.rsplit('/', 1)[1] + '</html>').encode(), 'text/html'
    net(monkeypatch, Echo())
    monkeypatch.setattr(u, 'rendered_names', lambda pn, url: None)
    v, why = u.classify_cited('C0603C681J5RACTX9', PAGE.format('C0603C681J5RACTX9'))
    assert v == 'wrong' and 'echo' in why
    # rendered check available and the fake renders "can't find": the real part is judged on its render
    monkeypatch.setattr(u, 'ECHO', {}); monkeypatch.setattr(u, 'RENDER_CONTROL', {})
    monkeypatch.setattr(u, 'rendered_names', lambda pn, url: pn == 'C0603C681J5RACTX9')
    assert u.classify_cited('C0603C681J5RACTX9', PAGE.format('C0603C681J5RACTX9'))[0] == 'ok'
    assert u.classify_cited('C0805X000Q0Q0Q0Q0', PAGE.format('C0805X000Q0Q0Q0Q0'))[0] == 'wrong'


def test_discriminating_pattern_stays_static_ok(monkeypatch):
    real = PAGE.format('REAL123')
    net(monkeypatch, {real: (200, b'REAL123 datasheet', 'text/html')})  # fake PN -> 404
    monkeypatch.setattr(u, 'rendered_names', lambda pn, url: 1 / 0)  # must not be needed
    assert u.classify_cited('REAL123', real) == ('ok', 200)


SPA = 'https://pim.example.com/products/{}'
SHELL = b'<html><body><div id="root"></div><script src="/app.js"></script></body></html>'


def spa_net(monkeypatch, render):
    """every URL serves the same JS shell; `render` stands in for headless Chromium"""
    net(monkeypatch, {})
    monkeypatch.setattr(u, 'fetch', lambda url: ('http', 200, SHELL, 'text/html'))
    monkeypatch.setattr(u, 'RENDERS', {'used': 0, 'denied': 0})
    monkeypatch.setattr(u, 'rendered_names', render)


def test_spa_shell_that_renders_the_pn_is_ok(monkeypatch):
    spa_net(monkeypatch, lambda pn, url: pn == 'GRM155R71C104KA88D')  # the fake renders "not found"
    pn = 'GRM155R71C104KA88D'
    assert u.classify_cited(pn, SPA.format(pn))[0] == 'ok'


def test_spa_shell_that_renders_not_found_is_wrong(monkeypatch):
    spa_net(monkeypatch, lambda pn, url: False)
    pn = 'GRM155R71C104KA88D'
    v, why = u.classify_cited(pn, SPA.format(pn))
    assert v == 'wrong' and 'echo' not in why


def test_spa_echo_pattern_still_scores_zero(monkeypatch):
    spa_net(monkeypatch, lambda pn, url: True)  # the fake "renders found" as well
    pn = 'GRM155R71C104KA88D'
    v, why = u.classify_cited(pn, SPA.format(pn))
    assert v == 'wrong' and 'echo' in why


def test_real_content_page_is_not_a_shell_and_is_not_rendered(monkeypatch):
    page = ('<html><body><p>' + 'lorem ipsum dolor sit amet ' * 60 + 'other part</p></body></html>').encode()
    net(monkeypatch, {SPA.format('GRM155R71C104KA88D'): (200, page, 'text/html')})
    monkeypatch.setattr(u, 'rendered_names', lambda pn, url: 1 / 0)
    # names the first characters of the PN: genuine wrong, not a shell
    page2 = page.replace(b'other part', b'GRM155 only')
    net(monkeypatch, {SPA.format('GRM155R71C104KA88D'): (200, page2, 'text/html')})
    assert u.classify_cited('GRM155R71C104KA88D', SPA.format('GRM155R71C104KA88D'))[0] == 'wrong'


def test_render_budget_is_capped(monkeypatch):
    monkeypatch.setattr(u, 'MAX_RENDERS', 0)
    monkeypatch.setattr(u, 'RENDERS', {'used': 0, 'denied': 0})
    monkeypatch.setitem(u._BR, 'pg', object())
    assert u.rendered_names('X1', 'https://a.example/X1') is None and u.RENDERS['denied'] == 1


def test_shell_detection_criteria():
    pn = 'GRM155R71C104KA88D'
    long = 'lorem ipsum dolor sit amet ' * 60
    assert u.is_shell(pn, '<html><body>GRM155 Loading</body></html>')                              # tiny body text
    assert u.is_shell(pn, '<div id="app"></div><script src=x></script><p>' + long + pn + '</p>')  # script-root div
    assert u.is_shell(pn, '<p>' + long + '</p>')                                              # no trace of the PN
    assert not u.is_shell(pn, '<p>' + long + 'GRM155 series</p>')
