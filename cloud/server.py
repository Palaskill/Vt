"""VetStock behind Render HTTPS, with persistent PostgreSQL-backed sessions.

This is a separate entry point. The Windows/LAN server remains unchanged.
"""
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.parse
from http import cookies
from http.server import ThreadingHTTPServer
from pathlib import Path

import psycopg2

import server as app
from cloud import postgres

ROOT = Path(__file__).resolve().parent
SESSION_SECONDS = 8 * 3600
DUMMY_HASH = 'pbkdf2_sha256$310000$' + '00' * 16 + '$' + '00' * 32


def expected_origin():
    origin = os.environ.get('VETSTOCK_PUBLIC_URL') or os.environ.get('RENDER_EXTERNAL_URL', '')
    url = urllib.parse.urlsplit(origin)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
        raise RuntimeError('A valid HTTPS public URL is required')
    return f'https://{url.netloc.lower()}'


def token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def bootstrap():
    """Create the first admin only from deployment secrets, never from a public form."""
    with app.db() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            return
        username = app.textval(os.environ.get('VETSTOCK_ADMIN_USERNAME'), 80, True)
        password = app.passwordval(os.environ.get('VETSTOCK_ADMIN_PASSWORD'))
        if len(password) < 12:
            raise RuntimeError('The initial administrator password must have at least 12 characters')
        uid = c.execute('INSERT INTO users(username,display_name,role,password_hash,created_at) VALUES(?,?,?,?,?)',
                        (username, username, 'admin', app.hash_password(password), app.now())).lastrowid
        app.audit(c, uid, 'setup', username)


