"""Salted PBKDF2 credentials; sessions exist only in process memory."""
import hashlib
import hmac
import secrets

ITERATIONS=600_000
DEFAULT_PASSWORD='stopp'
PASSWORD_REVISION='2'


class AdminAuth:
    def __init__(self,repository):
        self.repository=repository
        self.authenticated=False
        if not repository.connection.execute('SELECT 1 FROM admin_credentials WHERE id=1').fetchone():
            self._store(DEFAULT_PASSWORD)
        elif not repository.connection.execute("SELECT 1 FROM admin_settings WHERE key='default_password_revision'").fetchone():
            # Upgrade the previous initial password by verifying its salted hash.
            # User-changed credentials must remain valid after an application update.
            if self.verify('Innovanta_20102026'):
                self._store(DEFAULT_PASSWORD)
        with repository.connection:
            repository.connection.execute("INSERT OR IGNORE INTO admin_settings VALUES ('default_password_revision',?)",(PASSWORD_REVISION,))

    def _store(self,password):
        salt=secrets.token_bytes(32)
        digest=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,ITERATIONS)
        with self.repository.connection:
            self.repository.connection.execute('INSERT INTO admin_credentials VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET salt=excluded.salt,password_hash=excluded.password_hash,iterations=excluded.iterations',
                                               (salt,digest,ITERATIONS))

    def verify(self,password):
        salt,digest,iterations=self.repository.connection.execute('SELECT salt,password_hash,iterations FROM admin_credentials WHERE id=1').fetchone()
        candidate=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,iterations)
        return hmac.compare_digest(candidate,digest)

    def login(self,password):
        self.authenticated=self.verify(password)
        return self.authenticated

    def logout(self):
        self.authenticated=False

    def require(self):
        if not self.authenticated:
            raise PermissionError('Для этого действия войдите в режим администратора')

    def change_password(self,current,new):
        self.require()
        if not self.verify(current):
            raise ValueError('Неверный текущий пароль')
        if not new or not new.strip():
            raise ValueError('Новый пароль не должен быть пустым')
        self._store(new)
