// RugMunch fleet commitlint — canonical type set + rugbrain's extra
// 'migrate' type (schema/data migrations). ops:/security: are
// fleet-mandated types (AGENTS.md) — do not remove.
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
				"migrate",
			],
		],
		"scope-case": [2, "always", "lower-case"],
		"subject-case": [0, "never"],
		"subject-empty": [2, "never"],
		"type-empty": [2, "never"],
		"header-max-length": [2, "always", 120],
	},
};
