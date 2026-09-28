# Private source audit gate

T12 has no open items under the source acceptance policy. The
[T12 closure index](../../README.md) maps historical
pending items to their committed resolution and original reports, verified on
2026-09-21 at `943e544`. Dated counts below describe those snapshots; this
documentation reconciliation does not constitute a new test or gate run.

Run from the repository root with the supported Python 3.9 interpreter:

```sh
PYTHONDONTWRITEBYTECODE=1 /software/pkgs/python/3.9.13/bin/python3 -s -m pytest \
  -q -p no:cacheprovider utility/tests
PYTHONDONTWRITEBYTECODE=1 /software/pkgs/python/3.9.13/bin/python3 \
  utility/a_philosophy_gate.py --base-ref HEAD \
  --output .cad/a-philosophy/gate-report.json
```

For private CI, set `--base-ref` to the merge-base commit with the target branch.
The default `HEAD` compares an uncommitted working tree with the current commit.
Scanning reads working-tree contents of tracked, staged-new, and untracked
nonignored files. It is not a scan of staged blobs: a pre-commit caller that needs
exact index contents must run in a checkout of that index. The report records
source hashes and the comparison commit. Exit 0 means all configured static
checks passed, 1 means findings, and 2 means invalid configuration or Git input.

The entry point owns orchestration; separate modules own source discovery,
Python AST metrics, compatibility validation, size ceilings, import closure,
and SKILL style/load ownership. `skill_syntax.py` supplies the shared structural
reader to both SKILL checks and source-inventory tests.
All are internal tooling. Do not add these scripts, manifests, tests, or reports
to runtime inventories. No native extension, Virtuoso context, EDA job, archive,
or source-hidden acceptance runs as part of this gate.

The manifest responsibilities are:

- `a_philosophy_compatibility.json`: each reviewed legacy entry has one concrete
  replacement, source, owner, known repository consumers, migration condition,
  exposure status, and existing evidence files. `path:Class.method` references
  are checked by AST. A retained facade export is distinct from a canonical
  orchestration API defined in the same file. `removed` and `tombstone` records
  disallow operational exposure; their rejection behavior is verified by the
  linked product tests. External site migration is a stated exit condition,
  not something a repository AST scan can certify.
  The root includes 13 owner-area manifests under `compatibility/`, forming one
  registry of 193 records. Eleven earlier per-symbol records are consolidated
  into grouped records, preserving their owner authority. The 163 alias groups
  list 439 source/target symbol mappings; remaining records describe operations,
  state/property migrations, environment adapters and tombstones. Alias groups
  resolve direct imports, relative/absolute module references and assignments by
  AST; missing targets, cycles, copies and conditional reassignment fail. This
  is not a complete Python evaluator. The opt-in import regression checks exact
  object identity for every declared alias. Consumer lists are known importers
  of the source module, including tests, not proof that each uses every symbol.
  No repository consumer is represented by an empty list and an explicit
  external-consumer note; external migration still requires downstream evidence.
- `a_philosophy_reviews.json`: 66 reviewed keyword candidates, including 16
  modules with partial registrations before this batch. `has_registration`
  only means the file has at least one contract; it does not close review.
  Each decision names its scope, rationale, relevant contract IDs, evidence and
  an AST surface fingerprint. New imports/exports/signatures reopen the review;
  ordinary function-body edits do not. Missing evidence or invalid record links
  fail, while unreviewed/stale candidates remain visible review warnings.
  Canonical adapters and current data-format owners are not falsely scheduled
  for deletion. Keyword coverage is not an exhaustive compatibility inventory.
- `a_philosophy_size_baseline.json`: per-file self-owned exceptions above 500,
  with owner, rule, remediation ID, and exit condition. Increases and new
  exceptions fail against the comparison commit. Reductions require lowering
  the ceiling; resolved/deleted files require removing their entries. Tests
  obey the same rule. First introduction of the manifest requires review.
  Individually hashed third-party sources are a separate collection; naming a
  new directory `_vendor` cannot create an exemption.
