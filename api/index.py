import sys
import os
import urllib.parse

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.middleware.proxy_fix import ProxyFix
from app import app

# 1. Enforce HTTPS and trust Vercel Reverse Proxy
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config['PREFERRED_URL_SCHEME'] = 'https'

# 2. Extract actual requested path from Vercel rewrite parameter or proxy headers
class VercelRouteMiddleware:
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        environ['wsgi.url_scheme'] = 'https'

        qs = environ.get('QUERY_STRING', '')
        route = None

        # 1. Check explicit __route__ from query string (passed via vercel.json rewrite)
        if '__route__' in qs:
            params = urllib.parse.parse_qs(qs, keep_blank_values=True)
            route_list = params.pop('__route__', None)
            if route_list and route_list[0]:
                route = urllib.parse.unquote(route_list[0]).split('?')[0]
                # Rebuild query string without the internal __route__ parameter
                environ['QUERY_STRING'] = urllib.parse.urlencode([(k, v) for k, vals in params.items() for v in vals])

        # 2. Check proxy routing headers
        if not route:
            matched = environ.get('HTTP_X_MATCHED_PATH') or environ.get('HTTP_X_ORIGINAL_URI') or environ.get('HTTP_X_FORWARDED_URI')
            if matched and '(' not in matched and '*' not in matched and not matched.startswith('/api/index'):
                route = matched.split('?')[0]

        # 3. Check request URI
        if not route:
            req_uri = environ.get('REQUEST_URI') or environ.get('RAW_URI')
            if req_uri and '(' not in req_uri and '*' not in req_uri:
                route = req_uri.split('?')[0]

        # 4. Fallback to existing PATH_INFO
        if not route:
            route = environ.get('PATH_INFO', '/')

        # Normalize internal prefixes
        if not route or route.startswith('/api/index'):
            route = '/'

        # Ensure valid single leading slash
        if not route.startswith('/'):
            route = '/' + route
        while '//' in route:
            route = route.replace('//', '/')

        environ['PATH_INFO'] = route
        return self.wsgi_app(environ, start_response)

app.wsgi_app = VercelRouteMiddleware(app.wsgi_app)
