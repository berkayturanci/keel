1. **Optimize `_is_doc` in `src/keel/closure.py`:**
   - Change `any(part == "docs" for part in lowered.replace("\\", "/").split("/"))` to `"docs" in lowered.replace("\\", "/").split("/")`
   - This native `in` operation avoids the overhead of a generator expression with `any()`.

2. **Verify changes:**
   - Ensure `make test` still passes fully.
   - Run `pnpm lint` or associated Python lint (`make lint`) to verify codebase integrity.
   - Run `pre_commit_instructions` before submitting.

3. **Log learning (if applicable):**
   - The learning about native Python set operations vs `any()` generator expressions is already in `.jules/bolt.md`. We are just applying it.

4. **Submit PR:**
   - Title: "⚡ Bolt: optimize `_is_doc` check to use native `in` operator"
   - Description clearly defining what, why, impact, and measurement.