- `a_philosophy_inventories.json`: six native-input inventories, explicit source
  search roots, supported entry modules, and packages requiring full library
  coverage. Development `__main__` launchers are explicitly identified. Relative
  and multiline imports, package initializers, cross-package dependencies, and
  literal dynamic imports contribute to the closure. Library source/name
  mismatches, duplicate sources, and reachable missing inputs fail.
- `private_import_boundaries.json`: existing F06 rules run within the gate and
  remain independently executable. Its included T12 boundary manifests declare
  scoped UI/backend operations, identity/product object surfaces and explicitly
  registered consumers of intentionally shared internal modules. Alias chains,
  absolute/relative imports, literal `getattr`, scoped local names and exact
  call exceptions are checked without executing source. Composition contracts
  are additionally exercised by the frontend fake/window tests; the static
  checker does not claim to prove arbitrary dynamic Python behavior.
- `skill_load_ownership.json`: 57 `.il` implementation fragments belong to two
  versioned AI loaders; ten files have explicit worker, runtime-init or site
  template lifecycles. Parent source literals and parent version guards, owner
  paths, evidence anchors, duplicate entries and stale records are checked.
  Production `.il` procedures otherwise need an enclosing local version guard
  with matching comparison/assignment values. Dependency-load guards are not
  mistaken for definition guards. Fixture/reference sources and the independently
  governed AIVW tree are reported separately; filenames ending in `Worker` do
  not create an exemption. This manifest is source applicability evidence, not
  a build-input inventory or runtime qualification.

Source discovery covers Python, C/C++ headers and sources, SKILL `.il/.ils` and
`.il.src`, shell, Ocean, and CMake. Hidden work/output directories, reference,
training, build/cache/log trees are excluded. Third-party files are reported
separately and are not included in self-owned denominators. C/C++ and SKILL are
counted as files; only Python has AST metrics in this tool. SKILL style applies
to `.il/.ils/.il.src`; load ownership applies to production `.il` definitions,
retaining the historical rule's language scope. The standalone style command
also scans tracked reference files. The structural reader understands comments,
quoting and frame nesting; it is not a full SKILL parser or evaluator. Parent
literal checks cannot prove dynamic execution order; source probes and loader
behavior tests cover execution semantics.

Files above 300 lines generate review warnings. Function and class spans,
methods, annotated fields, assigned attributes, bases, multiline/private imports,
and same-name/same-body candidates are review data, not additional hard rules.
The duplicate list excludes ordinary `main` and `build_parser` functions and
requires semantic comparison before merging anything. Nonliteral dynamic imports
and compatibility docstring matches are likewise candidates, not proof of a
contract violation. `from package import name` can import an attribute; the gate
adds `package.name` only if a local module exists. Behavioral tests must verify
exports and runtime-selected imports.

T12 is complete under the source-test acceptance policy. Migration commit
`0043399` supplies the service owners and evidence; integration commit `a6b3b97`
adds nine contracts and seven reviews to the formal manifests. At T12 closure all 65 candidates
were reviewed: the seven service candidates are four mixed modules, two canonical
owners and one compatibility facade. Current launchers, shared value/result types
and the startup lifecycle remain required; only the registered compatibility
surfaces have retirement conditions.

At T12 closure the native inventory had 467 explicit inputs, including 91 reviewed service
migration modules. The resolved bridge (11 lines) and background worker (498
lines) size baselines are removed. No analyzer rule, ceiling or exemption changed.
On the clean integration commit, the full gate passes with no errors; utility,
entry inventories and opt-in alias identity checks pass 138 tests with three
unenabled SKILL source probes skipped. The affected AI source suite passes 148
more tests: 286 distinct passes in this batch. Historical totals are not added.

