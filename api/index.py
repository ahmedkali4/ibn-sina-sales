import sys
import os

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.middleware.proxy_fix import ProxyFix
from app import app

# 1. Enforce HTTPS and trust Vercel Reverse Proxy
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config['PREFERRED_URL_SCHEME'] = 'https'

# 2. Normalize Vercel rewrite paths safely
class VercelRouteMiddleware:
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        environ['wsgi.url_scheme'] = 'https'

        # Extract actual user-requested URI from Vercel proxy headers
        uri = environ.get('REQUEST_URI') or environ.get('RAW_URI') or environ.get('HTTP_X_FORWARDED_URI') or ''
        if uri:
            path = uri.split('?', 1)[0]
        else:
            path = environ.get('PATH_INFO', '/')

        # Discard any regex pattern that might leak from x-matched-path
        if '(' in path or '*' in path:
            path = '/'

        # Strip Vercel serverless function prefixes if present
        if path.startswith('/api/index.py'):
            path = path[len('/api/index.py'):]
        elif path.startswith('/api/index'):
            path = path[len('/api/index'):]
        elif path in ('/api', '/api/'):
            path = '/'

        if not path:
            path = '/'

        environ['PATH_INFO'] = path
        return self.wsgi_app(environ, start_response)

app.wsgi_app = VercelRouteMiddleware(app.wsgi_app)
