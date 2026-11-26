# RASP Detection Playbook (Anti-Debug / Root / Emulation / Hook / Tamper / Attestation)

Loaded on demand from `java-analysis`. Assumes the SKILL.md tool table, triage sequence, and KB-grounding rules. `code`-scope searches should pass a `package` filter on large APKs; use `get_strings` only for `res/values/strings*.xml`. The `llm-re-wiki` KB is **optional** — when its `kb_*` tools aren't registered, skip every KB step here and rely on direct analysis. Write any files only into the target workspace beside the APK (`<apk-dir>/<apk-name>_analysis/`, see `AGENTS.md`), never into the agent home.

**Scope:** Java/Smali-layer RASP. Native (`.so`) RASP — inline `ptrace`/SVC anti-debug, native `/proc/*/maps` scanning, JNI/native-method integrity — lives in the `native-analysis` skill; pivot there whenever a check is offloaded to a loaded library.

**For authorized testing only** (see `AGENTS.md`). Report *where* each check lives and *how* it works; neutralizing-for-testing guidance stays factual (which method to stub/return, which smali branch to patch), not a distribution-ready cracking recipe.

**Two-pass method**
1. **Plain-text pass** — code-search the literals below. Hits give you the class/method directly.
2. **Zero-hits pivot** — if a protected app returns little or nothing, the checks are almost certainly hidden by string encryption / reflection / native code. Switch to `references/anti-analysis.md` (§2 strings, §3 reflection) and to `native-analysis` — do **not** conclude "no protection".

Many RASP products centralize these in one SDK; identify it first: `kb_search("<vendor>", topic="protectors")` (e.g. DexGuard/Guardsquare, Promon SHIELD, AppSealing, Build38, zimperium, freeRASP, RootBeer) and read the profile before hand-searching.

---

## 1. Anti-debug
**Literals / symbols**
- `Debug.isDebuggerConnected`, `Debug.waitingForDebugger`, `android.os.Debug`.
- `ApplicationInfo.FLAG_DEBUGGABLE`, `ro.debuggable`, reading own `debuggable` flag.
- `/proc/self/status` + `TracerPid`, `/proc/<pid>/status`, `ptrace` (usually native → `native-analysis`).
- Timing checks: `System.nanoTime`/`currentTimeMillis` deltas bracketing a block.

**jadx queries:** `search_classes_by_keyword(search_in="code")` for `isDebuggerConnected`, `TracerPid`, `FLAG_DEBUGGABLE`, `waitingForDebugger`; `get_android_manifest(section="application")` to read the `debuggable` attribute.

**Analysis/report:** class+method of each check and the action on detection (exit/crash/silent flag). For testing, note the method whose boolean result to invert or the smali `if-*` to patch. Native `ptrace(PTRACE_TRACEME)` → hand to `native-analysis` (SVC/`ptrace` section).

**KB:** `kb_search("TracerPid")`, `kb_search("anti-debug android")`.

---

## 2. Anti-root
**Literals / symbols**
- su binaries: `/system/bin/su`, `/system/xbin/su`, `/sbin/su`, `/su/bin/su`, `which su`, `busybox`.
- Build tags / props: `ro.build.tags`, `test-keys`, `ro.secure`, `ro.debuggable`.
- Root managers / packages: `com.topjohnwu.magisk`, `eu.chainfire.supersu`, `com.noshufou.android.su`, `com.koushikdutta.superuser`, `de.robv.android.xposed.installer`.
- Magisk specifics: `magisk`, `magiskhide`, `/sbin/.magisk`, unix socket names.
- Writable-system / `mount` checks (`/proc/mounts` for `/system` rw), SELinux state (`getenforce`, `/sys/fs/selinux/enforce`, `ro.boot.selinux`), ADB / developer options (`Settings.Global`/`Settings.Secure` `ADB_ENABLED`, `development_settings_enabled`).
- `PackageManager.getInstalledPackages` scanning for root apps, the **RootBeer** library (`com.scottyab.rootbeer`), `Runtime.exec("su"/"which"/"getprop"/"mount")`.

> Build props (`ro.build.tags`, `ro.secure`, …) are frequently read via `SystemProperties.get` **through reflection**, so the literal won't appear — if root/emulation prop checks seem absent, see `references/anti-analysis.md` §3 (reflection) and trace the reflected getter.