Private final evidence, source hashes and the retained committed worktree are in
`.cad/a-philosophy-t12-final-20260920/`. Earlier draft/failure receipts remain in
their original private directories; their pending statuses are historical.
`../../docs/A_PHILOSOPHY_CODE_AUDIT.md` is the authoritative progress record. Subsequent
AS-06 execution/LSF changes and AS-07 work must satisfy the same gate with their
own inventory updates; they are outside this accepted T12 commit. No context,
native compilation, source-hidden execution or package qualification was run.

T11's live-model split (`5042d18`) subsequently adds two protocol alias groups
(ten exports) and one mixed-owner review: totals at that snapshot were 193 contracts,
439 exports and 66 reviewed candidates. Its three stateless production modules
bring the native input count to 470. Only the resolved live-model production/test
size baselines are removed. This uses the existing rules; T12 remains complete.

The declared UI/internal boundary batch is implemented (`d163eef`). Additional
service-migration boundaries belong to its AS-07 work, not an open-ended T12 expansion.
Run all declared alias identity checks with installed product dependencies:

```sh
CAD_RUN_COMPAT_SOURCE_IMPORTS=1 QT_QPA_PLATFORM=offscreen \
  PYTHONDONTWRITEBYTECODE=1 /software/pkgs/python/3.9.13/bin/python3 -s -m pytest \
  -q -p no:cacheprovider utility/tests/test_compatibility_source_exports.py
```

These tests import existing Python source and do not compile native modules.
The historical 19 style findings are resolved: four were structural-reader false
positives and 15 were normalized calls. The former blanket version-guard test now
uses the same ownership rule as this gate. Run source-only Virtuoso regressions
explicitly when available (this loads source, without compiling contexts):

```sh
CAD_RUN_SKILL_SOURCE_PROBES=1 PYTHONDONTWRITEBYTECODE=1 \
  /software/pkgs/python/3.9.13/bin/python3 -s -m pytest -q -p no:cacheprovider \
  utility/tests/test_skill_source_probes.py
```

Run the `utility/tests` and `rce/python/tests` suites in separate pytest invocations:
their existing bare `conftest` helper imports collide during combined collection.
The `ai/` Agent Service migration closed in `e262545`; its native source inventory,
compatibility retirement and source-only gate passed with zero errors. Current
evidence and limits are in [AS-07 records](../ai/docs/COPILOT_STUDIO_AGENT_SERVICE_AS07_RECORDS.md).
The T11/T12 counts and blockers below describe their dated source snapshots.
Keep entry checks and native input coverage intact for subsequent changes. Untracked root-level
debug `.il` sources also require explicit ownership; private evidence belongs
under `.cad/` and is excluded. Do not move another task's files just to pass.

The T11 PDK closure (`42ac3b0`) adds one retained policy-alias group (three
exports) and one mixed-owner review, using the existing rules. The registry now
has 194 contracts, 442 exports and 67 review decisions. On that source snapshot,
69 candidates include 66 currently reviewed, two unreviewed Agent background
modules and one stale project_sessions review. The PDK review is current; the
three parallel-service review clues remain with their owners. The four PDK native
inputs were already registered (474 total); this closure adds no production input.
Its full gate remains false solely for 15 Agent/LSF reachable native-input gaps.
Source acceptance of the PDK subtask does not qualify that complete gate or any
runtime release. New original evidence is outside the retained worktree in
`.cad/a-philosophy-t11-ai-pdk-closure-20260921/`; see the authoritative audit record.

The T11 schematic snapshot split (`750c6ec`) adds four explicit native inputs
(478 total), one schema/limit/error alias group (six mappings) and one mixed review.
The registry now has 195 contracts, 448 exports and 68 review decisions. Its
isolated source snapshot reports 70 candidates / 67 reviewed and 27 self-owned
files above 500 lines (eight tests); the three parallel-service review clues and
15 Agent/LSF native-input errors remain. Only the resolved schematic_snapshot size
baseline was removed. New original source/registry/JUnit/gate evidence is retained
outside its worktree in `.cad/a-philosophy-t11-ai-schematic-snapshot-20260921/`.
No rule, exemption or runtime qualification changed.

