// RugMunch fleet commitlint — canonical config (2026-08-23)
// Types match AGENTS.md convention. NOTE: ops: and security: are
// FLEET-MANDATED types (AGENTS.md, fleet-infra rules) — do not remove
// them or convention-compliant commits fail CI.
module.exports = {
	extends: ["@commitlint/config-conventional"],
	rules: {
		"type-enum": [
			2,
			"always",
			[
				"feat",
				"fix",
				"docs",
				"style",
				"refactor",
				"perf",
				"test",
				"build",
				"ci",
				"chore",
				"revert",
				"ops",
				"security",
			],
		],
		"scope-case": [2, "always", "lower-case"],
		"subject-case": [0, "never"],
		"subject-empty": [2, "never"],
		"type-empty": [2, "never"],
		"header-max-length": [2, "always", 120],
	},
};
