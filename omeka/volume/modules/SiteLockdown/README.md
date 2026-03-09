# Site Lockdown

Pre-launch lockdown module for Omeka S 4.x. When activated, it:

1. **Password gate** — every public URL shows a password prompt; correct password sets an HMAC cookie
2. **robots.txt** — serves `User-agent: * / Disallow: /` at `/robots.txt`
3. **Meta tag** — injects `<meta name="robots" content="noindex, nofollow">` into every public page `<head>`
4. **HTTP header** — adds `X-Robots-Tag: noindex, nofollow` to every public response

Deactivating the module removes all four behaviors instantly.

## Configuration

Admin → Modules → Site Lockdown → Configure:

- **Password**: shared password visitors must enter
- **Cookie duration**: how long the auth cookie lasts (session, 24h, 7d, 30d)

## Security

- Password stored as bcrypt hash
- Cookie value is HMAC-SHA256(password_hash, random_secret) — not guessable
- Changing the password invalidates all existing cookies
- Cookie attributes: HttpOnly, SameSite=Lax, Secure (when HTTPS)

## Bypasses

The password gate does not apply to:
- `/admin` and all admin routes
- `/robots.txt` (crawlers need to read the disallow)
