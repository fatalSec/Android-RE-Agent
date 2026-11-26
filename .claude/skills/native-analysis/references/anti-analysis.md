# Native Anti-Analysis & Obfuscation Playbook (`.so`)

Loaded on demand from `native-analysis`. Assumes the SKILL.md triage, the Ghidra-bridge prerequisite, and KB-grounding rules. Ghidra (`ghidra-mcp`) gives disassembly / decompilation / xrefs; the **resolution** steps below need tools Ghidra does not provide. The `llm-re-wiki` KB is **optional** — when its `kb_*` tools aren't registered, skip every KB step here and rely on direct analysis. Write any files only into the target workspace beside the APK (`<apk-dir>/<apk-name>_analysis/`, see `AGENTS.md`), never into the agent home.

**External tooling (not `ghidra-mcp`) — install with consent.** Emulation and MBA/SMT solving run via Bash on bytes/addresses/registers you extract from Ghidra:
- **Unicorn Engine** (`pip install unicorn`) — concrete CPU emulation of a block/function (opaque predicates, CFF, indirect branches, string decryptors).
- **z3** (`pip install z3-solver`) — SMT: prove equivalence / simplify MBA.
- **SiMBA** (`DenuvoSoftwareSolutions/SiMBA`, from source) — fast linear-MBA simplifier.
- **gooMBA / gooMBA++** — MBA simplifier (Hex-Rays/IDA plugin; use if you have IDA — for a Ghidra flow prefer z3/SiMBA).
Per AGENTS.md Rule 1, ask before `pip install`/building these. If one is unavailable, say what that blocks and fall back to static decompiler reasoning where you can.

**Emulation method (shared by opaque predicates / CFF / indirect branches / string decryptors).** From Ghidra: the function/block byte range, its load address, and the entry register/memory state. Then: map the bytes in Unicorn at the real address, seed registers/stack, run from the start address, stop at the branch/`ret`/output write, and read the result (branch taken? computed target? decrypted buffer?). Emulate the *smallest* self-contained slice; stub syscalls/`bl` to externals. Honest limit: a block that reads global/heap state needs that state seeded or it won't emulate faithfully — say so rather than trust a bad run.

---

## 1. String encryption
**What:** no plaintext in `.rodata`; strings are built on the stack byte-by-byte or decrypted on first use (XOR / add / table), so a `strings` sweep (SKILL triage) comes back thin — itself a signal.

**Find:** Ghidra — functions with tight loops over a byte/dword array doing `eor`/`add`/`sub` and writing to a stack buffer; xrefs from many call sites into one small routine (the decryptor); large `.rodata`/`.data` blobs of high entropy.

**Recover:** read the decryptor in the decompiler if simple; otherwise **emulate it with Unicorn** (seed the ciphertext pointer + key, run, dump the output buffer). Enumerate call-site arguments (the ciphertext index/const) and batch-decode. Mark `[I]`/`[U]` if inferred.

**KB:** `kb_search("<protector> string encryption")`, `kb_search("stack string obfuscation arm64")`.

## 2. Direct syscalls (SVC) — identify & resolve
Protectors issue inline **`SVC`** to bypass libc hooks (Frida on `openat`/`ptrace`). `svc #0` = `0xd4000001`.

**Fetch the syscall table from the KB** (authoritative, per-arch): `kb_search("arm64 syscall table")` / `kb_read` the article, and `kb_search("<syscall name>")` for RASP usage. Keep this compact core as a fallback only:

| arm64 nr | name | RASP use |
| :-- | :-- | :-- |
| 56 `openat` · 63 `read` · 57 `close` | file I/O | stealth-open `/proc/self/maps`, `/system/bin/su` |
| 117 `ptrace` | debug | `PTRACE_TRACEME` (x0=0) anti-debug |
| 129 `kill` · 130/131 `tkill`/`tgkill` | signal | `SIGKILL` on tamper |
| 167 `prctl` | | `PR_SET_DUMPABLE 0` |
| 226 `mprotect` · 222 `mmap` | memory | RWX for unpacking / patch own `.text` |
| 78 `readlinkat` · 26/27 `inotify*` | | `/proc/self/exe`, watch maps/mem |

(ARM32 `armeabi-v7a`: nr in `r7`; `openat`=322, `ptrace`=26, `kill`=37, `mprotect`=125.)

