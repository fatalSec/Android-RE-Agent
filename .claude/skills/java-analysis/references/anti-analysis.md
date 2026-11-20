# Anti-Analysis & Obfuscation Playbook

Loaded on demand from `java-analysis`. Assumes the SKILL.md tool table, triage sequence, and KB-grounding rules. Tools named here (`search_classes_by_keyword`, `get_method_body`, `get_xrefs_to_method`, `get_smali_of_class`, …) are defined there. All `code`-scope searches should pass a `package` filter on large APKs.

**Order of attack:** identify the obfuscator/packer first (it tells you what to expect). Fingerprint with **APKiD** (below), corroborate with `get_app_info` + `list_classes` for the package shape, then `kb_search` the vendor/product it reports (`kb_search("DexGuard", topic="protectors")`, `kb_search("Jiagu", topic="protectors")`, `kb_search("Obfuscator-LLVM")`) and read the `wiki/protectors/` profile. A named protector's profile lists its exact string-encryption scheme, CFF shape, and known decryptor signatures — far cheaper than rediscovering them.

### APKiD fingerprinting (run this first)
APKiD ("PEiD for Android", RedNaga) scans the APK's DEX with YARA rules and classifies the compiler, packer, obfuscator, and anti-analysis traits — the fastest way to learn what you're up against.

- **Run** (pass the APK path as an argument; never from inside an extracted dir — Rule 4):
  ```bash
  apkid -j <path-to-apk>
  ```
  `-j` is JSON; add `-r` to recurse a directory, `-t <sec>` to cap per-file time.
- **Read the output** — `matches` is keyed by category:
  - `compiler` — `dx`/`dexlib`/`r8`/`jack`… ; `dexlib` or unusual compilers often signal repackaging/tampering.
  - `packer` — Jiagu, Bangcle/SecShell, SecNeo, DexProtector, Tencent/Legu, ApkProtect, etc. → expect **runtime DEX loading** (§1); jump there.
  - `obfuscator` — DexGuard, Allatori, Arxan, Obfuscator-LLVM… → expect string encryption (§2), reflection (§3), MBA (§4), CFF/opaque predicates (§5).
  - `anti_vm` / `anti_debug` / `anti_disassembly` — tells you RASP is present even before searching → cross-link `references/rasp.md`.
  - `manipulator` / `abnormal` — signs of hand-editing or odd structure.
- **Availability & consent:** APKiD is a separate tool, not the jadx MCP. Check `PATH` first; if `apkid` isn't there, **ask the user before the anti-analysis fingerprint step** — either to install it (`pip install apkid`, which pulls a DEX-enabled `yara-python`) **or to give the path to an existing install** (they may have it in a venv/pipx not on `PATH`). Don't auto-install, and don't assume it's absent without asking. If they decline/skip, proceed with the manual indicators below.
- **Interpret honestly:** APKiD is static YARA signatures — it has **false negatives** on novel or custom protectors, and a clean result does **not** mean "no protection" (per Principle 4). Treat every hit as a lead to confirm in the code, feed the detected name to the KB (`kb_search`), and record it in the case notes (`Target → Protector`).

**Honest static limits:** encrypted classes and MBA often cannot be fully resolved from the DEX alone. Say so, extract what you can statically, and name the dynamic follow-up (Frida dump, `native-analysis`) instead of guessing.

---

## 1. Encrypted / packed classes (runtime DEX loading)
**What:** the real classes are decrypted at runtime and loaded by a custom `ClassLoader`; the DEX jadx sees is only the loader/stub. Common in commercial packers (DexGuard, Jiagu, Bangcle, SecNeo, AppSealing).

**Static indicators**
- Java: `DexClassLoader`, `PathClassLoader`, `InMemoryDexClassLoader`, `dalvik.system.DexFile`, `defineClass`, `ByteBuffer`-based DEX, decryption inside `Application.attachBaseContext` / `<clinit>`, `System.loadLibrary` of an unpacker `.so`.
- Resources: high-entropy or oddly-named blobs under `assets/`, `res/raw/`, or extra `classesN.dex` not referenced normally.

**jadx queries**
- `search_classes_by_keyword(search_term="DexClassLoader", search_in="code")` (and `InMemoryDexClassLoader`, `defineClass`, `openDexFile`).
- `list_resource_files()` then `get_resource_file(name=...)` on suspicious assets; check size/entropy and magic bytes (`dex\n035`).
- Inspect the app class: `get_main_application_classes_names` → `get_class_summary` → `get_method_body(method_name="attachBaseContext")`.

