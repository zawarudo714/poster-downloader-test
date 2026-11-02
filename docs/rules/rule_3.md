<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

### 3. Say what you verified — and what you did not

State it without being asked, every time:

- what you checked by READING the code
- what you checked by RUNNING something
- what you did not check at all

"It compiles" is not verification. `py_compile` does not catch an undefined
name; a missing import once 500'd a whole page after being called "verified".
If you claim something is verified, name the test.

**And confirm the test itself RAN.** On 2026-09-15 a shell line ended in
`|| echo NO_UNDEFINED`, pyflakes was not installed in the sandbox, and the
"all clear" word printed anyway — a verification that reports success when
its tool is absent. A fallback on a check must print a FAILURE word, never
the success word; better, look for the tool's own output before believing
anything. (The repo's real undefined-name coverage is `check_undefined_names`
inside `preflight.py`, which needs no outside tool.)

