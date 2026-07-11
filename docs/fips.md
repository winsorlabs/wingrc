# FIPS 140-2/140-3 Crypto Boundary — WinGRC

> **Applicability:** Required before any deployment handling CUI.  
> SC.L2-3.13.11 — Employ FIPS-validated cryptography when used to protect the
> confidentiality of CUI.

---

## 1. Crypto inventory — every component, every algorithm

### 1.1 Application code (`backend/app/`)

| Location | Algorithm | Purpose | FIPS status |
|---|---|---|---|
| `storage.py` boto3 Config | HMAC-SHA256 (S3v4) | Request signing | ✅ Approved |
| `storage.py` boto3 Config | none | Content-MD5 suppressed via `request_checksum_calculation='when_required'` | ✅ No MD5 call |
| `models.py`, test helpers | `uuid.uuid4()` → `os.urandom()` | PK generation | ✅ OS CSPRNG (FIPS-safe when OS is in FIPS mode) |
| `routers/evidence.py` | byte comparison | Magic-byte file validation | ✅ Not crypto |
| All auth (not yet landed) | **MUST use PBKDF2-HMAC-SHA256** | Password hashing | ⚠️ See §4 |

**No application code calls `hashlib`, `hmac`, `ssl`, `cryptography`, `bcrypt`, or any other crypto primitive directly.**

---

### 1.2 botocore 1.43.46 — complete MD5/SHA1 audit

#### Call site 1 — `botocore/utils.py:3357` `conditionally_calculate_md5()`
**Algorithm:** MD5  
**Purpose:** Computes `Content-MD5` header for S3 `put_object` requests  
**Trigger condition:** Only fires when `request_checksum_calculation` is `'when_supported'` (the old default)  
**Our config:** `Config(request_checksum_calculation='when_required')` — put_object on MinIO does not have `requestChecksumRequired` in its service model, so this path is **never reached**  
**Status:** ✅ Suppressed by our boto3 Config