**Resolve (ARM64):** nr in `w8`/`x8`, args `x0`–`x5`, return `x0`. 1) find every `svc`; 2) backtrace the value into `w8` — **de-obfuscate arithmetic** (`mov w8,#100; sub w8,w8,#44` → 56), resolving `add`/`sub`/`eor`/table lookups (emulate the setup slice if needed); 3) decode args (`w8==56` → `x1` = path string; `w8==117` → `x0==0` = `TRACEME`; `w8==129` → `x1==9` = `SIGKILL`); 4) note the `svc` offset for patching (`nop`=`0x1f2003d5`) — but see RASP §5 (`.text` integrity) first, patching may itself be detected.

**KB:** `kb_search("svc 0 arm64")`, `kb_search("direct syscall anti-debug")`.

## 3. BRK instruction presence
**What:** `brk #imm` (AArch64, encoding `0xD420xxxx`) raises `SIGTRAP`. Used for **anti-debug** (a `SIGTRAP` handler runs the app's own logic; if a debugger swallows the trap instead, the app detects it), breakpoint/trap-based dispatch, and self-debugging. The `#imm` often selects which branch/handler runs.

**Find & resolve:** search disassembly for `brk`; check for a `sigaction`/`signal` handler registered for `SIGTRAP` (that handler is the real continuation — read it). If control flow depends on the trap, **emulate** (Unicorn's interrupt hook) or read the handler to learn the taken path. Report each `brk` offset, its `#imm`, and the handler.

**KB:** `kb_search("brk SIGTRAP anti-debug")`.

## 4. Opaque predicates
**What:** a branch condition that is always true/false but looks data-dependent, creating dead/bogus blocks (often paired with CFF).

**Resolve:** **emulate the predicate block with Unicorn** to get its concrete truth value (seed any inputs it reads), or constant-propagate in the decompiler. Then mark the never-taken side as dead. Verify against disassembly — don't trust a decompiler that already "cleaned" it. Report the predicate, its constant value, and the dead branch.

**KB:** `kb_search("opaque predicate <protector>")`.

## 5. Control-flow flattening (CFF) recovery
**What:** the real basic blocks are hidden behind a dispatcher loop keyed on a state variable (`while(1){ switch(state){…} }`); static order is destroyed.

**Recover:** **emulate the dispatcher with Unicorn**, driving it across state values to record the real block-to-block transitions, or extract the state-transition table/constants statically. Rebuild the original CFG from the observed successors; annotate blocks in Ghidra so decompilation re-flows. Report the dispatcher, state var, and recovered order.

**KB:** `kb_search("control flow flattening <protector>")`, `kb_search("OLLVM flattening")`.

## 6. Indirect branch resolution
**What:** computed `br Xn` / `blr Xn` (jump tables, obfuscated dispatch) leave Ghidra unable to see successors, so functions look truncated.

**Resolve:** recover the jump table from Ghidra if it's a simple table-load; otherwise **emulate** the slice that computes the target register to get the concrete destination(s) (iterate the index for a table). Set the targets in Ghidra (data/xref) so auto-analysis continues into the real code. Report each indirect site and its resolved target(s).

**KB:** `kb_search("indirect branch obfuscation arm64")`.

## 7. Mixed Boolean-Arithmetic (MBA)
**What:** simple ops/constants rewritten as long chains mixing boolean (`& | ^ ~`) and arithmetic (`+ - * << >>`) — e.g. `x+y` as `(x^y)+2*(x&y)` — to hide keys, predicates, comparisons. Appears as dense arithmetic with no I/O feeding a compare/return.

**Resolve with SMT/MBA tools** (external, above): extract the expression from the Ghidra decompilation, express it for the solver, and simplify —
- **z3**: prove the obfuscated form equals a candidate, or simplify; good default in a Ghidra/Python flow.
- **SiMBA**: fast for linear MBA; feed the expression, get the reduced form.
- **gooMBA/gooMBA++**: if you're in IDA Hex-Rays.
For a small input domain you can also brute-force/evaluate (emulate) to confirm the reduced value. MBA is **not** simplified inside Ghidra — don't claim a reduced value you didn't verify (`[I]`/`[U]`). Feed the result back into §4 (predicates) or key recovery.

**KB:** `kb_search("MBA")`, `kb_search("mixed boolean arithmetic simplification")`.

---

## Reporting
Per finding: **technique → function@offset (`lib.so @ 0x…`) → what it hides/does → how resolved (which tool/emulation) → confidence**. Keep offsets/opcodes/registers verbatim, cross-check against the KB and cite on agreement, and record in the case notes (`notes.md`). If a resolution needed a tool that wasn't available, say so and what remains unresolved.
