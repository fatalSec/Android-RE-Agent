# Native Quick-Wins Playbook (`.so` low-hanging fruit)

Loaded on demand from `native-analysis`. Assumes the SKILL.md triage + Ghidra-bridge prerequisite. These are the cheap, high-value findings to grab **before** the deep anti-analysis/RASP work — mostly `strings`/`nm`/`readelf`/`checksec` on the extracted `.so` plus a few Ghidra xref lookups. Record every hit in the case notes (`notes.md`); treat matches as leads to confirm, keep `[U]` until verified.

> If an obfuscated lib returns almost nothing here, that's the **string-encryption pivot** (`references/anti-analysis.md` §1), not "clean".

---

## 1. Hardcoded secrets & keys (do this first)
Native libs are a favourite hiding place for keys devs think are "safe in C".
- **Find:** `strings -a -n 6 <so>` for API keys (`AIza`, `AKIA`), tokens, `Bearer`, `-----BEGIN`, JWT (`eyJ`), backend/C2 URLs (`http`/`https`), `.firebaseio.com`, S3 buckets; and crypto material — AES/HMAC key-looking byte arrays, IVs, embedded certs / pinned SPKI hashes.
- **Confirm in Ghidra:** xref the string / `.rodata` constant to the function that uses it (e.g. a key passed into an AES init) to prove it's live, not dead data.
- **Report:** value's location (`lib.so @ 0x…`) + kind + the using function. Don't copy a full live secret beyond what identifies it.

## 2. Weak / misused crypto
- **Find (imports + constants):** `nm -D`/`readelf --dyn-syms` for `AES_*`, `DES_*`, `RC4`, `MD5_*`, `SHA1_*`, OpenSSL `EVP_*`, Android `CCCrypt`; `strings` for `AES/ECB`, `DES`, `MD5`. In Ghidra, hardcoded key/IV constants fed into the cipher init; hand-rolled/custom crypto loops.
- **Flag:** ECB mode, static key/IV, MD5/SHA-1 used for integrity/auth, home-grown ciphers.
- **Report:** algorithm + where the key/IV comes from (hardcoded vs derived).

## 3. Dangerous imports & calls
- **Find:** `nm -D`/`readelf --dyn-syms` for `system`, `popen`, `exec*`, `dlopen`/`dlsym` (especially on derived/writable paths), `getenv`, and memory-safety smells `strcpy`/`strcat`/`sprintf`/`gets`/`memcpy`.
- **Flag:** `system`/`exec` of shell strings (command injection surface), `dlopen` of a path under app/world-writable storage (code-load hijack), classic unbounded-copy sinks.
- **Report:** the call site and the controllable input, if any.

## 4. Binary hardening posture
- **Find:** `checksec --file=<so>` (or `readelf -hld`): **NX**, **PIE**, **RELRO** (full vs partial/none), **stack canary**; `readelf -dW` for `TEXTREL`, `RPATH`/`RUNPATH`.
- **Flag:** partial/No RELRO (GOT-overwrite exposure), no canary, text relocations, insecure `RUNPATH`. These are reportable posture findings on their own.

## 5. Leftover debug / logging
- **Find:** `strings` + Ghidra xrefs to `__android_log_print`, `fprintf(stderr`, `printf`, `assert`; debug/verbose flag globals.
- **Flag:** logging of keys/tokens/PII/decisioning, or verbose logic that leaks the design. Note debug builds vs stripped release.

---

## Reporting
Per hit: **category → `lib.so @ 0x…` (or symbol/string) → why it matters → confidence.** Write to `notes.md` (Findings / Attack surface), mark unverified `[U]`, and cross-check key formats / known-vuln library versions against `llm-re-wiki`. Thin results on a clearly-protected lib → pivot to `references/anti-analysis.md`.
