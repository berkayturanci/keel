1. **Unroll `any()` generator in `src/keel/jsonschema_min.py`**
   - Replace the `any(_type_matches(instance, t) for t in types)` generator comprehension with an unrolled sequential `for` loop.
   - This avoids generator overhead and improves JSON schema validation performance, yielding up to a 2x speedup in type validation as demonstrated by benchmark.
2. **Log critical learning in `.jules/bolt.md`**
   - Add an entry detailing that unrolling `any()` generators avoids setup overhead and provides speedups in hot path logic like schema validation.
3. **Run testing and linting to verify functionality**
   - Execute `make test` and `make lint` to ensure validation still passes and conforms to codebase styles.
4. **Complete pre-commit steps to ensure proper testing, verification, review, and reflection are done.**
   - Run the pre commit instructions step to finalize everything.
5. **Submit the PR**
   - Branch: `bolt-unroll-any`
   - Title: `⚡ Bolt: [performance improvement] unroll any() generator in jsonschema_min type matching`
   - Follow PR body format with required emojis: `💡 What`, `🎯 Why`, `📊 Impact`, `🔬 Measurement`.
