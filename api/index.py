import sys
import os

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.middleware.proxy_fix import ProxyFix
from app import app

# 1. Enforce HTTPS and trust Vercel Reverse Proxy
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config['PREFERRED_URL_SCHEME'] = 'https'

# 2. Vercel Route Resolver Middleware
class VercelRouteMiddleware:
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        environ['wsgi.url_scheme'] = 'https'

        # 1. Read existing path
        path = environ.get('PATH_INFO', '/')

        # 2. When Vercel rewrites to /api/index, check if actual matched subpath was passed
        if path in ('/api/index', '/api/index/', '/api/index.py', '/api/index.py/'):
            matched = environ.get('HTTP_X_MATCHED_PATH') or environ.get('REQUEST_URI') or ''
            if matched:
                matched_clean = matched.split('?')[0]
                # If matched_clean is a concrete path (not a regex like /(.*)) and not /api/index
                if '(' not in matched_clean and '*' not in matched_clean and matched_clean not in ('/api/index', '/api/index.py'):
                    path = matched_clean

        # 3. Strip any internal /api/index prefixes if present
        for prefix in ('/api/index.py', '/api/index'):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break

        # 4. Normalize clean path
        if not path or path == '':
            path = '/'
        if not path.startswith('/'):
            path = '/' + path

        environ['PATH_INFO'] = path
        return self.wsgi_app(environ, start_response)

app.wsgi_app = VercelRouteMiddleware(app.wsgi_app)
