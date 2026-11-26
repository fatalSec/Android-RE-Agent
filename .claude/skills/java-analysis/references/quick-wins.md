# Quick-Wins Playbook (low-hanging fruit)

Loaded on demand from `java-analysis`. Assumes the SKILL.md tool table and triage sequence. These are the cheap, high-value checks to run **before** deep RASP/obfuscation work — they're usually the fastest findings in an assessment. Record every hit in the target's case notes (`notes.md`). The `llm-re-wiki` KB is **optional** — when its `kb_*` tools aren't registered, skip every KB step here and rely on direct analysis. Write any files only into the target workspace beside the APK (`<apk-dir>/<apk-name>_analysis/`, see `AGENTS.md`), never into the agent home.

Two caveats: `code`-scope searches (`search_classes_by_keyword search_in="code"`) must pass a `package` filter on large APKs; and treat matches as **leads, not confirmed vulns** — read the surrounding method and keep provenance (`[I]`/`[U]`) until verified.

---

## 1. Hardcoded secrets (do this first)
**Where they hide:** `res/values/strings.xml`, code string literals, `BuildConfig` fields, and `assets/` / `res/raw/` files.

- `strings.xml`: `get_strings(filter="api")`, then `key`, `secret`, `token`, `password`, `pwd`, `aws`, `bearer`, `firebase`, `client_secret`.
- Code literals (`search_in="code"`, with `package`): `AIza` (Google API key), `AKIA`/`ASIA` (AWS), `-----BEGIN` (private keys/certs), `eyJ` (JWT), `Bearer `, `password`, `apiKey`, `client_secret`, `.firebaseio.com`, `s3.amazonaws.com`.
- `BuildConfig`: `search_classes_by_keyword(search_term="BuildConfig", search_in="class")` → `get_class_source` the app's `BuildConfig` (constant fields often hold keys/URLs/`DEBUG`).
- Assets: `list_resource_files(filter="")` then `get_resource_file` anything like `*.json`, `*.properties`, `*.pem`, `*.key`, `google-services.json`, config blobs.

**Report:** the secret's location + kind. KB cross-check a vendor key format if unsure (`kb_search("AIza")`). Never paste a full live secret into the case notes beyond what identifies it.

## 2. Insecure manifest flags
Read with `get_android_manifest(section="application")` and `section="permissions"`:
- `android:debuggable="true"` — debuggable in prod.
- `android:allowBackup="true"` (or unset on old targetSdk) — data exfil via `adb backup`.
- `android:usesCleartextTraffic="true"` — plaintext HTTP allowed.
- `android:networkSecurityConfig` — then `get_resource_file` that XML and check for `cleartextTrafficPermitted="true"`, `<trust-anchors>` including `<certificates src="user"/>` (user CAs trusted → trivial MITM), or `<debug-overrides>`.
- `minSdkVersion` (from `get_app_info`): **< 17** → `addJavascriptInterface` is RCE-exploitable; low minSdk also weakens signature/other platform defenses.
- Custom `<permission>` with weak `protectionLevel` (`normal`/`dangerous` instead of `signature`) guarding sensitive components.

## 3. Exported components & deep links
From `get_android_manifest(section="activities"|"services"|"receivers"|"providers")`:
- **Exported + no/weak permission** (`android:exported="true"`, no `android:permission`) → IPC attack surface. On targetSdk ≥ 31 `exported` must be explicit; flag `true` ones.
- **ContentProvider**: exported, `grantUriPermissions="true"`, or path-permission gaps → data leak / path traversal. Inspect the provider class (`query`/`openFile`).
- **Browsable deep links**: `intent-filter` with `BROWSABLE` + `<data android:scheme=... host=...>` → open-redirect / credential or token leak. Inspect the handling activity's `onCreate`/`getIntent().getData()`.
- **Task hijacking**: non-default `launchMode` (`singleTask`/`singleInstance`) or custom `taskAffinity` on exported activities.

## 4. WebView misconfiguration
Code-search (`search_in="code"`): `addJavascriptInterface`, `setJavaScriptEnabled`, `setAllowFileAccess`, `setAllowFileAccessFromFileURLs`, `setAllowUniversalAccessFromFileURLs`, `onReceivedSslError`, `shouldOverrideUrlLoading`.
- `addJavascriptInterface` + `setJavaScriptEnabled(true)` → JS→Java bridge (RCE if minSdk < 17, or if the bridge exposes sensitive methods).
- `onReceivedSslError(...) { handler.proceed(); }` → **TLS validation disabled** (MITM). Read the method to confirm it calls `proceed()`.
- `setAllowUniversalAccessFromFileURLs(true)` / file access on → local file exfil from loaded content.

## 5. Weak crypto & insecure storage
Code-search: `Cipher.getInstance`, `SecretKeySpec`, `IvParameterSpec`, `MessageDigest.getInstance`, `MODE_WORLD_READABLE`, `MODE_WORLD_WRITEABLE`, `getExternalStorage`, `SharedPreferences`.
- `Cipher.getInstance("AES/ECB...")` or `"DES"`/`"RC4"`; hardcoded `SecretKeySpec`/`IvParameterSpec` (static key/IV); MD5/SHA-1 used for integrity/auth.
- Secrets/tokens written to `SharedPreferences`, or sensitive files on external storage (world-readable historically).

## 6. Sensitive logging, endpoints & cloud
- Logging: code-search `Log.d`, `Log.v`, `Log.i`, `printStackTrace` — then check nearby args for tokens/PII/credentials.
- Endpoints: code-search `http://` (cleartext), plus staging/internal hostnames, and debug/test URL constants (often in `BuildConfig`).
- Cloud/backends: `.firebaseio.com` (test for open DB), `firebaseFirestore`, `s3.amazonaws.com`/bucket names, Google Maps key (`AIza`).

---

## Reporting
For each hit: **category → location (`class#method` / `strings.xml` / asset / manifest attr) → why it matters → confidence**. Write them to `notes.md` under Findings/Attack surface, mark unverified ones `[U]`, and cross-check known key formats or SDK CVEs against `llm-re-wiki`. When the app is clearly hardened but these come back empty, that's the pivot signal — the values may be encrypted (see `references/anti-analysis.md`).
