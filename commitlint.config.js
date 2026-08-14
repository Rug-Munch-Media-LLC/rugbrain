// Minimal commitlint config for rugbrain
module.exports = {
	rules: {
		"type-enum": [
			2,
			"always",
			[
				"feat",
				"fix",
				"chore",
				"docs",
				"style",
				"refactor",
				"perf",
				"test",
				"ci",
				"build",
				"revert",
				"security",
				"migrate",
			],
		],
	},
};
