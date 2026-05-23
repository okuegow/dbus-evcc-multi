from vrm_tunnel import should_redirect


def test_login_htm_redirects():
    assert should_redirect(b"GET /login.htm HTTP/1.1") is True


def test_login_html_redirects():
    assert should_redirect(b"GET /login.html HTTP/1.1") is True


def test_login_htm_with_query_redirects():
    assert should_redirect(b"GET /login.htm?foo=bar HTTP/1.1") is True


def test_root_passes_through():
    assert should_redirect(b"GET / HTTP/1.1") is False


def test_api_state_passes_through():
    assert should_redirect(b"GET /api/state HTTP/1.1") is False


def test_websocket_upgrade_passes_through():
    assert should_redirect(b"GET /ws HTTP/1.1") is False


def test_malformed_first_line_passes_through():
    # No path token -> treat as "/" -> no redirect (fail open to splice).
    assert should_redirect(b"GARBAGE") is False