**jadx queries:** code-search the above; `get_strings(filter="su")` for resource-side lists; `search_classes_by_keyword(search_term="rootbeer")`; `get_xrefs_to_method` on a central `isRooted()`/`isDeviceRooted()` to find every gate.

**Analysis/report:** enumerate the techniques used, the aggregator method, and the on-detection action. For testing, identify the single boolean sink to neutralize.

**KB:** `kb_search("RootBeer")`, `kb_search("magisk detection")`, `kb_search("su path")`.

---

## 3. Anti-emulation
**Literals / symbols**
- `Build.FINGERPRINT` / `MODEL` / `MANUFACTURER` / `PRODUCT` / `BRAND` / `DEVICE` / `HARDWARE` compared against `generic`, `goldfish`, `ranchu`, `sdk`, `emulator`, `vbox`, `genymotion`, `Android SDK built for x86`.
- QEMU artifacts: `ro.kernel.qemu`, `ro.hardware` = `goldfish`/`ranchu`, `/dev/socket/qemud`, `/dev/qemu_pipe`, `/sys/qemu_trace`, `qemu.sf.lcd_density`.
- Emulator telephony/identity: IMEI `000000000000000`, phone number `15555215554`, `android_id` all-zero, operator `Android`.
- Missing sensors / `SensorManager` enumeration, `/proc/cpuinfo` without real SoC, battery/temperature always fixed.

**jadx queries:** code-search `Build.FINGERPRINT`, `goldfish`, `ranchu`, `ro.kernel.qemu`, `15555215554`, `genymotion`; `get_strings` for the same literals in resources.

**Analysis/report:** which signals are checked and the combining logic (any-one vs scored). For testing, note which properties/returns to spoof.

**KB:** `kb_search("emulator detection")`, `kb_search("goldfish ranchu")`, `kb_search("qemu props")`.

---

## 4. Anti-hook (Frida / Xposed)
**Frida literals / symbols**
- Ports / strings: `27042`, `27043`, `frida`, `frida-server`, `frida-agent`, `re.frida.server`, `LIBFRIDA`.
- Memory scan: reading `/proc/self/maps` (or `/proc/<pid>/maps`) for `frida`/`gadget`/`linjector`.
- Thread/pipe names: `gum-js-loop`, `gmain`, `gdbus`, `pool-frida`.
- Named pipes / D-Bus probes to the Frida server port.

**Xposed / hooking**
- Xposed & forks: `de.robv.android.xposed.XposedBridge`, `XposedHelpers`, `IXposedHookLoadPackage`, `de.robv.android.xposed.installer`; **LSPosed** (`org.lsposed`), **EdXposed**, and the **Riru / Zygisk** module stack they ride on.
- Stack-trace inspection: `Throwable().getStackTrace()` scanned for `xposed`/`substrate`/hook frames.
- Classloader / native-method integrity: checking whether a `native` method's implementation pointer was moved, loaded-library list scans, `Runtime.exec("cat /proc/self/maps")`.

**jadx queries:** code-search `frida`, `27042`, `/proc/self/maps`, `gum-js-loop`, `de.robv.android.xposed`, `XposedBridge`, `getStackTrace`; pivot native Frida/map-scanning checks to `native-analysis`.

**Analysis/report:** list the frameworks targeted and each detection vector (port, maps scan, thread names, stack trace, native). For testing, note the sink method and whether detection is in Java or the `.so`.

**KB:** `kb_search("frida detection")`, `kb_search("xposed detection")`, `kb_search("/proc/self/maps")`, protector profile for productized anti-hook.

---

## 5. Anti-tamper / integrity & signature verification
**Why it matters here:** this is the category that **breaks after `apk-repack` resigns an APK** — resigning swaps the signing certificate, so any signature check will fire. Find these *before* repacking so you can predict (and, for authorized testing, neutralize) what will trip.

