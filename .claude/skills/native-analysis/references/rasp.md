# Native RASP Playbook (`.so`)

Loaded on demand from `native-analysis`. Assumes the SKILL.md triage, the Ghidra-bridge prerequisite, and KB-grounding rules.

**Scope:** native (`.so`)-layer RASP. Java/Smali-layer RASP (manifest flags, `RootBeer`, Java signature checks, Play Integrity/SafetyNet) lives in the **`java-analysis`** skill's `references/rasp.md`; check both layers — hardened apps split checks across them, and native checks are the ones that survive Java-layer patching.

**For authorized testing only** (AGENTS.md). Report *where* each check lives and *how* it works; neutralizing-for-testing notes stay factual (which function/offset or branch), not a cracking recipe.

**Two cross-cutting facts:**
- Native RASP routinely uses **direct `SVC`** (not libc) to dodge Frida hooks on `openat`/`ptrace` → resolve those via `references/anti-analysis.md` §2.
- Reaction is often **decoupled** from detection (deferred crash, data corruption, watchdog pthread re-checking, or a verdict sent to a backend). Enumerate every consumer of a verdict (Ghidra xrefs) before assuming one patch neutralizes it.

---

## 1. Anti-debug
**Vectors:**
- `ptrace(PTRACE_TRACEME,0,0,0)` — via libc or direct `svc #117` (x0=0). A second attach then fails, blocking debuggers.
- **Self-debugging fork**: the lib `fork()`s and the child `ptrace`-attaches the parent, occupying the single tracer slot.
- `/proc/self/status` → parse **`TracerPid`** (non-zero = traced); `/proc/self/stat` field 2. Often read via `openat`+`read` SVC.
- `prctl(PR_SET_DUMPABLE, 0)`; `SIGTRAP`/`brk` tricks (see anti-analysis §3).
- Timing: `clock_gettime`/`gettimeofday` deltas bracketing a block.

**Find:** Ghidra xrefs to `ptrace`, `fork`, `prctl`, `__system_property_get`; `svc` sites (anti-analysis §2); strings `TracerPid`, `/proc/self/status`.

**Report:** function@offset, vector, reaction (immediate/deferred), and the boolean/return to flip for testing. `[I]`/`[U]` if behind obfuscation.

**KB:** `kb_search("TracerPid")`, `kb_search("ptrace anti-debug")`, `kb_search("self debugging fork ptrace")`.

## 2. Anti-root
**Vectors:** `stat`/`access`/`openat` on su & Magisk artifacts (`/system/bin/su`, `/sbin/su`, `/sbin/.magisk`, `/data/adb/magisk`, `magisk` sockets), often via **SVC** to evade hooks; `__system_property_get` for `ro.build.tags`/`ro.debuggable`/`ro.secure`; reading `/proc/mounts` for a rw `/system`; `dl_iterate_phdr`/maps for root-framework libs.

**Find:** Ghidra strings for the su/Magisk paths and prop names; xrefs to `__system_property_get`, `stat`, `access`; `svc openat` sites.

**Report:** the paths/props checked, the aggregator, and the reaction. **KB:** `kb_search("magisk detection native")`, `kb_search("su path")`.

## 3. Anti-Frida
**Vectors:**
- Scan `/proc/self/maps` (or `/proc/<pid>/maps`) for `frida-agent`, `frida-gadget`, `libgadget`, `linjector` — usually `openat`+`read` **SVC** to dodge libc hooks.
- `dl_iterate_phdr` / walking the loaded-module list for Frida libraries.
- Default port **27042/27043** connect-probe; D-Bus auth strings; thread names `gum-js-loop`, `pool-frida`, `gmain`.
- Scanning memory for Frida/GumJS signatures; checking `/data/local/tmp` for `re.frida.server`.
- Strings `frida`, `LIBFRIDA`, `gum-js-loop`.

**Find:** Ghidra strings + xrefs to `dl_iterate_phdr`, socket/`connect`, the maps-reading routine (anti-analysis §2).

**Report:** each vector, where it lives, and the reaction. **KB:** `kb_search("frida detection native")`, `kb_search("/proc/self/maps frida")`.

## 4. Anti-hook — trampoline / inline-hook detection
**What:** inline hooking frameworks (Frida Interceptor, Dobby, Substrate, xHook/SHADOWHOOK) overwrite a function **prologue** with a branch to a trampoline. Native RASP detects this by inspecting its own code.

**Vectors:**
- **Prologue inspection**: read the first few instructions of sensitive functions and check for an injected branch — `B`/`BR`, or the classic `LDR x16,#8 ; BR x16 ; <abs addr>` trampoline — instead of the expected prologue.
- **PLT/GOT integrity**: verify GOT entries resolve inside the expected library, not into an injected `.so`.
- **Known-good compare**: compare live bytes against an embedded copy/hash of the original prologue.
- Detecting breakpoints: scanning own `.text` for `brk`/`0xCC`-equivalent traps (ties to anti-analysis §3).

**Find:** Ghidra — routines that read executable addresses and compare the first bytes against constants, references to `/proc/self/maps` to locate module bases, and loops over a function-pointer list.

**Report:** which functions are self-checked, the detection method, and the reaction. **KB:** `kb_search("inline hook detection")`, `kb_search("trampoline detection arm64")`.

## 5. Anti-tamper / `.text` integrity (the hooking-vs-hash case)
**What:** the lib computes a hash/CRC over its own **`.text`** (or the executable pages from `/proc/self/maps`) and compares it to an **embedded expected digest**. Two things change those bytes and so trip it: a **binary patch** (e.g. your `nop`-ing an `svc`), and an **inline method hook** — Frida/Dobby rewriting a prologue literally mutates `.text`, so the hash no longer matches. This is why hooking a protected function can silently crash the app even though your hook "worked".

**Vectors / find in Ghidra:**
- A routine that resolves its own module base (via `dl_iterate_phdr` or parsing `/proc/self/maps`), walks the `.text` range, and feeds it to CRC32/MD5/SHA or a custom checksum.
- An embedded expected digest/const it compares against; the compare + branch to the tamper response.
- May run **periodically on a watchdog thread** (`pthread_create`) — not just once at init.

**Analysis/report:** the hashing function@offset, the `.text` range it covers, the expected digest, and the reaction. **Critical for this agent:** flag that (a) any native patch you make and (b) any inline hook on a covered function will trip this — so neutralizing it (or hooking outside the hashed range / restoring bytes after read) is a prerequisite for dynamic work. Cross-link §4 (same self-reading machinery). Note native integrity checks complement Java-layer signature checks (`java-analysis` rasp §5).

**KB:** `kb_search("text section integrity check")`, `kb_search("self crc anti-tamper")`, `kb_search("hook detection checksum")`.

---

## Reporting (all native RASP)
Summarize as: **technique → function@offset (or `.so`) → detection vector (note if via SVC) → reaction (immediate / deferred / watchdog / server) → confidence.** Enumerate every verdict consumer via xrefs before calling a check neutralized; keep offsets verbatim; cross-check each against the KB and cite on agreement; record in the case notes (`notes.md`). State which checks are plaintext vs inferred behind obfuscation (then see `references/anti-analysis.md` and keep `[I]`/`[U]`).