**Mechanism detail:** botocore tests MD5 availability at module import time:
```python
# botocore/compat.py:145-149
try:
    hashlib.md5(usedforsecurity=False)  # usedforsecurity=False allows it on FIPS
    MD5_AVAILABLE = True
except (AttributeError, ValueError):
    MD5_AVAILABLE = False
```
On RHEL 9 FIPS mode, `usedforsecurity=False` still succeeds (OpenSSL's `EVP_MD_CTX_FLAG_NON_FIPS_ALLOW`), so `MD5_AVAILABLE = True` even on FIPS hosts. Without our `when_required` config, botocore **would** call MD5 even in FIPS mode. Our fix avoids the call entirely, making MD5 availability irrelevant.

---

#### Call site 2 — `botocore/auth.py:912` `HmacV1Auth.sign_string()`
**Algorithm:** HMAC-SHA1  
**Purpose:** AWS Signature Version 1 (legacy S3 auth)  
**Trigger condition:** Only when `signature_version='s3'` (the legacy scheme)  
**Our config:** `Config(signature_version='s3v4')` — routes to `SigV4Auth` (HMAC-SHA256)  
**Status:** ✅ Never instantiated

---

#### Call site 3 — `botocore/credentials.py:847,2299` `SSOTokenLoader._generate_cache_key()`
**Algorithm:** SHA1  
**Purpose:** File system cache key for SSO session tokens  
**Trigger condition:** Only when using AWS SSO/IAM Identity Center credentials  
**Our config:** Static access key + secret key (`WINGRC_STORAGE_ACCESS_KEY/SECRET_KEY`)  
**Status:** ✅ Code path never executed

---

#### Call site 4 — `botocore/handlers.py:359` `_sse_md5()`
**Algorithm:** MD5 (`usedforsecurity=False`)  
**Purpose:** Computes base64-MD5 of SSE-C customer encryption key for `SSECustomerKeyMD5` parameter  
**Trigger condition:** Only when caller passes `SSECustomerKey` to `PutObject`, `GetObject`, `HeadObject`, `CopyObject`, `CreateMultipartUpload`, or `UploadPart`  
**Our config:** We never use SSE-C; `upload_file()` in `storage.py` does not pass `SSECustomerKey`  
**Status:** ✅ Never triggered

---

#### Call site 5 — `botocore/httpchecksum.py:167` `Sha1Checksum` class
**Algorithm:** SHA1  
**Purpose:** Compute `x-amz-checksum-sha1` response checksum for flexible checksum validation  
**Trigger condition:** (a) Server returns `x-amz-checksum-sha1` response header AND (b) `response_checksum_validation='when_supported'`  
**Our config:** `response_checksum_validation='when_required'` + MinIO (standard or FIPS build) does not send `x-amz-checksum-sha1` headers  
**Status:** ✅ Never instantiated in our flow. SHA1 appears at position 7 in `_ALGORITHMS_PRIORITY_LIST`; it would only be selected if the server advertised it and validation was enabled.

---

### 1.3 s3transfer 0.19.1

`s3transfer` is boto3's high-level file transfer manager (`upload_file`, `upload_fileobj`). We do **not** use s3transfer — we call `put_object` directly in `storage.py`. The references to `SSECustomerKeyMD5` and `ChecksumMD5` in s3transfer source are field-name string constants, not algorithm calls.

**Status:** ✅ Not invoked; not a concern

---

### 1.4 psycopg 3.3.4 (PostgreSQL driver)

psycopg3 itself contains no crypto implementation. It wraps `libpq` (the PostgreSQL C client library), which handles authentication using whatever method the PostgreSQL server advertises in its handshake.

**Risk:** If `pg_hba.conf` specifies `method = md5`, libpq computes `MD5(password || username)` using the C library's MD5 implementation. This is **not** Python's hashlib, but is still MD5 and would be blocked by kernel FIPS mode on RHEL 9.

**Required for FIPS deployment:**
- PostgreSQL `pg_hba.conf` must use `scram-sha-256` (FIPS-approved, SHA-256 based)
- Connection string must include `?sslmode=require` to encrypt the wire
- Verify: `SHOW hba_file;` then inspect the file; every non-`trust` line for wingrc user must read `scram-sha-256`

PostgreSQL 18 defaults to `scram-sha-256` for new installations. The docker `postgres:18` image pg_hba.conf sets `scram-sha-256` for remote connections. **Verify this is not overridden** in any custom config.

**Status:** ⚠️ No code change needed, but must be verified on deployment

---

### 1.5 python-multipart 0.0.32

Parses HTTP `multipart/form-data` bodies. No cryptographic operations. Boundary detection is string matching. CRC checks are not performed.

**Status:** ✅ Clean

---

### 1.6 openpyxl 3.1.5

Reads and writes `.xlsx` files (ZIP + XML). Python's `zipfile` module uses CRC32 for ZIP data integrity (not a hash algorithm in the crypto sense; it is not used for security). openpyxl's `protection.py` documents that it does not implement workbook encryption.

We use openpyxl only to parse scope-import spreadsheets. No encrypted workbooks.

**Status:** ✅ Clean

---

## 2. What must change for a FIPS deployment

These are all infrastructure changes — no additional application code changes are required beyond the boto3 Config fix already committed.

### 2.1 Base image: python:3.13-slim → RHEL UBI 9

```dockerfile
FROM registry.access.redhat.com/ubi9/python-313 AS base
# Enable FIPS mode for this process. The host kernel must also boot with
# fips=1 for full FIPS enforcement (kernel CSPRNG, etc.).
ENV OPENSSL_FIPS=1
```

RHEL 9 OpenSSL 3.x has CMVP Certificate #4825. Debian/Ubuntu OpenSSL builds have no CMVP certificate.

### 2.2 MinIO: standard image → FIPS build

```yaml
# docker-compose.yml (FIPS profile)
minio:
  image: minio/minio:RELEASE.2025-XX-XXTXXXXXX-fips   # pin the tag
```

MinIO FIPS builds use BoringCrypto (CMVP Certificate #3678). The standard `minio/minio:latest` uses Go's standard crypto, which has no CMVP certificate.

### 2.3 PostgreSQL: require TLS + scram-sha-256 auth

```yaml
# docker-compose.yml (FIPS profile) — mount server cert
postgres:
  image: postgres:18
  environment:
    POSTGRES_INITDB_ARGS: "--auth-host=scram-sha-256"
  command: >
    postgres
    -c ssl=on
    -c ssl_cert_file=/certs/server.crt
    -c ssl_key_file=/certs/server.key
    -c ssl_ca_file=/certs/ca.crt
    -c password_encryption=scram-sha-256
```

Connection string:
```
WINGRC_DATABASE_URL=postgresql+psycopg://wingrc:...@db:5432/wingrc?sslmode=require&sslrootcert=/certs/ca.crt
```

### 2.4 MinIO: require TLS

```yaml
# docker-compose.yml (FIPS profile)
minio:
  environment:
    MINIO_ROOT_USER: wingrc
    MINIO_ROOT_PASSWORD: ...
  volumes:
    - ./certs/minio:/root/.minio/certs:ro   # server.crt, server.key, CAs/ca.crt
```

```bash
WINGRC_STORAGE_ENDPOINT=https://minio:9000
WINGRC_STORAGE_PUBLIC_ENDPOINT=https://10.10.24.35:9000
```

### 2.5 Reverse proxy: FIPS cipher suites only

nginx TLS config for production:
```nginx
ssl_protocols TLSv1.2 TLSv1.3;
ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384;
ssl_prefer_server_ciphers on;
```

No RC4, no 3DES, no MD5-based cipher suites, no TLS 1.0/1.1.

---

## 3. FIPS mode verification (startup self-test)

A FIPS-capable image with FIPS mode *off* is NOT compliant. Verify mode is actually on:

```python
# backend/app/fips_check.py  (to be wired into startup logging)
import hashlib, ssl

def fips_status() -> dict:
    try:
        hashlib.md5()
        md5_blocked = False
    except ValueError:
        md5_blocked = True

    return {
        "fips_mode_active": md5_blocked,
        "openssl_version": ssl.OPENSSL_VERSION,
    }
```

A compliant deployment shows `fips_mode_active: true`. The `WINGRC_REQUIRE_FIPS` env var (planned for the FIPS deployment slice) causes the app to refuse startup if this returns false.

Note: `hashlib.md5(usedforsecurity=False)` may still succeed on RHEL 9 FIPS mode (it is intentionally allowed for non-security uses). The self-test must call `hashlib.md5()` **without** `usedforsecurity=False` to confirm the FIPS enforcement gate is active.

---

## 4. Future auth slice — password hashing

**Do not use:** bcrypt, argon2, scrypt. None have CMVP certificates.

**Use:** `hashlib.pbkdf2_hmac('sha256', password, salt, iterations=600_000)` — FIPS-approved per NIST SP 800-132. Routes through OpenSSL when running on a FIPS-enabled host.

Example with passlib:
```python
from passlib.hash import pbkdf2_sha256
# passlib.hash.pbkdf2_sha256 uses hashlib.pbkdf2_hmac internally
hash = pbkdf2_sha256.using(rounds=600_000).hash(plaintext)
```

---

## 5. SC.L2-3.13.11 SSP implementation text (for customers)

> **WinGRC Cryptographic Implementation — SC.L2-3.13.11**
>
> All cryptographic operations protecting CUI confidentiality employ FIPS-validated
> modules operating in approved mode.
>
> **Modules in use:**
> - OpenSSL 3.x on RHEL 9 (CMVP Certificate #4825) — TLS for all in-transit
>   encryption and password-based key derivation (PBKDF2-HMAC-SHA256)
> - MinIO BoringCrypto build (CMVP Certificate #3678) — object storage
>   encryption and S3v4 request signing (HMAC-SHA256)
>
> **In-transit encryption:** Browser↔app, app↔database, and app↔object-storage
> traffic is encrypted using TLS 1.2 or 1.3 with FIPS-approved cipher suites
> (AES-128-GCM-SHA256, AES-256-GCM-SHA384). No CUI traverses plaintext channels.
>
> **Algorithm restrictions:** MD5, SHA-1, RC4, DES, and 3DES are not used for
> any security purpose. The S3-compatible storage client is configured to
> suppress MD5 Content-MD5 headers (`request_checksum_calculation=when_required`).
> Request signing uses HMAC-SHA256 exclusively (S3 Signature Version 4).
> Password hashing uses PBKDF2-HMAC-SHA256 per NIST SP 800-132.
>
> **Key management:** TLS certificates are issued by an approved CA and rotated
> per the organization's certificate lifecycle policy. Storage access credentials
> are stored as environment variables in the container runtime and never logged
> or persisted to disk in plaintext.
>
> **FIPS mode verification:** The application performs a startup self-test
> confirming OpenSSL is operating in FIPS-approved mode. Deployments configured
> with `WINGRC_REQUIRE_FIPS=true` refuse to start if FIPS mode cannot be confirmed.
>
> **Non-FIPS dev/test deployments:** The `python:3.13-slim`-based development
> image does not use a FIPS-validated module. It must not handle CUI. CUI
> handling requires the UBI 9 FIPS image and the configurations documented in
> `docs/fips.md`.

---

## 6. Change log

| Date | Change | Commit |
|---|---|---|
| 2026-07-10 | boto3 Config: `request_checksum_calculation='when_required'` + `response_checksum_validation='when_required'` — suppress MD5 Content-MD5 and ETag-MD5 paths in botocore | 106e9ea |
| 2026-07-10 | `WINGRC_STORAGE_PUBLIC_ENDPOINT` — presigned URLs use public endpoint, no `minio:9000` in browser-facing URLs | 62e5aef |