**Literals / symbols**
- Signature check: `PackageManager.getPackageInfo(..., GET_SIGNING_CERTIFICATES)` (or legacy `GET_SIGNATURES`), `PackageInfo.signingInfo` / `.signatures`, `Signature.toByteArray`, then a `MessageDigest` (SHA-256/MD5) compared to a **hardcoded cert hash**.
- Installer / anti-sideload: `getInstallerPackageName()` / `getInstallSourceInfo()` compared to `com.android.vending` (Play) or an allowlist.
- Package identity: `getPackageName()` vs a hardcoded package; `applicationInfo.sourceDir` / `publicSourceDir`.
- APK/DEX integrity: CRC/hash of `classes.dex` or the whole APK via `ZipFile`/`ZipEntry.getCrc()`, reading own APK through `sourceDir`, embedded checksum constants.

**jadx queries:** code-search `GET_SIGNING_CERTIFICATES`, `GET_SIGNATURES`, `signingInfo`, `getInstallerPackageName`, `getInstallSourceInfo`, `publicSourceDir`, `getCrc`; `get_xrefs_to_method` on the central `verifySignature()`/`checkIntegrity()` aggregator.

**Analysis/report:** the hardcoded expected hash/package/installer, where it's compared, and the on-detection action. **Flag explicitly which of these your resign will trip** and (for testing) the boolean sink or the hardcoded hash to patch. Note native integrity checks (a `.so` CRC-ing the DEX) → `native-analysis`.

**KB:** `kb_search("signature verification bypass")`, `kb_search("APK integrity check")`, `kb_search("installer package check")`.

---

## 6. Device / app attestation (Play Integrity, SafetyNet)
**What:** server-backed integrity — the app asks Google (or a vendor) to attest device/app state and a **server** checks the verdict, so there's often no local boolean to flip; the real gate is remote.

**Literals / symbols**
- Play Integrity API: `com.google.android.play.core.integrity`, `IntegrityManager`, `StandardIntegrityManager`, `IntegrityTokenRequest`, `requestIntegrityToken`, `integrityToken`.
- SafetyNet (legacy): `com.google.android.gms.safetynet`, `SafetyNetClient`, `attest(`, `ctsProfileMatch`, `basicIntegrity`, `nonce`.
- Vendor attestation SDKs (zimperium, Build38, Approov) — identify via `kb_search`.

**jadx queries:** code-search `IntegrityManager`, `requestIntegrityToken`, `SafetyNet`, `ctsProfileMatch`, `basicIntegrity`, `nonce`.

**Analysis/report:** whether the verdict is checked **locally** (patchable) or **server-side** (not — the token/nonce round-trips to a backend). Say which; for server-side attestation, note that static patching won't help and the finding is "remote integrity gate present." Trace the nonce source and where the token is sent.

**KB:** `kb_search("Play Integrity")`, `kb_search("SafetyNet attestation")`, `kb_search("ctsProfileMatch")`.

---

## Reaction patterns (don't stop at the first gate)
Detection and reaction are often decoupled — patching the obvious boolean may not be enough. Look for:
- **Deferred / probabilistic** responses: the check flags a field now and the app degrades, corrupts data, or crashes **later / elsewhere** (not an immediate `System.exit`).
- **Watchdog threads / periodic re-checks**: background `Thread`/`Handler`/`ScheduledExecutor` re-running checks, so a one-time patch gets overwritten or re-triggered.
- **Out-of-band reporting**: the result is sent to a backend (telemetry/attestation) and enforcement happens server-side — nothing local to flip.
- **Multiple redundant sinks**: the same verdict read in several places. Use `get_xrefs_to_method`/`get_xrefs_to_field` on the verdict getter/field to enumerate *every* consumer before concluding you've neutralized it.

---

## Reporting (all RASP)
Summarize as: **technique → class/method (or `.so`) → detection vector → on-detection action (immediate vs deferred/server-side, per Reaction patterns) → confidence**. Cover all categories present (anti-debug/root/emulation/hook **and** integrity/signature + attestation), and for integrity checks state which ones your resign would trip. Note whether each check is plain-text (found this pass) or inferred behind obfuscation (then cite `references/anti-analysis.md` and keep `[I]`/`[U]` markers), and before calling a check neutralized, enumerate every consumer of its verdict (xrefs) so you don't miss a redundant or watchdog sink. Cross-check each conclusion against the KB and cite the article when it agrees; record findings in the case notes.
