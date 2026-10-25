"""Optional intel apps (code-intel, grepapp, searxng) behind one typed envelope.

None of these are dependencies: every adapter takes an injected ``invoke``
callable, so the render/search path works with all three switched off.
Own-repo questions never come here — that is zoekt/serena's job.
"""
from rugbrain.intel_apps.code_intel import code_intel_facts
from rugbrain.intel_apps.grepapp import grep_github
from rugbrain.intel_apps.searxng import web_cited_search

__all__ = ["code_intel_facts", "grep_github", "web_cited_search"]
