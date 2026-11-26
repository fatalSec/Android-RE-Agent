<!--
Per-target case notes (the agent's durable working memory). See AGENTS.md → Working memory.
Copy this file to the target workspace beside the APK (<apk-dir>/<apk-name>_analysis/notes.md — never
into the agent home), then APPEND as you confirm
facts and RE-READ before reasoning/answering. Keep offsets/opcodes/FQNs VERBATIM.
Provenance markers: [V: src] secondary · [I] inferred · [U] unverified · [VS: ver] version-specific
· [X: …] contradicted · [AI Synthesis] model knowledge. Never write release keystore passwords here.
-->

# Analysis notes — <app label / package>

## Target
- Package: <com.example.app>
- Version: <versionName> (versionCode <n>) · minSdk <n> / targetSdk <n>
- APK file: <path>
- APK SHA-256: <hash>
- Protector / packer: <name or "none identified"> [provenance]
- Analysis started: <YYYY-MM-DD> · last updated: <YYYY-MM-DD>

## Decisions
<!-- Every HITL decision point reached (asked or auto-resolved), so later sessions resume
     with the same choices instead of re-deciding. See AGENTS.md -> Decision policy. -->
- <YYYY-MM-DD> `analysis_scope` → <full|targeted: areas> (<asked|auto>)
- <YYYY-MM-DD> `prior_workspace_exists` → <reuse|fresh|compare> (<asked|auto>)

## Resolved map (obfuscated → meaning)
<!-- de-obfuscated names so you don't re-resolve them; exact FQNs -->
- `a.b.c` → string decryptor, `a.b.c.d(int): String`
- `x.y.Z` → root-check aggregator
- Entry points: Application=`…`, main Activity=`…`

## Findings
<!-- one entry per confirmed finding; cite exact location + mechanism + confidence + KB cross-ref -->
### RASP
- <technique> — `class#method` (or `lib.so @ 0x…`) → <detection vector> → <on-detection action>. Confidence: <high/med> [provenance] · KB: <article link or "no coverage">

### Anti-analysis / obfuscation
- <encrypted strings / reflection / MBA / opaque predicate / packed classes> — `location`, <algorithm/key/notes> [provenance]

### Native (.so)
- Library: `lib/arm64-v8a/<name>.so` — <JNI_OnLoad / RegisterNatives table / SVC syscall> at `0x…` → <meaning> [provenance]

### Attack surface
- Exported components, custom schemes, `networkSecurityConfig`, `debuggable`, bundled assets of interest.

## Recovered strings / keys
- "<decoded value>" ← `decryptor(0x…)` [I/U if not confirmed]

## Modifications & signing
<!-- record what was patched and how it was resigned — NEVER the password -->
- Patches: <files/smali changed, why>
- Resign: mode=<debug|release> · keystore=<path> · alias=<alias> · output=<signed.apk> · SHA-256=<hash> · verified=<yes/no>

## Open questions / next steps
- [ ] <e.g. dump decrypted DEX at runtime (Frida) — classes not present statically>
- [ ] <trace native check offloaded to .so>