**Analysis**
- Trace where the decrypted `byte[]`/`ByteBuffer` comes from and which key/algorithm feeds it. If the decryption is in native code, hand off to `native-analysis`.
- State plainly that the hidden classes are not in the static dump; recommend a runtime DEX dump (Frida `DexClassLoader` hook / memory carving) to recover them.

**KB cross-check:** `kb_search("<packer name>")`, `kb_search("DEX unpacking")`, `kb_search("InMemoryDexClassLoader")`.

**Report:** loader class + method, source of the encrypted blob, key/algorithm if recoverable, and the exact dynamic step needed to dump the real DEX.

---

## 2. Encrypted strings
**What:** string literals are replaced by calls to a decryptor fed from static tables, so plain-text symbols (`su`, `frida`, URLs, keys) never appear in a `get_strings` or code search.

**Static indicators**
- A small method taking `int`/`long`/`String`/`byte[]` and **returning `String`**, with a tight loop of `^` (XOR), `>>>`/`<<`, `+`, array indexing, or `Base64`/`Cipher`.
- A `<clinit>` building a large `byte[]`/`int[]`/`char[]` table or `String[]` pool.
- Hundreds of call sites passing only integer/offset constants.

**jadx queries**
- Find the decryptor: `search_classes_by_keyword(search_term="^", search_in="code", package="<app pkg>")` narrowed by also trying `Base64`, `javax.crypto`, `SecretKeySpec`; or look for one-arg `String`-returning helpers via `get_class_summary` on utility classes.
- Read it: `get_method_body(class_name=..., method_name=...)` (smali via `get_method_smali` if it won't decompile).
- Enumerate usage: `get_xrefs_to_method(class_name=..., method_name=...)` — the call-site constants are the ciphertext/index arguments.

**Recovery**
- Reconstruct the algorithm + key/table and decode the constants from the xref list to recover plain-text (this is how hidden `"su"`, `"frida"`, C2 URLs resurface). Reimplement the routine offline if the volume is large. Note `[I]` if you inferred rather than confirmed a value.
- Watch for decoded values that are **class or method names** (e.g. `"android.telephony.TelephonyManager"`, `"getMethod"`): those feed reflective calls — hand them to §3 to reconstruct the hidden API. The same decryptor usually serves both plain strings and reflection targets.

**KB cross-check:** `kb_search("string decryption <packer>")`, `kb_search("XOR string decryptor")`.

**Report:** decryptor class/method, algorithm + key source, and the recovered strings (mark inferred ones).

---

## 3. API calls via reflection (targets usually encrypted → chain with §2)
**What:** sensitive APIs are invoked reflectively so they don't appear as direct call sites. **Assume the reflected class/method names are encrypted strings** (§2), not literals — you'll see `cls.getMethod(decrypt(0x3a), …)`, never `getMethod("getDeviceId")`. In hardened apps even the reflection *API* names (`"forName"`, `"getMethod"`, `"invoke"`) may be decrypted and dispatched reflectively ("reflection-on-reflection"). So §3 and §2 are one problem: find the reflection sites, then decode the strings that flow into them.

**Static indicators:** `Class.forName`, `getMethod`/`getDeclaredMethod`, `getField`/`getDeclaredField`, `Method.invoke`, `Constructor.newInstance`, `setAccessible(true)` — but expect their *arguments* to be decryptor calls, and see the zero-hits case below.

**Key point — the API references survive, the argument strings don't.** `Class.forName`/`Method.invoke` are ordinary method calls: their references live in the DEX method-id table and are **not** string-encryptable, so the *call sites* stay visible in jadx/smali even when the name arguments are encrypted. The thing you can't read directly is the decrypted *argument*, not the reflection call itself.

**jadx queries**
- Code-scope search for the API: `search_classes_by_keyword(search_in="code", package=...)` for `Class.forName`, `getMethod`, `getDeclaredMethod`, `.invoke(`, `newInstance`, `setAccessible`.
- For each hit, `get_method_body` the enclosing method and look at the argument expressions — they will typically be `<decryptor>(<int/offset const>)` calls, not literals.
- Smali confirmation when the Java view is noisy: `get_method_smali` and look for `Ljava/lang/Class;->forName`, `Ljava/lang/Class;->getMethod`, `Ljava/lang/reflect/Method;->invoke` opcodes — these appear even when the string operands are produced at runtime.

**Analysis — recover the real targets via the decryptor**
- The argument feeding `forName`/`getMethod` is a §2 decryptor output. Resolve it: identify the decryptor (§2), then **decode the call-site constants** (reconstruct the algorithm, or enumerate with `get_xrefs_to_method` on the decryptor) to recover the actual class and method names — e.g. `Class.forName(dec(0x12))` → `"android.telephony.TelephonyManager"`, `getMethod(dec(0x7))` → `"getDeviceId"`.
- Pair them up: the decoded class + decoded method name + the `invoke` target reconstruct the real call (e.g. `Runtime.exec("su")`, a root/debug probe, telephony/device-ID access). Reflection + decrypted names is the classic way RASP and data-harvesting hide their probes.
- Flag reflective wrappers around `Runtime.exec`/`ProcessBuilder`, telephony, `System.getProperty`, and crypto.

**Zero-hits case — reflection-on-reflection (the case the user flagged).** If code-search for the reflection APIs returns little on an app that is clearly obfuscated, the API names themselves are likely decrypted and dispatched through a single generic bridge. Then:
- Don't conclude "no reflection" (Principle 4). The reliable anchor collapses to the invoke sink: smali-search `Ljava/lang/reflect/Method;->invoke` / `Ljava/lang/reflect/Constructor;->newInstance` — those references can't be hidden.
- Find the central reflective dispatcher (a method taking a `String`/`Object[]` and calling `invoke`), then `get_xrefs_to_method` it to enumerate every reflective call; the decrypted strings passed in are both the class and the method names — decode them via §2.
- The decryptor is the master key: once decoded, every `dec(const)` argument across all reflection sites resolves at once.

**KB cross-check:** `kb_search("reflection <api>")`, `kb_search("reflective dispatcher <packer>")`, plus the protector profile (its reflection bridge + string scheme are often documented together).

**Report:** the reflection site (or central dispatcher), the decoded class+method it targets, how the names were recovered (which decryptor), and what the call does — mark any name you inferred but didn't confirm `[I]`/`[U]`.

---

## 4. Mixed Boolean-Arithmetic (MBA)
**What:** simple expressions/constants are rewritten as long, equivalent chains mixing boolean (`& | ^ ~`) and arithmetic (`+ - * << >>`) operations — e.g. `x + y` as `(x ^ y) + 2*(x & y)`. Used to hide keys, predicate values, and comparisons.

**Static indicators:** a method or expression with a dense run of bitwise+arithmetic ops, no I/O or field access, feeding a comparison, array index, or a value returned to a decryptor/predicate. jadx often leaves these as verbose Java it won't simplify.

**jadx queries**
- `get_method_body` the suspect method; if Java is noisy or wrong, `get_method_smali` for the exact opcode sequence (operand order matters for simplification).

**Analysis**
- Recognize-and-flag: MBA is **not** simplified inside jadx. For constant-folding, extract the exact ops and simplify offline with an SMT/MBA tool (e.g. z3, Arybo, msynth) or by evaluating the function over its input domain when small. Don't hand-wave a "simplified" value you didn't verify — mark `[I]`/`[U]`.
- MBA usually guards something (a key byte, an opaque predicate, a license check); once reduced, feed the result back into §2/§5.

**KB cross-check:** `kb_search("MBA")`, `kb_search("mixed boolean arithmetic")`, `kb_search("<protector> arithmetic obfuscation")`.

**Report:** location, what the expression gates, and the reduced value (with method + confidence), or the offline tool needed to reduce it.

---

## 5. Opaque predicates
**What:** a branch condition that is in fact always true or always false but looks input-dependent, inserted to add dead/bogus code and break decompilers — frequently paired with Control-Flow Flattening (CFF).

**Static indicators**
- Branches on identities like `(x*(x+1)) % 2 == 0` (always true), `x*x >= 0`, or a field set once then compared; code after the always-taken side that is never reached, or junk blocks.
- CFF signature: a `while(true)` + large `switch` on a state variable with `packed-switch`/`sparse-switch` in smali; jadx may emit `/* JADX WARNING */`.

**jadx queries**
- `get_method_smali(class_name=..., method_name=...)` to see real branch/`if-eqz`/`goto` structure and the switch dispatch (Java view hides it).

**Analysis**
- Evaluate the predicate: if it's a known algebraic identity or an MBA (§4), resolve it to its constant truth value, then treat the unreachable side as dead code. For CFF, reconstruct the real block order by following the state values the switch assigns.
- Note that jadx sometimes already prunes trivial opaque predicates — verify against smali before claiming a branch is live.

**KB cross-check:** `kb_search("opaque predicate")`, `kb_search("control flow flattening <protector>")`.

**Report:** method, the predicate and its constant value, and the real control flow / which branch is dead.