The T11 pinyin test split (`2d0dde7`) changes only test organization and removes
its resolved 514-line size baseline. The isolated snapshot now has 26 self-owned
files above 500 lines, including seven tests; zero Python AST errors. Its full gate
still fails only on the same 15 Agent/LSF native-input gaps. No production input,
compatibility record, review or analyzer rule changed. Source tests pass 43 cases,
including the existing real IBus/D-Bus probes. Original logs/JUnit, collection and
AST comparison, and gate evidence are retained outside the worktree in
`.cad/a-philosophy-t11-ai-pinyin-tests-20260921/`.

The circuit-create GUI test split (`bb793dc`) removes only its resolved 679-line
size baseline. Its integrated snapshot on `d362051` reports 2467 self-owned files,
25 above 500 lines (six tests), and zero AST errors. The full gate remains false:
18 service native-input gaps, including the newly added remote_sessions,
startup_lifecycle and startup_result, replace the prior snapshot's count of 15.
All other hard checks pass; the three existing review clues remain. This task
changes no production input, registry or rule. Source/GUI acceptance passes 59
distinct cases with one separate project GUI skip; the initial offscreen launch
failure and successful xcb receipts are retained separately. New original evidence
is outside the integrated worktree in
`.cad/a-philosophy-t11-ai-circuit-create-gui-tests-20260921/`.


The T11 launch split (`7c406c2`) adds six explicit native inputs (484 total),
seven same-object alias groups (16 mappings) and one facade review. The registry
now has 202 contracts, 464 exports and 69 review decisions; 71 candidates include
68 currently reviewed and the three existing parallel-service review clues.
Only launch.py's resolved 824-line baseline is removed. Its isolated snapshot
on ba81ef9 reports 2474 self-owned files, 24 above 500 (six tests), zero AST
errors, and passed=false solely for the same 18 service native-input gaps.
No analyzer, exemption or parallel service implementation changed.
Source acceptance passes 247 distinct cases with one unavailable local reference
library/dbAccess skip; registry/alias/MCP inputs and the absent-skills source
policy probe pass 37 more. Three initial CLI failures from an absent test binary
path and the successful rerun against the installed Codex 0.154.0 are retained
separately. Original evidence is outside the worktree in
`.cad/a-philosophy-t11-ai-launch-20260921/`. No context/native compilation, wheel
build, source-hidden execution or runtime package qualification was performed.


The controller split is source-tested on the isolated 84f7b3a + 3fc68b0 tree;
integration remains pending while the parallel service owner edits the three
shared native/compatibility/review manifests in the main working tree. Do not
report this isolated result as the changing main workspace or as merged work.
It adds five explicit native inputs (489 total), five alias groups (seven
mappings), one hash-only legacy operation and one mixed review: 208 contracts,
471 exports, 70 review decisions; 72 candidates / 69 reviewed. The existing
three service review clues remain. Only controller.py's resolved size baseline
is removed. The fixed gate reports 2488 self-owned files, 23 above 500 (six
tests), zero AST errors, and passed=false solely for the same 18 service input
gaps. Related source tests pass 141 with one unavailable reference-library skip;
registry/alias/MCP input checks pass 36 more. Original evidence is retained
outside the worktree in `.cad/a-philosophy-t11-ai-controller-20260921/`. No rules,
exemptions, parallel service implementation or runtime qualification changed.


Controller integration correction: main commit `9e02b1a` has exactly the accepted
`e2dc26e` tree. Task-only index application and narrow JSON insertions preserve
all 114 parallel unstaged file diffs and untracked paths. Acceptance is reused
without redundant runs. The fixed gate above remains valid for that committed
tree, not the uncommitted service retirement in the live working directory.