class Handler(app.Handler):
    server_version = 'VetStock/Cloud'

    def log_message(self, fmt, *args):
        # Avoid logging usernames, tokens, invoice names or query strings.
        return

    def security_headers(self):
        super().security_headers()
        self.send_header('Strict-Transport-Security', 'max-age=31536000')

    def _host_ok(self):
        return self.headers.get('Host', '').lower() == urllib.parse.urlsplit(expected_origin()).netloc.lower()

    def payload(self):
        if not self._host_ok() or self.headers.get('Origin', '').rstrip('/').lower() != expected_origin().lower():
            raise app.AppError('CROSS_ORIGIN', 403)
        if self.headers.get('Sec-Fetch-Site', 'same-origin') not in ('same-origin', 'none'):
            raise app.AppError('CROSS_ORIGIN', 403)
        if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
            raise app.AppError('CONTENT_TYPE', 415)
        try:
            n = int(self.headers.get('Content-Length', '0'))
            if not 0 < n <= app.MAX_BODY:
                raise ValueError
            value = json.loads(self.rfile.read(n))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, UnicodeDecodeError):
            raise app.AppError('INVALID_BODY')

    def _token(self):
        try:
            jar = cookies.SimpleCookie()
            jar.load(self.headers.get('Cookie', ''))
            value = jar['vetstock_sid'].value if 'vetstock_sid' in jar else ''
            return value if len(value) <= 128 else ''
        except cookies.CookieError:
            return ''

    def session(self, c):
        token = self._token()
        if not token:
            return None
        return c.execute('''SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id
                            WHERE s.token_hash=? AND s.expires_at>? AND u.active=1''',
                         (token_hash(token), int(time.time()))).fetchone()

    def _new_session(self, c, user_id):
        token = secrets.token_urlsafe(32)
        c.execute('DELETE FROM sessions WHERE expires_at<=?', (int(time.time()),))
        c.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',
                  (token_hash(token), user_id, int(time.time()) + SESSION_SECONDS))
        return f'vetstock_sid={token}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}'

    def _login(self, c, data):
        username = app.textval(data.get('username'), 80, True)
        password = str(data.get('password') or '')
        if len(password) > 256:
            raise app.AppError('INVALID_LOGIN', 401)
        key = token_hash(username.lower())
        now = int(time.time())
        c.execute('DELETE FROM login_attempts WHERE window_start<=?', (now - 300,))
        row = c.execute('SELECT * FROM login_attempts WHERE username_hash=?', (key,)).fetchone()
        attempts = row['attempts'] if row and row['window_start'] > now - 300 else 0
        if attempts >= 12:
            raise app.AppError('TOO_MANY_ATTEMPTS', 429)
        user = c.execute('SELECT * FROM users WHERE lower(username)=lower(?) AND active=1', (username,)).fetchone()
        valid = app.check_password(password, user['password_hash'] if user else DUMMY_HASH)
        if not user or not valid:
            c.execute('''INSERT INTO login_attempts(username_hash,attempts,window_start) VALUES(?,?,?)
                         ON CONFLICT(username_hash) DO UPDATE SET attempts=EXCLUDED.attempts,
                         window_start=EXCLUDED.window_start''',
                      (key, attempts + 1, row['window_start'] if attempts else now))
            c.commit()  # Failed attempts survive request rollback and process restarts.
            raise app.AppError('INVALID_LOGIN', 401)
        c.execute('DELETE FROM login_attempts WHERE username_hash=?', (key,))
        return self._new_session(c, user['id'])

    def do_GET(self):
        try:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == '/healthz':
                with app.db() as c:
                    c.execute('SELECT 1 FROM users LIMIT 1').fetchone()
                return self.send_bytes(200, b'ok', 'text/plain; charset=utf-8')
            if not self._host_ok():
                raise app.AppError('FORBIDDEN', 403)
            if path == '/api/state':
                with app.db() as c:
                    user = self.session(c)
                    return self.send_json({'setup': False, 'user': app.user_public(user) if user else None, 'secure': True})
            if path == '/api/dashboard':
                with app.db() as c:
                    user = self.require(c)
                    result = app.dashboard(c, user)
                    result['backup_stale'] = False  # Local USB backups do not apply to the hosted version.
                    return self.send_json(result)
            if path.startswith('/api/'):
                if path == '/api/backup-file':
                    raise app.AppError('NOT_FOUND', 404)
                return self.get_api(path, urllib.parse.parse_qs(parsed.query))
            allowed = {'/': ('index.html', 'text/html; charset=utf-8'),
                       '/index.html': ('index.html', 'text/html; charset=utf-8'),
                       '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                       '/barcode.js': ('barcode.js', 'text/javascript; charset=utf-8'),
                       '/styles.css': ('styles.css', 'text/css; charset=utf-8'),
                       '/design.css': ('design.css', 'text/css; charset=utf-8'),
                       '/design.js': ('design.js', 'text/javascript; charset=utf-8'),
                       '/gsap.min.js': ('gsap.min.js', 'text/javascript; charset=utf-8'),
                       '/lenis.min.js': ('lenis.min.js', 'text/javascript; charset=utf-8'),
                       '/react-spotlight.js': ('react-spotlight.js', 'text/javascript; charset=utf-8'),
                       '/sw.js': ('sw.js', 'text/javascript; charset=utf-8'),
                       '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
            if path not in allowed:
                raise app.AppError('NOT_FOUND', 404)
            filename, mime = allowed[path]
            content = (app.STATIC / filename).read_bytes()
            if filename == 'app.js':
                head, separator, tail = content.rpartition(b'\nstart();')
                if not separator:
                    raise RuntimeError('Cloud UI injection marker is missing')
                content = head + b'\n' + (ROOT / 'overlay.js').read_bytes() + separator + tail
            return self.send_bytes(200, content, mime)
        except app.AppError as exc:
            self.send_json({'error': exc.code}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            print('VetStock GET failed:', type(exc).__name__, file=sys.stderr)
            self.send_json({'error': 'SERVER_ERROR'}, 500)

    def do_POST(self):
        try:
            data = self.payload()
            path = urllib.parse.urlsplit(self.path).path
            if not path.startswith('/api/') or path in ('/api/setup', '/api/backup-now'):
                raise app.AppError('NOT_FOUND', 404)
            with app.db() as c:
                # All writes, including login and role changes, share one transaction lock.
                c.execute('BEGIN IMMEDIATE')
                if path == '/api/login':
                    cookie = self._login(c, data)
                    c.commit()
                    return self.send_json({'ok': True}, headers={'Set-Cookie': cookie})
                user = self.require(c)
                if path == '/api/logout':
                    c.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash(self._token()),))
                    c.commit()
                    return self.send_json({'ok': True}, headers={'Set-Cookie': 'vetstock_sid=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0'})
                if path == '/api/settings' and 'backup_dir' in data:
                    raise app.AppError('INVALID_FIELD')
                result = self.post_action(c, path, data, user)
                if path == '/api/change-password':
                    c.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
                elif path == '/api/user-update' and (data.get('password') or not data.get('active')):
                    c.execute('DELETE FROM sessions WHERE user_id=?', (app.intval(data.get('id'), 1),))
                c.commit()
                return self.send_json(result if result is not None else {'ok': True})
        except (psycopg2.IntegrityError, psycopg2.DataError):
            self.send_json({'error': 'DATA_CONFLICT'}, 400)
        except app.AppError as exc:
            self.send_json({'error': exc.code}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            print('VetStock POST failed:', type(exc).__name__, file=sys.stderr)
            self.send_json({'error': 'SERVER_ERROR'}, 500)


def main():
    os.environ['TZ'] = 'Africa/Tunis'
    if hasattr(time, 'tzset'):
        time.tzset()
    expected_origin()
    app.db = postgres.db
    bootstrap()
    host, port = '0.0.0.0', int(os.environ.get('PORT', '10000'))
    print('VetStock cloud server starting', flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('VetStock could not start:', type(exc).__name__, file=sys.stderr)
        sys.exit(1)