The private normalize-manifest split (`cff7e00`, rebased onto AS-07C e262545)
adds four alias groups (20 mappings), one review and the private script directory
to existing compatibility source search paths. This is audit lookup only, not a
native input or release permission. The four helpers remain excluded skill assets.
Its integrated fixed gate PASSES with errors=[]: 2495 self-owned files, 21 above
500 (five tests), zero AST errors and 68/68 candidates reviewed. Totals: 203
contracts, 487 mappings, 69 review decisions, 503 native inputs. The former 18
service input gaps are resolved by the parallel owner's committed migration.
The two resolved hard-size entries since controller's snapshot are that owner's
agent circuit-error test and this batch's normalizer. One advisory clue remains:
local_startup's existing review no longer matches a candidate. No rule changed.
Source/consumer acceptance passes 19 distinct cases, registry/identity 35. The
initial AIVW invocation omitted CAD_PYTHON_ROOT; its failure and successful single
rerun are retained separately. Evidence is outside the worktree at
`.cad/a-philosophy-t11-ai-normalize-manifest-20260921/`. This is source acceptance,
not Virtuoso/native compilation, source-hidden or runtime package qualification.


T11 is now closed under source acceptance by `005f4d1` (2026-09-21).
The remaining 18 responsibility records are resolved; the size baseline is empty.
The fixed committed gate passes with errors=[], 2578 self-owned files, zero
self-owned/test files above 500 lines, zero Python AST errors and 77/77 reviewed
candidates. The 243 soft-size warnings and two historical noncandidate reviews
remain visible. No analyzer, exemption, ceiling or release policy changed.

There are now 538 explicit AI native inputs, 222 compatibility contracts with
571 alias mappings, and 79 review decisions. The added 29 production modules
are inventoried individually. Source-loader groups include the extracted SKILL
fragments; Maestro cleanup precedes validator reload. The context source order
was inspected without compiling. Original collector identity/order and SKILL
procedure tokens are checked separately from this static gate.

Selected source reports contain 856 distinct passing cases, two failures also
reproduced on unchanged main, and one opt-in licensed router skip. The final
340-case combined run and the last 120 affected cases pass; overlapping suites
are not added. Initial runtime reader/GUI failures and successful reruns are
retained, without claiming their timing cause is resolved. This is not an
all-repository pytest pass or compiled/runtime release qualification.
Private original receipts and committed hashes are retained outside the worktree
in `.cad/a-philosophy-t11-final-20260921/`. See the authoritative audit's final
T11 record for failure details and integration preservation.


The authorized source follow-up is closed by `2fcf81e` (2026-09-21).
Its original gate was run on base `551bfad` plus the accepted changes; source
hashes match the implementation commit. It passes with errors=[], 2579 self-owned
files, zero self-owned/test files above 500 lines, zero Python AST errors and
77/77 reviewed candidates. The two obsolete AS-07C retirement reviews have been
removed after checking their current owners and consumers: 77 decisions remain,
with zero stale-review warnings. The 243 soft-size warnings remain advisory.
Totals remain 222 compatibility contracts, 571 alias mappings and 538 explicit
AI native inputs. There are no new production modules or analyzer/policy changes.

Selected source reports reconcile to 625 distinct passing cases, zero remaining
failures/errors/skips in that selection. Later affected-case results replace
earlier failures/skips; overlapping runs and prior batches are not added.
PDK snapshot/projection contracts, circuit transport and conversation-budget
tests, replay/GUI delivery timing, cancellable Codex reader cleanup and historical
RCE/MTS/AIVW/template cases are covered. The deterministic reader regression
does not reconstruct every cause of the earlier seven wide-suite failures.
This is neither an all-repository pytest result nor licensed site/runtime release
qualification. Original failed and successful reports remain in the external
private evidence directory `.cad/a-philosophy-followup-20260921/`; summary indexes
do not replace them. Documentation-only closure reuses this fixed source report.
See [the authoritative closure](../../README.md)
for the history reconciliation, evidence and remaining acceptance boundaries.
